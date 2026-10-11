"""
Module Name: app.py
Author: AuralArchivist
Created: July 15, 2025
Last Modified: October 10, 2026
Description:
    Builds the Flask/SocketIO app, wires blueprints and starts core services.
    Run with `python app.py`, which starts Gunicorn (server.py).

Location:
    /app.py
"""

import sys

if __name__ == '__main__':
    # The Gunicorn worker imports this module itself.
    from server import main
    sys.exit(main(sys.argv[1:]))

import logging
import os
import threading
from flask import Flask, jsonify, request, redirect, url_for
from werkzeug.exceptions import HTTPException
from flask_socketio import SocketIO
from flask_login import LoginManager, current_user

from core.config.config import Config
from core.logging import get_module_logger, setup_logger
from services import event_subscribers
from services.container import container
from core.response_redaction import install_response_redaction

from api.audible.auth import audible_auth_api
from api.audible.library import audible_library_api
from api.downloads.management import download_management_bp
from api.system.imports import import_api_bp
from api.system.library_import import library_import_api_bp
from api.system.libraries import libraries_api
from api.system.audiobookshelf import abs_api
from api.downloads.manual import manual_search_api_bp
from api.settings.settings import settings_api_bp
from api.system.status import status_api_bp
from api.system.titles import titles_api_bp
from api.system.validation import system_validation_bp
from api.hardcover.hardcover_api import hardcover_api_bp
from api.discovery.calendar import calendar_api
from api.discovery.categories import discovery_api
from api.system.notifications import notifications_api_bp
from api.system.tasks import tasks_api_bp

from routes.auth import auth_bp
from routes.authors import authors_bp
from routes.debug_tools import debug_bp
from routes.discover import discover_bp
from routes.downloads import downloads_bp
from routes.hardcover import hardcover_bp
from routes.release_calendar import release_calendar_bp
from routes.imports import import_bp
from routes.library import library_bp
from routes.library_import import library_import_bp, legacy_library_import_bp
from routes.main import main_bp
from routes.search import search_bp
from routes.series import series_bp
from routes.settings import settings_bp
from routes.profile import profile_bp
from routes.cache_files import cache_files_bp
from routes.settings_tools.image_cache import cache_management_bp
from routes.settings_tools.indexers import indexers_bp

LOGGER_NAME = "Core.App"
logger = get_module_logger(LOGGER_NAME)

_VERSION_FILE = os.path.join(os.path.dirname(__file__), "VERSION")
try:
    with open(_VERSION_FILE, "r", encoding="utf-8") as _vf:
        APP_VERSION = _vf.read().strip()
except OSError:
    APP_VERSION = "unknown"
    logger.warning("VERSION file not found; version reported as 'unknown'")


