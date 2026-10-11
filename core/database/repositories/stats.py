"""
Module Name: stats.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Provides analytics and aggregation helpers for the database.

Location:
    /core/database/repositories/stats.py
"""

from typing import Dict
from sqlalchemy import text
from core.logging import get_module_logger
from core.text import runtime_to_minutes


class DatabaseStats:
    """Handles database statistics and analytics operations."""

    def __init__(self, connection_manager, *, logger=None):
        self.connection_manager = connection_manager
        self.logger = logger or get_module_logger("Service.Database.Stats")
    
    def get_library_stats(self) -> Dict:
        """Get library statistics."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            
            result = conn.execute(text("SELECT * FROM books"))
            books = result.mappings().fetchall()
            
            stats = {
                'total_books': len(books),
                'by_status': {},
                'by_language': {},
                'total_runtime_hours': 0,
                'total_authors': 0,
                'total_series': 0,
                'average_rating': 0,
                'rating_distribution': {},
                'recent_additions': 0,
                'completion_stats': {}
            }
            
            total_rating = 0
            rated_books = 0
            authors = set()
            series = set()
            runtime_minutes = 0
            
            for book in books:
                status = book['library_status'] or 'Unknown'
                stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
                
                language = book['language'] or 'Unknown'
                stats['by_language'][language] = stats['by_language'].get(language, 0) + 1
                
                if book['author']:
                    authors.add(book['author'])
                
                if book['series'] and book['series'] != 'N/A':
                    series.add(book['series'])
                
                runtime_minutes += runtime_to_minutes(book['runtime'])
                
                rating = book['overall_rating']
                if rating and rating != 'N/A':
                    try:
                        rating_val = float(rating)
                        total_rating += rating_val
                        rated_books += 1
                        
                        rating_range = f"{int(rating_val)}-{int(rating_val)+1}"
                        stats['rating_distribution'][rating_range] = stats['rating_distribution'].get(rating_range, 0) + 1
                    except (TypeError, ValueError):
                        pass
            
            stats['total_authors'] = len(authors)
            stats['total_series'] = len(series)
            stats['total_runtime_hours'] = round(runtime_minutes / 60, 1)
            stats['average_rating'] = round(total_rating / rated_books, 2) if rated_books > 0 else 0
            
            owned_books = stats['by_status'].get('owned', 0)
            wanted_books = stats['by_status'].get('wanted', 0)
            missing_books = stats['by_status'].get('missing', 0)

            stats['completion_stats'] = {
                'owned_percentage': round((owned_books / len(books)) * 100, 1) if books else 0,
                'wanted_percentage': round((wanted_books / len(books)) * 100, 1) if books else 0,
                'missing_percentage': round((missing_books / len(books)) * 100, 1) if books else 0
            }
            
            self.logger.debug(f"Calculated library stats: {len(books)} total books")
            return stats
        
        except Exception as e:
            self.logger.error(f"Error calculating library stats: {e}", exc_info=True)
            return {'error': str(e)}
        
        finally:
            if conn is not None:
                conn.close()
    
    def get_status_distribution(self) -> Dict[str, int]:
        """Get book count by status."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            
            result = conn.execute(text("""
                SELECT library_status, COUNT(*) as count
                FROM books
                GROUP BY library_status
                ORDER BY count DESC
            """))
            
            results = result.fetchall()
            distribution = {row[0] or 'Unknown': row[1] for row in results}
            
            self.logger.debug(f"Status distribution: {distribution}")
            return distribution
        
        except Exception as e:
            self.logger.error(f"Error getting status distribution: {e}", exc_info=True)
            return {}
        
        finally:
            if conn is not None:
                conn.close()
    
    def get_language_distribution(self) -> Dict[str, int]:
        """Get book count by language."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            
            result = conn.execute(text("""
                SELECT language, COUNT(*) as count
                FROM books
                WHERE language IS NOT NULL AND language != ''
                GROUP BY language
                ORDER BY count DESC
            """))
            
            results = result.fetchall()
            distribution = {row[0]: row[1] for row in results}
            
            self.logger.debug(f"Language distribution: {distribution}")
            return distribution
        
        except Exception as e:
            self.logger.error(f"Error getting language distribution: {e}", exc_info=True)
            return {}
        
        finally:
            if conn is not None:
                conn.close()
    
    def get_recent_activity_stats(self, days: int = 30) -> Dict:
        """Get recent activity statistics."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            days_param = f"-{days} days"
            
            recent_additions = conn.execute(
                text("SELECT COUNT(*) FROM books WHERE created_at >= datetime('now', :days_param)"),
                {"days_param": days_param}
            ).scalar()
            
            recent_updates = conn.execute(
                text("SELECT COUNT(*) FROM books WHERE updated_at >= datetime('now', :days_param) AND updated_at != created_at"),
                {"days_param": days_param}
            ).scalar()
            
            stats = {
                'period_days': days,
                'recent_additions': recent_additions,
                'recent_updates': recent_updates,
                'total_activity': recent_additions + recent_updates
            }
            
            self.logger.debug(f"Recent activity ({days} days): {stats}")
            return stats
        
        except Exception as e:
            self.logger.error(f"Error getting recent activity stats: {e}", exc_info=True)
            return {'error': str(e)}
        
        finally:
            if conn is not None:
                conn.close()
    
    def get_series_completion_stats(self) -> Dict:
        """Get statistics about series completion."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            
            result = conn.execute(text("""
                SELECT
                    series,
                    COUNT(*) as total_books,
                    SUM(CASE WHEN library_status = 'owned' THEN 1 ELSE 0 END) as owned_books,
                    SUM(CASE WHEN library_status = 'wanted' THEN 1 ELSE 0 END) as wanted_books
                FROM books
                WHERE series != 'N/A' AND series IS NOT NULL
                GROUP BY series
                HAVING total_books > 1
                ORDER BY total_books DESC
            """))
            
            results = result.fetchall()
            series_stats = []
            
            for row in results:
                series_name, total, owned, wanted = row
                completion_percentage = round((owned / total) * 100, 1) if total > 0 else 0
                
                series_stats.append({
                    'series_name': series_name,
                    'total_books': total,
                    'owned_books': owned,
                    'wanted_books': wanted,
                    'completion_percentage': completion_percentage,
                    'is_complete': completion_percentage == 100.0
                })
            
            total_series = len(series_stats)
            complete_series = sum(1 for s in series_stats if s['is_complete'])
            
            stats = {
                'total_series': total_series,
                'complete_series': complete_series,
                'incomplete_series': total_series - complete_series,
                'completion_rate': round((complete_series / total_series) * 100, 1) if total_series > 0 else 0,
                'series_details': series_stats[:20]
            }
            
            self.logger.debug(f"Series completion stats: {total_series} series, {complete_series} complete")
            return stats
        
        except Exception as e:
            self.logger.error(f"Error getting series completion stats: {e}", exc_info=True)
            return {'error': str(e)}
        
        finally:
            if conn is not None:
                conn.close()
