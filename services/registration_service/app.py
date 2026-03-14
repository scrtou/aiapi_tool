from __future__ import annotations

from fastapi import Depends, FastAPI, Request

from libs.contracts.common import HealthData
from libs.core.cors import configure_cors
from libs.core.auth import attach_request_context, require_internal_or_admin
from libs.core.config import env_bool
from libs.core.exceptions import ServiceError
from libs.core.responses import error_response, success_response
from libs.core.tracing import generate_trace_id
from services.registration_service.routes.tasks import router as tasks_router
from services.registration_service.routes.artifacts import router as artifacts_router
from services.registration_service.routes.events import router as events_router
from services.registration_service.service import RegistrationService


app = FastAPI(title="registration-service")
app.state.service_name = "registration-service"
configure_cors(app)
app.state.registration_service = RegistrationService()


@app.middleware("http")
async def attach_trace_id(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or generate_trace_id("trc_reg")
    try:
        attach_request_context(request, trace_id)
        return await call_next(request)
    except ServiceError as exc:
        return error_response(trace_id, exc)


@app.get("/api/v1/health")
def health(request: Request):
    return success_response(request.state.trace_id, HealthData(service="registration-service").model_dump(mode="json"))


@app.get("/api/v1/health/details", dependencies=[Depends(require_internal_or_admin())])
def health_details(request: Request):
    data = request.app.state.registration_service.metrics_snapshot()
    return success_response(request.state.trace_id, data)


@app.get("/api/v1/admin/metrics", dependencies=[Depends(require_internal_or_admin())])
def admin_metrics(request: Request):
    data = request.app.state.registration_service.metrics_snapshot()
    return success_response(request.state.trace_id, data)


app.include_router(tasks_router)

app.include_router(artifacts_router)

app.include_router(events_router)


@app.on_event("startup")
def recover_incomplete_tasks():
    if env_bool("REGISTRATION_ENABLE_STARTUP_RECOVERY", True):
        app.state.registration_service.recover_incomplete_tasks()
    if env_bool("REGISTRATION_ENABLE_EMBEDDED_WORKER", True):
        app.state.registration_service.start_worker()


@app.on_event("shutdown")
def stop_worker():
    if env_bool("REGISTRATION_ENABLE_EMBEDDED_WORKER", True):
        app.state.registration_service.stop_worker()
