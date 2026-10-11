"""
Module Name: author_scraper.py
Author: AuralArchivist
Created: August 15, 2025
Last Modified: October 9, 2026
Description:
    Scrape Audible author pages when API data is insufficient.

Location:
    /integrations/audible/catalog/author_scraper.py
"""

import re
from typing import Dict, Optional, List
from bs4 import BeautifulSoup

from core.logging import get_module_logger
from core.http import HttpSession

class AudibleAuthorScraper:
    """Handles scraping author information from Audible author pages"""
    
    def __init__(self):
        self.logger = get_module_logger("Service.Audible.Catalog.AuthorScraper")
        self.base_url = "https://www.audible.com"
        self.session = self._setup_session()
    
    def _setup_session(self) -> HttpSession:
        """Session with browser headers for Audible's author pages"""
        return HttpSession(headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        })
    
    def search_author_page(self, author_name: str) -> Optional[Dict]:
        """Search for an author's page on Audible and return author information"""
        try:
            self.logger.info("Searching for author page", extra={"author": author_name})
            
            search_url = f"{self.base_url}/search"
            search_params = {
                "keywords": author_name,
                "node": "18573211011",
                "ref": "a_search_c1_lProduct_1_1"
            }
            
            response = self.session.get(search_url, params=search_params, timeout=15)
            if response.status_code != 200:
                self.logger.warning("Search failed for author", extra={"author": author_name, "status_code": response.status_code})
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            author_link = self._find_author_link(soup, author_name)
            
            if not author_link:
                self.logger.warning("No author page found", extra={"author": author_name})
                return None
            
            author_url = author_link if author_link.startswith('http') else f"{self.base_url}{author_link}"
            
            return self.scrape_author_page(author_url, author_name)
        
        except Exception as e:
            self.logger.exception("Error searching for author", extra={"author": author_name, "error": str(e)})
            return None
    
    def _find_author_link(self, soup: BeautifulSoup, author_name: str) -> Optional[str]:
        """Find the author page link from search results"""
        try:
            author_links = soup.find_all('a', href=True)
            
            for link in author_links:
                href = link.get('href', '')
                text = link.get_text(strip=True).lower()
                
                if '/author/' in href and author_name.lower() in text:
                    return href
                
                if '/narrator/' in href and author_name.lower() in text:
                    return href
            
            author_containers = soup.find_all(['div', 'li'], class_=re.compile(r'author|result'))
            
            for container in author_containers:
                links = container.find_all('a', href=True)
                for link in links:
                    href = link.get('href', '')
                    if '/author/' in href:
                        text_content = container.get_text(strip=True).lower()
                        if author_name.lower() in text_content:
                            return href
            
            return None
        
        except Exception as e:
            self.logger.exception("Error finding author link", extra={"author": author_name, "error": str(e)})
            return None
    
    def scrape_author_page(self, author_url: str, author_name: str) -> Optional[Dict]:
        """Scrape detailed information from an author's Audible page"""
        try:
            self.logger.info("Scraping author page", extra={"author": author_name, "url": author_url})
            
            response = self.session.get(author_url, timeout=15)
            if response.status_code != 200:
                self.logger.warning("Failed to load author page", extra={"author": author_name, "status_code": response.status_code})
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            author_data = {
                'name': author_name,
                'author_page_url': author_url,
                'author_image_url': None,
                'author_bio': None,
                'audible_author_id': None,
                'total_books_count': 0,
                'audible_books_count': 0
            }
            
            author_id_match = re.search(r'/author/([^/]+)', author_url)
            if author_id_match:
                author_data['audible_author_id'] = author_id_match.group(1)
            
            author_data['author_image_url'] = self._extract_author_image(soup)
            
            author_data['author_bio'] = self._extract_author_bio(soup)
            
            author_data['audible_books_count'] = self._extract_book_count(soup)
            author_data['total_books_count'] = author_data['audible_books_count']
            
            books = self._extract_author_books(soup, author_url)
            author_data['books'] = books
            
            self.logger.info("Successfully scraped author data", extra={"author": author_name, "books_found": len(books)})
            return author_data
        
        except Exception as e:
            self.logger.exception("Error scraping author page", extra={"author": author_name, "url": author_url, "error": str(e)})
            return None
    
    def _extract_author_image(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract author image URL from the page"""
        try:
            image_selectors = [
                'img.author-image',
                'img.bc-pub-block-image',
                '.bc-container img',
                '.author-profile-image img',
                'img[alt*="author"]',
                '.hero-content img'
            ]
            
            for selector in image_selectors:
                img = soup.select_one(selector)
                if img and img.get('src'):
                    src = img.get('src')
                    if src.startswith('//'):
                        src = 'https:' + src
                    elif src.startswith('/'):
                        src = 'https://www.audible.com' + src
                    return src
            
            images = soup.find_all('img', src=True)
            for img in images:
                src = img.get('src')
                alt = img.get('alt', '').lower()
                if any(keyword in alt for keyword in ['author', 'writer', 'portrait']):
                    if src.startswith('//'):
                        src = 'https:' + src
                    elif src.startswith('/'):
                        src = 'https://www.audible.com' + src
                    return src
            
            return None
        
        except Exception as e:
            self.logger.exception("Error extracting author image", extra={"error": str(e)})
            return None
    
    def _extract_author_bio(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract author biography from the page"""
        try:
            bio_selectors = [
                '.bc-section[data-widget="AuthorBio"] .bc-text',
                '.author-bio .bc-text',
                '.bc-container .bc-text',
                '.bio-text',
                '.author-description',
                '[data-widget="AuthorBio"] p',
                '.bc-section .bc-text'
            ]
            
            for selector in bio_selectors:
                bio_element = soup.select_one(selector)
                if bio_element:
                    bio_text = bio_element.get_text(strip=True)
                    if bio_text and len(bio_text) > 50:
                        return bio_text
            
            text_elements = soup.find_all(['p', 'div'], string=True)
            for element in text_elements:
                text = element.get_text(strip=True)
                if len(text) > 100 and any(word in text.lower() for word in ['author', 'writer', 'born', 'published', 'writes', 'career']):
                    return text
            
            return None
        
        except Exception as e:
            self.logger.exception("Error extracting author bio", extra={"error": str(e)})
            return None
    
    def _extract_book_count(self, soup: BeautifulSoup) -> int:
        """Extract the number of books by this author"""
        try:
            count_selectors = [
                '.bc-text:contains("results")',
                '.bc-text:contains("titles")',
                '.bc-text:contains("books")',
                '.result-count',
                '.results-count'
            ]
            
            for selector in count_selectors:
                element = soup.select_one(selector)
                if element:
                    text = element.get_text(strip=True)
                    numbers = re.findall(r'\d+', text)
                    if numbers:
                        return max(int(num) for num in numbers)
            
            book_elements = soup.find_all(['li', 'div'], class_=re.compile(r'product|result|book'))
            return len(book_elements)
        
        except Exception as e:
            self.logger.exception("Error extracting book count", extra={"error": str(e)})
            return 0
    
    def _extract_author_books(self, soup: BeautifulSoup, author_url: str) -> List[Dict]:
        """Extract list of books by this author from the page"""
        try:
            books = []
            
            book_selectors = [
                '.bc-list li',
                '.product-row',
                '.adbl-search-result',
                '.bc-row-responsive'
            ]
            
            for selector in book_selectors:
                book_elements = soup.select(selector)
                if book_elements:
                    for element in book_elements:
                        book_data = self._extract_single_book(element)
                        if book_data:
                            books.append(book_data)
                    break
            
            self.logger.debug("Extracted books from author page", extra={"count": len(books), "author_url": author_url})
            return books
        
        except Exception as e:
            self.logger.exception("Error extracting author books", extra={"error": str(e)})
            return []
    
    def _extract_single_book(self, element) -> Optional[Dict]:
        """Extract data for a single book from its container element"""
        try:
            book_data = {}
            
            title_selectors = [
                '.bc-link.bc-color-link',
                'h3 a',
                '.product-title a',
                'a[title]'
            ]
            
            for selector in title_selectors:
                title_element = element.select_one(selector)
                if title_element:
                    book_data['title'] = title_element.get_text(strip=True)
                    book_data['audible_url'] = title_element.get('href', '')
                    break
            
            if not book_data.get('title'):
                return None
            
            if book_data.get('audible_url'):
                asin_match = re.search(r'/pd/([A-Z0-9]+)', book_data['audible_url'])
                if asin_match:
                    book_data['asin'] = asin_match.group(1)
            
            img = element.select_one('img')
            if img and img.get('src'):
                book_data['cover_image'] = img.get('src')
            
            runtime_element = element.select_one('.bc-text:contains("hrs"), .runtime, .length')
            if runtime_element:
                book_data['runtime'] = runtime_element.get_text(strip=True)
            
            rating_element = element.select_one('.bc-text:contains("stars"), .rating')
            if rating_element:
                rating_text = rating_element.get_text(strip=True)
                rating_match = re.search(r'(\d+\.?\d*)', rating_text)
                if rating_match:
                    book_data['rating'] = rating_match.group(1)
            
            return book_data
        
        except Exception as e:
            self.logger.exception("Error extracting single book", extra={"error": str(e)})
            return None
