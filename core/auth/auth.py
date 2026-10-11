"""
Module Name: auth.py
Author: AuralArchivist
Created: January 18, 2026
Last Modified: October 9, 2026
Description:
    Authentication utilities for user login and password management.
    Passwords are hashed with werkzeug's default KDF (scrypt) and stored in
    users.json in the config directory (mode 0600).

Location:
    /core/auth/auth.py
"""

import os
import json
from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from core.logging import get_module_logger
from core.paths import resolve_users_file

logger = get_module_logger("Core.Auth")

USERS_FILE_PATH = resolve_users_file()

PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 128

_DUMMY_PASSWORD_HASH = None


def _dummy_password_hash():
    global _DUMMY_PASSWORD_HASH
    if _DUMMY_PASSWORD_HASH is None:
        _DUMMY_PASSWORD_HASH = generate_password_hash(os.urandom(16).hex())
    return _DUMMY_PASSWORD_HASH


def validate_password(password):
    """Return an error message if the password doesn't meet the length policy, else None."""
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LENGTH:
        return f"Password must be at least {PASSWORD_MIN_LENGTH} characters"
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Password must be at most {PASSWORD_MAX_LENGTH} characters"
    return None


class User(UserMixin):
    """Simple user class for Flask-Login"""
    def __init__(self, username):
        self.id = username
        self.username = username


def load_users_from_config():
    """Load users from users.json file"""
    try:
        if not os.path.exists(USERS_FILE_PATH):
            logger.info("No users file found, will create on first user setup")
            return {}

        try:
            os.chmod(USERS_FILE_PATH, 0o600)
        except OSError:
            pass

        with open(USERS_FILE_PATH, 'r') as f:
            users_data = json.load(f)
            return users_data
    except json.JSONDecodeError:
        logger.error("Users file is corrupted, starting fresh")
        return {}
    except Exception as e:
        logger.error(f"Error loading users: {e}", exc_info=True)
        return {}


def save_users_to_config(users):
    """Save users to users.json file"""
    try:
        os.makedirs(os.path.dirname(USERS_FILE_PATH), exist_ok=True)
        
        with open(USERS_FILE_PATH, 'w') as f:
            json.dump(users, f, indent=4)
        os.chmod(USERS_FILE_PATH, 0o600)

        logger.success("Users saved successfully")
        return True
    except Exception as e:
        logger.error(f"Error saving users: {e}", exc_info=True)
        return False


def has_users():
    """Check if any users exist in the system"""
    users = load_users_from_config()
    return len(users) > 0


def create_user(username, password):
    """Create a new user with hashed password"""
    users = load_users_from_config()
    
    if username in users:
        logger.warning(f"Attempted to create duplicate user: {username}")
        return False, "Username already exists"

    password_error = validate_password(password)
    if password_error:
        return False, password_error
    
    password_hash = generate_password_hash(password)
    
    users[username] = {
        'password_hash': password_hash,
        'created_at': datetime.now(timezone.utc).isoformat(),
    }
    
    if save_users_to_config(users):
        logger.success(f"User created: {username}")
        return True, "User created successfully"
    else:
        return False, "Error saving user to config"


def _verify_against(users, username, password):
    """Check a password against an already-loaded users dict.

    Always performs one hash comparison, even for unknown usernames or
    accounts without a hash, so timing doesn't reveal which usernames exist.
    """
    password = password if isinstance(password, str) else ''
    user_data = users.get(username)
    password_hash = user_data.get('password_hash') if user_data else None

    if not password_hash:
        check_password_hash(_dummy_password_hash(), password)
        if user_data is None:
            logger.warning(f"Login attempt for non-existent user: {username}")
        else:
            logger.error(f"User {username} has no password hash")
        return False

    return check_password_hash(password_hash, password)


def verify_user(username, password):
    """Verify username and password"""
    users = load_users_from_config()
    is_valid = _verify_against(users, username, password)
    if username not in users or not users[username].get('password_hash'):
        return False
    
    if is_valid:
        logger.success(f"Successful login: {username}")
    else:
        logger.warning(f"Failed login attempt: {username}")
    
    return is_valid


def get_user(username):
    """Get a User object by username"""
    users = load_users_from_config()
    
    if username in users:
        return User(username)
    
    return None


def change_password(username, old_password, new_password):
    """Change user password"""
    users = load_users_from_config()
    
    if username not in users:
        return False, "User not found"
    
    if not _verify_against(users, username, old_password):
        return False, "Current password is incorrect"

    password_error = validate_password(new_password)
    if password_error:
        return False, password_error

    password_hash = generate_password_hash(new_password)
    
    users[username]['password_hash'] = password_hash
    
    if save_users_to_config(users):
        logger.success(f"Password changed for user: {username}")
        return True, "Password changed successfully"
    else:
        return False, "Error saving password"


def delete_user(username):
    users = load_users_from_config()
    
    if username not in users:
        return False, "User not found"
    
    del users[username]
    
    if save_users_to_config(users):
        logger.success(f"User deleted: {username}")
        return True, "User deleted successfully"
    else:
        return False, "Error deleting user"


def get_all_users():
    """Get list of all usernames"""
    users = load_users_from_config()
    return list(users.keys())


def get_user_profile_data(username: str) -> dict:
    """Return extended profile data for a user (display_name, created_at)."""
    users = load_users_from_config()
    user_data = users.get(username, {})
    return {
        "username": username,
        "display_name": user_data.get("display_name") or username,
        "created_at": user_data.get("created_at"),
    }


def update_user_profile_data(username: str, display_name: str) -> tuple[bool, str]:
    """Update mutable profile fields for a user."""
    users = load_users_from_config()
    if username not in users:
        return False, "User not found"
    display_name = (display_name or "").strip()
    if not display_name:
        return False, "Display name cannot be empty"
    if len(display_name) > 64:
        return False, "Display name must be 64 characters or fewer"
    users[username]["display_name"] = display_name
    if save_users_to_config(users):
        logger.success(f"Profile updated for user: {username}")
        return True, "Profile updated"
    return False, "Error saving profile"
