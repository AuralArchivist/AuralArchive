"""
Module Name: optimize_database.py
Author: AuralArchivist
Created: August 5, 2025
Last Modified: October 9, 2026
Description:
    Settings route that optimizes the database (see core/database/maintenance.py).

Location:
    /routes/settings_tools/optimize_database.py
"""

from flask import jsonify

from core.database import maintenance
from core.logging import get_module_logger
from services.container import container

logger = get_module_logger("Routes.Settings.OptimizeDatabase")


def handle_optimize_database():
    try:
        return jsonify(maintenance.optimize(container.get_database_service()))
    except Exception as exc:
        logger.error("Error optimizing database: %s", exc, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to optimize database (details in server log)'}), 500
