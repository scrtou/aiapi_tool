from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

from libs.contracts.common import ProgressInfo
from libs.contracts.login import LoginRequest
from libs.contracts.mail import CreateMailAccountRequest
from libs.contracts.registration import CreateRegistrationTaskRequest
from libs.contracts.workflow import (
    LoginWorkflowRequest,
    RegisterWorkflowRequest,
    WorkflowTask,
    WorkflowTaskData,
    WorkflowTaskDetailData,
    WorkflowTasksData,
)
from libs.core.auth import assert_project_access
from libs.core.config import env_int, env_str
from libs.core.exceptions import ServiceError
from libs.core.http import ServiceHttpClient
from libs.core.sqlite import SQLiteCallbackEventStore, SQLiteResultStore, SQLiteSessionStore, SQLiteTaskStore, SQLiteWorkerHeartbeatStore
from libs.core.tracing import generate_task_id


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "timeout"}


class CancellationRequested(Exception):
    def __init__(self, reason: str = "workflow cancelled"):
        super().__init__(reason)
        self.reason = reason


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrchestratorService:
    def __init__(self):
        self.store = SQLiteTaskStore("orchestrator_service")
        self.session_store = SQLiteSessionStore()
        self.result_store = SQLiteResultStore()
        internal_token = env_str("INTERNAL_SERVICE_TOKEN")
        timeout = env_int("WORKFLOW_HTTP_TIMEOUT_SECONDS", 600)
        self.mail_client = ServiceHttpClient(
            "mail-service",
            env_str("MAIL_SERVICE_URL", "http://localhost:8001"),
            internal_token,
            timeout,
        )
        self.proxy_client = ServiceHttpClient(
            "proxy-service",
            env_str("PROXY_SERVICE_URL", "http://localhost:8002"),
            internal_token,
            timeout,
        )
        self.registration_client = ServiceHttpClient(
            "registration-service",
            env_str("REGISTRATION_SERVICE_URL", "http://localhost:8003"),
            internal_token,
            timeout,
        )
        self.login_client = ServiceHttpClient(
            "login-service",
            env_str("LOGIN_SERVICE_URL", "http://localhost:8004"),
            internal_token,
            timeout,
        )
        self.poll_interval_seconds = env_int("WORKFLOW_TASK_POLL_INTERVAL_SECONDS", 3)
        self.max_polls = env_int("WORKFLOW_TASK_MAX_POLLS", 120)
        self.heartbeat_store = SQLiteWorkerHeartbeatStore()
        self.callback_store = SQLiteCallbackEventStore()
        self.worker_name = env_str("WORKFLOW_WORKER_NAME") or env_str("HOSTNAME") or "orchestrator-worker"
        self.worker_poll_interval_seconds = env_int("WORKFLOW_WORKER_POLL_INTERVAL_SECONDS", 2)
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def _event(self, task_id: str, *, status: str, state: str, message: str, level: str = "info", data: dict | None = None):
        self.store.add_event(task_id, {
            "time": utcnow_iso(),
            "service": "orchestrator-service",
            "task_id": task_id,
            "status": status,
            "state": state,
            "level": level,
            "message": message,
            "data": data or {},
        })

    def _request_payload(self, payload: Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            return payload.model_dump(mode="json")
        return dict(payload)

    def _request_fingerprint(self, request_payload: dict[str, Any]) -> str:
        serialized = json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _current_task_data(self, task_id: str) -> WorkflowTaskData:
        payload = self.store.get_task(task_id) or {}
        return WorkflowTaskData(
            task_id=task_id,
            status=str(payload.get("status") or "unknown"),
            state=str(payload.get("state") or "unknown"),
        )

    def _find_existing_task(
        self,
        *,
        workflow_type: str,
        project_id: str | None,
        idempotency_key: str | None,
        request_fingerprint: str,
    ) -> WorkflowTaskData | None:
        if not idempotency_key:
            return None
        rows = self.store.list_tasks(
            project_id=project_id,
            include_all=project_id is None,
            limit=10000,
        )
        for row in rows:
            if row.get("workflow_type") != workflow_type:
                continue
            if row.get("idempotency_key") != idempotency_key:
                continue
            existing_fingerprint = row.get("request_fingerprint")
            if existing_fingerprint and existing_fingerprint != request_fingerprint:
                raise ServiceError(
                    code="IDEMPOTENCY_CONFLICT",
                    message="idempotency key has already been used with a different request payload",
                    service="orchestrator-service",
                    state="idempotency",
                    status_code=409,
                )
            return WorkflowTaskData(task_id=row["task_id"], status=row["status"], state=row["state"])
        return None

    def _create_task(
        self,
        *,
        workflow_type: str,
        site: str,
        request_payload: dict[str, Any],
        total_steps: int,
        project_id: str | None,
        idempotency_key: str | None,
    ) -> tuple[str, WorkflowTaskData, bool]:
        request_fingerprint = self._request_fingerprint(request_payload)
        existing = self._find_existing_task(
            workflow_type=workflow_type,
            project_id=project_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if existing:
            return existing.task_id, existing, False

        task_id = generate_task_id("wft")
        now = utcnow_iso()
        task = WorkflowTask(
            task_id=task_id,
            project_id=project_id,
            workflow_type=workflow_type,
            site=site,
            status="queued",
            state="init",
            progress=ProgressInfo(step=0, total_steps=total_steps, message="queued"),
            created_at=now,
            updated_at=now,
        )
        self.store.create_task(
            task_id,
            {
                **task.model_dump(mode="json"),
                "request": request_payload,
                "request_fingerprint": request_fingerprint,
                "idempotency_key": idempotency_key,
                "cancel_requested": False,
                "cancel_reason": None,
                "result": None,
                "error": None,
                "finished_at": None,
            },
        )
        self._event(task_id, status="queued", state="init", message="workflow queued", data={"site": site, "workflow_type": workflow_type})
        return task_id, WorkflowTaskData(task_id=task_id, status=task.status, state=task.state), True

    def _start_thread(self, target, *args):
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()

    def _touch_heartbeat(self, *, state: str, active_task_id: str | None = None, queued: int | None = None):
        payload = {
            "state": state,
            "active_task_id": active_task_id,
            "queued_tasks": queued,
            "poll_interval_seconds": self.worker_poll_interval_seconds,
        }
        self.heartbeat_store.touch("orchestrator-service", self.worker_name, payload)

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
            task_payload = self.store.claim_next_task(self.worker_name)
            if not task_payload:
                self._touch_heartbeat(state="idle", queued=self._queued_count())
                self._stop_event.wait(self.worker_poll_interval_seconds)
                continue
            task_id = task_payload.get("task_id")
            self._touch_heartbeat(state="processing", active_task_id=task_id, queued=self._queued_count())
            request_payload = task_payload.get("request") or {}
            workflow_type = task_payload.get("workflow_type")
            project_id = task_payload.get("project_id")
            try:
                if workflow_type == "register_and_login":
                    self._run_register_and_login(task_id, RegisterWorkflowRequest.model_validate(request_payload), project_id)
                elif workflow_type == "register":
                    self._run_register(task_id, RegisterWorkflowRequest.model_validate(request_payload), project_id)
                elif workflow_type == "login":
                    self._run_login(task_id, LoginWorkflowRequest.model_validate(request_payload), project_id)
                else:
                    raise ServiceError(
                        code="UNSUPPORTED_OPERATION",
                        message=f"unsupported workflow type: {workflow_type}",
                        service="orchestrator-service",
                        state="worker_loop",
                        status_code=422,
                    )
            except ServiceError as exc:
                self._fail_task(task_id, exc)
            except Exception as exc:
                self._fail_task(task_id, ServiceError(
                    code="TASK_REQUEST_INVALID",
                    message=str(exc),
                    service="orchestrator-service",
                    state="worker_loop",
                    status_code=500,
                ))
            finally:
                self._touch_heartbeat(state="idle", queued=self._queued_count())

    def _callback_config(self, task_payload: dict[str, Any]) -> dict[str, Any] | None:
        request_payload = task_payload.get("request") or {}
        callback = request_payload.get("callback") or {}
        if not isinstance(callback, dict):
            return None
        url = str(callback.get("url") or "").strip()
        if not url:
            return None
        return callback

    def _callback_event_type(self, task_payload: dict[str, Any]) -> str:
        workflow_type = task_payload.get("workflow_type") or "workflow"
        status = task_payload.get("status") or "unknown"
        return f"workflow.{workflow_type}.{status}"

    def _stable_callback_event_id(self, task_payload: dict[str, Any]) -> str:
        event_type = self._callback_event_type(task_payload)
        task_id = str(task_payload.get("task_id") or "")
        digest = hashlib.sha256(f"{task_id}:{event_type}".encode("utf-8")).hexdigest()[:24]
        return f"wcb_{digest}"

    def _callback_payload(self, task_payload: dict[str, Any], event_id: str) -> dict[str, Any]:
        return {
            "event_id": event_id,
            "event_type": self._callback_event_type(task_payload),
            "task_id": task_payload.get("task_id"),
            "workflow_type": task_payload.get("workflow_type"),
            "project_id": task_payload.get("project_id"),
            "site": task_payload.get("site"),
            "status": task_payload.get("status"),
            "state": task_payload.get("state"),
            "result": task_payload.get("result"),
            "error": task_payload.get("error"),
            "occurred_at": task_payload.get("finished_at") or utcnow_iso(),
        }

    def _ensure_callback_event(self, task_id: str) -> dict[str, Any] | None:
        task_payload = self.store.get_task(task_id) or {}
        if not self._callback_config(task_payload):
            return None
        event_id = task_payload.get("callback_event_id") or self._stable_callback_event_id(task_payload)
        task_payload["callback_event_id"] = event_id
        task_payload["callback_event_type"] = self._callback_event_type(task_payload)
        self.store.update_task(
            task_id,
            callback_event_id=task_payload["callback_event_id"],
            callback_event_type=task_payload["callback_event_type"],
        )
        payload = self._callback_payload(task_payload, event_id)
        record = self.callback_store.create_or_get(
            event_id=event_id,
            service_name="orchestrator-service",
            task_id=task_id,
            event_type=payload["event_type"],
            payload=payload,
        )
        return record

    def _dispatch_callback(self, task_id: str):
        event_record = self._ensure_callback_event(task_id)
        if not event_record:
            return
        self._start_thread(self._deliver_callback, event_record["event_id"])


    def recover_pending_callbacks(self) -> int:
        recovered = 0
        rows = self.store.list_tasks(project_id=None, include_all=True, limit=100000)
        for task_payload in rows:
            if task_payload.get("status") not in TERMINAL_STATUSES:
                continue
            if not self._callback_config(task_payload):
                continue
            event_record = self._ensure_callback_event(task_payload["task_id"])
            if not event_record:
                continue
            if event_record.get("delivery_status") == "delivered":
                continue
            self._start_thread(self._deliver_callback, event_record["event_id"])
            recovered += 1
        return recovered

    def _deliver_callback(self, event_id: str):
        record = self.callback_store.claim(event_id)
        if not record:
            return
        task_id = record["task_id"]
        task_payload = self.store.get_task(task_id) or {}
        callback = self._callback_config(task_payload)
        if not callback:
            return

        payload = record.get("payload") or self._callback_payload(task_payload, event_id)
        payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        url = callback["url"]
        timeout_seconds = max(1, int(callback.get("timeout_seconds") or 15))
        max_attempts = max(1, int(callback.get("max_attempts") or 3))
        backoff_seconds = max(1, int(callback.get("retry_backoff_seconds") or 3))
        secret = str(callback.get("secret") or "").strip() or None
        attempts_done = int(record.get("attempts", 0))

        headers = {
            "Content-Type": "application/json",
            "X-Task-Id": task_id,
            "X-Workflow-Type": str(task_payload.get("workflow_type") or "workflow"),
            "X-Callback-Event-Id": event_id,
        }
        extra_headers = callback.get("headers") or {}
        if isinstance(extra_headers, dict):
            headers.update({str(key): str(value) for key, value in extra_headers.items()})
        if secret:
            signature = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
            headers["X-Callback-Signature-256"] = f"sha256={signature}"

        for attempt in range(attempts_done + 1, max_attempts + 1):
            try:
                response = requests.post(
                    url,
                    data=payload_bytes,
                    headers=headers,
                    timeout=timeout_seconds,
                )
                response.raise_for_status()
                self.callback_store.mark_delivered(event_id, attempts=attempt)
                self.store.update_task(
                    task_id,
                    callback_delivery_status="delivered",
                    callback_attempts=attempt,
                    callback_last_error=None,
                    callback_delivered_at=utcnow_iso(),
                )
                self._event(
                    task_id,
                    status=task_payload.get("status") or "unknown",
                    state=task_payload.get("state") or "callback",
                    message="workflow callback delivered",
                    data={"url": url, "status_code": response.status_code, "attempt": attempt, "event_id": event_id},
                )
                return
            except Exception as exc:
                self.callback_store.mark_attempt_failed(event_id, attempts=attempt, error=str(exc))
                self.store.update_task(
                    task_id,
                    callback_delivery_status="failed",
                    callback_attempts=attempt,
                    callback_last_error=str(exc),
                )
                if attempt >= max_attempts:
                    self._event(
                        task_id,
                        status=task_payload.get("status") or "unknown",
                        state="callback_failed",
                        level="error",
                        message=f"workflow callback failed: {exc}",
                        data={"url": url, "attempt": attempt, "max_attempts": max_attempts, "event_id": event_id},
                    )
                    return
                self._event(
                    task_id,
                    status=task_payload.get("status") or "unknown",
                    state="callback_retry",
                    level="warning",
                    message=f"workflow callback attempt failed, retrying: {exc}",
                    data={"url": url, "attempt": attempt, "next_attempt": attempt + 1, "event_id": event_id},
                )
                time.sleep(backoff_seconds * attempt)

    def _ensure_not_cancelled(self, task_id: str):
        payload = self.store.get_task(task_id) or {}
        if payload.get("status") == "cancelled" or payload.get("cancel_requested"):
            raise CancellationRequested(str(payload.get("cancel_reason") or "workflow cancelled"))

    def _lease_proxy(self, task_id: str, proxy_policy, project_id: str | None) -> dict[str, Any] | None:
        if not getattr(proxy_policy, "enabled", False):
            return None
        self._ensure_not_cancelled(task_id)
        lease_request = dict(getattr(proxy_policy, "lease_request", {}) or {})
        lease_response = self.proxy_client.post(
            "/api/v1/proxies/lease",
            trace_id=task_id,
            project_id=project_id,
            json=lease_request,
        )
        lease = lease_response["data"]["lease"]
        self._event(task_id, status="running", state="proxy_leased", message="proxy leased", data={"proxy_id": lease.get("proxy_id")})
        return lease

    def _release_proxy(self, task_id: str, proxy: dict[str, Any] | None, project_id: str | None):
        if not proxy:
            return
        proxy_id = proxy.get("proxy_id")
        if not proxy_id:
            return
        try:
            self.proxy_client.post(
                f"/api/v1/proxies/{proxy_id}/release",
                trace_id=task_id,
                project_id=project_id,
            )
            current = self.store.get_task(task_id) or {}
            self._event(
                task_id,
                status=current.get("status") or "unknown",
                state="proxy_released",
                message="proxy released",
                data={"proxy_id": proxy_id},
            )
        except Exception as exc:
            current = self.store.get_task(task_id) or {}
            self._event(
                task_id,
                status=current.get("status") or "unknown",
                state="proxy_release_failed",
                level="warning",
                message=f"proxy release failed: {exc}",
                data={"proxy_id": proxy_id},
            )

    def _cancel_registration_task(self, task_id: str, registration_task_id: str | None, project_id: str | None):
        if not registration_task_id:
            return
        try:
            self.registration_client.post(
                f"/api/v1/registrations/tasks/{registration_task_id}/cancel",
                trace_id=task_id,
                project_id=project_id,
            )
            current = self.store.get_task(task_id) or {}
            self._event(
                task_id,
                status=current.get("status") or "unknown",
                state="registration_cancel_requested",
                level="warning",
                message="downstream registration cancellation requested",
                data={"registration_task_id": registration_task_id},
            )
        except Exception as exc:
            current = self.store.get_task(task_id) or {}
            self._event(
                task_id,
                status=current.get("status") or "unknown",
                state="registration_cancel_failed",
                level="warning",
                message=f"downstream registration cancellation failed: {exc}",
                data={"registration_task_id": registration_task_id},
            )

    def _create_mail_account(self, task_id: str, payload: RegisterWorkflowRequest, project_id: str | None) -> dict[str, Any]:
        provider_order = payload.mail_policy.providers or ["gptmail", "moemail"]
        last_error: Exception | None = None
        for provider in provider_order:
            self._ensure_not_cancelled(task_id)
            try:
                mail_request = CreateMailAccountRequest(
                    provider=provider,
                    domain=payload.mail_policy.domain_preference[0] if payload.mail_policy.domain_preference else None,
                    expiry_time_ms=payload.mail_policy.expiry_time_ms,
                    options={},
                )
                mail_response = self.mail_client.post(
                    "/api/v1/mail/accounts",
                    trace_id=task_id,
                    project_id=project_id,
                    json=mail_request.model_dump(mode="json"),
                )
                self._ensure_not_cancelled(task_id)
                return mail_response["data"]["account"]
            except CancellationRequested:
                raise
            except Exception as exc:
                last_error = exc
                continue
        raise last_error or RuntimeError("failed to create mail account")

    def _submit_registration(self, task_id: str, payload: RegisterWorkflowRequest, mail_account: dict[str, Any], proxy: dict[str, Any] | None, project_id: str | None) -> str:
        self._ensure_not_cancelled(task_id)
        reg_request = CreateRegistrationTaskRequest(
            site=payload.site,
            identity=payload.identity,
            mail_account=mail_account,
            proxy=proxy,
            strategy=payload.strategy.model_dump(mode="json"),
        )
        reg_response = self.registration_client.post(
            "/api/v1/registrations/tasks",
            trace_id=task_id,
            project_id=project_id,
            json=reg_request.model_dump(mode="json"),
        )
        reg_task_id = reg_response["data"]["task"]["task_id"]
        self.store.update_task(task_id, registration_task_id=reg_task_id)
        return reg_task_id

    def _poll_registration(self, task_id: str, reg_task_id: str, project_id: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        reg_detail = None
        for _ in range(self.max_polls):
            self._ensure_not_cancelled(task_id)
            reg_detail = self.registration_client.get(
                f"/api/v1/registrations/tasks/{reg_task_id}",
                trace_id=task_id,
                project_id=project_id,
            )
            reg_task = reg_detail["data"]["task"]
            if reg_task["status"] in TERMINAL_STATUSES:
                break
            time.sleep(self.poll_interval_seconds)

        if not reg_detail:
            raise ServiceError(
                code="WORKFLOW_REGISTRATION_SERVICE_FAILED",
                message="registration task detail missing",
                service="orchestrator-service",
                state="registration_running",
                status_code=502,
            )

        reg_task = reg_detail["data"]["task"]
        if reg_task["status"] != "succeeded":
            reg_error = reg_detail["data"].get("error") or {}
            raise ServiceError(
                code=reg_error.get("code", "WORKFLOW_REGISTRATION_SERVICE_FAILED"),
                message=reg_error.get("message", f"registration task failed with status {reg_task['status']}"),
                service=reg_error.get("service", "orchestrator-service"),
                state=reg_task.get("state", "registration_running"),
                retryable=bool(reg_error.get("retryable", False)),
                details={**reg_error.get("details", {}), "registration_task_id": reg_task_id},
                status_code=422,
            )

        reg_result = reg_detail["data"]["result"]
        reg_artifacts = reg_detail["data"].get("artifacts", [])
        if not reg_artifacts:
            for _ in range(5):
                self._ensure_not_cancelled(task_id)
                time.sleep(1)
                reg_detail = self.registration_client.get(
                    f"/api/v1/registrations/tasks/{reg_task_id}",
                    trace_id=task_id,
                    project_id=project_id,
                )
                reg_artifacts = reg_detail["data"].get("artifacts", [])
                if reg_artifacts:
                    break
        return reg_result, reg_artifacts

    def _execute_login(
        self,
        task_id: str,
        site: str,
        credentials: dict[str, Any],
        login_mode: str,
        project_id: str | None,
        proxy: dict[str, Any] | None,
        strategy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._ensure_not_cancelled(task_id)
        strategy_payload = dict(strategy or {})
        strategy_payload.setdefault("mode", login_mode)
        strategy_payload.setdefault("login_mode", login_mode)
        login_request = LoginRequest(
            site=site,
            credentials=credentials,
            proxy=proxy,
            strategy=strategy_payload,
        )
        login_response = self.login_client.post(
            "/api/v1/logins",
            trace_id=task_id,
            project_id=project_id,
            json=login_request.model_dump(mode="json"),
        )
        self._ensure_not_cancelled(task_id)
        return login_response["data"]["result"]

    def _store_workflow_result(self, task_id: str, site: str, result: dict[str, Any], project_id: str | None):
        primary_result = result.get("login") or result.get("registration") or {}
        session = primary_result.get("session") or {}
        identity = primary_result.get("identity") or {}
        access_token = session.get("access_token")
        if access_token:
            self.session_store.save(
                access_token,
                site,
                primary_result,
                identity.get("external_subject"),
                identity.get("external_user_id"),
                project_id=project_id,
            )
        self.result_store.save("workflow", task_id, site, result, project_id=project_id)

    def _mark_succeeded(
        self,
        *,
        task_id: str,
        site: str,
        step: int,
        total_steps: int,
        result: dict[str, Any],
        message: str,
        event_data: dict[str, Any] | None,
        project_id: str | None,
        artifacts: list[dict[str, Any]] | None = None,
    ):
        self._ensure_not_cancelled(task_id)
        self.store.update_task(
            task_id,
            status="succeeded",
            state="complete",
            progress={"step": step, "total_steps": total_steps, "message": "completed"},
            result=result,
            updated_at=utcnow_iso(),
            finished_at=utcnow_iso(),
        )
        if artifacts is not None:
            self.store.set_artifacts(task_id, artifacts)
        self._event(task_id, status="succeeded", state="complete", message=message, data=event_data or {})
        self._store_workflow_result(task_id, site, result, project_id)
        self._dispatch_callback(task_id)

    def _mark_cancelled(self, task_id: str, reason: str):
        payload = self.store.get_task(task_id) or {}
        if payload.get("status") == "cancelled":
            return
        progress = payload.get("progress") or {}
        self.store.update_task(
            task_id,
            status="cancelled",
            state="cancelled",
            progress=progress,
            error=None,
            cancel_requested=False,
            cancel_reason=reason,
            updated_at=utcnow_iso(),
            finished_at=utcnow_iso(),
        )
        self._event(task_id, status="cancelled", state="cancelled", message=reason, level="warning")
        self._dispatch_callback(task_id)

    def _fail_task(self, task_id: str, error: ServiceError):
        self.store.update_task(
            task_id,
            status="failed",
            state=error.state or "failed",
            error={
                "code": error.code,
                "message": error.message,
                "service": error.service,
                "state": error.state,
                "retryable": error.retryable,
                "details": error.details,
            },
            updated_at=utcnow_iso(),
            finished_at=utcnow_iso(),
        )
        self._event(
            task_id,
            status="failed",
            state=error.state or "failed",
            message=error.message,
            level="error",
            data={"code": error.code, **error.details},
        )
        self._dispatch_callback(task_id)

    def create_register_and_login_task(
        self,
        payload: RegisterWorkflowRequest,
        *,
        project_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> WorkflowTaskData:
        task_id, task_data, created = self._create_task(
            workflow_type="register_and_login",
            site=payload.site,
            request_payload=self._request_payload(payload),
            total_steps=4,
            project_id=project_id,
            idempotency_key=idempotency_key,
        )
        return task_data

    def recover_incomplete_tasks(self) -> int:
        rows = self.store.list_tasks(project_id=None, include_all=True, limit=100000)
        recovered = 0
        for row in rows:
            status = row.get("status")
            if status in TERMINAL_STATUSES:
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
                self._event(task_id, status="cancelled", state="cancelled", message="workflow cancelled during service recovery", level="warning")
            else:
                self.store.update_task(
                    task_id,
                    status="failed",
                    state="service_restarted",
                    error={
                        "code": "TASK_RECOVERY_REQUIRED",
                        "message": "workflow interrupted by service restart; retry is required",
                        "service": "orchestrator-service",
                        "state": "service_restarted",
                        "retryable": True,
                        "details": {},
                    },
                    updated_at=utcnow_iso(),
                    finished_at=utcnow_iso(),
                )
                self._event(task_id, status="failed", state="service_restarted", message="workflow marked failed during service recovery", level="warning")
            recovered += 1
        return recovered

    def create_register_task(
        self,
        payload: RegisterWorkflowRequest,
        *,
        project_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> WorkflowTaskData:
        task_id, task_data, created = self._create_task(
            workflow_type="register",
            site=payload.site,
            request_payload=self._request_payload(payload),
            total_steps=3,
            project_id=project_id,
            idempotency_key=idempotency_key,
        )
        return task_data

    def create_login_task(
        self,
        payload: LoginWorkflowRequest,
        *,
        project_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> WorkflowTaskData:
        task_id, task_data, created = self._create_task(
            workflow_type="login",
            site=payload.site,
            request_payload=self._request_payload(payload),
            total_steps=2,
            project_id=project_id,
            idempotency_key=idempotency_key,
        )
        return task_data

    def cancel_task(
        self,
        task_id: str,
        *,
        project_id: str | None = None,
        allow_cross_project: bool = False,
        reason: str = "workflow cancelled by client",
    ) -> WorkflowTaskData:
        payload = self.store.get_task(task_id)
        if not payload:
            raise ServiceError(
                code="TASK_NOT_FOUND",
                message=f"workflow task not found: {task_id}",
                service="orchestrator-service",
                state="cancel_task",
                status_code=404,
            )
        assert_project_access(
            service="orchestrator-service",
            resource_project_id=payload.get("project_id"),
            request_project_id=project_id,
            allow_cross_project=allow_cross_project,
            resource_name="workflow task",
            state="cancel_task",
        )
        if payload.get("status") in TERMINAL_STATUSES:
            return self._current_task_data(task_id)
        registration_task_id = payload.get("registration_task_id")
        if payload.get("status") == "queued":
            self.store.update_task(task_id, cancel_requested=True, cancel_reason=reason, updated_at=utcnow_iso())
            self._cancel_registration_task(task_id, registration_task_id, project_id)
            self._mark_cancelled(task_id, reason)
            return self._current_task_data(task_id)
        self.store.update_task(
            task_id,
            cancel_requested=True,
            cancel_reason=reason,
            state="cancel_requested",
            updated_at=utcnow_iso(),
        )
        self._cancel_registration_task(task_id, registration_task_id, project_id)
        self._event(task_id, status=payload.get("status") or "running", state="cancel_requested", message=reason, level="warning")
        return self._current_task_data(task_id)

    def retry_task(
        self,
        task_id: str,
        *,
        project_id: str | None = None,
        allow_cross_project: bool = False,
        idempotency_key: str | None = None,
    ) -> WorkflowTaskData:
        payload = self.store.get_task(task_id)
        if not payload:
            raise ServiceError(
                code="TASK_NOT_FOUND",
                message=f"workflow task not found: {task_id}",
                service="orchestrator-service",
                state="retry_task",
                status_code=404,
            )
        assert_project_access(
            service="orchestrator-service",
            resource_project_id=payload.get("project_id"),
            request_project_id=project_id,
            allow_cross_project=allow_cross_project,
            resource_name="workflow task",
            state="retry_task",
        )
        if payload.get("status") not in {"failed", "cancelled", "timeout"}:
            raise ServiceError(
                code="TASK_RETRY_NOT_ALLOWED",
                message="only failed, cancelled or timeout tasks can be retried",
                service="orchestrator-service",
                state="retry_task",
                status_code=409,
            )

        workflow_type = payload.get("workflow_type")
        request_payload = payload.get("request") or {}
        resource_project_id = payload.get("project_id") or project_id

        if workflow_type == "register_and_login":
            result = self.create_register_and_login_task(
                RegisterWorkflowRequest.model_validate(request_payload),
                project_id=resource_project_id,
                idempotency_key=idempotency_key,
            )
        elif workflow_type == "register":
            result = self.create_register_task(
                RegisterWorkflowRequest.model_validate(request_payload),
                project_id=resource_project_id,
                idempotency_key=idempotency_key,
            )
        elif workflow_type == "login":
            result = self.create_login_task(
                LoginWorkflowRequest.model_validate(request_payload),
                project_id=resource_project_id,
                idempotency_key=idempotency_key,
            )
        else:
            raise ServiceError(
                code="UNSUPPORTED_OPERATION",
                message=f"unsupported workflow type: {workflow_type}",
                service="orchestrator-service",
                state="retry_task",
                status_code=422,
            )

        self._event(
            task_id,
            status=payload.get("status") or "failed",
            state="retry_requested",
            message="workflow retry requested",
            data={"new_task_id": result.task_id},
        )
        return result

    def _run_register_and_login(self, task_id: str, payload: RegisterWorkflowRequest, project_id: str | None = None):
        leased_proxy = None
        try:
            self.store.update_task(
                task_id,
                status="running",
                state="mail_ready",
                progress={"step": 1, "total_steps": 4, "message": "creating mail account"},
                updated_at=utcnow_iso(),
            )
            self._event(task_id, status="running", state="mail_ready", message="creating mail account")
            mail_account = self._create_mail_account(task_id, payload, project_id)
            leased_proxy = self._lease_proxy(task_id, payload.proxy_policy, project_id)

            self.store.update_task(
                task_id,
                state="registration_running",
                progress={"step": 2, "total_steps": 4, "message": "registration running"},
                updated_at=utcnow_iso(),
            )
            self._event(task_id, status="running", state="registration_running", message="registration task submitted")
            reg_task_id = self._submit_registration(task_id, payload, mail_account, leased_proxy, project_id)
            reg_result, reg_artifacts = self._poll_registration(task_id, reg_task_id, project_id)

            self.store.update_task(
                task_id,
                state="login_running",
                progress={"step": 3, "total_steps": 4, "message": "login running"},
                updated_at=utcnow_iso(),
            )
            self._event(task_id, status="running", state="login_running", message="login request submitted")
            login_result = self._execute_login(
                task_id,
                payload.site,
                credentials=reg_result["account"],
                login_mode=payload.strategy.login_mode,
                project_id=project_id,
                proxy=leased_proxy,
                strategy=payload.strategy.model_dump(mode="json"),
            )

            result = {
                "registration": reg_result,
                "login": login_result,
                "proxy": leased_proxy,
            }
            self._mark_succeeded(
                task_id=task_id,
                site=payload.site,
                step=4,
                total_steps=4,
                result=result,
                message="workflow completed",
                event_data={"email": reg_result["account"]["email"]},
                project_id=project_id,
                artifacts=reg_artifacts,
            )
        except CancellationRequested as exc:
            self._mark_cancelled(task_id, exc.reason)
        except ServiceError as exc:
            self._fail_task(task_id, exc)
        except Exception as exc:
            self._fail_task(task_id, ServiceError(
                code="WORKFLOW_STEP_FAILED",
                message=str(exc),
                service="orchestrator-service",
                state="failed",
                status_code=500,
            ))
        finally:
            self._release_proxy(task_id, leased_proxy, project_id)

    def _run_register(self, task_id: str, payload: RegisterWorkflowRequest, project_id: str | None = None):
        leased_proxy = None
        try:
            self.store.update_task(
                task_id,
                status="running",
                state="mail_ready",
                progress={"step": 1, "total_steps": 3, "message": "creating mail account"},
                updated_at=utcnow_iso(),
            )
            self._event(task_id, status="running", state="mail_ready", message="creating mail account")
            mail_account = self._create_mail_account(task_id, payload, project_id)
            leased_proxy = self._lease_proxy(task_id, payload.proxy_policy, project_id)

            self.store.update_task(
                task_id,
                state="registration_running",
                progress={"step": 2, "total_steps": 3, "message": "registration running"},
                updated_at=utcnow_iso(),
            )
            self._event(task_id, status="running", state="registration_running", message="registration task submitted")
            reg_task_id = self._submit_registration(task_id, payload, mail_account, leased_proxy, project_id)
            reg_result, reg_artifacts = self._poll_registration(task_id, reg_task_id, project_id)

            result = {"registration": reg_result, "proxy": leased_proxy}
            self._mark_succeeded(
                task_id=task_id,
                site=payload.site,
                step=3,
                total_steps=3,
                result=result,
                message="register workflow completed",
                event_data={"email": reg_result["account"]["email"]},
                project_id=project_id,
                artifacts=reg_artifacts,
            )
        except CancellationRequested as exc:
            self._mark_cancelled(task_id, exc.reason)
        except ServiceError as exc:
            self._fail_task(task_id, exc)
        except Exception as exc:
            self._fail_task(task_id, ServiceError(
                code="WORKFLOW_STEP_FAILED",
                message=str(exc),
                service="orchestrator-service",
                state="failed",
                status_code=500,
            ))
        finally:
            self._release_proxy(task_id, leased_proxy, project_id)

    def _run_login(self, task_id: str, payload: LoginWorkflowRequest, project_id: str | None = None):
        leased_proxy = None
        try:
            self.store.update_task(
                task_id,
                status="running",
                state="login_running",
                progress={"step": 1, "total_steps": 2, "message": "login running"},
                updated_at=utcnow_iso(),
            )
            self._event(task_id, status="running", state="login_running", message="login request submitted")
            leased_proxy = self._lease_proxy(task_id, payload.proxy_policy, project_id)
            login_result = self._execute_login(
                task_id,
                payload.site,
                credentials=payload.credentials.model_dump(mode="json"),
                login_mode=payload.strategy.login_mode,
                project_id=project_id,
                proxy=leased_proxy,
                strategy=payload.strategy.model_dump(mode="json"),
            )
            result = {"login": login_result, "proxy": leased_proxy}
            self._mark_succeeded(
                task_id=task_id,
                site=payload.site,
                step=2,
                total_steps=2,
                result=result,
                message="login workflow completed",
                event_data={"email": login_result.get("account", {}).get("email")},
                project_id=project_id,
            )
        except CancellationRequested as exc:
            self._mark_cancelled(task_id, exc.reason)
        except ServiceError as exc:
            self._fail_task(task_id, exc)
        except Exception as exc:
            self._fail_task(task_id, ServiceError(
                code="WORKFLOW_STEP_FAILED",
                message=str(exc),
                service="orchestrator-service",
                state="failed",
                status_code=500,
            ))
        finally:
            self._release_proxy(task_id, leased_proxy, project_id)

    def get_task(self, task_id: str, *, project_id: str | None = None, allow_cross_project: bool = False) -> WorkflowTaskDetailData:
        payload = self.store.get_task(task_id)
        if not payload:
            raise ServiceError(
                code="TASK_NOT_FOUND",
                message=f"workflow task not found: {task_id}",
                service="orchestrator-service",
                state="get_task",
                status_code=404,
            )
        assert_project_access(
            service="orchestrator-service",
            resource_project_id=payload.get("project_id"),
            request_project_id=project_id,
            allow_cross_project=allow_cross_project,
            resource_name="workflow task",
            state="get_task",
        )
        task = WorkflowTask(
            task_id=payload["task_id"],
            project_id=payload.get("project_id"),
            workflow_type=payload["workflow_type"],
            site=payload["site"],
            status=payload["status"],
            state=payload["state"],
            progress=ProgressInfo(**payload.get("progress", {})),
            created_at=payload["created_at"],
            updated_at=payload["updated_at"],
        )
        return WorkflowTaskDetailData(
            task=task,
            result=payload.get("result"),
            error=payload.get("error"),
            artifacts=self.store.list_artifacts(task_id),
            events=self.store.list_events(task_id),
        )

    def metrics_snapshot(self) -> dict:
        rows = self.store.list_tasks(project_id=None, include_all=True, limit=100000)
        counts: dict[str, int] = {}
        workflow_counts: dict[str, int] = {}
        for row in rows:
            status = str(row.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
            workflow_type = str(row.get("workflow_type") or "unknown")
            workflow_counts[workflow_type] = workflow_counts.get(workflow_type, 0) + 1
        workers = self.heartbeat_store.list(service_name="orchestrator-service")
        callback_events = self.callback_store.list(service_name="orchestrator-service")
        callback_counts: dict[str, int] = {}
        for item in callback_events:
            status = str(item.get("delivery_status") or "unknown")
            callback_counts[status] = callback_counts.get(status, 0) + 1
        return {
            "service": "orchestrator-service",
            "task_counts": counts,
            "workflow_counts": workflow_counts,
            "queue_depth": counts.get("queued", 0),
            "running_tasks": counts.get("running", 0),
            "callback_event_counts": callback_counts,
            "workers": workers,
            "worker": workers[0] if workers else {
                "service_name": "orchestrator-service",
                "worker_name": self.worker_name,
                "state": "unknown",
            },
        }

    def list_tasks(
        self,
        *,
        status: str | None = None,
        state: str | None = None,
        site: str | None = None,
        project_id: str | None = None,
        allow_cross_project: bool = False,
        limit: int = 50,
    ) -> WorkflowTasksData:
        rows = self.store.list_tasks(
            status=status,
            state=state,
            site=site,
            project_id=project_id,
            include_all=allow_cross_project,
            limit=limit,
        )
        tasks = [
            WorkflowTask(
                task_id=row["task_id"],
                project_id=row.get("project_id"),
                workflow_type=row["workflow_type"],
                site=row["site"],
                status=row["status"],
                state=row["state"],
                progress=ProgressInfo(**row.get("progress", {})),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
        return WorkflowTasksData(tasks=tasks, total=len(tasks))
