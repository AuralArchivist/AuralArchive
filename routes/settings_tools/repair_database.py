"""
Module Name: repair_database.py
Author: AuralArchivist
Created: August 8, 2025
Last Modified: October 9, 2026
Description:
    Settings route that checks and repairs the database (see core/database/maintenance.py).

Location:
    /routes/settings_tools/repair_database.py
"""

from flask import jsonify

from core.database import maintenance
from core.logging import get_module_logger
from services.container import container

logger = get_module_logger("Routes.Settings.RepairDatabase")


def handle_repair_database():
    """Back up, check and repair the database."""
    try:
        return jsonify(maintenance.repair(container.get_database_service()))
    except FileNotFoundError:
        return jsonify({'success': False, 'error': 'Database file not found'}), 404
    except Exception as exc:
        logger.error("Error repairing database: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to repair database (details in server log)'}), 500
