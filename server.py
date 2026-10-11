"""
Module Name: server.py
Author: AuralArchivist
Created: October 10, 2026
Last Modified: October 10, 2026
Description:
    Runs AuralArchive on Gunicorn (started by `python app.py`), or prints
    every storage location with --show-paths.

Location:
    /server.py
"""

import os
import sys

from gunicorn.app.base import BaseApplication

DEFAULT_PORT = 8765
THREADS = 100


def listen_port() -> int:
    value = os.environ.get('AURALARCHIVE_PORT') or os.environ.get('PORT')
    if not value:
        return DEFAULT_PORT
    try:
        return int(value)
    except ValueError:
        print(f"Invalid port {value!r}; using {DEFAULT_PORT}", file=sys.stderr)
        return DEFAULT_PORT


def _worker_exit(server, worker) -> None:
    from core.logging import get_module_logger
    get_module_logger("Core.App").info("AuralArchive stopped")
    # Open live-update connections would otherwise keep the worker alive until it is killed.
    os._exit(0)


class AuralArchiveServer(BaseApplication):
    def __init__(self, port: int):
        self.port = port
        super().__init__()

    def load_config(self) -> None:
        settings = {
            'bind': f'0.0.0.0:{self.port}',
            # One worker: the scheduler and live updates live in a single process.
            'workers': 1,
            'worker_class': 'gthread',
            'threads': THREADS,
            'timeout': 0,
            'graceful_timeout': 5,
            'accesslog': None,
            'errorlog': '-',
            'loglevel': 'warning',
            'proc_name': 'auralarchive',
            'control_socket_disable': True,
            'worker_exit': _worker_exit,
        }
        for key, value in settings.items():
            self.cfg.set(key, value)

    def load(self):
        import app
        import background_tasks
        background_tasks.start()
        app.logger.info(f"AuralArchive is running on port {self.port}")
        return app.app


def show_paths() -> None:
    from core.paths import get_path_resolver
    pr = get_path_resolver()

    print("=" * 60)
    print("AuralArchive Path Configuration")
    print("=" * 60)
    print(f"Environment: {'Docker' if pr.is_docker() else 'Bare Metal'}")
    print(f"Data folder: {pr.get_data_dir()}  ({pr.get_data_dir_source()})")
    print("-" * 60)
    print(f"Config:      {pr.get_config_file()}")
    print(f"Secrets:     {pr.get_secrets_dir()}")
    print(f"Database:    {pr.get_database_path()}")
    print(f"Cache:       {pr.get_cache_dir()}")
    print(f"Logs:        {pr.get_logs_dir()}")
    print(f"Backups:     {pr.get_backups_dir()}")
    print("-" * 60)
    if not pr.has_fixed_media_paths():
        print(f"Media root:  {pr.get_media_dir()}")
    for name, info in pr.get_all_path_info().items():
        source = info['env_var'] if info['source'] == 'env' else info['source']
        print(f"{name.capitalize() + ':':<12} {info['path']}  ({source})")
    print("=" * 60)


def main(argv) -> int:
    if argv and argv[0] == '--show-paths':
        show_paths()
        return 0
    AuralArchiveServer(listen_port()).run()
    return 0
