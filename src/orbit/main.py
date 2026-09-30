"""FastAPI application factory and process lifespan.

The app is built by `create_app()` so tests can construct isolated instances.
`app` is the module-level instance served in production (`orbit.main:app`,
see CLAUDE.md §4.3).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from orbit.api import health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage startup and shutdown.

    Resources (DB pool, Redis, telemetry) are wired here in later tickets.
    The context-manager shape guarantees ordered shutdown on SIGINT/SIGTERM.
    """
    # startup: nothing to initialise yet.
    yield
    # shutdown: nothing to tear down yet.


def create_app() -> FastAPI:
    """Build and configure the Orbit FastAPI application."""
    app = FastAPI(title="Orbit", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
