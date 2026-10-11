"""
Module Name: result_processor.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Processes and ranks search results for manual and automatic
    selection.

Location:
    /services/acquisition/search/result_processor.py
"""

from datetime import datetime
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

from core.logging import get_module_logger


_LOGGER = get_module_logger("Service.SearchEngine.ResultProcessor")


class ResultProcessor:
    """Process and rank search results for optimal selection."""
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
        
        
        self.max_results_manual = 20
        self.max_results_automatic = 50
        
        self.initialized = False
        self._initialize()
    
    def _initialize(self):
        """Initialize the result processor components."""
        try:
            self.logger.debug("Initializing ResultProcessor")
            self.initialized = True
            self.logger.debug("ResultProcessor initialized successfully")
            
        except Exception as e:
            self.logger.error("Failed to initialize ResultProcessor", extra={"error": str(e)}, exc_info=True)
            self.initialized = False
    
    def process_manual_search_results(self, raw_results: List[Dict[str, Any]], 
                                    title: str, author: str) -> List[Dict[str, Any]]:
        """Process search results for manual user selection."""
        try:
            if not raw_results:
                return []
            
            self.logger.debug(
                "Processing manual search results",
                extra={"raw_count": len(raw_results), "title": title, "author": author},
            )
            
            processed_results = []
            for i, result in enumerate(raw_results[:self.max_results_manual]):
                processed_result = {
                    'id': i + 1,
                    'title': result.get('title', 'Unknown Title'),
                    'author': result.get('author', 'Unknown Author'),
                    'indexer': result.get('indexer', 'Unknown'),
                    'format': result.get('format', 'unknown').upper(),
                    'bitrate': result.get('bitrate', 0),
                    'size': self._format_file_size(result.get('size', 0)),
                    'size_bytes': result.get('size', 0),
                    'seeders': result.get('seeders', 0),
                    'peers': result.get('peers', 0),
                    'download_url': result.get('download_url', ''),
                    'info_hash': result.get('info_hash', ''),
                    'quality_score': result.get('quality_score', 0.0),
                    'result_type': result.get('result_type', ''),
                    'protocol': result.get('protocol', ''),
                    'download_type': result.get('download_type', ''),
                    'freeleech': result.get('freeleech'),
                    'private': result.get('private', False),
                    'mam_free': result.get('mam_free'),
                    'mam_vip': result.get('mam_vip'),
                    'mam_fl_vip': result.get('mam_fl_vip'),
                    'mam_personal_freeleech': result.get('mam_personal_freeleech'),
                    'mam_my_snatched': result.get('mam_my_snatched'),
                    'mam_bookmarked': result.get('mam_bookmarked'),
                    'mam_owner': result.get('mam_owner'),
                    'mam_times_completed': result.get('mam_times_completed'),
                    'mam_numfiles': result.get('mam_numfiles'),
                    'mam_comments': result.get('mam_comments'),
                    'mam_browseflags': result.get('mam_browseflags'),
                }

                if 'quality_assessment' in result:
                    quality_assessment = result['quality_assessment']
                    processed_result['quality_assessment'] = asdict(quality_assessment)
                
                processed_results.append(processed_result)
            
            return processed_results
            
        except Exception as e:
            self.logger.error(
                "Failed to process manual search results",
                extra={"error": str(e), "title": title, "author": author},
                exc_info=True,
            )
            return []
    
    def process_automatic_search_results(self, raw_results: List[Dict[str, Any]], 
                                       book_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process search results for automatic selection."""
        try:
            if not raw_results:
                return None
            
            for result in raw_results:
                if (result.get('title') and result.get('author') and
                    result.get('download_url')):
                    quality_dict = self._extract_quality_dict(result)
                    confidence = 0.0
                    release_quality = 0.0
                    if quality_dict:
                        confidence = float(quality_dict.get('match_confidence', quality_dict.get('confidence', 0)) or 0)
                        release_quality = float(quality_dict.get('release_quality', 0) or 0)
                        result = result.copy()
                        result['quality_assessment'] = quality_dict
                    if 'confidence_score' not in result:
                        result['confidence_score'] = confidence
                    if 'release_quality_score' not in result:
                        result['release_quality_score'] = release_quality
                    return {
                        'book_id': book_info.get('id'),
                        'selected_result': result,
                        'selection_timestamp': datetime.now().isoformat(),
                        'confidence_score': result.get('confidence_score', confidence),
                        'release_quality_score': result.get('release_quality_score', release_quality)
                    }
            
            return None
            
        except Exception as e:
            self.logger.error(
                "Failed to process automatic search results",
                extra={"error": str(e), "book_id": book_info.get("id")},
                exc_info=True,
            )
            return None

    def _extract_quality_dict(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Normalize quality_assessment to a plain dict if possible."""
        qa = result.get('quality_assessment')
        if qa is None:
            return None
        if isinstance(qa, dict):
            return qa
        if is_dataclass(qa):
            return asdict(qa)
        return None
    
    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in human readable format."""
        if size_bytes <= 0:
            return "Unknown"
        
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        
        return f"{size_bytes:.1f} TB"
    
    def get_processing_stats(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get statistics about processed results."""
        if not results:
            return {'total_results': 0}
        
        stats = {
            'total_results': len(results),
            'formats': {},
        }

        for result in results:
            format_str = (result.get('format') or 'unknown').lower()
            stats['formats'][format_str] = stats['formats'].get(format_str, 0) + 1
        
        return stats