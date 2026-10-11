"""
Module Name: quality_assessor.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Quality scoring system for audiobook search results. Assesses format
    preferences, bitrate, source reputation, metadata completeness, and
    relevance to rank search results.

Location:
    /services/acquisition/search/quality_assessor.py
"""

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional, Tuple

from core.logging import get_module_logger
from core.language_matching import language_allowed
from services.settings.languages import get_preferred_languages
from core.database.repositories.search_preferences import DEFAULT_PREFERENCES

from .fuzzy_matcher import FuzzyMatcher
from .book_matcher import BookMatcher, BookComponents


_LOGGER = get_module_logger("Service.SearchEngine.QualityAssessor")


def get_search_preferences() -> Dict[str, Any]:
    """Saved search preferences, or the defaults if they can't be read."""
    try:
        from services.container import container
        return container.get_database_service().search_preferences.get_search_preferences()
    except Exception:
        _LOGGER.warning("Could not load search preferences; using defaults", exc_info=True)
        return dict(DEFAULT_PREFERENCES)


@dataclass
class QualityScore:
    """Represents a quality assessment result.

    Two independent 0-100 numbers, deliberately not blended together:
      match_confidence  - is this the book/edition searched for (title,
                           author, series/number, language)?
      release_quality    - given it IS the right book, how good is this
                           specific release (format, bitrate, availability,
                           metadata), judged against the user's actual
                           search_preferences?
    total_score/confidence are kept as aliases of the pre-split fields
    (relevance_score*10 / match_confidence) for any caller not yet updated -
    new code should read match_confidence/release_quality directly.
    """
    total_score: float
    format_score: float
    bitrate_score: float
    metadata_score: float
    relevance_score: float
    confidence: float
    match_confidence: float
    release_quality: float
    breakdown: Dict[str, Any]


