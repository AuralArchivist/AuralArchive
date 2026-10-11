"""
Module Name: maintenance.py
Author: AuralArchivist
Created: September 25, 2026
Last Modified: October 9, 2026
Description:
    Database maintenance for Settings › Maintenance: optimize and repair.

Location:
    /core/database/maintenance.py
"""

import os
import shutil
from datetime import datetime
from typing import Any, Dict

from core.logging import get_module_logger
from core.paths import get_path_resolver

logger = get_module_logger("Service.Database.Maintenance")


def database_size(db_file: str) -> str:
    """Database file size in human-readable form."""
    try:
        size = os.path.getsize(db_file)
    except OSError:
        return "Unknown"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{round(size / 1024, 2)} KB"
    return f"{round(size / (1024 * 1024), 2)} MB"


def optimize(db_service) -> Dict[str, Any]:
    """VACUUM/ANALYZE/REINDEX the database. Raises on failure."""
    db_file = getattr(db_service, 'db_file', '')
    old_size = database_size(db_file)
    steps = []
    raw_conn = db_service.connection_manager.get_engine().raw_connection()
    try:
        cursor = raw_conn.cursor()
        for statement in ("VACUUM", "ANALYZE", "REINDEX"):
            cursor.execute(statement)
            steps.append(f"{statement} completed")
        try:
            cursor.execute("PRAGMA optimize")
            steps.append("PRAGMA optimize completed")
        except Exception as exc:
            logger.warning("PRAGMA optimize failed: %s", exc)
        try:
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()
            steps.append("Integrity check passed" if result and result[0] == 'ok' else f"Integrity check: {result}")
        except Exception as exc:
            logger.warning("Integrity check failed: %s", exc)
    finally:
        raw_conn.close()
    logger.info("Database optimization completed successfully")
    return {
        'success': True,
        'message': 'Database optimized successfully',
        'old_size': old_size,
        'new_size': database_size(db_file),
        'steps_completed': steps,
        'steps_count': len(steps),
        'optimization_time': datetime.now().isoformat(),
    }


def repair(db_service) -> Dict[str, Any]:
    """Back up, check and repair the database. Raises FileNotFoundError if
    the database file is missing, and other exceptions on failure."""
    db_file = getattr(db_service, 'db_file', '')
    if not db_file or not os.path.exists(db_file):
        raise FileNotFoundError(db_file)

    backup_name = f"auralarchive_pre_repair_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
    shutil.copy2(db_file, os.path.join(get_path_resolver().get_backups_dir('database'), backup_name))

    steps, issues = [], []
    repaired = False
    raw_conn = db_service.connection_manager.get_engine().raw_connection()
    try:
        cursor = raw_conn.cursor()

        def check(label, fn):
            try:
                fn()
            except Exception as exc:
                issues.append(f"{label} failed: {exc}")
                steps.append(f"{label}: Failed")

        def integrity():
            cursor.execute("PRAGMA integrity_check")
            rows = cursor.fetchall()
            if len(rows) == 1 and rows[0][0] == 'ok':
                steps.append("Integrity check: Database is healthy")
            else:
                issues.extend(f"Integrity issue: {row[0]}" for row in rows)
                steps.append(f"Integrity check: Found {len(rows)} issues")

        def foreign_keys():
            cursor.execute("PRAGMA foreign_key_check")
            rows = cursor.fetchall()
            if rows:
                issues.extend(f"Foreign key violation: {row}" for row in rows)
                steps.append(f"Foreign key check: Found {len(rows)} violations")
            else:
                steps.append("Foreign key check: All constraints valid")

        def schema():
            cursor.execute("PRAGMA schema_version")
            steps.append(f"Schema version: {cursor.fetchone()[0]}")

        check("Integrity check", integrity)
        check("Foreign key check", foreign_keys)
        check("Schema check", schema)
        if issues:
            check("Index rebuild", lambda: (cursor.execute("REINDEX"), steps.append("Indexes rebuilt")))
        check("Vacuum", lambda: (cursor.execute("VACUUM"), steps.append("Database vacuumed")))
        check("Analyze", lambda: (cursor.execute("ANALYZE"), steps.append("Statistics analyzed")))

        try:
            cursor.execute("PRAGMA integrity_check")
            final = cursor.fetchall()
            repaired = len(final) == 1 and final[0][0] == 'ok'
            steps.append("Final integrity check: Database is healthy" if repaired
                         else f"Final integrity check: Still has {len(final)} issues")
        except Exception as exc:
            issues.append(f"Final integrity check failed: {exc}")
            steps.append("Final integrity check: Failed")
    finally:
        raw_conn.close()

    success = not issues or repaired
    if success:
        logger.info("Database repair completed successfully")
    else:
        logger.warning("Database repair completed with %s issues", len(issues))
    return {
        'success': success,
        'message': 'Database repair completed' + (' successfully' if success else ' with issues'),
        'backup_created': backup_name,
        'repair_steps': steps,
        'repair_issues': issues,
        'issues_found': len(issues),
        'steps_completed': len(steps),
        'repair_successful': repaired,
        'timestamp': datetime.now().isoformat(),
    }
