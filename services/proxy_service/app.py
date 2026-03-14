from __future__ import annotations

from fastapi import Depends, FastAPI, Request

from libs.contracts.common import HealthData
from libs.core.cors import configure_cors
from libs.core.auth import attach_request_context, require_internal_or_admin
from libs.core.exceptions import ServiceError
from libs.core.responses import error_response, success_response
from libs.core.tracing import generate_trace_id
from services.proxy_service.routes.leases import router as leases_router
from services.proxy_service.routes.pools import router as pools_router
from services.proxy_service.service import ProxyService


app = FastAPI(title="proxy-service")
app.state.service_name = "proxy-service"
configure_cors(app)
app.state.proxy_service = ProxyService()


@app.middleware("http")
async def attach_trace_id(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or generate_trace_id("trc_proxy")
    try:
        attach_request_context(request, trace_id)
        return await call_next(request)
    except ServiceError as exc:
        return error_response(trace_id, exc)


@app.get("/api/v1/health")
def health(request: Request):
    return success_response(request.state.trace_id, HealthData(service="proxy-service").model_dump(mode="json"))


@app.get("/api/v1/health/details", dependencies=[Depends(require_internal_or_admin())])
def health_details(request: Request):
    data = request.app.state.proxy_service.metrics_snapshot()
    return success_response(request.state.trace_id, data)


@app.get("/api/v1/admin/metrics", dependencies=[Depends(require_internal_or_admin())])
def admin_metrics(request: Request):
    data = request.app.state.proxy_service.metrics_snapshot()
    return success_response(request.state.trace_id, data)


app.include_router(leases_router)
app.include_router(pools_router)
