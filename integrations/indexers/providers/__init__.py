"""
Module Name: __init__.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Registry and resolver for direct provider adapters used by the direct
    indexer.

Location:
    /integrations/indexers/providers/__init__.py
"""

from __future__ import annotations

from typing import Any, Dict, List, Type

from .base import DirectProviderAdapter, ProviderRequestSpec

_PROVIDER_REGISTRY: Dict[str, Type[DirectProviderAdapter]] = {}
_PROVIDER_ORDER: List[Type[DirectProviderAdapter]] = []


def register_provider(adapter_cls: Type[DirectProviderAdapter]) -> Type[DirectProviderAdapter]:
    """Decorator used by provider modules to register themselves."""
    key = adapter_cls.key.lower()
    _PROVIDER_REGISTRY[key] = adapter_cls
    if adapter_cls not in _PROVIDER_ORDER:
        _PROVIDER_ORDER.append(adapter_cls)
    return adapter_cls


def resolve_provider_adapter(base_url: str, config: Dict[str, Any]) -> DirectProviderAdapter:
    """Return the adapter instance matching the supplied configuration."""
    provider_key = (config.get('provider_key') or config.get('provider') or '').lower()

    if provider_key and provider_key in _PROVIDER_REGISTRY:
        adapter_cls = _PROVIDER_REGISTRY[provider_key]
        return adapter_cls(config)

    for adapter_cls in _PROVIDER_ORDER:
        if adapter_cls.matches(base_url, config):
            config['provider_key'] = adapter_cls.key
            return adapter_cls(config)

    from .generic import GenericJSONAdapter
    config['provider_key'] = GenericJSONAdapter.key
    return GenericJSONAdapter(config)


__all__ = [
    'DirectProviderAdapter',
    'ProviderRequestSpec',
    'register_provider',
    'resolve_provider_adapter'
]

from . import generic  # noqa: E402,F401
from . import myanonamouse  # noqa: E402,F401
from . import audiobookbay  # noqa: E402,F401
