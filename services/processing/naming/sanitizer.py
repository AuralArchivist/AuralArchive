"""
Module Name: sanitizer.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Sanitizes file paths and names for cross-platform compatibility. Handles
    character replacement, length limits, invalid patterns, and optional
    Windows-safe substitutions.

Location:
    /services/processing/naming/sanitizer.py
"""

import os
import re
import unicodedata
from core.logging import get_module_logger

_LOGGER = get_module_logger("Service.FileNaming.Sanitizer")


class PathSanitizer:
    """Sanitizes file paths and names for Linux/Unix systems."""
    
    INVALID_CHARS = r'[\x00/]'
    
    PROBLEMATIC_CHARS = {
        '\t': ' ',
        '\n': ' ',
        '\r': ' ',
    }
    
    WINDOWS_RESERVED_NAMES = {
        'CON', 'PRN', 'AUX', 'NUL',
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
    }
    
    MAX_COMPONENT_LENGTH = 255
    
    MAX_PATH_LENGTH = 4096
    
    def __init__(self, windows_compatible: bool = False, *, logger=None):
        self.logger = logger or _LOGGER
        self.windows_compatible = windows_compatible
        
        self.replacements = {
            '/': '-',
            '\x00': '',
        }
        
        if windows_compatible:
            self.replacements.update({
                ':': ' -',
                '"': "'",
                '<': '[',
                '>': ']',
                '|': '-',
                '?': '',
                '*': '',
                '\\': '-',
            })
    
    def sanitize_path(self, path: str) -> str:
        """Sanitize a complete file path."""
        try:
            path = unicodedata.normalize('NFC', path)
            
            is_absolute = path.startswith(os.sep)

            # Drop standalone '.' / '..' segments (path traversal). Titles that
            # merely contain '..' (e.g. "Ph..D") are left intact.
            components = path.split(os.sep)
            if any(comp.strip() in ('.', '..') for comp in components):
                self.logger.warning(f"Path traversal attempt detected: {path}")
            components = [comp for comp in components if comp and comp.strip() not in ('.', '..')]
            sanitized_components = [self.sanitize_path_component(comp) for comp in components]
            
            sanitized_path = os.sep.join(sanitized_components)
            
            if is_absolute and not sanitized_path.startswith(os.sep):
                sanitized_path = os.sep + sanitized_path
            
            if len(sanitized_path) > self.MAX_PATH_LENGTH:
                self.logger.warning(f"Path exceeds maximum length: {len(sanitized_path)} > {self.MAX_PATH_LENGTH}")
                sanitized_path = self._truncate_path(sanitized_path)
            
            return sanitized_path
            
        except Exception as e:
            self.logger.error(f"Error sanitizing path: {e}", exc_info=True)
            return "unknown_path"
    
    def sanitize_filename(self, filename: str) -> str:
        """Sanitize a filename (no path separators)."""
        try:
            filename = filename.replace('/', '-').replace('\\', '-')
            
            return self.sanitize_path_component(filename)
            
        except Exception as e:
            self.logger.error(f"Error sanitizing filename: {e}", exc_info=True)
            return "unknown_file"
    
    def sanitize_path_component(self, component: str) -> str:
        """Sanitize a single path component (folder or filename)."""
        try:
            if not component or component.strip() == '':
                return ''

            if component.strip() in ('.', '..'):
                return 'unknown'
            
            component = unicodedata.normalize('NFC', component)
            
            for problematic, replacement in self.PROBLEMATIC_CHARS.items():
                component = component.replace(problematic, replacement)
            
            for invalid, replacement in self.replacements.items():
                component = component.replace(invalid, replacement)
            
            component = re.sub(self.INVALID_CHARS, '', component)
            
            component = ''.join(char for char in component if ord(char) >= 32 or char in '\t\n')
            
            component = component.strip().strip('.')
            
            if self.windows_compatible and component.upper() in self.WINDOWS_RESERVED_NAMES:
                component = f"_{component}"
                self.logger.debug(f"Windows reserved name detected, prefixed with underscore: {component}")
            
            if not component or component.strip() == '':
                component = 'unknown'
            
            if len(component) > self.MAX_COMPONENT_LENGTH:
                if '.' in component:
                    name, ext = component.rsplit('.', 1)
                    max_name_length = self.MAX_COMPONENT_LENGTH - len(ext) - 1
                    component = f"{name[:max_name_length]}.{ext}"
                else:
                    component = component[:self.MAX_COMPONENT_LENGTH]
                self.logger.debug(f"Truncated component to {self.MAX_COMPONENT_LENGTH} chars")
            
            component = re.sub(r'\s+', ' ', component)
            
            component = component.rstrip()
            
            return component
            
        except Exception as e:
            self.logger.error(f"Error sanitizing path component: {e}", exc_info=True)
            return "unknown"
    
    def _truncate_path(self, path: str) -> str:
        """Truncate a path that's too long.

        Strategy:
        1. Keep the first component (base path)
        2. Keep the last component (filename)
        3. Truncate middle components if needed
        """
        import os
        components = path.split(os.sep)
        
        if len(components) <= 2:
            if components:
                components[-1] = components[-1][:self.MAX_COMPONENT_LENGTH]
            return os.sep.join(components)
        
        first = components[0]
        last = components[-1]
        middle = components[1:-1]
        
        reserved_space = len(first) + len(last) + (len(os.sep) * 2)
        available_space = self.MAX_PATH_LENGTH - reserved_space
        
        if middle:
            space_per_component = available_space // len(middle)
            middle = [comp[:space_per_component] for comp in middle]
        
        truncated = os.sep.join([first] + middle + [last])
        
        if len(truncated) > self.MAX_PATH_LENGTH:
            truncated = os.sep.join([first, last])
        
        return truncated
    