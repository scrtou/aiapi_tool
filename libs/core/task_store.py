from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any


class InMemoryTaskStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._artifacts: dict[str, list[dict[str, Any]]] = {}

    def create_task(self, task_id: str, payload: dict[str, Any]):
        with self._lock:
            self._tasks[task_id] = deepcopy(payload)
            self._events.setdefault(task_id, [])
            self._artifacts.setdefault(task_id, [])

    def update_task(self, task_id: str, **fields: Any):
        with self._lock:
            task = self._tasks[task_id]
            task.update(deepcopy(fields))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return deepcopy(task) if task else None

    def add_event(self, task_id: str, event: dict[str, Any]):
        with self._lock:
            self._events.setdefault(task_id, []).append(deepcopy(event))

    def list_events(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._events.get(task_id, []))

    def set_artifacts(self, task_id: str, artifacts: list[dict[str, Any]]):
        with self._lock:
            self._artifacts[task_id] = deepcopy(artifacts)

    def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._artifacts.get(task_id, []))
