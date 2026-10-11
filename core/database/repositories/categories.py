"""
Module Name: categories.py
Author: AuralArchivist
Created: July 7, 2026
Last Modified: October 9, 2026
Description:
    Manages the locally-cached Audible category/genre tree, the user's
    favorited categories, and each book's categories.

Location:
    /core/database/repositories/categories.py
"""

from typing import List, Optional, Dict, Any, Iterable
from sqlalchemy import text
from core.logging import get_module_logger


class CategoryOperations:
    """Manages the audible_categories tree and user_categories favorites."""

    def __init__(self, connection_manager, *, logger=None):
        self.connection_manager = connection_manager
        self.logger = logger or get_module_logger("Service.Database.Categories")

    def upsert_category(
        self,
        audible_category_id: str,
        name: str,
        parent_db_id: Optional[int],
        level: int,
        path: str,
    ) -> Optional[int]:
        """Insert or update a category node, returning its internal database id."""
        engine = self.connection_manager.get_engine()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                    INSERT INTO audible_categories (
                        audible_category_id, name, parent_id, level, path, is_active, last_updated
                    ) VALUES (:audible_id, :name, :parent_id, :level, :path, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(audible_category_id) DO UPDATE SET
                        name=excluded.name,
                        parent_id=excluded.parent_id,
                        level=excluded.level,
                        path=excluded.path,
                        is_active=1,
                        last_updated=CURRENT_TIMESTAMP
                    """),
                    {
                        "audible_id": audible_category_id,
                        "name": name,
                        "parent_id": parent_db_id,
                        "level": level,
                        "path": path,
                    },
                )
                row = conn.execute(
                    text("SELECT id FROM audible_categories WHERE audible_category_id = :audible_id"),
                    {"audible_id": audible_category_id},
                ).fetchone()
                return row[0] if row else None
        except Exception as exc:
            self.logger.error(
                "Error upserting category",
                extra={"audible_category_id": audible_category_id, "error": str(exc)}, exc_info=True,
            )
            return None

    def mark_missing_as_inactive(self, seen_audible_ids: Iterable[str]) -> int:
        """Flag any category not present in seen_audible_ids as inactive (never hard-deleted)."""
        seen_ids = list(seen_audible_ids)
        engine = self.connection_manager.get_engine()
        try:
            with engine.begin() as conn:
                if not seen_ids:
                    result = conn.execute(
                        text("UPDATE audible_categories SET is_active = 0 WHERE is_active = 1")
                    )
                else:
                    placeholders = ", ".join(f":id_{i}" for i in range(len(seen_ids)))
                    params = {f"id_{i}": value for i, value in enumerate(seen_ids)}
                    result = conn.execute(
                        text(
                            f"UPDATE audible_categories SET is_active = 0 "
                            f"WHERE is_active = 1 AND audible_category_id NOT IN ({placeholders})"
                        ),
                        params,
                    )
                return result.rowcount or 0
        except Exception as exc:
            self.logger.error("Error marking removed categories inactive", extra={"error": str(exc)}, exc_info=True)
            return 0

    def is_empty(self) -> bool:
        """Return True if no categories have been synced yet."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            row = conn.execute(text("SELECT COUNT(*) FROM audible_categories")).fetchone()
            return not row or row[0] == 0
        except Exception as exc:
            self.logger.error("Error checking if categories table is empty", extra={"error": str(exc)}, exc_info=True)
            return True
        finally:
            if conn is not None:
                conn.close()

    def get_category_tree(self) -> List[Dict[str, Any]]:
        """Return all active categories assembled into a nested tree."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            rows = conn.execute(
                text("""
                SELECT id, audible_category_id, name, parent_id, level, path
                FROM audible_categories
                WHERE is_active = 1
                ORDER BY level, name COLLATE NOCASE
                """)
            ).fetchall()

            nodes: Dict[int, Dict[str, Any]] = {}
            for row in rows:
                nodes[row[0]] = {
                    "id": row[1],
                    "name": row[2],
                    "path": row[5],
                    "children": [],
                    "_parent_id": row[3],
                }

            roots: List[Dict[str, Any]] = []
            for node in nodes.values():
                parent_id = node.pop("_parent_id")
                if parent_id and parent_id in nodes:
                    nodes[parent_id]["children"].append(node)
                else:
                    roots.append(node)

            return roots
        except Exception as exc:
            self.logger.error("Error building category tree", extra={"error": str(exc)}, exc_info=True)
            return []
        finally:
            if conn is not None:
                conn.close()

    def search_categories(self, query: str) -> List[Dict[str, Any]]:
        """Search active categories by name or path."""
        term = f"%{(query or '').strip()}%"
        if term == "%%":
            return []

        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            rows = conn.execute(
                text("""
                SELECT audible_category_id, name, path, level
                FROM audible_categories
                WHERE is_active = 1 AND (name LIKE :term OR path LIKE :term)
                ORDER BY level, name COLLATE NOCASE
                LIMIT 100
                """),
                {"term": term},
            ).fetchall()
            return [
                {"id": row[0], "name": row[1], "path": row[2], "level": row[3]}
                for row in rows
            ]
        except Exception as exc:
            self.logger.error("Error searching categories", extra={"query": query, "error": str(exc)}, exc_info=True)
            return []
        finally:
            if conn is not None:
                conn.close()

    def get_category_by_audible_id(self, audible_category_id: str) -> Optional[Dict[str, Any]]:
        """Return a single category's name/path by its Audible category id."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            row = conn.execute(
                text("""
                SELECT audible_category_id, name, path
                FROM audible_categories
                WHERE audible_category_id = :audible_id
                """),
                {"audible_id": audible_category_id},
            ).fetchone()
            if not row:
                return None
            return {"id": row[0], "name": row[1], "path": row[2]}
        except Exception as exc:
            self.logger.error(
                "Error fetching category by id",
                extra={"audible_category_id": audible_category_id, "error": str(exc)}, exc_info=True,
            )
            return None
        finally:
            if conn is not None:
                conn.close()

    def set_favorite(self, audible_category_id: str, enabled: bool) -> bool:
        """Add, enable, or disable a favorited category."""
        engine = self.connection_manager.get_engine()
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("""
                    INSERT INTO user_categories (audible_category_id, enabled, created_date)
                    VALUES (:audible_id, :enabled, CURRENT_TIMESTAMP)
                    ON CONFLICT(audible_category_id) DO UPDATE SET
                        enabled=excluded.enabled
                    """),
                    {"audible_id": audible_category_id, "enabled": enabled},
                )
            return True
        except Exception as exc:
            self.logger.error(
                "Error setting favorite category",
                extra={"audible_category_id": audible_category_id, "error": str(exc)}, exc_info=True,
            )
            return False

    def list_user_categories(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """Return favorited categories joined with their name/path."""
        engine = self.connection_manager.get_engine()
        conn = None
        try:
            conn = engine.connect()
            query = """
                SELECT uc.audible_category_id, uc.enabled, uc.created_date, ac.name, ac.path
                FROM user_categories uc
                LEFT JOIN audible_categories ac ON ac.audible_category_id = uc.audible_category_id
            """
            if enabled_only:
                query += " WHERE uc.enabled = 1"
            query += " ORDER BY uc.created_date DESC"

            rows = conn.execute(text(query)).fetchall()
            return [
                {
                    "id": row[0],
                    "enabled": bool(row[1]),
                    "created_date": row[2],
                    "name": row[3],
                    "path": row[4],
                }
                for row in rows
            ]
        except Exception as exc:
            self.logger.error("Error listing user categories", extra={"error": str(exc)}, exc_info=True)
            return []
        finally:
            if conn is not None:
                conn.close()

    def set_book_categories(self, book_id: int, ladders: List[Dict[str, Any]]) -> int:
        """Replace a book's categories with the ids in its Audible category ladders."""
        depths: Dict[str, int] = {}
        for ladder in ladders or []:
            for depth, node in enumerate(ladder.get("ladder") or []):
                category_id = str(node.get("id") or "").strip()
                if category_id:
                    depths[category_id] = max(depth, depths.get(category_id, 0))
        if not depths:
            return 0
        engine = self.connection_manager.get_engine()
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM book_categories WHERE book_id = :book_id"), {"book_id": book_id})
            conn.execute(
                text("INSERT INTO book_categories (book_id, audible_category_id, depth) VALUES (:book_id, :cid, :depth)"),
                [{"book_id": book_id, "cid": cid, "depth": depth} for cid, depth in depths.items()],
            )
        return len(depths)

    def get_library_top_categories(self, limit: int = 5, min_depth: int = 1) -> List[Dict[str, Any]]:
        """Categories with the most owned books, most specific first on ties."""
        engine = self.connection_manager.get_engine()
        with engine.connect() as conn:
            rows = conn.execute(text(
                """
                SELECT bc.audible_category_id, ac.name, COUNT(*) AS books, MAX(bc.depth) AS depth
                FROM book_categories bc
                JOIN books b ON b.id = bc.book_id AND b.library_status = 'owned'
                LEFT JOIN audible_categories ac ON ac.audible_category_id = bc.audible_category_id
                WHERE bc.depth >= :min_depth
                GROUP BY bc.audible_category_id
                ORDER BY books DESC, depth DESC
                LIMIT :limit
                """
            ), {"limit": limit, "min_depth": min_depth}).fetchall()
        return [{"id": r[0], "name": r[1], "books": r[2], "depth": r[3]} for r in rows]

