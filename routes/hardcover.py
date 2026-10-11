"""
Module Name: hardcover.py
Author: AuralArchivist
Created: July 1, 2026
Last Modified: October 9, 2026
Description:
    Page route for the Hardcover.app discovery dashboard (Trending, New
    Releases, My Shelves, Search, Lists, Genres & Moods). All data is loaded
    client-side via /api/hardcover/* (see api/hardcover/hardcover_api.py) -
    this route just renders the shell template.

Location:
    /routes/hardcover.py
"""

from flask import Blueprint, render_template
from flask_login import login_required

from core.logging import get_module_logger

hardcover_bp = Blueprint('hardcover', __name__)
logger = get_module_logger("Routes.Hardcover")


@hardcover_bp.route("/hardcover")
@login_required
def hardcover_page():
    return render_template("hardcover.html")
