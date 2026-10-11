"""
Module Name: get_config.py
Author: AuralArchivist
Created: July 30, 2025
Last Modified: October 9, 2026
Description:
    Settings helper that returns structured configuration data for the UI.

Location:
    /routes/settings_tools/get_config.py
"""

from datetime import datetime

from flask import jsonify

from services.container import container
from core.config.secrets_crypto import redact_config
from core.logging import get_module_logger

logger = get_module_logger("Routes.Settings.GetConfig")

def handle_get_config():
    """Get current configuration for AuralArchive settings UI."""
    try:
        config_service = container.get_config_service()
        config_data = config_service.list_config()
        
        organized_config = {}
        
        for section_name, section_data in config_data.items():
            organized_config[section_name] = {}
            
            if isinstance(section_data, dict):
                # Mask credentials: list_config() returns decrypted values.
                organized_config[section_name] = redact_config(section_data)
            else:
                organized_config[section_name] = {'value': section_data}
        
        config_metadata = {
            'sections_count': len(config_data),
            'last_loaded': datetime.now().isoformat(),
            'configuration_valid': bool(config_data)
        }
        
        return jsonify({
            'success': True,
            'config': organized_config,
            'metadata': config_metadata,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        logger.error(f"Error getting configuration: {e}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Failed to get configuration: {str(e)}'
        }), 500