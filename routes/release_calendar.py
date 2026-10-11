"""
Module Name: release_calendar.py
Author: AuralArchivist
Created: October 3, 2026
Last Modified: October 9, 2026
Description:
    The Release Calendar page.

Location:
    /routes/release_calendar.py
"""

from flask import Blueprint, render_template
from flask_login import login_required

release_calendar_bp = Blueprint("release_calendar", __name__)


@release_calendar_bp.route("/calendar")
@login_required
def calendar_page():
    return render_template("release_calendar.html")
