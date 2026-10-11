"""
Module Name: availability.py
Author: AuralArchivist
Created: September 29, 2026
Last Modified: October 9, 2026
Description:
    Whether an Audible catalog product can still be bought.

Location:
    /integrations/audible/availability.py
"""

from typing import Any, Dict

_UNAVAILABLE_STATES = {
    'NOT_FOR_SALE',
    'NO_LONGER_AVAILABLE',
    'UNAVAILABLE',
    'UNAVAILABLE_FOR_PURCHASE',
    'REMOVED',
    'WITHDRAWN',
    'ARCHIVED',
}


def is_buyable(product: Dict[str, Any]) -> bool:
    """False only when Audible explicitly marks the product unbuyable;
    products with no availability fields count as buyable."""
    rights = product.get('customer_rights')
    if not isinstance(rights, dict):
        rights = {}
    buyable = product.get('is_buyable')
    if buyable is None:
        buyable = rights.get('is_buyable')
    if buyable is False:
        return False
    state = product.get('product_state') or rights.get('product_state')
    return not state or str(state).strip().upper() not in _UNAVAILABLE_STATES
