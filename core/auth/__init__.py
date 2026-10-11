"""
Module Name: __init__.py
Author: AuralArchivist
Created: April 14, 2026
Last Modified: October 9, 2026
Description:
    Re-exports the authentication helpers (User, user storage, password
    verification) from core/auth/auth.py.

Location:
    /core/auth/__init__.py
"""

from .auth import (
    User,
    has_users,
    create_user,
    verify_user,
    get_user,
    change_password,
    delete_user,
    get_all_users
)

__all__ = [
    'User',
    'has_users',
    'create_user',
    'verify_user',
    'get_user',
    'change_password',
    'delete_user',
    'get_all_users'
]
