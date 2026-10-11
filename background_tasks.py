"""
Module Name: background_tasks.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    The list of every background task and its schedule, registered with the
    task engine in core/scheduler.py and started once the app is up.

Location:
    /background_tasks.py
"""

from datetime import datetime, timedelta

from core.logging import get_module_logger
from core.scheduler import Task, get_scheduler
from services.container import container

_LOGGER = get_module_logger("Core.BackgroundTasks")

UPDATE_CHECK_SECONDS = 24 * 60 * 60
CATEGORY_CHECK_SECONDS = 60 * 60
STARTUP_DELAY_SECONDS = 3


def start() -> None:
    """Register every task and start their workers."""
    scheduler = get_scheduler()
    for task in _tasks():
        scheduler.register(task)
    scheduler.start()


def _tasks():
    from api.settings.settings import run_update_check
    from services.sync import audible_wishlist as wishlist
    from services.sync import hardcover
    from services.sync.audiobookshelf import runner as abs_runner
    from services.acquisition import automatic_download_service as automatic
    from services.acquisition import pipeline as downloads
    from services.metadata.refresh import scheduler as metadata

    downloads_service = container.get_download_management_service()
    automatic_service = container.get_automatic_download_service()
    wishlist_service = container.get_audible_wishlist_service()
    hardcover_service = container.get_hardcover_service()
    metadata_scheduler = container.get_metadata_refresh_scheduler()
    abs = container.get_audiobookshelf_runner()

    return [
        Task(downloads.MONITOR_TASK, "Download monitor", downloads_service.monitor_tick,
             downloads_service.monitor_interval, startup_delay=STARTUP_DELAY_SECONDS,
             description="Moves downloads through search, download, conversion and import"),
        Task(automatic.TASK_NAME, "Automatic downloads", automatic_service.run_cycle,
             automatic_service.scan_interval, description="Searches your indexers for wanted books"),
        Task(wishlist.TASK_NAME, "Audible wishlist sync", wishlist_service.run_scheduled_sync,
             lambda: wishlist_service.sync_interval,
             startup_delay=STARTUP_DELAY_SECONDS + wishlist.STARTUP_DELAY_SECONDS,
             is_ready=wishlist_service.audible_manager.is_configured,
             not_ready_retry=wishlist.NOT_CONNECTED_RETRY_SECONDS,
             description="Adds books on your Audible wishlist as wanted"),
        Task(hardcover.TASK_NAME, "Hardcover sync", hardcover_service.tick,
             lambda: hardcover.TICK_SECONDS, is_ready=hardcover_service.is_connected,
             description="Pushes shelf changes and refreshes trending and new releases"),
        Task(abs_runner.TASK_NAME, "Audiobookshelf", abs.tick, lambda: abs_runner.JOB_POLL_SECONDS,
             startup_delay=STARTUP_DELAY_SECONDS + abs_runner.STARTUP_DELAY_SECONDS,
             description="Tells Audiobookshelf about imports and syncs listening progress"),
        Task(metadata.TASK_NAME, "Metadata refresh", metadata_scheduler.tick,
             lambda: metadata.CHECK_INTERVAL_SECONDS,
             startup_delay=STARTUP_DELAY_SECONDS + metadata.STARTUP_DELAY_SECONDS,
             description="Runs the Scheduled Refresh jobs above when they're due"),
        Task("update_check", "Update check", run_update_check, lambda: UPDATE_CHECK_SECONDS,
             description="Checks for a new AuralArchive version"),
        Task("category_sync", "Audible category sync", _sync_categories_if_due,
             lambda: CATEGORY_CHECK_SECONDS, description="Refreshes the category list used by Discover"),
    ]


def _sync_categories_if_due() -> None:
    """Sync the Audible category tree when it's empty or older than the
    Settings > Discovery interval (re-read every run)."""
    from api.discovery.categories import run_category_sync

    db_service = container.get_database_service()
    config_service = container.get_config_service()

    needs_sync = db_service.categories.is_empty()
    if not needs_sync:
        interval_days = config_service.get_config_int('discovery', 'refresh_interval_days', 30)
        last_synced_at = config_service.get_config_value('discovery', 'last_synced_at')
        try:
            needs_sync = not last_synced_at or datetime.now() - datetime.fromisoformat(last_synced_at) >= timedelta(days=interval_days)
        except ValueError:
            needs_sync = True

    if needs_sync:
        run_category_sync()
