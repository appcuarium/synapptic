"""FastAPI application factory."""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from synapptic.relay.config import SYNAPPTIC_DIR
from synapptic.relay.store import MetricsStore
from synapptic.relay.routers import anthropic, browser, dashboard, openai


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared resources on startup, clean up on shutdown."""
    SYNAPPTIC_DIR.mkdir(parents=True, exist_ok=True)

    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        follow_redirects=True,
    )
    app.state.metrics_store = MetricsStore()
    app.state.metrics_store.init_db()
    app.state.ended_sessions = app.state.metrics_store.get_ended_session_ids()  # persisted across restarts
    app.state.ws_clients = set()      # Connected browser WebSocket clients

    print(f"synapptic relay started — http://127.0.0.1:5100")
    print(f"dashboard — http://127.0.0.1:5100/dashboard/")
    print(f"sessions  — http://127.0.0.1:5100/browser/")

    yield

    await app.state.http_client.aclose()
    app.state.metrics_store.close()


def create_app() -> FastAPI:
    """Build and return the FastAPI app."""
    app = FastAPI(title="synapptic relay", version="0.1.0b4", lifespan=lifespan)

    app.include_router(anthropic.router, prefix="/v1")
    app.include_router(openai.router, prefix="/v1")
    app.include_router(dashboard.router, prefix="/dashboard")
    app.include_router(browser.router, prefix="/browser")

    return app
