"""
Module Name: library_import.py
Author: AuralArchivist
Created: July 6, 2026
Last Modified: October 9, 2026
Description:
    Page route for the Library Import dashboard - scanning an existing,
    organized audiobook library and cataloging it in place. Lives under the
    Import page at /import/library; the old /library-import URL redirects.

Location:
    /routes/library_import.py
"""

from flask import Blueprint, redirect, render_template, url_for
from flask_login import login_required
from services.container import container

library_import_bp = Blueprint('library_import_page', __name__)
legacy_library_import_bp = Blueprint('library_import_legacy', __name__)


@legacy_library_import_bp.route('/library-import')
@legacy_library_import_bp.route('/library-import/')
def legacy_library_import_redirect():
    return redirect(url_for('library_import_page.library_import_dashboard'), code=301)


@library_import_bp.route('')
@library_import_bp.route('/')
@login_required
def library_import_dashboard():
    """Render the Library Import UI."""
    try:
        libraries = [{'id': lib['id'], 'name': lib['name'], 'is_default': lib['is_default']}
                     for lib in container.get_library_repository().enabled()]
    except Exception:
        libraries = []
    return render_template('library_import.html', title='Library Import', libraries=libraries)
