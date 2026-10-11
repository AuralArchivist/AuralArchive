"""
Module Name: imports.py
Author: AuralArchivist
Created: July 21, 2025
Last Modified: October 9, 2026
Description:
    Manual import dashboard route.

Location:
    /routes/imports.py
"""

from flask import Blueprint, render_template
from flask_login import login_required

from services.container import container
from core.logging import get_module_logger
from core.paths import get_path_resolver

logger = get_module_logger("Routes.Import")

import_bp = Blueprint('import_page', __name__)


@import_bp.route('')
@import_bp.route('/')
@login_required
def import_dashboard():
    """Render the manual import UI."""
    resolver = get_path_resolver()
    default_library_path = resolver.get_library_dir()
    import_directory = resolver.get_import_dir()
    default_template = 'simple'

    try:
        config_service = container.get_config_service()
        if config_service:
            naming_config = config_service.get_section('naming') or {}
            default_template = naming_config.get('naming_template', default_template)
    except Exception as exc:
        logger.warning("Unable to load config for import page: %s", exc)

    try:
        libraries = [{'id': lib['id'], 'name': lib['name'], 'is_default': lib['is_default']}
                     for lib in container.get_library_repository().enabled()]
    except Exception:
        libraries = []
    return render_template(
        'import.html',
        libraries=libraries,
        title='Import',
        default_library_path=default_library_path,
        default_template=default_template,
        import_directory=import_directory
    )
