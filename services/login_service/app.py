from __future__ import annotations

from fastapi import FastAPI, Request

from libs.contracts.common import HealthData
from libs.core.cors import configure_cors
from libs.core.auth import attach_request_context
from libs.core.exceptions import ServiceError
from libs.core.responses import error_response, success_response
from libs.core.tracing import generate_trace_id
from services.login_service.routes.login import router as login_router
from services.login_service.routes.verify import router as verify_router
from services.login_service.service import LoginService


app = FastAPI(title="login-service")
app.state.service_name = "login-service"
configure_cors(app)
app.state.login_service = LoginService()


@app.middleware("http")
async def attach_trace_id(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or generate_trace_id("trc_login")
    try:
        attach_request_context(request, trace_id)
        return await call_next(request)
    except ServiceError as exc:
        return error_response(trace_id, exc)


@app.get("/api/v1/health")
def health(request: Request):
    return success_response(request.state.trace_id, HealthData(service="login-service").model_dump(mode="json"))


app.include_router(login_router)
app.include_router(verify_router)