def create_app(config_class=Config):
    """Application factory pattern"""
    app = Flask(__name__,
                template_folder='frontend/templates',
                static_folder='frontend/static')
    app.config.from_object(config_class)

    proxies = app.config.get('TRUSTED_PROXY_COUNT', 0)
    if proxies > 0:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)

    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.jinja_env.auto_reload = True

    global logger
    setup_logger(LOGGER_NAME, app.config.get('LOG_FILE', 'auralarchive_web.log'))
    logger = get_module_logger(LOGGER_NAME)
    logger.info(
        "Starting AuralArchive Flask application",
        extra={"socketio_async_mode": app.config.get('SOCKETIO_ASYNC_MODE', 'threading')},
    )

    class _SuppressInvalidSession(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return 'Invalid session' not in record.getMessage()

    logging.getLogger('engineio.server').addFilter(_SuppressInvalidSession())

    _async_mode = app.config.get('SOCKETIO_ASYNC_MODE', 'threading')
    socketio = SocketIO(
        app,
        async_mode=_async_mode,
        allow_upgrades=True,
        cors_allowed_origins=app.config.get('CORS_ALLOWED_ORIGINS'),
        logger=app.config.get('SOCKETIO_LOGGER', False),
        engineio_logger=app.config.get('ENGINEIO_LOGGER', False)
    )

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        """Load user by ID for Flask-Login"""
        try:
            from core.auth.auth import get_user
            return get_user(user_id)
        except Exception as e:
            logger.error(f"Error loading user: {e}", exc_info=True)
            return None

    @login_manager.unauthorized_handler
    def unauthorized():
        """Redirect to setup if no users, otherwise to login"""
        try:
            from core.auth.auth import has_users

            sec_fetch_mode = request.headers.get('Sec-Fetch-Mode')
            if (
                request.path.startswith('/api/')
                or '/api/' in request.path
                or (sec_fetch_mode and sec_fetch_mode != 'navigate')
            ):
                return jsonify({'error': 'Authentication required'}), 401

            if not has_users():
                return redirect(url_for('auth.setup'))

            next_page = request.path if request.path != '/' else None
            return redirect(url_for('auth.login', next=next_page))
        except Exception as e:
            logger.error(f"Error in unauthorized handler: {e}", exc_info=True)
            return redirect('/auth/login')

    _PUBLIC_ENDPOINTS = {
        'static',
        'auth.setup',
        'auth.login',
        'auth.logout',
        'health_check',
    }

    @app.before_request
    def _require_authentication():
        if request.endpoint in _PUBLIC_ENDPOINTS or request.endpoint is None:
            return None
        if current_user.is_authenticated:
            return None
        return login_manager.unauthorized()

    @app.before_request
    def _reject_cross_site_writes():
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return None
        if request.headers.get('Sec-Fetch-Site', 'same-origin') in ('same-origin', 'none'):
            return None
        logger.warning("Blocked cross-site request", extra={"path": request.path, "method": request.method})
        return jsonify({'error': 'Cross-site request refused'}), 403

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Content-Security-Policy', "frame-ancestors 'self'")
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        return response

    install_response_redaction(app, {
        settings_bp.name, settings_api_bp.name, indexers_bp.name,
        cache_management_bp.name, profile_bp.name, hardcover_api_bp.name, abs_api.name,
    })

    @app.context_processor
    def _inject_secret_key_warning():
        from core.config.secrets_crypto import decryption_failure_count
        from services.library.storage_status import get_banner_alerts
        storage_alerts = get_banner_alerts() if current_user.is_authenticated else []
        import_review_count = 0
        notification_unread = 0
        if current_user.is_authenticated:
            from services.library.library_check import count_pending_reviews
            from services.notifications import unread_count
            try:
                import_review_count = count_pending_reviews()
                notification_unread = unread_count()
            except Exception:
                logger.warning("Could not load header badge counts", exc_info=True)
        return {
            'secrets_decrypt_failures': decryption_failure_count(),
            'storage_alerts': storage_alerts,
            'import_review_count': import_review_count,
            'notification_unread': notification_unread,
        }

    app.register_blueprint(profile_bp)
    app.register_blueprint(cache_files_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(main_bp, url_prefix='/')
    app.register_blueprint(library_bp, url_prefix='/library')
    app.register_blueprint(search_bp, url_prefix='/search')
    app.register_blueprint(series_bp, url_prefix='/series')
    app.register_blueprint(authors_bp, url_prefix='/authors')
    app.register_blueprint(downloads_bp, url_prefix='/downloads')
    app.register_blueprint(debug_bp, url_prefix='/')
    app.register_blueprint(discover_bp, url_prefix='/')
    app.register_blueprint(import_bp, url_prefix='/import')
    app.register_blueprint(library_import_bp, url_prefix='/import/library')
    app.register_blueprint(legacy_library_import_bp)
    app.register_blueprint(settings_bp, url_prefix='/settings')
    app.register_blueprint(settings_api_bp)
    app.register_blueprint(cache_management_bp, url_prefix='/settings')
    app.register_blueprint(indexers_bp, url_prefix='/settings')
    app.register_blueprint(manual_search_api_bp, url_prefix='/api/search')
    app.register_blueprint(download_management_bp, url_prefix='/api/downloads')
    app.register_blueprint(audible_auth_api)
    app.register_blueprint(audible_library_api)
    app.register_blueprint(status_api_bp)
    app.register_blueprint(titles_api_bp, url_prefix='/api/titles')
    app.register_blueprint(import_api_bp)
    app.register_blueprint(library_import_api_bp)
    app.register_blueprint(libraries_api)
    app.register_blueprint(abs_api)
    app.register_blueprint(system_validation_bp)
    app.register_blueprint(hardcover_api_bp)
    app.register_blueprint(hardcover_bp, url_prefix='/')
    app.register_blueprint(release_calendar_bp)
    app.register_blueprint(discovery_api)
    app.register_blueprint(calendar_api)
    app.register_blueprint(notifications_api_bp)
    app.register_blueprint(tasks_api_bp)

    try:
        event_subscribers.register()
        database_service = container.get_database_service()
        container.get_download_management_service()
        automatic_download_service = container.get_automatic_download_service()
        logger.success(
            "Download management services started successfully",
            extra={"services": ["download_management", "automatic_download"]},
        )

        container.get_audible_account()
        logger.success(
            "Audible Service Manager started successfully",
            extra={"services": ["audible_catalog", "wishlist", "series"]},
        )

        logger.success(
            "Core services started successfully",
            extra={"services": ["database", "config", "audible"]},
        )

        logger.debug("Initializing search services...")
        try:
            from api.downloads.manual import init_search_services as init_manual_search_api

            search_engine_service = container.get_search_engine_service()
            container.get_indexer_manager()

            init_manual_search_api(
                auto_search_svc=automatic_download_service,
                manual_search_svc=search_engine_service,
                db_service=database_service,
            )

            logger.success(
                "Search services started successfully",
                extra={"indexers_initialized": True},
            )
        except Exception as exc:
            logger.error(
                "Error initializing search services",
                extra={"error": str(exc)}, exc_info=True,
            )

        try:
            def preload_images():
                """Background thread to preload images into cache."""
                try:
                    from services.metadata.covers import preload_images_from_database

                    preload_images_from_database()
                except Exception as exc:
                    logger.warning(
                        "Error preloading images",
                        extra={"error": str(exc)},
                    )

            container.get_image_cache_service()

            preload_thread = threading.Thread(target=preload_images, daemon=True)
            preload_thread.start()

        except Exception as exc:
            logger.warning(
                "Error initializing image cache service",
                extra={"error": str(exc)},
            )

    except Exception as exc:
        logger.error(
            "Error initializing services at startup",
            extra={"error": str(exc)}, exc_info=True,
        )

    register_api_routes(app)

    register_error_handlers(app)

    logger.success("AuralArchive Flask application started successfully")
    return app, socketio

def register_api_routes(app):
    """Register routes defined on the app itself."""

    @app.route('/health')
    def health_check():
        """Health check endpoint."""
        return jsonify({
            'status': 'healthy',
            'service': 'AuralArchive',
            'version': APP_VERSION
        })

def register_error_handlers(app):

    @app.errorhandler(404)
    def not_found_error(error):
        return jsonify({'error': 'Resource not found'}), 404

    @app.errorhandler(500)
    def internal_error(error):
        logger.error("Internal server error", extra={"error": str(error)})
        return jsonify({'error': 'Internal server error'}), 500

    @app.errorhandler(Exception)
    def handle_exception(e):
        if isinstance(e, HTTPException):
            return e
        logger.error("Unhandled exception", extra={"type": type(e).__name__}, exc_info=e)
        return jsonify({'error': 'An unexpected error occurred'}), 500

app, socketio = create_app()

@socketio.on('connect')
def handle_connect():
    if not current_user.is_authenticated:
        return False
    logger.debug("SocketIO client connected", extra={"sid": request.sid})

@socketio.on('disconnect')
def handle_disconnect():
    logger.debug("SocketIO client disconnected", extra={"sid": request.sid})
