"""
Module Name: manual_import.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    The Import page: preview local audiobook files, match them with the
    shared matcher, and hand them to ImportService to move into the library.

Location:
    /services/library/manual_import.py
"""

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.processing.conversion.format_detector import FormatDetector
from services.container import container

from core.logging import get_module_logger

from .matcher import BookFileMatcher, normalize_search_title

_LOGGER = get_module_logger("Service.Import.LocalImporter")


class LocalFileImportCoordinator:
    """High-level helper that turns local audiobook files into library imports."""

    def __init__(self, *, logger=None) -> None:
        self.logger = logger or _LOGGER
        self.database_service = container.get_database_service()
        self.import_service = container.get_import_service()
        self.format_detector = FormatDetector()
        self.matcher = BookFileMatcher(logger=self.logger)
        self.metadata_extractor = self.matcher.metadata_extractor
        self.file_naming_service = container.get_file_naming_service()

    # --- Public API ---

    def preview_files(self, file_paths: List[str]) -> Dict[str, Any]:
        """Collect lightweight metadata for UI preview before importing."""
        entries = [self._preview_single(path.strip()) for path in file_paths if path and path.strip()]

        summary = {
            'total': len(entries),
            'ready': len([e for e in entries if e['status'] == 'ready']),
            'missing': len([e for e in entries if e['status'] == 'missing']),
            'invalid': len([e for e in entries if e['status'] == 'invalid']),
            'errors': len([e for e in entries if e['status'] == 'error'])
        }

        return {'entries': entries, 'summary': summary}

    # --- Batch preview helpers (new staged workflow) ---

    def build_batch_preview(
        self,
        file_paths: List[str],
        template_name: Optional[str] = None,
        library_path: Optional[str] = None
    ) -> Dict[str, Any]:
        template_name, library_path = self._resolve_destination_defaults(template_name, library_path)
        cards: List[Dict[str, Any]] = []
        summary = {
            'total': 0,
            'ready': 0,
            'pending': 0,
            'invalid': 0,
            'missing': 0,
            'error': 0,
            'imported': 0
        }

        for file_path in file_paths:
            if not file_path:
                continue
            card = self._build_card_entry(file_path.strip(), template_name, library_path)
            cards.append(card)
            summary['total'] += 1
            summary_key = card.get('status', 'pending')
            if summary_key not in summary:
                summary_key = 'pending'
            summary[summary_key] += 1

        return {
            'cards': cards,
            'summary': summary,
            'template': template_name,
            'library_path': library_path
        }

    def refresh_card_metadata(
        self,
        card: Dict[str, Any],
        asin: Optional[str] = None,
        metadata_override: Optional[Dict[str, Any]] = None,
        template_name: Optional[str] = None,
        library_path: Optional[str] = None
    ) -> Dict[str, Any]:
        template_name, library_path = self._resolve_destination_defaults(
            template_name or card.get('template'),
            library_path or card.get('library_path')
        )

        extracted = card.get('extracted') or {}
        asin_hint = asin or extracted.get('asin')
        title_hint = metadata_override.get('title') if metadata_override else None
        author_hint = metadata_override.get('author') if metadata_override else None

        if not title_hint:
            title_hint = card.get('metadata', {}).get('Title') or extracted.get('title') or extracted.get('clean_title')
        title_hint = normalize_search_title(title_hint) if title_hint else None
        if not author_hint:
            author_hint = card.get('metadata', {}).get('Author') or extracted.get('author') or extracted.get('album_artist')

        match_context = {'strategy': 'manual_override', 'confidence': None}
        if metadata_override:
            normalized = self.matcher.normalize_metadata(metadata_override, asin_hint, title_hint, author_hint, extracted)
            source = metadata_override.get('source', 'manual_override')
        else:
            normalized, match_context = self.matcher.resolve(asin_hint, title_hint, author_hint, extracted)
            source = normalized.get('source', 'manual_import') if normalized else 'manual_import'

        if not normalized:
            normalized = self.matcher.build_fallback_book(asin_hint, title_hint, author_hint, extracted)
            source = 'manual_import'
            match_context = {'strategy': 'fallback', 'confidence': None}

        normalized = self._persist_metadata_for_card(normalized) or normalized

        destination = self._generate_destination_preview(normalized, template_name, library_path, card['source_path'])

        card.update({
            'metadata': normalized,
            'source': source,
            'destination': destination,
            'template': template_name,
            'library_path': library_path,
            'status': 'ready' if card.get('status') == 'ready' else card.get('status', 'pending'),
            'match': match_context
        })
        return card

    def import_prepared_cards(
        self,
        cards: List[Dict[str, Any]],
        options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        options = options or {}
        template = options.get('template_name')
        library_path = options.get('library_path')
        move = options.get('move', True)

        results: List[Dict[str, Any]] = []
        success = failure = 0

        for card in cards:
            metadata = (card.get('metadata') or {}).copy()
            if not metadata:
                failure += 1
                results.append({
                    'path': card.get('source_path'),
                    'success': False,
                    'message': 'No metadata available for this card',
                    'destination_path': None
                })
                continue

            metadata = self._persist_metadata_for_card(metadata) or metadata

            template_name, library_root = self._resolve_destination_defaults(
                card.get('template') or template,
                card.get('library_path') or library_path
            )

            import_source = card.get('source') or metadata.get('source')
            if not import_source or import_source in ('audible', 'audible_catalog'):
                import_source = 'manual_import'

            try:
                ok, message, destination = self.import_service.import_book(
                    source_file_path=card['source_path'],
                    book_data=metadata,
                    template_name=template_name,
                    library_path=library_root,
                    move=move,
                    import_source=import_source
                )
            except Exception as exc:
                self.logger.error("Import of %s failed", card.get('source_path'), exc_info=True)
                ok, message, destination = False, str(exc), None

            if ok:
                success += 1
            else:
                failure += 1

            results.append({
                'path': card.get('source_path'),
                'success': ok,
                'message': message,
                'destination_path': destination
            })

        return {
            'summary': {
                'total': len(cards),
                'successful': success,
                'failed': failure
            },
            'results': results
        }

    # --- Internal helpers ---

    def _preview_single(self, file_path: str) -> Dict[str, Any]:
        entry: Dict[str, Any] = {
            'path': file_path,
            'status': 'pending',
            'messages': [],
            'size_bytes': None,
            'format': 'unknown',
            'asin': None,
            'title': None,
            'author': None,
            'narrator': None,
            'series': None,
            'sequence': None,
            'duration_seconds': None,
            'exists_in_library': False
        }

        if not file_path:
            entry['status'] = 'error'
            entry['messages'].append('No file path provided')
            return entry

        if not os.path.exists(file_path):
            entry['status'] = 'missing'
            entry['messages'].append('File not found on server')
            return entry

        try:
            entry['size_bytes'] = os.path.getsize(file_path)
        except OSError as exc:
            entry['messages'].append(f'Unable to read file size: {exc}')

        validation = self.format_detector.validate_input_file(file_path)
        if validation.get('valid'):
            entry['format'] = validation.get('format', 'unknown')
            entry['status'] = 'ready'
        else:
            entry['status'] = 'invalid'
            if validation.get('error'):
                entry['messages'].append(validation['error'])

        extracted = self.metadata_extractor.extract_metadata(file_path)
        entry['asin'] = extracted.get('asin')
        entry['title'] = extracted.get('title') or extracted.get('clean_title')
        entry['author'] = extracted.get('author') or extracted.get('album_artist')
        entry['narrator'] = extracted.get('narrator')
        entry['series'] = extracted.get('series')
        entry['sequence'] = extracted.get('sequence')
        entry['duration_seconds'] = extracted.get('duration_seconds')

        asin = entry['asin']
        if asin:
            try:
                entry['exists_in_library'] = self.database_service.books.get_book_by_asin(asin) is not None
            except Exception as exc:
                self.logger.warning("Library lookup for %s failed", asin, exc_info=True)
                entry['messages'].append(f'Database lookup failed: {exc}')

        entry['messages'].extend(extracted.get('warnings', []))
        return entry

    def _get_existing_book(self, asin: Optional[str]) -> Optional[Dict[str, Any]]:
        if not asin:
            return None
        return self.database_service.books.get_book_by_asin(asin)

    # --- Internal helpers for batch mode ---

    def _build_card_entry(
        self,
        file_path: str,
        template_name: str,
        library_path: str
    ) -> Dict[str, Any]:
        extracted = self.metadata_extractor.extract_metadata(file_path)
        preview = self._preview_single(file_path)
        card_id = hashlib.sha1(file_path.encode('utf-8')).hexdigest()

        card = {
            'card_id': card_id,
            'source_path': file_path,
            'status': preview['status'],
            'format': preview.get('format'),
            'size_bytes': preview.get('size_bytes'),
            'duration_seconds': preview.get('duration_seconds'),
            'messages': preview.get('messages', []),
            'extracted': extracted,
            'template': template_name,
            'library_path': library_path,
        }

        if preview['status'] == 'ready':
            try:
                metadata, match_context = self.matcher.resolve(
                    preview.get('asin'),
                    preview.get('title'),
                    preview.get('author'),
                    extracted
                )
            except Exception as exc:
                self.logger.warning("Unable to resolve metadata for %s: %s", file_path, exc)
                metadata, match_context = None, {'strategy': 'error', 'confidence': None}
        else:
            metadata, match_context = None, {'strategy': preview['status'], 'confidence': None}

        if not metadata:
            metadata = self.matcher.build_fallback_book(preview.get('asin'), preview.get('title'), preview.get('author'), extracted)
            card['status'] = 'invalid' if preview['status'] != 'ready' else 'ready'
            match_context = {'strategy': 'fallback', 'confidence': None}

        metadata = self._persist_metadata_for_card(metadata) or metadata

        card['metadata'] = metadata
        card['destination'] = self._generate_destination_preview(metadata, template_name, library_path, file_path)
        card['source'] = metadata.get('source', 'manual_import')
        card['match'] = match_context
        return card

    def _persist_metadata_for_card(self, metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not metadata:
            return None
        asin = metadata.get('ASIN') or metadata.get('asin')
        try:
            stored = self._get_existing_book(asin)
            if stored:
                if asin and stored.get('ASIN') != asin:
                    self.logger.debug("Metadata ASIN remapped from %s to %s", asin, stored.get('ASIN'))
                return stored
        except Exception as exc:
            self.logger.warning("Unable to load existing metadata for '%s': %s", metadata.get('Title'), exc)
        return metadata

    def _generate_destination_preview(
        self,
        metadata: Dict[str, Any],
        template_name: str,
        library_path: str,
        source_path: str
    ) -> Dict[str, Any]:
        try:
            file_extension = Path(source_path).suffix.lstrip('.') or 'm4b'
            full_path = self.file_naming_service.generate_file_path(
                book_data=metadata,
                base_path=library_path,
                template_name=template_name,
                file_extension=file_extension
            )
            return {
                'full_path': full_path,
                'folder': os.path.dirname(full_path),
                'filename': os.path.basename(full_path),
                'template': template_name,
                'library_path': library_path
            }
        except Exception as exc:
            self.logger.warning("Unable to generate destination preview for %s: %s", metadata.get('Title'), exc)
            return {
                'full_path': None,
                'folder': None,
                'filename': None,
                'error': str(exc)
            }

    def _resolve_destination_defaults(
        self,
        template_name: Optional[str],
        library_path: Optional[str]
    ) -> Tuple[str, str]:
        load_config = getattr(self.import_service, '_load_configuration', None)
        if callable(load_config):
            load_config()

        default_template = getattr(self.import_service, 'naming_template', 'simple')
        default_library = getattr(self.import_service, 'library_base_path', '/audiobooks')

        return template_name or default_template, library_path or default_library


__all__ = ['LocalFileImportCoordinator']
