"""FastAPI application factory and router wiring."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from agent.app.api.health import router as health_router
from agent.app.api.auth import router as auth_router, set_session_cookie
from agent.app.api.collector import router as collector_router
from agent.app.api.internal import router as internal_router
from agent.app.api.product import router as product_router
from agent.app.api.recommendations import router as recommendations_router
from agent.app.api.topology import router as topology_router
from agent.app.api.tasks import router as tasks_router
from agent.app.api.workos_webhooks import router as workos_webhooks_router
from agent.app.product.auth import SESSION_COOKIE
from agent.app.product.csrf import CSRF_HEADER
from agent.app.config import get_settings
from agent.app.logging_utils import configure_logging
from fastapi.middleware.cors import CORSMiddleware


OPENAPI_TAGS = [
    {"name": "Health", "description": "Operational liveness and readiness probes."},
    {"name": "Auth", "description": "WorkOS AuthKit browser authentication, CSRF, and logout routes."},
    {"name": "Product", "description": "Customer-facing frontend API for sessions, teams, clusters, knowledge, audit, and analysis history."},
    {"name": "Topology", "description": "Persisted topology graph API for dashboard rendering."},
    {"name": "Recommendations", "description": "Architecture analysis and recommendation pipeline API."},
    {"name": "Collector", "description": "Bearer-token authenticated collector registration and ingest API."},
    {"name": "Internal", "description": "Staff-only internal administration API."},
    {"name": "Webhooks", "description": "Signed third-party webhook ingress."},
    {"name": "Tasks", "description": "Private Cloud Tasks handlers."},
]


API_DESCRIPTION = """
ArchAgent API contract for browser frontend, collector, staff, webhook, and worker integrations.

Use `/openapi.json` as the machine-readable source of truth. Swagger UI is available at `/docs`
and ReDoc is available at `/redoc` when the FastAPI app is running.
"""


def _service_path_allowed(service_role: str, path: str) -> bool:
    if path in {"/healthz", "/readyz"}:
        return True
    if service_role == "api":
        return not path.startswith(("/collector/", "/internal/"))
    if service_role == "ops":
        return path.startswith("/internal/v1/")
    if service_role == "ingest":
        return path.startswith("/collector/v1/")
    if service_role == "worker":
        return path.startswith("/internal/tasks/")
    return False


def _install_openapi_contract(app: FastAPI) -> None:
    """Add security scheme metadata that custom auth dependencies cannot infer."""

    mutation_methods = {"post", "put", "patch", "delete"}
    operation_methods = {"get", "post", "put", "patch", "delete", "options", "head"}

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=OPENAPI_TAGS,
        )
        security_schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        security_schemes.update(
            {
                "WorkOSSessionCookie": {
                    "type": "apiKey",
                    "in": "cookie",
                    "name": SESSION_COOKIE,
                    "description": "Sealed WorkOS AuthKit session cookie used in hosted auth mode.",
                },
                "CSRFHeader": {
                    "type": "apiKey",
                    "in": "header",
                    "name": CSRF_HEADER,
                    "description": "Signed double-submit CSRF token required for unsafe browser requests in WorkOS auth mode.",
                },
                "DevelopmentIdentityHeader": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "x-archagent-user",
                    "description": "Local development/test identity selector. Disabled in production.",
                },
                "CollectorBearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Collector credential returned by /collector/v1/register or /collector/v1/credentials/rotate.",
                },
                "WorkOSWebhookSignature": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "workos-signature",
                    "description": "WorkOS webhook signature header validated with the configured webhook secret.",
                },
                "CloudTasksOIDC": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Cloud Run OIDC token presented by Google Cloud Tasks in production.",
                },
            }
        )
        for path, path_item in schema.get("paths", {}).items():
            for method, operation in path_item.items():
                if method not in operation_methods or not isinstance(operation, dict):
                    continue
                requires_csrf = method in mutation_methods
                browser_security = (
                    [{"WorkOSSessionCookie": [], "CSRFHeader": []}, {"DevelopmentIdentityHeader": []}]
                    if requires_csrf
                    else [{"WorkOSSessionCookie": []}, {"DevelopmentIdentityHeader": []}]
                )
                if path.startswith("/collector/v1/") and path != "/collector/v1/register":
                    operation.setdefault("security", [{"CollectorBearer": []}])
                elif path.startswith("/internal/tasks/"):
                    operation.setdefault("security", [{"CloudTasksOIDC": []}])
                elif path.startswith("/webhooks/workos/"):
                    operation.setdefault("security", [{"WorkOSWebhookSignature": []}])
                elif path.startswith("/internal/v1/") or path.startswith("/v1/"):
                    operation.setdefault("security", browser_security)
                elif path == "/auth/logout":
                    operation.setdefault("security", [{"WorkOSSessionCookie": [], "CSRFHeader": []}])
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi


def create_app() -> FastAPI:
    """Create and configure the ArchAgent HTTP app."""
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="ArchAgent",
        version="0.1.0",
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origin_list,
        allow_origin_regex=settings.allowed_origin_regex,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "x-archagent-user",
            "x-archagent-organization",
            "x-archagent-csrf",
            "ngrok-skip-browser-warning",
        ],
    )

    @app.middleware("http")
    async def enforce_production_identity_headers(request: Request, call_next):
        if settings.environment == "prod" and not _service_path_allowed(settings.service_role, request.url.path):
            return JSONResponse(status_code=404, content={"detail": "Not found."})
        if settings.environment == "prod" and (
            request.headers.get("x-archagent-user") or request.headers.get("x-archagent-organization")
        ):
            return JSONResponse(status_code=400, content={"detail": "Development identity headers are disabled in production."})
        response = await call_next(request)
        rotated_session = getattr(request.state, "archagent_rotated_session", None)
        if rotated_session:
            set_session_cookie(response, str(rotated_session), settings)
        return response

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(collector_router)
    app.include_router(product_router)
    app.include_router(internal_router)
    app.include_router(recommendations_router)
    app.include_router(topology_router)
    app.include_router(workos_webhooks_router)
    app.include_router(tasks_router)
    _install_openapi_contract(app)
    return app


app = create_app()
