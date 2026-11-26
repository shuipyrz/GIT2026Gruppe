"""Bounded, defensive in-memory execution history for one business session."""
from copy import deepcopy
from threading import RLock
from uuid import uuid4


class TaskJournal:
    """Keep task evidence separate from map data and conversational prose."""

    def __init__(self):
        self._lock = RLock()
        self._tasks = []

    @property
    def history(self):
        with self._lock:
            return deepcopy(self._tasks)

    @property
    def latest(self):
        with self._lock:
            return deepcopy(self._tasks[-1]) if self._tasks else None

    def save(self, task):
        with self._lock:
            value = deepcopy(task)
            for index, existing in enumerate(self._tasks):
                if existing["task_id"] == value["task_id"]:
                    self._tasks.pop(index)
                    self._tasks.append(value)
                    break
            else:
                self._tasks.append(value)
                self._tasks = self._tasks[-20:]

    def begin(self, goal, plan, *, resume=False, task_id=None):
        with self._lock:
            previous = next((deepcopy(task) for task in reversed(self._tasks)
                             if task_id is None or task["task_id"] == task_id), None)
            if resume and previous and previous["status"] in {"waiting", "partial", "failed", "running"}:
                task = previous
                task["follow_up"] = goal
                task["status"] = "running"
                task["stop_reason"] = ""
            else:
                task = {"task_id": str(uuid4()), "goal": goal, "plan": deepcopy(plan),
                        "steps": [], "status": "running", "stop_reason": "",
                        "follow_up": "", "pending_question": ""}
            self.save(task)
            return task

    def reset(self):
        with self._lock:
            self._tasks.clear()
