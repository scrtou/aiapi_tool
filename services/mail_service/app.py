from __future__ import annotations

from fastapi import Depends, FastAPI, Request

from libs.contracts.common import HealthData
from libs.core.cors import configure_cors
from libs.core.auth import attach_request_context, require_internal_or_admin
from libs.core.exceptions import ServiceError
from libs.core.responses import error_response, success_response
from libs.core.tracing import generate_trace_id
from services.mail_service.routes.accounts import router as accounts_router
from services.mail_service.routes.messages import router as messages_router
from services.mail_service.routes.providers import router as providers_router
from services.mail_service.service import MailService


app = FastAPI(title="mail-service")
app.state.service_name = "mail-service"
configure_cors(app)
app.state.mail_service = MailService()


@app.middleware("http")
async def attach_trace_id(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or generate_trace_id("trc_mail")
    try:
        attach_request_context(request, trace_id)
        return await call_next(request)
    except ServiceError as exc:
        return error_response(trace_id, exc)


@app.get("/api/v1/health")
def health(request: Request):
    return success_response(request.state.trace_id, HealthData(service="mail-service").model_dump(mode="json"))


@app.get("/api/v1/health/details", dependencies=[Depends(require_internal_or_admin())])
def health_details(request: Request):
    data = request.app.state.mail_service.metrics_snapshot()
    return success_response(request.state.trace_id, data)


@app.get("/api/v1/admin/metrics", dependencies=[Depends(require_internal_or_admin())])
def admin_metrics(request: Request):
    data = request.app.state.mail_service.metrics_snapshot()
    return success_response(request.state.trace_id, data)


app.include_router(accounts_router)
app.include_router(messages_router)
app.include_router(providers_router)
