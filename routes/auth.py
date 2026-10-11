"""
Module Name: auth.py
Author: AuralArchivist
Created: January 18, 2026
Last Modified: October 9, 2026
Description:
    Authentication routes for login, logout, and initial user setup.

Location:
    /routes/auth.py
"""

from flask import Blueprint, render_template, request, redirect, url_for
from flask_login import login_user, logout_user, login_required, current_user
from urllib.parse import urlparse
from core.auth.auth import has_users, create_user, verify_user, get_user, validate_password
from core.logging import get_module_logger
from core.rate_limit import SlidingWindowLimiter

logger = get_module_logger("Routes.Auth")

auth_bp = Blueprint('auth', __name__)

_LOGIN_WINDOW_SECONDS = 15 * 60
_ip_failures = SlidingWindowLimiter(10, _LOGIN_WINDOW_SECONDS)
_user_failures = SlidingWindowLimiter(30, _LOGIN_WINDOW_SECONDS)


def _is_safe_next(next_page):
    """Only allow same-site relative paths ('/library'), never '//host' or '/\\host'
    (browsers treat a backslash like a slash), and never an absolute URL."""
    if not next_page or not next_page.startswith('/') or next_page.startswith('//'):
        return False
    if '\\' in next_page:
        return False
    parsed = urlparse(next_page)
    return parsed.scheme == '' and parsed.netloc == ''


@auth_bp.route('/setup', methods=['GET', 'POST'])
def setup():
    """Initial setup page for creating the first user"""
    if has_users():
        logger.info("Setup attempted but users already exist, redirecting to login")
        return redirect(url_for('auth.login'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        accept_terms = request.form.get('accept_terms')
        
        if not accept_terms:
            logger.warning("Setup validation failed: terms not accepted")
            return render_template('setup.html', error='You must accept the disclaimer and terms of use to continue')
        
        if not username or len(username) < 3:
            logger.warning("Setup validation failed: username too short")
            return render_template('setup.html', error='Username must be at least 3 characters')
        
        password_error = validate_password(password)
        if password_error:
            logger.warning("Setup validation failed: password length")
            return render_template('setup.html', error=password_error)
        
        if password != confirm_password:
            logger.warning("Setup validation failed: passwords don't match")
            return render_template('setup.html', error='Passwords do not match')
        
        success, message = create_user(username, password)
        
        if success:
            logger.success(f"First user created during setup: {username}")
            user = get_user(username)
            login_user(user)
            return redirect(url_for('auth.setup_storage'))
        else:
            logger.error(f"Failed to create user during setup: {message}")
            return render_template('setup.html', error=message)
    
    return render_template('setup.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if not has_users():
        logger.info("Login attempted but no users exist, redirecting to setup")
        return redirect(url_for('auth.setup'))
    
    if current_user.is_authenticated:
        return redirect(url_for('main.index'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'
        
        if not username or not password:
            logger.warning("Login attempt with missing credentials")
            return render_template('login.html', error='Please provide both username and password', username=username)

        client_key = request.remote_addr or 'unknown'
        user_key = username.lower()
        if _ip_failures.is_limited(client_key) or _user_failures.is_limited(user_key):
            wait_minutes = max(_ip_failures.retry_after(client_key), _user_failures.retry_after(user_key)) // 60 + 1
            logger.warning(f"Login throttled for user {username} from {client_key}")
            return render_template(
                'login.html',
                error=f'Too many failed attempts. Try again in about {wait_minutes} minutes.',
                username=username,
            ), 429

        if verify_user(username, password):
            _ip_failures.reset(client_key)
            _user_failures.reset(user_key)
            user = get_user(username)
            login_user(user, remember=remember)

            next_page = request.args.get('next')
            if _is_safe_next(next_page):
                return redirect(next_page)

            return redirect(url_for('main.index'))
        else:
            _ip_failures.hit(client_key)
            _user_failures.hit(user_key)
            logger.warning(f"Failed login attempt for user: {username}")
            return render_template('login.html', error='Invalid username or password', username=username)
    
    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    """Logout the current user"""
    username = current_user.username if current_user.is_authenticated else 'Unknown'
    logout_user()
    logger.success(f"User logged out: {username}")
    return redirect(url_for('auth.login'))


@auth_bp.route('/setup/storage')
@login_required
def setup_storage():
    """First-run step 2: review where books, downloads and scratch files go."""
    return render_template('setup_storage.html', title='Storage setup')
