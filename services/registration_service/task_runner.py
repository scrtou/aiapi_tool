from __future__ import annotations

import threading
from datetime import datetime, timezone

from libs.contracts.registration import CreateRegistrationTaskRequest
from libs.core.config import env_int, env_str
from libs.core.exceptions import ServiceError
from libs.core.sqlite import SQLiteResultStore, SQLiteSessionStore, SQLiteWorkerHeartbeatStore


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RegistrationCancellationRequested(Exception):
    def __init__(self, reason: str = "registration task cancelled"):
        super().__init__(reason)
        self.reason = reason


class RegistrationTaskRunner:
    def __init__(self, store, adapter_registry):
        self.store = store
        self.adapter_registry = adapter_registry
        self.session_store = SQLiteSessionStore()
        self.result_store = SQLiteResultStore()
        self.heartbeat_store = SQLiteWorkerHeartbeatStore()
        self.poll_interval_seconds = env_int("REGISTRATION_WORKER_POLL_INTERVAL_SECONDS", 2)
        self.worker_name = env_str("REGISTRATION_WORKER_NAME") or env_str("HOSTNAME") or "registration-worker"
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def _event(self, task_id: str, *, status: str, state: str, message: str, level: str = "info", data: dict | None = None):
        self.store.add_event(task_id, {
            "time": utcnow_iso(),
            "service": "registration-service",
            "task_id": task_id,
            "status": status,
            "state": state,
            "level": level,
            "message": message,
            "data": data or {},
        })

    def _touch_heartbeat(self, *, state: str, active_task_id: str | None = None, queued: int | None = None):
        payload = {
            "state": state,
            "active_task_id": active_task_id,
            "queued_tasks": queued,
            "poll_interval_seconds": self.poll_interval_seconds,
        }
        self.heartbeat_store.touch("registration-service", self.worker_name, payload)

    def start_worker(self):
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def stop_worker(self):
        self._stop_event.set()
        worker = self._worker_thread
        if worker and worker.is_alive():
            worker.join(timeout=1)
        self._touch_heartbeat(state="stopped")
        self._worker_thread = None

    def _queued_count(self) -> int:
        return len(self.store.list_tasks(status="queued", project_id=None, include_all=True, limit=100000))

    def _worker_loop(self):
        while not self._stop_event.is_set():
            task = self.store.claim_next_task(self.worker_name)
            if not task:
                self._touch_heartbeat(state="idle", queued=self._queued_count())
                self._stop_event.wait(self.poll_interval_seconds)
                continue
            task_id = task.get("task_id")
            self._touch_heartbeat(state="processing", active_task_id=task_id, queued=self._queued_count())
            request_payload = task.get("request") or {}
            try:
                request = CreateRegistrationTaskRequest.model_validate(request_payload)
            except Exception as exc:
                self.store.update_task(
                    task_id,
                    status="failed",
                    state="invalid_request",
                    error={
                        "code": "TASK_REQUEST_INVALID",
                        "message": str(exc),
                        "service": "registration-service",
                        "state": "invalid_request",
                        "retryable": False,
                        "details": {},
                    },
                    updated_at=utcnow_iso(),
                    finished_at=utcnow_iso(),
                )
                self._event(task_id, status="failed", state="invalid_request", message=str(exc), level="error")
                continue
            self._run(task_id, request.site, request.identity, request.mail_account, request.proxy, request.strategy)

    def _ensure_not_cancelled(self, task_id: str):
        payload = self.store.get_task(task_id) or {}
        if payload.get("status") == "cancelled" or payload.get("cancel_requested"):
            raise RegistrationCancellationRequested(str(payload.get("cancel_reason") or "registration task cancelled"))

    def _mark_cancelled(self, task_id: str, reason: str):
        task = self.store.get_task(task_id) or {}
        self.store.update_task(
            task_id,
            status="cancelled",
            state="cancelled",
            progress=task.get("progress", {}),
            error=None,
            cancel_requested=False,
            cancel_reason=reason,
            updated_at=utcnow_iso(),
            finished_at=utcnow_iso(),
        )
        self._event(task_id, status="cancelled", state="cancelled", message=reason, level="warning")

    def _run(self, task_id: str, site: str, identity, mail_account, proxy=None, strategy=None):
        self._ensure_not_cancelled(task_id)
        self.store.update_task(task_id, status="running", state="init", updated_at=utcnow_iso())
        self._event(task_id, status="running", state="init", message="registration task started", data={"site": site, "email": mail_account.address})
        adapter = self.adapter_registry.get(site)
        task = self.store.get_task(task_id) or {}
        project_id = task.get("project_id")
        try:
            execution_strategy = dict(strategy or {})
            execution_strategy["cancel_check"] = lambda: self._ensure_not_cancelled(task_id)
            result = adapter.register(identity, mail_account, proxy, execution_strategy)
            self._ensure_not_cancelled(task_id)
            task = self.store.get_task(task_id) or {}
            progress = task.get("progress", {})
            if project_id:
                result = result.model_copy(update={"project_id": project_id})
            progress.update({"step": 12, "total_steps": 12, "message": "completed"})
            self.store.set_artifacts(task_id, result.artifacts)
            stored_artifacts = self.store.list_artifacts(task_id)
            result.artifacts = stored_artifacts
            payload = result.model_dump(mode="json")
            self.store.update_task(
                task_id,
                status="succeeded",
                state="complete",
                progress=progress,
                result=payload,
                updated_at=utcnow_iso(),
                finished_at=utcnow_iso(),
            )
            self._event(task_id, status="succeeded", state="complete", message="registration task completed", data={"email": result.account["email"], "personid": result.identity.external_subject})
            self.session_store.save(
                result.session.access_token,
                result.site,
                payload,
                result.identity.external_subject,
                result.identity.external_user_id,
                project_id=project_id,
            )
            self.result_store.save("registration", task_id, result.site, payload, project_id=project_id)
        except RegistrationCancellationRequested as exc:
            self._mark_cancelled(task_id, exc.reason)
        except ServiceError as exc:
            task = self.store.get_task(task_id) or {}
            progress = task.get("progress", {})
            self.store.update_task(
                task_id,
                status="failed",
                state=exc.state or "failed",
                progress=progress,
                error={
                    "code": exc.code,
                    "message": exc.message,
                    "service": exc.service,
                    "state": exc.state,
                    "retryable": exc.retryable,
                    "details": exc.details,
                },
                updated_at=utcnow_iso(),
                finished_at=utcnow_iso(),
            )
            self._event(task_id, status="failed", state=exc.state or "failed", message=exc.message, level="error", data={"code": exc.code, **exc.details})
        except Exception as exc:
            task = self.store.get_task(task_id) or {}
            progress = task.get("progress", {})
            self.store.update_task(
                task_id,
                status="failed",
                state="failed",
                progress=progress,
                error={
                    "code": "INTERNAL_ERROR",
                    "message": str(exc),
                    "service": "registration-service",
                    "state": "failed",
                    "retryable": False,
                    "details": {},
                },
                updated_at=utcnow_iso(),
                finished_at=utcnow_iso(),
            )
            self._event(task_id, status="failed", state="failed", message=str(exc), level="error")
        finally:
            queued = self._queued_count()
            self._touch_heartbeat(state="idle", queued=queued)
