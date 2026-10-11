"""
Module Name: matcher.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    The one "which book is this?" matcher for files on disk, shared by
    manual import, Library Import and the library check.

Location:
    /services/library/matcher.py
"""

import os
import re
import uuid
from difflib import SequenceMatcher
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from core.logging import get_module_logger
from core.text import format_runtime
from services.container import container

from .local_metadata_extractor import LocalMetadataExtractor

_LOGGER = get_module_logger("Service.Library.Matcher")

AUDIO_EXTENSIONS = {'.m4b', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wav'}
MATCH_CONFIDENCE_THRESHOLD = 0.85
CONFIDENT_STRATEGIES = {'library_asin', 'library_title', 'external_asin'}
_ASIN_IN_NAME = re.compile(r'\[([A-Za-z0-9]{10})\]')


# --- Files on disk ------------------------------------------------------------

def asin_in_names(*names: str) -> Optional[str]:
    """The first [ASIN] found in the given file or folder names."""
    for name in names:
        match = _ASIN_IN_NAME.search(name or '')
        if match:
            return match.group(1).upper()
    return None


def audio_files_in(directory: str) -> List[str]:
    """Audio files directly inside directory, sorted."""
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return sorted(
        os.path.join(directory, name) for name in names
        if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS
        and os.path.isfile(os.path.join(directory, name))
    )


def book_folders(root: str) -> Iterator[Tuple[str, List[str]]]:
    """(folder, sorted audio files) for every folder under root that directly holds audio."""
    for folder, _dirs, files in os.walk(root):
        audio = sorted(os.path.join(folder, name) for name in files
                       if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS)
        if audio:
            yield folder, audio


def choose_primary_file(audio_files: List[str]) -> str:
    """Prefer a single .m4b; otherwise the largest file (richest tags)."""
    m4b = [p for p in audio_files if p.lower().endswith('.m4b')]
    pool = m4b or audio_files

    def size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    return max(pool, key=size)


# --- Matching -----------------------------------------------------------------

def normalize_search_title(raw: Optional[str]) -> str:
    if not raw:
        return ''

    text = str(raw).strip()
    text = os.path.basename(text)
    text = os.path.splitext(text)[0]

    text = re.sub(r"\s*\[[^\]]+\]\s*$", '', text)

    text = text.replace('_', ' ')

    def _normalize_number(match: re.Match) -> str:
        label = match.group(1)
        number = match.group(2)
        try:
            number_val = str(int(number))
        except ValueError:
            number_val = number.lstrip('0') or number
        return f"{label} {number_val}"

    text = re.sub(
        r"\b(Book|Bk|Volume|Vol|Part|Episode)\s+0*([0-9]+)\b",
        _normalize_number,
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(r"\s+", ' ', text)
    text = re.sub(r"\s*[:;,]\s*", lambda m: f"{m.group(0).strip()} ", text)
    text = text.strip(" .-:_")
    return text


def classify_match(metadata: Optional[Dict[str, Any]], match_context: Dict[str, Any]) -> str:
    """'matched', 'needs_review' or 'unmatched' for a resolved match."""
    strategy = (match_context or {}).get('strategy')
    confidence = (match_context or {}).get('confidence')

    if not metadata or strategy in ('fallback', 'error', None):
        return 'unmatched'
    source = str(metadata.get('source', '')).lower()
    if source in ('manual_import', 'manual') and not confidence:
        return 'unmatched'
    if strategy in CONFIDENT_STRATEGIES:
        return 'matched'
    if confidence is not None and confidence >= MATCH_CONFIDENCE_THRESHOLD:
        return 'matched'
    return 'needs_review'


class BookFileMatcher:
    """Resolves local audio files to book metadata: the library first, then
    the Audible catalog, the hybrid search and Audnexus."""

    def __init__(self, *, logger=None) -> None:
        self.logger = logger or _LOGGER
        self.database_service = container.get_database_service()
        self.metadata_service = container.get_metadata_update_service()
        self.metadata_extractor = LocalMetadataExtractor()

    def match_folder(self, folder: Dict[str, Any]) -> Dict[str, Any]:
        """Extract embedded metadata and resolve the best metadata match."""
        primary = folder['primary_file']

        extracted = self.metadata_extractor.extract_metadata(primary)
        asin_hint = extracted.get('asin')
        if not asin_hint:
            asin_hint = asin_in_names(extracted.get('filename') or os.path.basename(primary))
        title_source = extracted.get('title') or extracted.get('clean_title') or folder['name']
        title_hint = normalize_search_title(title_source) if title_source else None
        author_hint = extracted.get('author') or extracted.get('album_artist')

        abs_hint = None
        if not asin_hint:
            from services.sync.audiobookshelf.hints import asin_for_folder
            abs_hint = asin_for_folder(folder['path'])
            asin_hint = abs_hint

        metadata, match_context = self.resolve(asin_hint, title_hint, author_hint, extracted)

        if metadata is not None:
            metadata['file_path'] = primary

        state = classify_match(metadata, match_context)
        message = None
        if abs_hint and metadata is not None and (metadata.get('ASIN') or metadata.get('asin') or '').upper() == abs_hint:
            from services.sync.audiobookshelf.hints import agrees
            match_context = dict(match_context or {}, strategy='abs_hint')
            if agrees(metadata, title_hint or folder['name'], author_hint):
                state = 'matched'
            else:
                state = 'needs_review'
                message = (f"Audiobookshelf suggests {metadata.get('Title') or metadata.get('title')} "
                           f"({abs_hint}), but it doesn't match the folder name or tags - check before accepting.")
        if state == 'unmatched':
            message = 'No confident metadata match found - review and assign manually.'
        return {'metadata': metadata, 'match': match_context, 'state': state, 'message': message}

    def resolve(
        self,
        asin_hint: Optional[str],
        title_hint: Optional[str],
        author_hint: Optional[str],
        extracted: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        match_context = {'strategy': 'fallback', 'confidence': None}
        if title_hint:
            title_hint = normalize_search_title(title_hint)
        asin = asin_hint.upper() if asin_hint else None

        if asin:
            existing = self.database_service.books.get_book_by_asin(asin)
            if existing:
                match_context = {'strategy': 'library_asin', 'confidence': 1.0}
                return existing, match_context

        existing = self._find_existing_by_title_author(title_hint, author_hint)
        if existing:
            match_context = {'strategy': 'library_title', 'confidence': 0.9}
            return existing, match_context

        lookup_source = None
        metadata_payload = self._lookup_external_metadata(asin, title_hint, author_hint)
        if metadata_payload:
            lookup_source = 'audible_metadata'
        else:
            metadata_payload = self._lookup_hybrid_metadata(title_hint, author_hint)
            if metadata_payload:
                lookup_source = 'hybrid_search'
            else:
                metadata_payload = self._lookup_audnexus_metadata(title_hint, author_hint)
                if metadata_payload:
                    lookup_source = 'audnexus_author'

        if metadata_payload:
            normalized = self.normalize_metadata(metadata_payload, asin, title_hint, author_hint, extracted)
            match_context = {
                'strategy': lookup_source or 'external_lookup',
                'confidence': metadata_payload.get('_match_confidence') if isinstance(metadata_payload, dict) else None
            }
            # The catalog returned the exact ASIN from the file, so no review is needed
            if asin and lookup_source == 'audible_metadata' and self._payload_asin(metadata_payload) == asin:
                match_context = {'strategy': 'external_asin', 'confidence': 1.0}
        else:
            normalized = self.build_fallback_book(asin, title_hint, author_hint, extracted)
            match_context = {'strategy': 'fallback', 'confidence': None}

        return normalized, match_context

    def search_candidates(
        self,
        query: Optional[str] = None,
        author: Optional[str] = None,
        asin: Optional[str] = None,
        limit: int = 10
    ) -> Dict[str, Any]:
        """Return potential metadata matches for manual remapping UI."""

        limit = max(1, min(limit or 10, 50))
        raw_query = (query or '').strip()
        normalized_query = normalize_search_title(raw_query) if raw_query else ''
        search_query = normalized_query or raw_query
        query_display = raw_query or search_query
        author = (author or '').strip()
        asin = asin.strip().upper() if isinstance(asin, str) and asin.strip() else None

        results: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        extracted_stub = {
            'source_path': '',
            'clean_title': search_query,
            'title': search_query,
            'author': author
        }

        def add_candidate(metadata: Optional[Dict[str, Any]], strategy: str, confidence: Optional[float]) -> None:
            if not metadata:
                return
            asin_value = metadata.get('ASIN') or metadata.get('asin') or asin
            title_hint_raw = metadata.get('Title') or metadata.get('title') or search_query
            title_hint = normalize_search_title(title_hint_raw) if title_hint_raw else search_query
            author_hint_local = metadata.get('Author') or metadata.get('author') or author

            enriched = metadata
            if asin_value:
                existing = self.database_service.books.get_book_by_asin(asin_value)
                if existing:
                    enriched = existing
                elif not self._metadata_has_multiple_authors(metadata):
                    external = self._lookup_external_metadata(asin_value, title_hint or query, author_hint_local or author)
                    if external:
                        enriched = external

            normalized = self.normalize_metadata(
                enriched,
                asin_value,
                title_hint,
                author_hint_local,
                extracted_stub
            )
            candidate_asin = normalized.get('ASIN')
            dedupe_key = candidate_asin or f"{normalized.get('Title')}::{normalized.get('Author')}"
            if dedupe_key in seen:
                return
            seen.add(dedupe_key)
            results.append({
                'asin': candidate_asin,
                'metadata': normalized,
                'source': normalized.get('source', strategy),
                'match': {'strategy': strategy, 'confidence': confidence}
            })

        if asin:
            existing = self.database_service.books.get_book_by_asin(asin)
            add_candidate(existing, 'library_asin', 1.0)

            if len(results) < limit:
                external = self._lookup_external_metadata(asin, search_query or None, author or None)
                add_candidate(external, 'external_asin', 0.9)

        if search_query:
            library_matches = self.database_service.books.search_books(search_query)

            for match in library_matches:
                add_candidate(match, 'library_search', 0.75)
                if len(results) >= limit:
                    break

            if len(results) < limit:
                hybrid_service = container.get_hybrid_audiobook_service()

                if hybrid_service:
                    try:
                        hybrid_matches = hybrid_service.search_books(search_query, num_results=max(limit * 2, 10)) or []
                    except Exception:
                        self.logger.warning("Catalog search failed for '%s'", search_query, exc_info=True)
                        hybrid_matches = []

                    title_lower = search_query.lower()
                    author_lower = author.lower() if author else ''
                    for candidate in hybrid_matches:
                        cand_title = (candidate.get('Title') or candidate.get('title') or '').strip()
                        if not cand_title:
                            continue
                        cand_author = (candidate.get('Author') or candidate.get('author') or '').strip()
                        score = SequenceMatcher(None, title_lower, cand_title.lower()).ratio()
                        if author_lower and cand_author:
                            cand_author_lower = cand_author.lower()
                            if author_lower in cand_author_lower or cand_author_lower in author_lower:
                                score += 0.1
                        if score < 0.45:
                            continue
                        add_candidate(candidate, 'hybrid_search', min(score, 1.0))
                        if len(results) >= limit:
                            break

            if len(results) < limit and author:
                audnexus_candidate = self._lookup_audnexus_metadata(search_query, author)
                add_candidate(audnexus_candidate, 'audnexus_search', 0.65)

        return {
            'query': query_display,
            'normalized_query': search_query,
            'author': author,
            'asin': asin,
            'results': results[:limit],
            'count': min(len(results), limit)
        }

    @staticmethod
    def _payload_asin(payload: Any) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        value = payload.get('ASIN') or payload.get('asin')
        return str(value).strip().upper() if value else None

    def _find_existing_by_title_author(self, title: Optional[str], author: Optional[str]) -> Optional[Dict[str, Any]]:
        if not title:
            return None
        matches = self.database_service.books.search_books(title)

        title_lower = title.lower()
        author_lower = author.lower() if author else None
        for match in matches:
            match_title = (match.get('Title') or '').lower()
            if title_lower not in match_title and match_title not in title_lower:
                continue
            if author_lower:
                match_author = (match.get('Author') or '').lower()
                if match_author and author_lower not in match_author and match_author not in author_lower:
                    continue
            return match
        return None

    def _lookup_external_metadata(
        self,
        asin: Optional[str],
        title: Optional[str],
        author: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        if not self.metadata_service:
            return None
        try:
            prepare = getattr(self.metadata_service, '_ensure_dependencies', None)
            if callable(prepare):
                prepare()
            searcher = getattr(self.metadata_service, 'search_strategies', None)
            if not searcher:
                return None
            return searcher.search_for_book_metadata(title or '', author or '', asin or '')
        except Exception as exc:
            self.logger.warning("Metadata lookup failed for %s/%s: %s", title, author, exc)
            return None

    def _lookup_hybrid_metadata(
        self,
        title: Optional[str],
        author: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Fallback search using the hybrid catalog service when metadata strategies fail."""

        if not title:
            return None

        try:
            hybrid_service = container.get_hybrid_audiobook_service()
        except Exception as exc:
            self.logger.debug("Hybrid service unavailable for title '%s': %s", title, exc)
            return None

        if not hybrid_service:
            return None

        queries: List[str] = []
        title_clean = title.strip()
        author_clean = (author or '').strip()

        if title_clean and author_clean:
            queries.append(f"{title_clean} {author_clean}")
        if title_clean:
            queries.append(title_clean)

        for query in queries:
            try:
                results = hybrid_service.search_books(query, num_results=15)
            except Exception as exc:
                self.logger.debug("Hybrid search failed for query '%s': %s", query, exc)
                continue

            best_match = self._select_best_hybrid_match(results, title_clean, author_clean)
            if best_match:
                self.logger.info("Hybrid lookup matched '%s' to '%s'", title_clean, best_match.get('Title'))
                return best_match

        self.logger.debug("Hybrid lookup found no suitable matches for '%s'", title_clean)
        return None

    def _lookup_audnexus_metadata(
        self,
        title: Optional[str],
        author: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Fallback search using the Audnexus author catalog when Audible APIs fail."""

        if not title or not author:
            return None

        try:
            audnexus_client = container.get_audnexus_client()
        except Exception as exc:
            self.logger.debug("Audnexus service unavailable for '%s': %s", title, exc)
            return None

        if not audnexus_client:
            return None

        title_clean = title.strip()
        author_clean = author.strip()
        if not title_clean or not author_clean:
            return None

        try:
            author_details = audnexus_client.find_author_by_name(author_clean)
        except Exception as exc:
            self.logger.debug("Audnexus author lookup failed for '%s': %s", author_clean, exc)
            return None

        if not author_details:
            return None

        candidates = author_details.get('titles', []) or []
        if not candidates:
            self.logger.debug("Audnexus author '%s' has no titles to evaluate", author_clean)
            return None

        best_match = self._select_best_audnexus_match(candidates, title_clean)
        if not best_match:
            self.logger.debug("Audnexus lookup found author '%s' but no matching title for '%s'", author_clean, title_clean)
            return None

        region = author_details.get('region', 'us') if isinstance(author_details, dict) else 'us'
        asin = best_match.get('asin') or best_match.get('ASIN')

        if asin:
            try:
                detailed = audnexus_client.get_book_details(asin, region=region)
            except Exception as exc:
                self.logger.debug("Audnexus book detail fetch failed for %s: %s", asin, exc)
            else:
                if detailed:
                    formatted = audnexus_client.format_book_for_compatibility(detailed)
                    formatted['source'] = 'audnexus'
                    self.logger.info("Audnexus lookup matched '%s' to '%s'", title_clean, formatted.get('Title'))
                    return formatted

        formatted = audnexus_client.format_book_for_compatibility(best_match)
        formatted['source'] = 'audnexus'
        self.logger.info("Audnexus lookup matched '%s' via author '%s' without detail fetch", title_clean, author_clean)
        return formatted

    def _select_best_hybrid_match(
        self,
        candidates: List[Dict[str, Any]],
        title_hint: str,
        author_hint: str
    ) -> Optional[Dict[str, Any]]:
        if not candidates:
            return None

        title_lower = title_hint.lower()
        author_lower = author_hint.lower() if author_hint else ''
        best: Optional[Dict[str, Any]] = None
        best_score = 0.0

        for candidate in candidates:
            cand_title = (candidate.get('Title') or '').strip()
            cand_author = (candidate.get('Author') or '').strip()
            cand_title_lower = cand_title.lower()
            cand_author_lower = cand_author.lower()

            if not cand_title:
                continue

            title_score = SequenceMatcher(None, title_lower, cand_title_lower).ratio()

            if title_lower in cand_title_lower or cand_title_lower in title_lower:
                title_score += 0.2

            if author_lower:
                if author_lower in cand_author_lower or cand_author_lower in author_lower:
                    title_score += 0.1

            if title_score > best_score and title_score >= 0.55:
                best_score = title_score
                best = candidate

            if title_score >= 0.95:
                return candidate

        return best

    def _select_best_audnexus_match(
        self,
        candidates: List[Dict[str, Any]],
        title_hint: str
    ) -> Optional[Dict[str, Any]]:
        if not candidates:
            return None

        title_lower = title_hint.lower()
        best: Optional[Dict[str, Any]] = None
        best_score = 0.0

        for candidate in candidates:
            cand_title = (candidate.get('Title') or candidate.get('title') or '').strip()
            if not cand_title:
                continue

            cand_lower = cand_title.lower()
            score = SequenceMatcher(None, title_lower, cand_lower).ratio()
            if title_lower in cand_lower or cand_lower in title_lower:
                score += 0.25

            if score > best_score and score >= 0.5:
                best_score = score
                best = candidate

            if score >= 0.95:
                return candidate

        return best

    def normalize_metadata(
        self,
        metadata: Dict[str, Any],
        asin_hint: Optional[str],
        title_hint: Optional[str],
        author_hint: Optional[str],
        extracted: Dict[str, Any]
    ) -> Dict[str, Any]:
        def pick(*keys: str, default: Optional[str] = None) -> Optional[str]:
            for key in keys:
                if key in metadata and metadata[key]:
                    value = metadata[key]
                    if isinstance(value, list):
                        flattened = [str(item).strip() for item in value if item]
                        if flattened:
                            return ', '.join(flattened)
                        continue
                    return value
            return default

        title = pick('Title', 'title', default=title_hint)
        authors_value = pick('Author', 'author', 'authors', default=author_hint)
        if isinstance(authors_value, list):
            author = ', '.join(authors_value)
        else:
            author = authors_value

        asin = asin_hint or pick('ASIN', 'asin')
        narrator = pick('Narrator', 'narrator', default=extracted.get('narrator'))
        series = pick('Series', 'series', default=extracted.get('series') or '').strip()
        if series.upper() == 'N/A':
            series = ''

        sequence = pick('Sequence', 'sequence', default=extracted.get('sequence') or '').strip()
        if sequence.upper() == 'N/A':
            sequence = ''
        publisher = pick('Publisher', 'publisher', default=extracted.get('publisher') or 'Unknown Publisher')
        release = pick('Release Date', 'release_date', default=extracted.get('year') or 'Unknown Release Date')
        runtime_minutes = metadata.get('runtime_length_min') or metadata.get('runtime_length')
        runtime = self._format_runtime(runtime_minutes, extracted.get('duration_seconds'))
        summary = pick('Summary', 'summary') or f'Manually imported audiobook ({title or "Unknown Title"}).'
        cover_image = pick('Cover Image', 'cover_image', 'cover_image_url') or ''

        normalized = {
            'ASIN': asin or self._generate_manual_asin(title or extracted.get('filename')),
            'Title': title or extracted.get('clean_title') or extracted.get('filename') or 'Untitled Import',
            'Author': author or 'Unknown Author',
            'Narrator': narrator or 'Unknown Narrator',
            'Series': series or '',
            'Sequence': sequence,
            'series_asin': pick('series_asin', 'Series ASIN'),
            'Publisher': publisher,
            'Release Date': release,
            'Runtime': runtime,
            'Summary': summary,
            'Cover Image': cover_image,
            'Language': pick('Language', 'language', default='English'),
            'Overall Rating': pick('Overall Rating', 'overall_rating', 'rating', default='N/A'),
            'Rating': pick('Rating', 'rating', default='N/A'),
            'num_ratings': metadata.get('num_ratings') or metadata.get('ratings', 0),
            'source': metadata.get('source', 'audible'),
            'library_status': 'owned',
            'file_path': extracted.get('source_path')
        }
        return normalized

    def build_fallback_book(
        self,
        asin_hint: Optional[str],
        title_hint: Optional[str],
        author_hint: Optional[str],
        extracted: Dict[str, Any]
    ) -> Dict[str, Any]:
        runtime = self._format_runtime(None, extracted.get('duration_seconds'))
        series = (extracted.get('series') or '').strip()
        if series.upper() == 'N/A':
            series = ''

        sequence = (extracted.get('sequence') or '').strip()
        if sequence.upper() == 'N/A':
            sequence = ''
        return {
            'ASIN': asin_hint or self._generate_manual_asin(title_hint or extracted.get('filename')),
            'Title': title_hint or extracted.get('clean_title') or extracted.get('filename') or 'Untitled Import',
            'Author': author_hint or extracted.get('author') or 'Unknown Author',
            'Narrator': extracted.get('narrator') or 'Unknown Narrator',
            'Series': series,
            'Sequence': sequence,
            'Publisher': extracted.get('publisher') or 'Unknown Publisher',
            'Release Date': extracted.get('year') or 'Unknown Release Date',
            'Runtime': runtime or 'Unknown Runtime',
            'Summary': f'Manually imported from {os.path.basename(extracted.get("source_path", "unknown"))}',
            'Cover Image': '',
            'Language': 'English',
            'Overall Rating': 'N/A',
            'Rating': 'N/A',
            'num_ratings': 0,
            'source': 'manual_import',
            'library_status': 'owned',
            'file_path': extracted.get('source_path')
        }

    def _format_runtime(self, runtime_minutes: Optional[Any], duration_seconds: Optional[Any]) -> Optional[str]:
        minutes = None
        if runtime_minutes:
            try:
                minutes = float(runtime_minutes)
            except (TypeError, ValueError):
                minutes = None
        if minutes is None and duration_seconds:
            try:
                minutes = float(duration_seconds) / 60.0
            except (TypeError, ValueError):
                minutes = None
        if minutes is None:
            return None
        return format_runtime(minutes)

    def _generate_manual_asin(self, seed: Optional[str]) -> str:
        cleaned = (seed or 'MANUAL').upper().replace(' ', '')[:6]
        return f"MANUAL-{cleaned}-{uuid.uuid4().hex[:6].upper()}"

    @staticmethod
    def _metadata_has_multiple_authors(metadata: Optional[Dict[str, Any]]) -> bool:
        if not metadata:
            return False
        author_fields = []
        for key in ('Authors', 'authors', 'Author', 'author'):
            value = metadata.get(key)
            if value:
                author_fields.append(value)
        for value in author_fields:
            if isinstance(value, list) and len(value) > 1:
                return True
            if isinstance(value, str) and ',' in value:
                return True
        return False
