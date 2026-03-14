from __future__ import annotations

from datetime import datetime, timezone

from libs.contracts.common import ProgressInfo
from libs.contracts.registration import (
    CreateRegistrationTaskRequest,
    RegistrationTask,
    RegistrationTaskData,
    RegistrationTaskDetailData,
    RegistrationTasksData,
)
from libs.core.auth import assert_project_access
from libs.core.exceptions import ServiceError
from libs.core.sqlite import SQLiteTaskStore, SQLiteWorkerHeartbeatStore
from libs.core.tracing import generate_task_id
from services.registration_service.adapter_registry import RegistrationAdapterRegistry
from services.registration_service.task_runner import RegistrationTaskRunner


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RegistrationService:
    def __init__(self):
        self.store = SQLiteTaskStore("registration_service")
        self.registry = RegistrationAdapterRegistry()
        self.runner = RegistrationTaskRunner(self.store, self.registry)
        self.heartbeat_store = SQLiteWorkerHeartbeatStore()

    def create_task(self, payload: CreateRegistrationTaskRequest, *, project_id: str | None = None) -> RegistrationTaskData:
        if payload.mail_account.project_id and project_id and payload.mail_account.project_id != project_id:
            raise ServiceError(
                code="PROJECT_CONTEXT_INVALID",
                message="mail account project does not match request project",
                service="registration-service",
                state="create_task",
                status_code=403,
            )
        if project_id and payload.mail_account.project_id != project_id:
            payload = payload.model_copy(update={"mail_account": payload.mail_account.model_copy(update={"project_id": project_id})})
        task_id = generate_task_id("tsk_reg")
        now = utcnow_iso()
        task = RegistrationTask(
            task_id=task_id,
            project_id=project_id,
            site=payload.site,
            status="queued",
            state="init",
            progress=ProgressInfo(step=0, total_steps=12, message="queued"),
            created_at=now,
            updated_at=now,
        )
        self.store.create_task(
            task_id,
            {
                **task.model_dump(mode="json"),
                "request": payload.model_dump(mode="json"),
                "cancel_requested": False,
                "cancel_reason": None,
                "result": None,
                "error": None,
                "finished_at": None,
            },
        )
        return RegistrationTaskData(task=task)

    def recover_incomplete_tasks(self) -> int:
        rows = self.store.list_tasks(project_id=None, include_all=True, limit=100000)
        recovered = 0
        for row in rows:
            status = row.get("status")
            if status in {"succeeded", "failed", "cancelled", "timeout"}:
                continue
            task_id = row["task_id"]
            if status == "queued" and not row.get("cancel_requested"):
                continue
            if row.get("cancel_requested"):
                self.store.update_task(
                    task_id,
                    status="cancelled",
                    state="cancelled",
                    cancel_requested=False,
                    updated_at=utcnow_iso(),
                    finished_at=utcnow_iso(),
                )
                self.store.add_event(task_id, {
                    "time": utcnow_iso(),
                    "service": "registration-service",
                    "task_id": task_id,
                    "status": "cancelled",
                    "state": "cancelled",
                    "level": "warning",
                    "message": "task cancelled during service recovery",
                    "data": {},
                })
            else:
                self.store.update_task(
                    task_id,
                    status="failed",
                    state="service_restarted",
                    error={
                        "code": "TASK_RECOVERY_REQUIRED",
                        "message": "task interrupted by service restart; retry is required",
                        "service": "registration-service",
                        "state": "service_restarted",
                        "retryable": True,
                        "details": {},
                    },
                    updated_at=utcnow_iso(),
                    finished_at=utcnow_iso(),
                )
                self.store.add_event(task_id, {
                    "time": utcnow_iso(),
                    "service": "registration-service",
                    "task_id": task_id,
                    "status": "failed",
                    "state": "service_restarted",
                    "level": "warning",
                    "message": "task marked failed during service recovery",
                    "data": {},
                })
                recovered += 1
        return recovered

    def start_worker(self):
        self.runner.start_worker()

    def stop_worker(self):
        self.runner.stop_worker()

    def cancel_task(self, task_id: str, *, project_id: str | None = None, allow_cross_project: bool = False, reason: str = "registration task cancelled by client") -> RegistrationTaskData:
        payload = self.store.get_task(task_id)
        if not payload:
            raise ServiceError(
                code="TASK_NOT_FOUND",
                message=f"registration task not found: {task_id}",
                service="registration-service",
                state="cancel_task",
                status_code=404,
            )
        assert_project_access(
            service="registration-service",
            resource_project_id=payload.get("project_id"),
            request_project_id=project_id,
            allow_cross_project=allow_cross_project,
            resource_name="registration task",
            state="cancel_task",
        )

        status = payload.get("status")
        if status in {"succeeded", "failed", "cancelled", "timeout"}:
            task = RegistrationTask(
                task_id=payload["task_id"],
                project_id=payload.get("project_id"),
                site=payload["site"],
                status=payload["status"],
                state=payload["state"],
                progress=ProgressInfo(**payload.get("progress", {})),
                created_at=payload["created_at"],
                updated_at=payload["updated_at"],
            )
            return RegistrationTaskData(task=task)

        if status == "queued":
            self.store.update_task(
                task_id,
                status="cancelled",
                state="cancelled",
                cancel_requested=False,
                cancel_reason=reason,
                updated_at=utcnow_iso(),
                finished_at=utcnow_iso(),
            )
            self.store.add_event(task_id, {
                "time": utcnow_iso(),
                "service": "registration-service",
                "task_id": task_id,
                "status": "cancelled",
                "state": "cancelled",
                "level": "warning",
                "message": reason,
                "data": {},
            })
        else:
            self.store.update_task(
                task_id,
                cancel_requested=True,
                cancel_reason=reason,
                state="cancel_requested",
                updated_at=utcnow_iso(),
            )
            self.store.add_event(task_id, {
                "time": utcnow_iso(),
                "service": "registration-service",
                "task_id": task_id,
                "status": status,
                "state": "cancel_requested",
                "level": "warning",
                "message": reason,
                "data": {},
            })

        updated = self.store.get_task(task_id) or payload
        task = RegistrationTask(
            task_id=updated["task_id"],
            project_id=updated.get("project_id"),
            site=updated["site"],
            status=updated["status"],
            state=updated["state"],
            progress=ProgressInfo(**updated.get("progress", {})),
            created_at=updated["created_at"],
            updated_at=updated["updated_at"],
        )
        return RegistrationTaskData(task=task)

    def get_task(self, task_id: str, *, project_id: str | None = None, allow_cross_project: bool = False) -> RegistrationTaskDetailData:
        payload = self.store.get_task(task_id)
        if not payload:
            raise ServiceError(
                code="TASK_NOT_FOUND",
                message=f"registration task not found: {task_id}",
                service="registration-service",
                state="get_task",
                status_code=404,
            )
        assert_project_access(
            service="registration-service",
            resource_project_id=payload.get("project_id"),
            request_project_id=project_id,
            allow_cross_project=allow_cross_project,
            resource_name="registration task",
            state="get_task",
        )
        task = RegistrationTask(
            task_id=payload["task_id"],
            project_id=payload.get("project_id"),
            site=payload["site"],
            status=payload["status"],
            state=payload["state"],
            progress=ProgressInfo(**payload.get("progress", {})),
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )
        return RegistrationTaskDetailData(
            task=task,
            result=payload.get("result"),
            error=payload.get("error"),
            artifacts=self.store.list_artifacts(task_id),
            events=self.store.list_events(task_id),
        )


    def list_tasks(
        self,
        *,
        status: str | None = None,
        state: str | None = None,
        site: str | None = None,
        project_id: str | None = None,
        allow_cross_project: bool = False,
        limit: int = 50,
    ) -> RegistrationTasksData:
        rows = self.store.list_tasks(
            status=status,
            state=state,
            site=site,
            project_id=project_id,
            include_all=allow_cross_project,
            limit=limit,
        )
        tasks = [
            RegistrationTask(
                task_id=row["task_id"],
                project_id=row.get("project_id"),
                site=row["site"],
                status=row["status"],
                state=row["state"],
                progress=ProgressInfo(**row.get("progress", {})),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
        return RegistrationTasksData(tasks=tasks, total=len(tasks))

    def metrics_snapshot(self) -> dict:
        rows = self.store.list_tasks(project_id=None, include_all=True, limit=100000)
        counts: dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        workers = self.heartbeat_store.list(service_name="registration-service")
        return {
            "service": "registration-service",
            "task_counts": counts,
            "queue_depth": counts.get("queued", 0),
            "running_tasks": counts.get("running", 0),
            "workers": workers,
            "worker": workers[0] if workers else {
                "service_name": "registration-service",
                "worker_name": self.runner.worker_name,
                "state": "unknown",
            },
        }
