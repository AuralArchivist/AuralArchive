"""
Module Name: auth.py
Author: AuralArchivist
Created: June 24, 2025
Last Modified: October 9, 2026
Description:
    Audible sign-in: login, OTP, status and revoke for the sign-in modal.

Location:
    /api/audible/auth.py

Endpoints:

GET
    /api/audible/auth/status       Check current auth status

POST
    /api/audible/auth/revoke       Revoke authentication and clear state
    /api/audible/auth/start        Start authentication (username/password)
    /api/audible/auth/submit-otp   Submit OTP for pending session
"""

import os
import uuid
from pathlib import Path

import audible
import time
from flask import Blueprint, jsonify, request
from flask_login import current_user

from core.logging import get_module_logger
from core.rate_limit import SlidingWindowLimiter
from core.paths import resolve_audible_auth_file
from core.config.secrets_crypto import get_audible_auth_password
from services.container import container

audible_auth_api = Blueprint('audible_auth_api', __name__)
logger = get_module_logger("API.Audible.Auth")


_pending_auth_sessions = {}
_SESSION_TTL_SECONDS = 600

# Throttle credential and OTP attempts per app user. Amazon's own throttling is
# otherwise the only backstop against repeated guesses through this endpoint.
_start_limiter = SlidingWindowLimiter(max_attempts=5, window_seconds=900)
_otp_limiter = SlidingWindowLimiter(max_attempts=5, window_seconds=600)


def _rate_limited_response(limiter, key):
    retry_after = limiter.retry_after(key)
    response = jsonify({
        'success': False,
        'error': 'Too many attempts. Please wait a few minutes and try again.'
    })
    response.headers['Retry-After'] = str(retry_after)
    return response, 429


def _prune_expired_auth_sessions():
    """Remove sessions older than _SESSION_TTL_SECONDS."""
    cutoff = time.time() - _SESSION_TTL_SECONDS
    expired = [k for k, v in list(_pending_auth_sessions.items()) if v.get('created_at', 0) < cutoff]
    for k in expired:
        del _pending_auth_sessions[k]


@audible_auth_api.route('/api/audible/auth/start', methods=['POST'])
def start_authentication():
    """
    Start authentication flow with username/password.
    Returns session ID if OTP is needed, or success if not.
    
    Request JSON:
        {
            "username": "email@example.com",
            "password": "password123",
            "country_code": "us"
        }
    
    Response JSON:
        Success without OTP:
        {
            "success": true,
            "requires_otp": false,
            "account": {
                "name": "John Doe",
                "marketplace": "US"
            }
        }
        
        Success with OTP required:
        {
            "success": true,
            "requires_otp": true,
            "session_id": "uuid-here",
            "message": "Please enter your OTP code"
        }
        
        Error:
        {
            "success": false,
            "error": "Error message"
        }
    """
    try:
        _prune_expired_auth_sessions()
        user_key = str(current_user.get_id())
        if _start_limiter.is_limited(user_key):
            return _rate_limited_response(_start_limiter, user_key)

        data = request.get_json(silent=True) or {}
        username = data.get('username')
        password = data.get('password')
        country_code = data.get('country_code', 'us')
        
        if not username or not password:
            return jsonify({
                'success': False,
                'error': 'Username and password are required'
            }), 400
        
        _start_limiter.hit(user_key)
        logger.info(f"Starting authentication for {username} in {country_code}")
        
        session_id = str(uuid.uuid4())
        
        otp_requested = False
        
        def otp_callback():
            nonlocal otp_requested
            otp_requested = True
            logger.info(f"OTP required for session {session_id}")
            raise Exception("OTP_REQUIRED")
        
        def captcha_callback(captcha_url):
            logger.warning(f"CAPTCHA required: {captcha_url}")
            raise Exception("CAPTCHA required. Please visit Audible website first to establish trust, then try again.")
        
        def cvf_callback():
            logger.warning("CVF challenge detected")
            raise Exception("CVF challenge detected. Please enable 2FA on your Audible account for better security.")
        
        def approval_callback():
            logger.warning("Approval alert detected")
            raise Exception("Approval required. Please check your email/SMS from Amazon and approve the login, then try again.")
        
        try:
            auth = audible.Authenticator.from_login(
                username=username,
                password=password,
                locale=country_code,
                with_username=False,
                otp_callback=otp_callback,
                captcha_callback=captcha_callback,
                cvf_callback=cvf_callback,
                approval_callback=approval_callback
            )
            
            logger.info("Authentication successful without OTP")
            
            auth_file = resolve_audible_auth_file()
            os.makedirs(os.path.dirname(auth_file), exist_ok=True)
            auth.to_file(auth_file, password=get_audible_auth_password(), encryption='json')
            os.chmod(auth_file, 0o600)
            logger.info(f"Auth file saved to {auth_file}")
            container.get_config_service().update_config('audible', 'country_code', country_code)

            with audible.Client(auth=auth) as client:
                account_info = client.get("1.0/account/information")

            return jsonify({
                'success': True,
                'requires_otp': False,
                'account': {
                    'name': account_info.get('name'),
                    'marketplace': account_info.get('marketplace')
                }
            })
            
        except Exception as e:
            error_msg = str(e)
            
            if "OTP_REQUIRED" in error_msg or otp_requested:
                _pending_auth_sessions[session_id] = {
                    'created_at': time.time(),
                    'app_user_id': user_key,
                    'username': username,
                    'password': password,
                    'country_code': country_code,
                    'waiting_for_otp': True
                }
                logger.info(f"OTP required, created session {session_id}")
                
                return jsonify({
                    'success': True,
                    'requires_otp': True,
                    'session_id': session_id,
                    'message': 'Please enter your OTP code'
                })
            
            logger.error(f"Authentication failed: {error_msg}", exc_info=True)
            return jsonify({
                'success': False,
                'error': error_msg
            }), 400
        
    except Exception as e:
        logger.error(f"Authentication start error: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Authentication error. Check the server log for details.'
        }), 500


