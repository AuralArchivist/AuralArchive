"""
Module Name: validation.py
Author: AuralArchivist
Created: July 21, 2025
Last Modified: October 9, 2026
Description:
    System status API: overall status snapshot (services, configuration,
    storage health) and detailed service state.

Location:
    /api/system/validation.py

Endpoints:

GET
    /api/system/services/status   Detailed service status
    /api/system/status            Get overall system status
"""
from flask import Blueprint, jsonify
from core.logging import get_module_logger
from core.decorators import handle_errors

system_validation_bp = Blueprint('system_validation', __name__)
logger = get_module_logger("API.System.Validation")


@system_validation_bp.route('/api/system/status', methods=['GET'])
@handle_errors
def get_system_status():
    try:
        from services.container import container
        
        service_status = container.get_service_status()
        
        try:
            config_service = container.get_config_service()
            config_validation = config_service.validate_config() if config_service else None
            enabled_services = config_service.get_enabled_services() if config_service else None
        except Exception:
            logger.exception('Config validation failed')
            config_validation = None
            enabled_services = None
        
        try:
            from services.library.storage_status import get_storage_report
            storage_report = get_storage_report()
            storage = {
                'healthy': not any(n['level'] == 'error' for n in storage_report['notices']),
                'notices': storage_report['notices'],
            }
        except Exception:
            logger.exception('Failed to build storage report')
            storage = None

        return jsonify({
            'success': True,
            'system_status': {
                'services': service_status,
                'configuration': {
                    'validation': config_validation,
                    'enabled_services': enabled_services
                },
                'storage': storage,
            }
        })
        
    except Exception:
        logger.exception('Failed to get system status')
        return jsonify({'error': 'Failed to get system status'}), 500

@system_validation_bp.route('/api/system/services/status', methods=['GET'])
@handle_errors
def get_detailed_service_status():
    """Get detailed status of all audiobook services"""
    try:
        from services.container import container
        
        services_to_check = {
            'database': container.get_database_service(),
            'config': container.get_config_service(),
        }
        
        detailed_status = {}
        
        for service_name, service_instance in services_to_check.items():
            if service_instance is None:
                detailed_status[service_name] = {
                    'status': 'not_initialized',
                    'available': False,
                    'type': None
                }
            else:
                service_type = type(service_instance).__name__
                
                has_health_check = hasattr(service_instance, 'health_check') or hasattr(service_instance, 'is_healthy')
                
                is_running = None
                if hasattr(service_instance, '_monitoring'):
                    is_running = service_instance._monitoring
                elif hasattr(service_instance, '_processing'):
                    is_running = service_instance._processing
                elif hasattr(service_instance, '_running'):
                    is_running = service_instance._running
                
                detailed_status[service_name] = {
                    'status': 'initialized',
                    'available': True,
                    'type': service_type,
                    'has_health_check': has_health_check,
                    'is_running': is_running
                }
        
        total_services = len(detailed_status)
        initialized_services = sum(1 for s in detailed_status.values() if s['available'])
        running_services = sum(1 for s in detailed_status.values() if s.get('is_running') is True)
        
        return jsonify({
            'success': True,
            'services': detailed_status,
            'summary': {
                'total_services': total_services,
                'initialized_services': initialized_services,
                'running_services': running_services,
                'initialization_rate': f"{initialized_services}/{total_services}",
                'running_rate': f"{running_services}/{total_services}"
            }
        })
        
    except Exception:
        logger.exception('Failed to get detailed service status')
        return jsonify({'error': 'Failed to get detailed service status'}), 500
