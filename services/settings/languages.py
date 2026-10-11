"""
Module Name: languages.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    The preferred book languages from Settings, for the language filters in
    core/language_matching.py.

Location:
    /services/settings/languages.py
"""

from typing import Optional, Set

from core.logging import get_module_logger

logger = get_module_logger("Core.LanguageMatching")


def get_preferred_languages() -> Optional[Set[str]]:
    """Fetch the configured language preferences (defaults to English-only).

    config_service.get_author_preferred_languages() already defaults to
    {'english', 'en'} when nothing is explicitly configured, so the only way
    this except branch is reached is a genuine infrastructure failure (config
    service unavailable). Fail closed to English-only there too, rather than
    None - which language_allowed() treats as "no preference, allow anything"
    - so a broken config service can't silently turn off language filtering.
    """
    try:
        from services.container import container
        return container.get_config_service().get_author_preferred_languages()
    except Exception as exc:
        logger.debug(f"Unable to load preferred languages; defaulting to English-only: {exc}")
        return {'english', 'en'}
