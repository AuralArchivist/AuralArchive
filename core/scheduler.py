"""
Module Name: scheduler.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    The background task engine. Each registered task runs on its own worker
    with its own interval, so tasks never wait on each other; the engine
    handles waiting, Run now, pause/resume and status.

Location:
    /core/scheduler.py
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from core.logging import get_module_logger

_LOGGER = get_module_logger("Core.Scheduler")


def _always_ready() -> bool:
    return True


@dataclass
class Task:
    """A background job. interval() returns seconds until the next run, or None
    to run only on request. is_ready() False skips a run (rechecked after
    not_ready_retry seconds, or the interval when that's None)."""
    name: str
    label: str
    run: Callable[[], Any]
    interval: Callable[[], Optional[float]]
    startup_delay: float = 0.0
    is_ready: Callable[[], bool] = _always_ready
    not_ready_retry: Optional[float] = None
    start_paused: bool = False
    description: str = ""


@dataclass
class _TaskState:
    paused: bool = False
    running: bool = False
    waiting_for_ready: bool = False
    run_requested: bool = False
    last_started: Optional[datetime] = None
    last_finished: Optional[datetime] = None
    last_error: Optional[str] = None
    next_run: Optional[datetime] = None
    wake: threading.Event = field(default_factory=threading.Event)


class Scheduler:
    """Registry and workers for background tasks. Use get_scheduler()."""

    def __init__(self, *, logger=None):
        self.logger = logger or _LOGGER
        self._tasks: Dict[str, Task] = {}
        self._states: Dict[str, _TaskState] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def register(self, task: Task) -> None:
        with self._lock:
            if task.name in self._tasks:
                return
            self._tasks[task.name] = task
            self._states[task.name] = _TaskState(paused=task.start_paused)

    def start(self) -> None:
        """Start a worker for every registered task that isn't running yet."""
        with self._lock:
            pending = [name for name in self._tasks if name not in self._threads]
            for name in pending:
                thread = threading.Thread(target=self._work, args=(name,), name=f"task-{name}", daemon=True)
                self._threads[name] = thread
                thread.start()
        if pending:
            self.logger.info("Background tasks started", extra={"tasks": pending})

    def run_now(self, name: str) -> bool:
        """Run the task as soon as possible; False when it isn't registered."""
        state = self._states.get(name)
        if state is None:
            return False
        with self._lock:
            state.run_requested = True
        state.wake.set()
        return True

    def pause(self, name: str) -> bool:
        """Stop scheduled runs; True when this changed the state."""
        return self._set_paused(name, True)

    def resume(self, name: str) -> bool:
        """Restart scheduled runs; True when this changed the state."""
        return self._set_paused(name, False)

    def _set_paused(self, name: str, paused: bool) -> bool:
        state = self._states.get(name)
        if state is None:
            return False
        with self._lock:
            changed = state.paused != paused
            state.paused = paused
        state.wake.set()
        return changed

    def is_active(self, name: str) -> bool:
        """Registered and not paused."""
        with self._lock:
            state = self._states.get(name)
            return bool(state) and not state.paused

    def status(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [self._describe(task, self._states[task.name]) for task in self._tasks.values()]

    def task_status(self, name: str) -> Dict[str, Any]:
        with self._lock:
            return self._describe(self._tasks[name], self._states[name])

    @staticmethod
    def _describe(task: Task, state: _TaskState) -> Dict[str, Any]:
        def iso(value: Optional[datetime]) -> Optional[str]:
            return value.isoformat(timespec="seconds") if value else None
        try:
            interval = task.interval()
        except Exception:
            interval = None
        return {
            "name": task.name,
            "label": task.label,
            "description": task.description,
            "interval_seconds": interval,
            "paused": state.paused,
            "running": state.running,
            "waiting_for_ready": state.waiting_for_ready,
            "last_started": iso(state.last_started),
            "last_finished": iso(state.last_finished),
            "last_error": state.last_error,
            "next_run": iso(state.next_run),
        }

    def _work(self, name: str) -> None:
        task, state = self._tasks[name], self._states[name]
        self._wait(state, task.startup_delay)
        while True:
            with self._lock:
                requested, state.run_requested = state.run_requested, False
                paused = state.paused
            delay: Optional[float] = None
            if requested or not paused:
                delay = self._run_once(task, state)
            with self._lock:
                schedule = not state.paused and delay
                state.next_run = datetime.now() + timedelta(seconds=delay) if schedule else None
            self._wait(state, delay if schedule else None)

    def _run_once(self, task: Task, state: _TaskState) -> Optional[float]:
        """Run the task if it's ready; return seconds until it should run again."""
        try:
            ready = task.is_ready()
        except Exception:
            self.logger.warning("Task readiness check failed", extra={"task": task.name}, exc_info=True)
            ready = False
        with self._lock:
            state.waiting_for_ready = not ready
        if not ready:
            return task.not_ready_retry if task.not_ready_retry is not None else self._interval(task)
        with self._lock:
            state.running = True
            state.last_started = datetime.now()
        try:
            task.run()
            error = None
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            self.logger.error("Background task failed", extra={"task": task.name, "error": error}, exc_info=True)
        with self._lock:
            state.running = False
            state.last_finished = datetime.now()
            state.last_error = error
        return self._interval(task)

    def _interval(self, task: Task) -> Optional[float]:
        try:
            value = task.interval()
        except Exception:
            self.logger.warning("Task interval lookup failed", extra={"task": task.name}, exc_info=True)
            return None
        return value if value and value > 0 else None

    @staticmethod
    def _wait(state: _TaskState, seconds: Optional[float]) -> None:
        if seconds is None:
            state.wake.wait()
        elif seconds > 0:
            state.wake.wait(seconds)
        state.wake.clear()


_scheduler: Optional[Scheduler] = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = Scheduler()
    return _scheduler
