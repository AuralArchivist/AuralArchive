"""
Module Name: matching.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Implements matching and similarity scoring helpers used by the metadata
    service to select the best search results.

Location:
    /services/metadata/matching.py
"""

from typing import List, Dict, Optional

from core.logging import get_module_logger


_LOGGER = get_module_logger("Service.Metadata.Matching")

class MetadataMatching:
    """Handles text matching and similarity algorithms for metadata updates"""
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
    
    def find_best_match(self, search_results: List[Dict], target_title: str, target_author: str) -> Optional[Dict]:
        """Find the best matching book from search results"""
        try:
            if not search_results:
                return None
            
            target_title_lower = target_title.lower().strip()
            target_author_lower = target_author.lower().strip() if target_author else ""
            
            best_match = None
            best_score = 0
            
            self.logger.debug(
                "Matching search results",
                extra={"target_title": target_title, "target_author": target_author},
            )
            
            for result in search_results:
                score = self._calculate_match_score(result, target_title_lower, target_author_lower)
                
                self.logger.debug(
                    "Match score computed",
                    extra={
                        "score": score,
                        "result_title": result.get('Title', 'Unknown'),
                        "result_author": result.get('Author', 'Unknown'),
                    },
                )
                
                if score > best_score:
                    best_score = score
                    best_match = result
            
            if best_score >= 10:
                self.logger.info(
                    "Best match found",
                    extra={"score": best_score, "title": best_match.get('Title')},
                )
                return best_match
            
            if search_results:
                self.logger.info(
                    "No confident match, using fallback",
                    extra={"best_score": best_score},
                )
                return search_results[0]
            
            return None
            
        except Exception as e:
            self.logger.error(
                "Error finding best match",
                extra={"error": str(e)},
                exc_info=True,
            )
            return search_results[0] if search_results else None
    
    def _calculate_match_score(self, result: Dict, target_title: str, target_author: str) -> float:
        """Calculate match score for a search result"""
        try:
            result_title = result.get('Title', '').lower().strip()
            result_author = result.get('Author', '').lower().strip()
            
            score = 0
            
            title_score = self._calculate_title_score(target_title, result_title)
            score += title_score
            
            if target_author and result_author:
                author_score = self._calculate_author_score(target_author, result_author)
                score += author_score
            
            if target_title == result_title:
                score += 15
                self.logger.debug(
                    "Exact title match bonus",
                    extra={"bonus": 15},
                )
            
            if target_author and target_author == result_author:
                score += 10
                self.logger.debug(
                    "Exact author match bonus",
                    extra={"bonus": 10},
                )
            
            return score
            
        except Exception as e:
            self.logger.error(
                "Error calculating match score",
                extra={"error": str(e)},
                exc_info=True,
            )
            return 0
    
    def _calculate_title_score(self, target: str, candidate: str) -> float:
        """Calculate title matching score"""
        if not target or not candidate:
            return 0
        
        score = 0
        
        if target == candidate:
            return 20
        
        if target in candidate or candidate in target:
            score += 15
            self.logger.debug("Title substring match bonus", extra={"bonus": 15})
        
        word_similarity = self._calculate_word_similarity(target, candidate)
        score += word_similarity * 5
        
        char_similarity = self._calculate_character_similarity(target, candidate)
        if char_similarity > 0.8:
            score += 5
            self.logger.debug("High character similarity bonus", extra={"bonus": 5})
        
        return score
    
    def _calculate_author_score(self, target: str, candidate: str) -> float:
        """Calculate author matching score"""
        if not target or not candidate:
            return 0
        
        score = 0
        
        if target == candidate:
            return 10
        
        if target in candidate or candidate in target:
            score += 8
            self.logger.debug("Author substring match bonus", extra={"bonus": 8})
        
        word_similarity = self._calculate_word_similarity(target, candidate)
        score += word_similarity * 3
        
        return score
    
    def _calculate_word_similarity(self, str1: str, str2: str) -> float:
        """Calculate similarity based on common words"""
        try:
            words1 = set(self._normalize_text(str1).split())
            words2 = set(self._normalize_text(str2).split())
            
            if not words1 or not words2:
                return 0.0
            
            intersection = len(words1 & words2)
            union = len(words1 | words2)
            
            similarity = intersection / union if union > 0 else 0.0
            
            if similarity > 0.5:
                self.logger.debug(
                    "Word similarity",
                    extra={"similarity": round(similarity, 2), "first": str1, "second": str2},
                )
            
            return similarity
            
        except Exception as e:
            self.logger.error(
                "Error calculating word similarity",
                extra={"error": str(e)},
                exc_info=True,
            )
            return 0.0
    
    def _calculate_character_similarity(self, str1: str, str2: str) -> float:
        """Calculate character-level similarity using Levenshtein-like approach"""
        try:
            if not str1 or not str2:
                return 0.0
            
            max_len = max(len(str1), len(str2))
            if max_len == 0:
                return 1.0
            
            matches = 0
            min_len = min(len(str1), len(str2))
            
            for i in range(min_len):
                if str1[i] == str2[i]:
                    matches += 1
            
            remaining1 = str1[min_len:]
            remaining2 = str2[min_len:]
            
            for char in remaining1:
                if char in remaining2:
                    matches += 0.5
            
            similarity = matches / max_len
            return similarity
            
        except Exception as e:
            self.logger.error(
                "Error calculating character similarity",
                extra={"error": str(e)},
                exc_info=True,
            )
            return 0.0
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison"""
        if not text:
            return ""
        
        common_words = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 
            'for', 'of', 'with', 'by', 'book', 'volume', 'vol', 'part'
        }
        
        normalized = text.lower()
        normalized = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in normalized)
        words = [word for word in normalized.split() if word not in common_words and len(word) > 1]
        
        return ' '.join(words)
    
    def find_exact_asin_match(self, search_results: List[Dict], target_asin: str) -> Optional[Dict]:
        """Find exact ASIN match in search results"""
        try:
            if not target_asin or target_asin == 'N/A':
                return None
            
            for result in search_results:
                result_asin = result.get('ASIN', '')
                if result_asin == target_asin:
                    self.logger.info(
                        "Found exact ASIN match",
                        extra={"target_asin": target_asin, "title": result.get('Title')},
                    )
                    return result
            
            return None
            
        except Exception as e:
            self.logger.error(
                "Error finding exact ASIN match",
                extra={"error": str(e)},
                exc_info=True,
            )
            return None

matcher = MetadataMatching()
