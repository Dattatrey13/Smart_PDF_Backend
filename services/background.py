"""Background task management for async processing without blocking responses."""
import asyncio
import logging
import time
from typing import Callable, Awaitable, Any
from collections import deque
from enum import Enum

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskResult:
    """Result of a completed background task."""
    __slots__ = ("task_id", "status", "result", "error", "started_at", "completed_at")

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.status = TaskStatus.PENDING
        self.result: Any = None
        self.error: str | None = None
        self.started_at: float | None = None
        self.completed_at: float | None = None


class BackgroundTaskManager:
    """
    Lightweight background task manager for non-blocking operations.

    Use cases:
    - PDF post-processing (Firestore metadata update after upload)
    - AI usage counter increments
    - Cache warming
    - Notification triggers

    NOT for:
    - Long-running jobs > 30 seconds (use a proper queue for those)
    - Critical operations that must not be lost
    """

    def __init__(self, max_concurrent: int = 5, max_history: int = 100):
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[str, TaskResult] = {}
        self._history: deque[str] = deque(maxlen=max_history)
        self._task_counter = 0

    def submit(
        self,
        func: Callable[..., Awaitable[Any]],
        *args,
        task_id: str = None,
        **kwargs,
    ) -> str:
        """
        Submit an async function to run in the background.
        Returns a task_id for status checking.
        """
        self._task_counter += 1
        if task_id is None:
            task_id = f"task_{self._task_counter}_{int(time.time())}"

        result = TaskResult(task_id)
        self._tasks[task_id] = result
        self._history.append(task_id)

        # Fire and forget
        asyncio.create_task(self._run_task(result, func, *args, **kwargs))
        return task_id

    async def _run_task(
        self, result: TaskResult, func: Callable[..., Awaitable[Any]], *args, **kwargs
    ) -> None:
        """Execute a task with semaphore limiting."""
        async with self._semaphore:
            result.status = TaskStatus.RUNNING
            result.started_at = time.time()
            try:
                result.result = await func(*args, **kwargs)
                result.status = TaskStatus.COMPLETED
            except Exception as e:
                result.status = TaskStatus.FAILED
                result.error = str(e)
                logger.error(f"Background task {result.task_id} failed: {e}")
            finally:
                result.completed_at = time.time()

    def get_status(self, task_id: str) -> dict | None:
        """Get the status of a background task."""
        result = self._tasks.get(task_id)
        if result is None:
            return None
        return {
            "task_id": result.task_id,
            "status": result.status.value,
            "error": result.error,
            "started_at": result.started_at,
            "completed_at": result.completed_at,
        }

    @property
    def stats(self) -> dict:
        """Return task manager statistics."""
        statuses = [t.status for t in self._tasks.values()]
        return {
            "total_tasks": len(self._tasks),
            "pending": statuses.count(TaskStatus.PENDING),
            "running": statuses.count(TaskStatus.RUNNING),
            "completed": statuses.count(TaskStatus.COMPLETED),
            "failed": statuses.count(TaskStatus.FAILED),
            "max_concurrent": self._max_concurrent,
        }

    def cleanup_completed(self, max_age_seconds: int = 300) -> int:
        """Remove completed/failed tasks older than max_age_seconds."""
        now = time.time()
        to_remove = []
        for task_id, result in self._tasks.items():
            if result.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                if result.completed_at and (now - result.completed_at) > max_age_seconds:
                    to_remove.append(task_id)
        for task_id in to_remove:
            del self._tasks[task_id]
        return len(to_remove)


# Singleton
task_manager = BackgroundTaskManager()