class QualityAssessor:
    """Assesses the quality of audiobook search results."""
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
        
        self.format_scores = {
            'm4b': 10,
            'm4a': 8,
            'mp3': 6,
            'aac': 5,
            'ogg': 4,
            'flac': 7,
            'unknown': 1
        }
        
        self.min_bitrate = 64
        self.preferred_bitrate = 128
        self.max_useful_bitrate = 320
        
        self.weights = {
            'relevance': 0.95,
            'format': 0.03,
            'bitrate': 0.00,
            'metadata': 0.02,
            'availability': 0.00
        }
        
        self.initialized = False
        self._initialize()
    
    def _initialize(self):
        """Initialize the quality assessor."""
        try:
            self.logger.debug("Initializing QualityAssessor")
            
            self.fuzzy_matcher = FuzzyMatcher()
            self.book_matcher = BookMatcher()
            
            if not self.format_scores or not self.weights:
                raise Exception("Missing configuration data")
            
            self.initialized = True
            self.logger.debug("QualityAssessor initialized successfully")
            
        except Exception as e:
            self.logger.error(
                "Failed to initialize QualityAssessor",
                extra={"error": str(e)},
                exc_info=True,
            )
            self.initialized = False

    @staticmethod
    def _reports_bitrate(result: Dict[str, Any]) -> bool:
        """Return False for indexers that structurally never expose bitrate.

        MyAnonamouse and AudiobookBay hardcode bitrate to 0 in their provider
        adapters - their search APIs simply don't return it, so a 0 here means
        "unknowable," not "bad." That's a different case from torznab/newznab
        indexers, which genuinely attempt to extract bitrate (from media
        attributes or by regex-matching the title) and only land on 0 when
        that attempt fails - a weaker but real signal of incomplete metadata,
        which should still be penalized.
        """
        if any(str(key).startswith('mam_') for key in result.keys()):
            return False
        indexer_name = (result.get('indexer') or '').lower()
        source_tag = (result.get('_source') or '').lower()
        if 'audiobookbay' in indexer_name or 'audiobookbay' in source_tag:
            return False
        # MAM proxied through Prowlarr/Jackett arrives as plain Torznab
        if 'myanonamouse' in indexer_name:
            return False
        return True

    @staticmethod
    def _is_usenet_result(result: Dict[str, Any]) -> bool:
        """Return True when the result originates from a Usenet (NZB) indexer.

        Usenet is a direct download: there are no seeders, and the contained
        files are obfuscated so the real audio format is unknown until after the
        download completes. Neither seeders nor format should affect confidence
        for these results - the post-download MP3->M4B conversion exists exactly
        because the format isn't known up front. Newznab indexers emit
        ``protocol``/``result_type`` of ``"nzb"``.
        """
        protocol = (result.get('protocol') or result.get('result_type') or '').lower()
        return protocol in ('nzb', 'newznab', 'usenet')
    
    def assess_result_quality(self, result: Dict[str, Any], search_title: str = '', search_author: str = '',
                               preferred_languages: Optional[Any] = None,
                               search_preferences: Optional[Dict[str, Any]] = None,
                               query_components: Optional[BookComponents] = None) -> QualityScore:
        """Assess the quality of a search result."""
        prefs = search_preferences or DEFAULT_PREFERENCES
        try:
            self.logger.debug(
                "Assessing result quality",
                extra={
                    "search_title": search_title,
                    "search_author": search_author,
                    "result_title": result.get('title', 'N/A'),
                },
            )
            
            format_str = result.get('format', 'unknown').lower()
            bitrate = result.get('bitrate', 0)
            seeders = result.get('seeders', 0)
            result_title = result.get('title', '')
            result_author = result.get('author', '')
            
            relevance_score, relevance_meta = self._assess_relevance(
                result_title,
                result_author,
                search_title,
                search_author,
                result=result,
                query_components=query_components,
            )
            format_score = self._assess_format_quality(format_str, prefs)
            bitrate_score = self._assess_bitrate_quality(bitrate, prefs)
            metadata_score = self._assess_metadata_quality(result)
            availability_score = self._assess_availability_quality(seeders)

            is_usenet = self._is_usenet_result(result)
            if is_usenet:
                format_score_eff = 7.0
                availability_score = 7.0
            else:
                format_score_eff = format_score
                indexer_name = (result.get('indexer') or '').lower()
                source_tag = (result.get('_source') or '').lower()
                if ('audiobookbay' in indexer_name or 'audiobookbay' in source_tag) and seeders <= 1:
                    availability_score = 8.0

            bitrate_score_eff = bitrate_score if self._reports_bitrate(result) else 7.0

            self.logger.debug(
                "Quality scores computed",
                extra={
                    "result_title": result_title,
                    "relevance_score": round(relevance_score, 1),
                    "format_score": round(format_score, 1),
                },
            )
            
            total_score = (
                relevance_score * self.weights['relevance'] +
                format_score_eff * self.weights['format'] +
                bitrate_score_eff * self.weights['bitrate'] +
                metadata_score * self.weights['metadata'] +
                availability_score * self.weights['availability']
            )

            match_confidence = self._calculate_match_confidence(
                relevance_score=relevance_score,
                relevance_meta=relevance_meta,
                result=result,
                preferred_languages=preferred_languages,
            )

            release_quality = self._calculate_release_quality(
                format_score=format_score_eff,
                bitrate_score=bitrate_score_eff,
                metadata_score=metadata_score,
                availability_score=availability_score,
            )

            return QualityScore(
                total_score=total_score,
                format_score=format_score,
                bitrate_score=bitrate_score,
                metadata_score=metadata_score,
                relevance_score=relevance_score,
                confidence=match_confidence,
                match_confidence=match_confidence,
                release_quality=release_quality,
                breakdown={
                    'relevance_score': relevance_score,
                    'book_number_status': relevance_meta.get('book_number_status'),
                    'author': relevance_meta.get('author'),
                    'title': relevance_meta.get('title'),
                    'series': relevance_meta.get('series')
                }
            )

        except Exception as e:
            self.logger.error(
                "Quality assessment failed",
                extra={"error": str(e), "search_title": search_title, "search_author": search_author},
                exc_info=True,
            )
            return QualityScore(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, {})
    
    def _assess_relevance(self, result_title: str, result_author: str,
                          search_title: str, search_author: str,
                          result: Optional[Dict[str, Any]] = None,
                          query_components: Optional[BookComponents] = None) -> Tuple[float, Dict[str, Any]]:
        """Assess how well the result matches the search query using multi-strategy matching.

        Relevance Scoring (NEW SYSTEM):
        - Author match: 60% weight (0-6 points) - MOST IMPORTANT
          Strategy 1: Exact normalized match (remove ALL punctuation/spaces)
          Strategy 2: Token set overlap (handles "Author1, Author2")
          Strategy 3: Fuzzy character match (handles typos)

        - Title match: 25% weight (0-2.5 points)
          Strategy 1: All search tokens present in result
          Strategy 2: Token set overlap (70%+ words match)
          Strategy 3: Substring or fuzzy match

        - Series match: 15% weight (0-1.5 points)

        Total: 0-10 points

        Returns:
            Relevance score (0-10)
        """
        search_title = search_title or ''
        search_author = search_author or ''
        result_title = result_title or ''
        result_author = result_author or ''
        
        score = 0.0
        meta: Dict[str, Any] = {
            'author': {'score': 0.0, 'status': 'unknown'},
            'title': {'score': 0.0, 'status': 'unknown'},
            'series': {'score': 0.0, 'status': 'unknown'},
            'book_number_status': 'not_applicable',
            'search_numbers': [],
            'result_numbers': []
        }
        
        if not search_title and not search_author:
            return 5.0, meta
        
        # --- Author matching (60% = 6 points max) ---

        author_score = 0.0
        if search_author and result_author:
            search_author_norm = self.fuzzy_matcher.normalize_author(search_author)
            result_author_norm = self.fuzzy_matcher.normalize_author(result_author)
            
            self.logger.debug(
                "Author normalization",
                extra={"search_author_norm": search_author_norm, "result_author_norm": result_author_norm},
            )
            
            if search_author_norm in result_author_norm or result_author_norm in search_author_norm:
                author_score = 6.0
                self.logger.debug(
                    "Author normalized substring match",
                    extra={"author_score": author_score},
                )
            else:
                search_author_cleaned = self.fuzzy_matcher.normalize_title(search_author)
                result_author_cleaned = self.fuzzy_matcher.normalize_title(result_author)
                
                search_tokens = self.fuzzy_matcher.tokenize(search_author_cleaned)
                result_tokens = self.fuzzy_matcher.tokenize(result_author_cleaned)
                
                if search_tokens and result_tokens:
                    common = search_tokens & result_tokens
                    
                    if common:
                        overlap = len(common) / max(len(search_tokens), len(result_tokens))
                        
                        if overlap >= 0.5:
                            author_score = 6.0 * overlap
                            self.logger.debug(
                                "Author token overlap",
                                extra={"overlap": overlap, "author_score": author_score},
                            )
                        else:
                            author_match = self.fuzzy_matcher.fuzzy_match(result_author, search_author)
                            
                            if author_match.score >= 0.7:
                                author_score = 6.0 * author_match.score
                                self.logger.debug(
                                    "Author fuzzy match",
                                    extra={"match_score": author_match.score, "author_score": author_score},
                                )
                    else:
                        author_match = self.fuzzy_matcher.fuzzy_match(result_author, search_author)
                        if author_match.score >= 0.7:
                            author_score = 6.0 * author_match.score
                            self.logger.debug(
                                "Author fuzzy match",
                                extra={"match_score": author_match.score, "author_score": author_score},
                            )
                else:
                    author_match = self.fuzzy_matcher.fuzzy_match(result_author, search_author)
                    if author_match.score >= 0.7:
                        author_score = 6.0 * author_match.score
                        self.logger.debug(
                            "Author fuzzy match",
                            extra={"match_score": author_match.score, "author_score": author_score},
                        )
            
            if author_score == 0.0:
                self.logger.debug("Author match not found", extra={"author_score": author_score})
        elif search_author:
            author_score = 0.0
            self.logger.debug("Author missing in result", extra={"author_score": author_score})
        else:
            author_score = 3.0
            self.logger.debug("Author not searched (neutral)", extra={"author_score": author_score})
        
        meta['author']['score'] = author_score
        if not search_author:
            meta['author']['status'] = 'not_provided'
        elif author_score > 0:
            meta['author']['status'] = 'match'
        else:
            meta['author']['status'] = 'no_match'

        score += author_score

        raw_result_title = result_title
        result_title, title_segments = self._clean_release_title(result_title, result_author or search_author)
        book_title_attr = ((result or {}).get('raw_attributes') or {}).get('booktitle')
        if book_title_attr:
            title_segments = title_segments + [str(book_title_attr)]
        exact_title = self._has_exact_title(title_segments, search_title)

        search_series = self._extract_series_from_title(search_title)
        if result is not None and (result.get('series') or result.get('sequence')):
            rc = self.book_matcher.extract_from_result(result)
            result_series = {
                'series_name': rc.series_name,
                'book_number': str(int(rc.book_number)) if rc.book_number is not None and rc.book_number == int(rc.book_number) else (str(rc.book_number) if rc.book_number is not None else None),
                'full_series': rc.series_name,
            }
        else:
            result_series = self._extract_series_from_title(result_title)
        
        self.logger.debug(
            "Series extraction",
            extra={
                "search_title": search_title,
                "result_title": result_title,
                "search_series": search_series,
                "result_series": result_series,
            },
        )
        
        # --- Title matching (25% = 2.5 points max) ---

        title_score = 0.0
        if search_title and result_title:
            search_title_norm = self.fuzzy_matcher.normalize_title(search_title)
            result_title_norm = self.fuzzy_matcher.normalize_title(result_title)
            
            if result_series['full_series']:
                result_title_core = result_title_norm.replace(result_series['full_series'].lower(), '').strip(' ,:;-')
            else:
                result_title_core = result_title_norm
                
            if search_series['full_series']:
                search_title_core = search_title_norm.replace(search_series['full_series'].lower(), '').strip(' ,:;-')
            else:
                search_title_core = search_title_norm
            
            self.logger.debug(
                "Title normalization",
                extra={"search_title_core": search_title_core, "result_title_core": result_title_core},
            )
            
            search_tokens = self.fuzzy_matcher.tokenize(search_title_core)
            result_tokens = self.fuzzy_matcher.tokenize(result_title_core)
            
            if search_tokens and result_tokens:
                if search_tokens.issubset(result_tokens):
                    title_score = 2.5
                    self.logger.debug(
                        "Title tokens match",
                        extra={"title_score": title_score, "search_tokens": list(search_tokens)},
                    )
                else:
                    overlap = self.fuzzy_matcher.token_set_overlap(search_tokens, result_tokens)
                    
                    if overlap >= 0.7:
                        title_score = 2.5 * overlap
                        self.logger.debug(
                            "Title token overlap",
                            extra={"overlap": overlap, "title_score": title_score},
                        )
                    else:
                        if (search_title_core in result_title_core or 
                            result_title_core in search_title_core or
                            search_title_norm in result_title_norm or 
                            result_title_norm in search_title_norm):
                            title_score = 2.5
                            self.logger.debug("Title substring match", extra={"title_score": title_score})
                        else:
                            title_match = self.fuzzy_matcher.fuzzy_match(result_title_core, search_title_core)
                            
                            if title_match.score >= 0.7:
                                title_score = 2.5 * title_match.score
                                self.logger.debug(
                                    "Title fuzzy match",
                                    extra={"match_score": title_match.score, "title_score": title_score},
                                )
            else:
                if search_title_core in result_title_core or result_title_core in search_title_core:
                    title_score = 2.5
                    self.logger.debug("Title substring match", extra={"title_score": title_score})
                else:
                    title_match = self.fuzzy_matcher.fuzzy_match(result_title_core, search_title_core)
                    if title_match.score >= 0.7:
                        title_score = 2.5 * title_match.score
                        self.logger.debug(
                            "Title fuzzy match",
                            extra={"match_score": title_match.score, "title_score": title_score},
                        )
            
            query_comps = query_components or self.book_matcher.extract_from_title(search_title)
            result_comps = (
                self.book_matcher.extract_from_result(result)
                if result is not None
                else self.book_matcher.extract_from_title(result_title)
            )

            result_number = result_comps.book_number
            is_part = self._is_part_release(raw_result_title, search_title)
            series_bundle = bool(query_comps.series_name) and self._series_range(result_title, query_comps.series_name)
            if is_part or series_bundle:
                result_number = None
            elif result_number is None and query_comps.series_name:
                result_number = self._number_after_series(result_title, query_comps.series_name)

            meta['query_book_number'] = query_comps.book_number
            meta['result_book_number'] = result_number
            meta['result_is_bundle'] = result_comps.is_bundle
            meta['result_components_source'] = result_comps.source
            meta['exact_title'] = exact_title

            number_status = self.book_matcher.compare_numbers(query_comps.book_number, result_number)
            meta['book_number_status'] = number_status

            if is_part:
                title_score = 0.0
                meta['book_number_status'] = 'part_split'
                self.logger.debug("Partial release for a whole-book search")
            elif (result_comps.is_bundle or series_bundle) and query_comps.book_number is not None:
                title_score = 0.0
                meta['book_number_status'] = 'bundle_mismatch'
                self.logger.debug("Bundle detected for single-book search")
            elif number_status == 'exact':
                title_score = min(title_score + 0.75, 2.5)
                self.logger.debug(
                    "Book number exact match",
                    extra={"number": query_comps.book_number, "source": result_comps.source},
                )
            elif number_status == 'mismatch':
                title_score = 0.0
                self.logger.debug(
                    "Book number mismatch",
                    extra={
                        "query": query_comps.book_number,
                        "result": result_comps.book_number,
                        "source": result_comps.source,
                    },
                )
            elif number_status == 'missing' and query_comps.book_number is not None and exact_title:
                meta['book_number_status'] = 'missing_title_exact'
            elif number_status == 'missing' and query_comps.book_number is not None:
                title_score *= 0.2
                meta['book_number_status'] = 'result_missing'
                self.logger.debug(
                    "Book number missing in result",
                    extra={"query_number": query_comps.book_number, "source": result_comps.source},
                )
            
            if title_score == 0.0:
                self.logger.debug("Title match not found", extra={"title_score": title_score})
        elif search_title:
            title_score = 0.0
            self.logger.debug("Title missing in result", extra={"title_score": title_score})
        else:
            title_score = 1.25
            self.logger.debug("Title not searched (neutral)", extra={"title_score": title_score})
        
        meta['title']['score'] = title_score
        if not search_title:
            meta['title']['status'] = 'not_provided'
        elif title_score > 0:
            meta['title']['status'] = 'match'
        else:
            meta['title']['status'] = 'no_match'

        score += title_score
        
        # --- Series matching (15% = 1.5 points max) ---

        series_score = 0.0
        if result_series['series_name'] and search_series['series_name']:
            series_match = self.fuzzy_matcher.fuzzy_match(
                result_series['series_name'], 
                search_series['series_name']
            )
            
            self.logger.debug(
                "Series match",
                extra={
                    "result_series": result_series['series_name'],
                    "search_series": search_series['series_name'],
                    "match_score": series_match.score,
                },
            )
            
            if series_match.exact or series_match.score >= 0.8:
                series_score = 1.5
            elif series_match.score >= 0.7:
                series_score = 1.2
            elif series_match.score >= 0.6:
                series_score = 0.9
            elif series_match.score >= 0.5:
                series_score = 0.6
                
            if (result_series['book_number'] and search_series['book_number'] and 
                result_series['book_number'] == search_series['book_number']):
                series_score = min(series_score + 0.3, 1.5)
                self.logger.debug(
                    "Series book number bonus",
                    extra={"book_number": result_series['book_number']},
                )
        elif search_series['series_name'] and not result_series['series_name']:
            series_score = 0.0
        elif result_series['series_name'] and search_title and search_series['series_name']:
            if search_series['series_name'].lower() in search_title.lower():
                series_score = 1.0
                self.logger.debug(
                    "Series name found in search query",
                    extra={"series_name": result_series['series_name']},
                )
        else:
            series_score = 0.75
        
        meta['series']['score'] = series_score
        if not (result_series['series_name'] and search_series['series_name']):
            meta['series']['status'] = 'not_applicable'
        elif series_score > 0:
            meta['series']['status'] = 'match'
        else:
            meta['series']['status'] = 'no_match'

        score += series_score
        
        self.logger.debug(
            "Final relevance computed",
            extra={
                "total": score,
                "author_score": author_score,
                "title_score": title_score,
                "series_score": series_score,
            },
        )
        meta['combined_score'] = min(score, 10.0)
        return min(score, 10.0), meta
    
    _BRACKET_TAGS = re.compile(r'\[[^\]]*\]')
    _TRAILING_BY = re.compile(r'\s+by\s+[^()\[\]]+$', re.IGNORECASE)
    _SEGMENT_SPLIT = re.compile(r'\s+[-\u2013\u2014]\s+')
    _PART = re.compile(r'\b(?:part|pt\.?)\s*(?:\d+|one|two|three|four|five|six)\b', re.IGNORECASE)

    def _clean_release_title(self, title: str, author: str) -> Tuple[str, List[str]]:
        """Release name without [tags], " by Author" or a leading "Author - ".
        Segments are joined with spaces because normalize_title drops
        everything after a dash, which would lose the title in
        "Author - Title" names."""
        cleaned = self._TRAILING_BY.sub('', self._BRACKET_TAGS.sub(' ', title or '')).strip()
        author_norm = self.fuzzy_matcher.normalize_author(author) if author else ''
        segments = [s.strip() for s in self._SEGMENT_SPLIT.split(cleaned) if s.strip()]
        if author_norm and len(segments) > 1:
            segments = [s for s in segments if self.fuzzy_matcher.normalize_author(s) != author_norm] or segments
        return ' '.join(segments), segments

    def _has_exact_title(self, segments: List[str], search_title: str) -> bool:
        target = self.fuzzy_matcher.normalize_title(search_title)
        if not target:
            return False
        for segment in segments:
            without_groups = self.fuzzy_matcher.brackets_pattern.sub(' ', segment)
            if self.fuzzy_matcher.normalize_title(without_groups) == target:
                return True
        return False

    def _is_part_release(self, result_title: str, search_title: str) -> bool:
        """A "Part 2" release is only half the book, unless the title says Part."""
        return bool(self._PART.search(result_title or '')) and not self._PART.search(search_title or '')

    @staticmethod
    def _series_range(text: str, series_name: str) -> bool:
        """Series name followed by a number range ("Stormlight Archive 1-4") is a collection."""
        pattern = re.escape(series_name.strip()) + r'[\s,:#-]*(?:books?|bks?|vols?|volumes?)?\.?\s*#?\s*\d+\s*(?:-|\u2013|to|&|and)\s*\d+'
        return bool(re.search(pattern, text or '', re.IGNORECASE))

    @staticmethod
    def _number_after_series(text: str, series_name: str) -> Optional[float]:
        """Book number right after the known series name: "(Ends of Magic 1)",
        "Stormlight Archive 01", "Series, Book 3"."""
        pattern = re.escape(series_name.strip()) + r'[\s,:#-]*(?:book|bk|vol(?:ume)?)?\.?\s*#?\s*(\d+(?:\.\d+)?)\b'
        match = re.search(pattern, text or '', re.IGNORECASE)
        return float(match.group(1)) if match else None

    def _extract_series_from_title(self, title: str) -> Dict[str, Any]:
        """Extract series information from a title.

        Handles patterns like:
        - "Title: Series Name, Book 3"
        - "Title (Series Name, #3)"
        - "Title [Series Name 3]"
        - "Series Name: Title"
        """
        result = {
            'series_name': None,
            'book_number': None,
            'full_series': None
        }

        if not title:
            return result

        match = re.search(r'[:\s]([^,:]+),\s*(?:Book|#)\s*(\d+)', title, re.IGNORECASE)
        if match:
            result['series_name'] = match.group(1).strip()
            result['book_number'] = match.group(2)
            result['full_series'] = match.group(0)
            return result

        match = re.search(r'\(([^()]+),\s*(?:Book|#)\s*(\d+)\)', title, re.IGNORECASE)
        if match:
            result['series_name'] = match.group(1).strip()
            result['book_number'] = match.group(2)
            result['full_series'] = match.group(0)
            return result

        match = re.search(r'\[([^\]]+)\s+(\d+)\]', title, re.IGNORECASE)
        if match:
            result['series_name'] = match.group(1).strip()
            result['book_number'] = match.group(2)
            result['full_series'] = match.group(0)
            return result

        match = re.search(r'^([^:]+):\s*(.+)$', title)
        if match:
            first_part = match.group(1).strip()
            if len(first_part.split()) >= 2:
                result['series_name'] = first_part
                result['full_series'] = first_part
                return result

        match = re.search(r'^(.+?)\s+(?:Book\s+)?(\d+)$', title, re.IGNORECASE)
        if match:
            potential_series = match.group(1).strip()
            book_num = match.group(2)
            if len(potential_series.split()) >= 2:
                result['series_name'] = potential_series
                result['book_number'] = book_num
                result['full_series'] = f"{potential_series} {book_num}"
                return result

        return result
    
    def _assess_format_quality(self, format_str: str, prefs: Dict[str, Any]) -> float:
        """Score format against the user's format_priority preference.

        Formats the user explicitly ranked are scored by rank (first = 10,
        decreasing). Formats they didn't rank fall back to the general
        audiobook-format sanity table (self.format_scores) - still a
        reasonable score, just not preference-driven.
        """
        format_str = (format_str or 'unknown').lower()
        priority_raw = prefs.get('format_priority') or ''
        priority_list = [f.strip().lower() for f in priority_raw.split(',') if f.strip()]

        if priority_list and format_str in priority_list:
            position = priority_list.index(format_str)
            return max(10.0 - (position * 1.5), 5.0)

        return float(self.format_scores.get(format_str, self.format_scores['unknown']))

    def _assess_bitrate_quality(self, bitrate: int, prefs: Dict[str, Any]) -> float:
        """Assess bitrate quality score against the user's min/preferred bitrate."""
        min_bitrate = prefs.get('min_bitrate', self.min_bitrate)
        preferred_bitrate = prefs.get('preferred_bitrate', self.preferred_bitrate)
        max_useful_bitrate = self.max_useful_bitrate

        if preferred_bitrate <= min_bitrate:
            preferred_bitrate = min_bitrate + 1

        if bitrate <= 0:
            return 0.0

        if bitrate < min_bitrate:
            return 1.0

        if bitrate >= max_useful_bitrate:
            return 10.0

        if bitrate >= preferred_bitrate:
            ratio = (bitrate - preferred_bitrate) / (max_useful_bitrate - preferred_bitrate)
            return 8.0 + (2.0 * ratio)

        ratio = (bitrate - min_bitrate) / (preferred_bitrate - min_bitrate)
        return 3.0 + (5.0 * ratio)
    
    def _assess_metadata_quality(self, result: Dict[str, Any]) -> float:
        """Assess metadata completeness quality score."""
        score = 0.0
        
        if result.get('title'):
            score += 4.0
        if result.get('author'):
            score += 4.0
        if result.get('size'):
            score += 2.0
        
        return min(score, 10.0)
    
    def _assess_availability_quality(self, seeders: int) -> float:
        """Assess availability quality based on seeders."""
        if seeders <= 0:
            return 0.0
        if seeders >= 50:
            return 10.0
        if seeders >= 10:
            return 8.0
        if seeders >= 5:
            return 6.0
        if seeders >= 2:
            return 4.0
        return 2.0
    
    def _calculate_match_confidence(self, relevance_score: float,
                                     relevance_meta: Optional[Dict[str, Any]],
                                     result: Dict[str, Any],
                                     preferred_languages: Optional[Any] = None) -> float:
        """Calculate match confidence (0-100): is this the book/edition that was
        searched for? Title, author, series/book-number, and language only -
        deliberately no format/bitrate/availability/metadata input. A result
        can have terrible file quality and still score 100 here if it's
        unambiguously the right book.

        Returns:
            Match confidence percentage (0-100)
        """
        base_confidence = min(relevance_score * 10, 100.0)

        penalties = 0.0
        bonuses = 0.0

        meta = relevance_meta or {}
        number_status = meta.get('book_number_status')
        if number_status in ('mismatch', 'bundle_mismatch', 'part_split'):
            penalties += 55.0
        elif number_status == 'result_missing':
            penalties += 20.0
        elif number_status == 'exact':
            bonuses += 10.0
            if meta.get('result_components_source') == 'structured':
                bonuses += 5.0

        title_meta = (meta.get('title') or {})
        title_status = title_meta.get('status')
        if title_status == 'no_match':
            penalties += 35.0
        elif title_status == 'match' and (title_meta.get('score') or 0) >= 2.0:
            bonuses += 5.0

        series_meta = (meta.get('series') or {})
        series_status = series_meta.get('status')
        if series_status == 'no_match':
            penalties += 15.0
        elif series_status == 'match' and (series_meta.get('score') or 0) >= 1.2:
            bonuses += 5.0

        if preferred_languages and not language_allowed(result.get('language'), preferred_languages):
            penalties += 25.0

        confidence = base_confidence - penalties + bonuses
        return max(0.0, min(100.0, confidence))

    def _calculate_release_quality(self, format_score: float, bitrate_score: float,
                                    metadata_score: float, availability_score: float) -> float:
        """Calculate release quality (0-100): given the result IS the right book,
        how good is this specific release? Format, bitrate, availability, and
        metadata completeness only - deliberately no title/author/series/
        language input. format_score/bitrate_score arrive already neutralised
        for Usenet/bitrate-blind-indexer cases by the caller.

        Returns:
            Release quality percentage (0-100)
        """
        base = (
            format_score * 0.35 +
            bitrate_score * 0.35 +
            metadata_score * 0.10 +
            availability_score * 0.20
        ) * 10.0

        penalties = 0.0
        bonuses = 0.0

        if format_score < 5.0:
            penalties += 15.0
        elif format_score < 7.0:
            penalties += 5.0

        if bitrate_score == 0.0:
            penalties += 10.0
        elif bitrate_score < 3.0:
            penalties += 10.0
        elif bitrate_score < 6.0:
            penalties += 5.0

        if metadata_score < 8.0:
            penalties += 5.0
        if metadata_score < 5.0:
            penalties += 10.0

        if availability_score == 0.0:
            penalties += 20.0
        elif availability_score < 4.0:
            penalties += 10.0
        elif availability_score < 6.0:
            penalties += 5.0

        if format_score >= 9.0:
            bonuses += 5.0
        if bitrate_score >= 9.0:
            bonuses += 3.0
        if metadata_score >= 9.0:
            bonuses += 2.0
        if availability_score >= 9.0:
            bonuses += 5.0

        quality = base - penalties + bonuses
        return max(0.0, min(100.0, quality))
    
    def rank_results_by_quality(self, results: List[Dict[str, Any]],
                               search_title: str = '',
                               search_author: str = '',
                               query_components: Optional[BookComponents] = None) -> List[Dict[str, Any]]:
        """Rank results by quality score including relevance to search query."""
        try:
            self.logger.info(
                "Ranking results by quality",
                extra={"result_count": len(results), "search_title": search_title, "search_author": search_author},
            )
            preferred_languages = get_preferred_languages()
            search_preferences = get_search_preferences()
            scored_results = []

            for i, result in enumerate(results):
                self.logger.info(
                    "Assessing result",
                    extra={"index": i + 1, "total": len(results), "result_title": result.get('title', 'NO TITLE')},
                )
                # Score against the book itself, not the query variant that found the result
                query_title = search_title or result.get('_search_query_used') or ''
                quality_score = self.assess_result_quality(
                    result, query_title, search_author, preferred_languages, search_preferences,
                    query_components,
                )
                result_copy = result.copy()
                result_copy['quality_assessment'] = quality_score
                scored_results.append(result_copy)
                self.logger.info(
                    "Result scored",
                    extra={
                        "match_confidence": round(quality_score.match_confidence, 1),
                        "release_quality": round(quality_score.release_quality, 1),
                    },
                )

            scored_results.sort(
                key=lambda x: (x['quality_assessment'].match_confidence, x['quality_assessment'].release_quality),
                reverse=True,
            )
            self.logger.info(
                "Ranking complete",
                extra={"result_count": len(scored_results)},
            )
            return scored_results
            
        except Exception as e:
            self.logger.error(
                "Failed to rank results by quality",
                extra={"error": str(e)},
                exc_info=True,
            )
            return results