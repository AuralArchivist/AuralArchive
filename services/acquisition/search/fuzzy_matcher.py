"""
Module Name: fuzzy_matcher.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Enhanced fuzzy string matching combining token-based and character-level
    strategies for audiobook title/author matching. Provides aggressive
    normalization and multi-strategy scoring for search relevance.

Location:
    /services/acquisition/search/fuzzy_matcher.py
"""

from typing import Set
import re
from dataclasses import dataclass

from core.logging import get_module_logger

_LOGGER = get_module_logger("Service.SearchEngine.FuzzyMatcher")


@dataclass
class MatchResult:
    """Result of a fuzzy match operation."""
    score: float
    matched: bool
    exact: bool
    word_boundary: bool
    algorithm_used: str
    normalized_match: bool = False
    token_overlap: float = 0.0
    

class FuzzyMatcher:
    """Enhanced fuzzy string matching combining multiple strategies."""
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
        
        self.max_distance = 2
        self.match_threshold = 0.8
        self.word_boundary_bonus = 0.2
        
        self._compile_patterns()
        
        self.initialized = False
        self._initialize()
    
    def _compile_patterns(self):
        """Compile regex patterns for title cleaning."""
        try:
            self.clean_pattern = re.compile(r'[^\w\s]')
            self.space_pattern = re.compile(r'\s+')
            
            
            self.article_pattern = re.compile(r'\b(the|a|an)\b', re.IGNORECASE)
            
            self.alphanumeric_only = re.compile(r'[^\w]')
            
            self.brackets_pattern = re.compile(r'[\[\(][^\[\]\(\)]*[\]\)]')
            self.bracket_noise_pattern = re.compile(
                r'\b(audio ?books?|unabridged|abridged|dramati[sz]ed|mp3|m4b|m4a|flac|aac|opus|ogg|'
                r'\d+\s*k(bps)?|kbps|retail|free(leech)?|vip|eng(lish)?|multi|web|rip|repack|proper|'
                r'narrated|read by|book\s*\d+|vol(ume)?\.?\s*\d+|\d{4})\b', re.IGNORECASE)
            
            self.dash_pattern = re.compile(r'\s*[-\u2013\u2014]\s*.*$')
            
        except Exception as e:
            self.logger.error(
                "Failed to compile regex patterns",
                extra={"error": str(e)},
                exc_info=True,
            )
            raise
    
    def _initialize(self):
        """Initialize the fuzzy matcher."""
        try:
            self.logger.debug("Initializing FuzzyMatcher", extra={"max_distance": self.max_distance})
            
            test_result = self.clean_pattern.sub('', 'test-string')
            if test_result != 'teststring':
                raise Exception("Regex pattern test failed")
            
            self.initialized = True
            self.logger.debug("FuzzyMatcher initialized successfully")
            
        except Exception as e:
            self.logger.error(
                "Failed to initialize FuzzyMatcher",
                extra={"error": str(e)},
                exc_info=True,
            )
            self.initialized = False
    
    def normalize_author(self, author: str) -> str:
        """Aggressively normalize author name for matching.
        Aggressive normalization - remove ALL punctuation and spaces.

        Examples:
            "SouppatchHero" -> "sourpatchhero"
            "Sourpatch Hero" -> "sourpatchhero"
            "O'Brien, Patrick" -> "obrienpatrick"
            "Tommy Kerper" -> "tommykerper"

        Returns:
            Normalized author string (lowercase alphanumeric only)
        """
        if not author:
            return ""
        
        normalized = self.alphanumeric_only.sub('', author.lower())
        
        return normalized
    
    def _strip_bracket_noise(self, text: str) -> str:
        """Drop bracketed release tags and trailing groups like "(Book 2)", but
        keep a leading group that is part of the title ("[Traveling] Merchant Crab")."""
        def replace(match):
            inner = match.group(0)[1:-1]
            leading = not text[:match.start()].strip()
            if leading and not self.bracket_noise_pattern.search(inner):
                return f' {inner} '
            return ' '
        return self.brackets_pattern.sub(replace, text)

    def normalize_title(self, title: str) -> str:
        """Normalize title for matching.
        Moderate normalization - remove brackets, articles, and extra spaces.
        Less aggressive than author normalization (keeps spaces for tokenization).

        Examples:
            "I'm Not the Hero: An Isekai LitRPG" -> "im not hero isekai litrpg"
            "Secrets and Strife [Audiobook]" -> "secrets and strife"
            "The Hero of Ages" -> "hero of ages"

        Returns:
            Normalized title (lowercase, cleaned, spaces preserved)
        """
        if not title:
            return ""
        
        cleaned = title.lower()
        
        cleaned = self._strip_bracket_noise(cleaned)
        
        cleaned = self.dash_pattern.sub('', cleaned)
        
        cleaned = self.article_pattern.sub('', cleaned)
        
        cleaned = self.clean_pattern.sub(' ', cleaned)
        
        cleaned = self.space_pattern.sub(' ', cleaned)
        
        cleaned = cleaned.strip()
        
        return cleaned
    
    def tokenize(self, text: str) -> Set[str]:
        """Tokenize text into word set (for token set matching)."""
        if not text:
            return set()
        
        tokens = set(text.lower().split())
        tokens = {t for t in tokens if t}
        
        return tokens
    
    def token_set_overlap(self, tokens1: Set[str], tokens2: Set[str]) -> float:
        """Calculate token set overlap ratio using Jaccard similarity.

        Returns:
            Overlap ratio (0.0 to 1.0)
        """
        if not tokens1 or not tokens2:
            return 0.0
        
        intersection = tokens1 & tokens2
        union = tokens1 | tokens2
        
        if not union:
            return 0.0
        
        return len(intersection) / len(union)
    
    def fuzzy_match(self, text1: str, text2: str) -> MatchResult:
        """Perform fuzzy matching between two strings using multiple strategies.

        Strategy order (returns first successful match):
        1. Exact match (case-insensitive)
        2. Exact normalized match (after cleaning)
        3. Token set overlap (word-based)
        4. Sliding-window edit-distance match (character-level)
        """
        try:
            if not text1 or not text2:
                return MatchResult(0.0, False, False, False, "empty_input")
            
            clean1 = text1.lower().strip()
            clean2 = text2.lower().strip()
            
            if clean1 == clean2:
                return MatchResult(1.0, True, True, True, "exact")
            
            norm1 = self.normalize_title(text1)
            norm2 = self.normalize_title(text2)
            
            if norm1 == norm2:
                return MatchResult(
                    score=1.0,
                    matched=True,
                    exact=False,
                    word_boundary=True,
                    algorithm_used="normalized_exact",
                    normalized_match=True
                )
            
            tokens1 = self.tokenize(norm1)
            tokens2 = self.tokenize(norm2)
            
            if tokens1 and tokens2:
                overlap = self.token_set_overlap(tokens1, tokens2)
                
                if overlap >= 0.7:
                    return MatchResult(
                        score=overlap,
                        matched=True,
                        exact=False,
                        word_boundary=True,
                        algorithm_used="token_set",
                        token_overlap=overlap
                    )
            
            edit_distance_score = self._edit_distance_search(norm1, norm2)
            
            word_boundary_match = self._check_word_boundary_match(norm1, norm2)
            
            final_score = edit_distance_score
            if word_boundary_match:
                final_score = min(1.0, final_score + self.word_boundary_bonus)
            
            is_match = final_score >= 0.6
            
            return MatchResult(
                score=final_score,
                matched=is_match,
                exact=False,
                word_boundary=word_boundary_match,
                algorithm_used="edit_distance",
                token_overlap=self.token_set_overlap(tokens1, tokens2) if tokens1 and tokens2 else 0.0
            )
            
        except Exception as e:
            self.logger.error(
                "Fuzzy match failed",
                extra={"text1": text1, "text2": text2, "error": str(e)},
                exc_info=True,
            )
            return MatchResult(0.0, False, False, False, "error")
    
    def _edit_distance_search(self, pattern: str, text: str) -> float:
        """Best edit-distance similarity of ``pattern`` against every window of
        ``text`` (a plain sliding-window Levenshtein search, not the bit-parallel
        Bitap/shift-or algorithm; fine for short title/author strings).
        """
        try:
            if not pattern or not text:
                return 0.0
            
            if len(pattern) > len(text):
                pattern, text = text, pattern
            
            if pattern == text:
                return 1.0
            
            if pattern in text:
                return len(pattern) / len(text)
            
            pattern_length = len(pattern)
            text_length = len(text)
            
            best_score = 0.0
            
            for i in range(text_length - pattern_length + 1):
                substring = text[i:i + pattern_length]
                distance = self._edit_distance(pattern, substring)
                
                max_distance = max(len(pattern), len(substring))
                score = 1.0 - (distance / max_distance)
                best_score = max(best_score, score)
            
            return best_score
            
        except Exception as e:
            self.logger.error(
                "Edit-distance search error",
                extra={"pattern": pattern, "text": text, "error": str(e)},
                exc_info=True,
            )
            return 0.0
    
    def _edit_distance(self, s1: str, s2: str) -> int:
        """Calculate edit distance between two strings."""
        if len(s1) < len(s2):
            return self._edit_distance(s2, s1)
        
        if len(s2) == 0:
            return len(s1)
        
        previous_row = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        
        return previous_row[-1]
    
    def _check_word_boundary_match(self, text1: str, text2: str) -> bool:
        """Check if strings match at word boundaries."""
        try:
            words1 = set(text1.split())
            words2 = set(text2.split())
            
            common_words = words1.intersection(words2)
            total_words = words1.union(words2)
            
            if not total_words:
                return False
            
            overlap_ratio = len(common_words) / len(total_words)
            return overlap_ratio >= 0.5
            
        except Exception as e:
            self.logger.error(
                "Word boundary check error",
                extra={"text1": text1, "text2": text2, "error": str(e)},
                exc_info=True,
            )
            return False
    
    def clean_title_for_matching(self, title: str) -> str:
        """Clean title for better matching accuracy."""
        try:
            if not title:
                return ""
            
            cleaned = title.lower()
            
            cleaned = self.article_pattern.sub('', cleaned)
            
            cleaned = self.clean_pattern.sub(' ', cleaned)
            
            cleaned = self.space_pattern.sub(' ', cleaned)
            
            cleaned = cleaned.strip()
            
            return cleaned
            
        except Exception as e:
            self.logger.error(
                "Title cleaning failed",
                extra={"title": title, "error": str(e)},
                exc_info=True,
            )
            return title.lower() if title else ""