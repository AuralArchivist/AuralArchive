"""
Module Name: file_operations.py
Author: AuralArchivist
Created: August 26, 2025
Last Modified: October 9, 2026
Description:
    Handles atomic file moves and copies with optional checksum verification
    for the import service. Encapsulates disk checks and cleanup of
    partially moved files.

Location:
    /services/processing/filing/file_operations.py
"""

import os
import shutil
import hashlib
from typing import Tuple

from core.logging import get_module_logger


_LOGGER = get_module_logger("Service.Import.FileOperations")


class FileOperations:
    """Handles file system operations for importing."""
    
    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
    
    def move_file_atomic(self, source: str, destination: str, verify: bool = True) -> Tuple[bool, str]:
        """Move a file atomically with optional verification.

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            if not os.path.exists(source):
                return False, f"Source file does not exist: {source}"
            
            source_size = os.path.getsize(source)
            
            dest_dir = os.path.dirname(destination)
            if not self._check_disk_space(dest_dir, source_size):
                return False, "Insufficient disk space at destination"
            
            os.makedirs(dest_dir, exist_ok=True)
            
            source_checksum = None
            if verify:
                source_checksum = self._calculate_checksum(source)
            
            try:
                shutil.move(source, destination)
            except OSError as e:
                self.logger.error("Moving %s to %s failed", source, destination, exc_info=True)
                return False, f"File move failed: {str(e)}"
            
            if verify:
                if not os.path.exists(destination):
                    return False, "Destination file does not exist after move"
                
                dest_checksum = self._calculate_checksum(destination)
                if source_checksum != dest_checksum:
                    self.logger.error(f"Checksum mismatch after move: {source} -> {destination}")
                    try:
                        os.remove(destination)
                    except OSError:
                        self.logger.warning("Could not remove bad copy %s", destination, exc_info=True)
                    return False, "File verification failed - checksums don't match"
            
            self.logger.info("Successfully moved file", extra={"source": source, "destination": destination})
            return True, "File moved successfully"
            
        except Exception as e:
            self.logger.error(f"Error during file move: {e}", exc_info=True)
            return False, f"File move error: {str(e)}"
    
    def copy_file_atomic(self, source: str, destination: str, verify: bool = True) -> Tuple[bool, str]:
        """Copy a file atomically with verification (for seeding preservation).

        This is an alias for copy_file with a name that matches the move_file_atomic pattern.
        Used when importing torrents that need to continue seeding - copies instead of moving
        so the original file remains in place for the torrent client.

        Returns:
            Tuple of (success: bool, message: str)
        """
        return self.copy_file(source, destination, verify)
    
    def copy_file(self, source: str, destination: str, verify: bool = True) -> Tuple[bool, str]:
        """Copy a file (non-destructive alternative to move).

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            if not os.path.exists(source):
                return False, f"Source file does not exist: {source}"
            
            source_size = os.path.getsize(source)
            
            dest_dir = os.path.dirname(destination)
            if not self._check_disk_space(dest_dir, source_size):
                return False, "Insufficient disk space at destination"
            
            os.makedirs(dest_dir, exist_ok=True)
            
            source_checksum = None
            if verify:
                source_checksum = self._calculate_checksum(source)
            
            shutil.copy2(source, destination)
            
            if verify:
                if not os.path.exists(destination):
                    return False, "Destination file does not exist after copy"
                
                dest_checksum = self._calculate_checksum(destination)
                if source_checksum != dest_checksum:
                    self.logger.error(f"Checksum mismatch after copy: {source} -> {destination}")
                    try:
                        os.remove(destination)
                    except OSError:
                        self.logger.warning("Could not remove bad copy %s", destination, exc_info=True)
                    return False, "File verification failed - checksums don't match"
            
            self.logger.info("Successfully copied file", extra={"source": source, "destination": destination})
            return True, "File copied successfully"
            
        except Exception as e:
            self.logger.error(f"Error during file copy: {e}", exc_info=True)
            return False, f"File copy error: {str(e)}"
    
    def _calculate_checksum(self, file_path: str, algorithm: str = 'sha256') -> str:
        """Calculate file checksum."""
        hash_func = hashlib.sha256() if algorithm == 'sha256' else hashlib.md5()
        
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    hash_func.update(chunk)
            
            return hash_func.hexdigest()
        except Exception as e:
            self.logger.error(f"Error calculating checksum for {file_path}: {e}", exc_info=True)
            return ""
    
    def _check_disk_space(self, path: str, required_bytes: int) -> bool:
        """Check if there's enough disk space at the destination."""
        try:
            probe_path = path or '/'
            probe_path = os.path.abspath(probe_path)

            while not os.path.exists(probe_path):
                parent = os.path.dirname(probe_path)
                if not parent or parent == probe_path:
                    probe_path = '/'
                    break
                probe_path = parent

            stat = os.statvfs(probe_path)
            available_bytes = stat.f_bavail * stat.f_frsize
            
            required_with_buffer = required_bytes * 1.1
            
            if available_bytes < required_with_buffer:
                self.logger.warning(
                    f"Insufficient disk space: {available_bytes / (1024**3):.2f} GB available, "
                    f"{required_with_buffer / (1024**3):.2f} GB required"
                )
                return False
            
            return True
        except Exception as e:
            self.logger.error(f"Error checking disk space: {e}", exc_info=True)
            return True
    