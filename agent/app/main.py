"""FastAPI application factory and router wiring."""

from __future__ import annotations

from fastapi import FastAPI

from agent.app.api.health import router as health_router
from agent.app.api.recommendations import router as recommendations_router
from agent.app.api.topology import router as topology_router
from agent.app.config import get_settings
from agent.app.logging_utils import configure_logging


def create_app() -> FastAPI:
    """Create and configure the ArchAgent HTTP app."""
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="ArchAgent",
        version="0.1.0",
        description="Structured signals + topology -> smells, recommendations, critiques, plan, explanation.",
    )
    app.include_router(health_router)
    app.include_router(recommendations_router)
    app.include_router(topology_router)
    return app


app = create_app()