@audible_auth_api.route('/api/audible/auth/submit-otp', methods=['POST'])
def submit_otp():
    """
    Submit OTP code for pending authentication session.
    
    Request JSON:
        {
            "session_id": "uuid-here",
            "otp_code": "123456"
        }
    
    Response JSON:
        Success:
        {
            "success": true,
            "account": {
                "name": "John Doe",
                "marketplace": "US"
            }
        }
        
        Error:
        {
            "success": false,
            "error": "Error message"
        }
    """
    try:
        _prune_expired_auth_sessions()
        user_key = str(current_user.get_id())
        if _otp_limiter.is_limited(user_key):
            return _rate_limited_response(_otp_limiter, user_key)

        data = request.get_json(silent=True) or {}
        session_id = data.get('session_id')
        otp_code = data.get('otp_code')
        
        if not session_id or not otp_code:
            return jsonify({
                'success': False,
                'error': 'Session ID and OTP code are required'
            }), 400
        
        _otp_limiter.hit(user_key)

        session = _pending_auth_sessions.get(session_id)
        if session and session.get('app_user_id') != user_key:
            session = None
        if not session:
            logger.warning(f"Invalid or expired session: {session_id}")
            return jsonify({
                'success': False,
                'error': 'Invalid or expired session. Please start authentication again.'
            }), 400
        
        username = session['username']
        password = session['password']
        country_code = session['country_code']
        
        logger.info(f"Submitting OTP for session {session_id}")
        
        def otp_callback():
            return otp_code
        
        try:
            auth = audible.Authenticator.from_login(
                username=username,
                password=password,
                locale=country_code,
                with_username=False,
                otp_callback=otp_callback
            )
            
            auth_file = resolve_audible_auth_file()
            os.makedirs(os.path.dirname(auth_file), exist_ok=True)
            auth.to_file(auth_file, password=get_audible_auth_password(), encryption='json')
            os.chmod(auth_file, 0o600)
            logger.info(f"Auth file saved to {auth_file}")
            container.get_config_service().update_config('audible', 'country_code', country_code)

            with audible.Client(auth=auth) as client:
                account_info = client.get("1.0/account/information")

            _pending_auth_sessions.pop(session_id, None)
            _start_limiter.reset(user_key)
            _otp_limiter.reset(user_key)
            logger.info(f"Authentication successful for session {session_id}")
            
            return jsonify({
                'success': True,
                'account': {
                    'name': account_info.get('name'),
                    'marketplace': account_info.get('marketplace')
                }
            })
            
        except Exception as e:
            logger.error(f"OTP verification failed: {e}", exc_info=True)
            if session_id in _pending_auth_sessions:
                del _pending_auth_sessions[session_id]
            
            return jsonify({
                'success': False,
                'error': f'OTP verification failed: {str(e)}'
            }), 400
        
    except Exception as e:
        logger.error(f"OTP submission error: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'OTP submission error. Check the server log for details.'
        }), 500


@audible_auth_api.route('/api/audible/auth/status', methods=['GET'])
def get_auth_status():
    """
    Check if user is currently authenticated with Audible.
    
    Response JSON:
        Authenticated:
        {
            "authenticated": true,
            "account": {
                "name": "John Doe",
                "marketplace": "US"
            }
        }
        
        Not authenticated:
        {
            "authenticated": false
        }
    """
    try:
        auth_file = Path(resolve_audible_auth_file())
        
        if not auth_file.exists():
            return jsonify({'authenticated': False})
        
        auth = audible.Authenticator.from_file(str(auth_file), password=get_audible_auth_password())
        
        with audible.Client(auth=auth) as client:
            account_info = client.get("1.0/account/information")
        
        return jsonify({
            'authenticated': True,
            'account': {
                'name': account_info.get('name'),
                'marketplace': account_info.get('marketplace')
            }
        })
            
    except Exception as e:
        logger.error(f"Auth status check failed: {e}", exc_info=True)
        return jsonify({
            'authenticated': False,
            'error': 'Could not verify Audible authentication'
        })


@audible_auth_api.route('/api/audible/auth/revoke', methods=['POST'])
def revoke_authentication():
    """
    Revoke Audible authentication by deleting auth file.
    
    Response JSON:
        {
            "success": true,
            "message": "Authentication revoked successfully"
        }
    """
    try:
        auth_file = Path(resolve_audible_auth_file())
        
        if auth_file.exists():
            os.remove(auth_file)
            logger.info("Audible authentication revoked - auth file deleted")
        
        _pending_auth_sessions.clear()

        return jsonify({
            'success': True,
            'message': 'Authentication revoked successfully'
        })
        
    except Exception as e:
        logger.error(f"Failed to revoke authentication: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Failed to revoke authentication'
        }), 500
