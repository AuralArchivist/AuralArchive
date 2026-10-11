"""
Module Name: tasks.py
Author: AuralArchivist
Created: October 9, 2026
Last Modified: October 9, 2026
Description:
    JSON API for the background task list in Settings: status of every task,
    and Run now, pause and resume.

Location:
    /api/system/tasks.py

Endpoints:

GET
    /api/tasks                        List every background task and its state

POST
    /api/tasks/<name>/run             Run a task now
    /api/tasks/<name>/pause           Pause a task's scheduled runs
    /api/tasks/<name>/resume          Resume a task's scheduled runs
"""

from flask import Blueprint, jsonify

from core.scheduler import get_scheduler

tasks_api_bp = Blueprint('tasks_api', __name__, url_prefix='/api/tasks')

_ACTIONS = {
    'run': lambda scheduler, name: scheduler.run_now(name),
    'pause': lambda scheduler, name: scheduler.pause(name),
    'resume': lambda scheduler, name: scheduler.resume(name),
}


@tasks_api_bp.route('', methods=['GET'])
def list_tasks():
    return jsonify({'success': True, 'tasks': get_scheduler().status()})


@tasks_api_bp.route('/<name>/<action>', methods=['POST'])
def task_action(name: str, action: str):
    handler = _ACTIONS.get(action)
    scheduler = get_scheduler()
    if handler is None:
        return jsonify({'success': False, 'error': f'Unknown action: {action}'}), 404
    if not any(task['name'] == name for task in scheduler.status()):
        return jsonify({'success': False, 'error': f'Unknown task: {name}'}), 404
    handler(scheduler, name)
    return jsonify({'success': True, 'task': scheduler.task_status(name)})
