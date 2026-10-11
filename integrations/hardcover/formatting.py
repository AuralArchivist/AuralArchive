"""
Module Name: formatting.py
Author: AuralArchivist
Created: September 23, 2026
Last Modified: October 9, 2026
Description:
    Small helpers shared by the Hardcover discovery modules (trending, new
    releases, list detail) for shaping Hardcover GraphQL edition data.

Location:
    /integrations/hardcover/formatting.py
"""

from typing import Any, Dict, Optional


def pick_narrator(edition: Dict[str, Any]) -> Optional[str]:
    """Return the first narrator's name from an edition's contributions, if any."""
    for contribution in (edition.get("contributions") or []):
        if (contribution.get("contribution") or "").lower() == "narrator" and contribution.get("author"):
            return contribution["author"].get("name")
    return None
