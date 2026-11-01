"""The dashboard's FastAPI app: the websocket at /api/ws, GET /api/health, POST /api/session-ended (the
SessionEnd hook) and the built React app at /.

Routes are module-level functions that read their settings from app.state (set by create_app).
"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synapptic.dashboard import protocol
from synapptic.dashboard.handlers import METHODS, MethodError, record_session_ended
from synapptic.dashboard.hub import Connection, Hub

SERVER_VERSION = "0.1.0b6"
STATIC_DIR = Path(__file__).parent / "static"   # Vite builds ui/ here
CLOSE_POLICY_VIOLATION = 1008
VITE_DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def origin_allowed(origin: str | None, host: str | None, extra_origins: tuple[str, ...]) -> bool:
    """A browser may connect only from the dashboard's own origin (or a listed dev origin).

    Browsers always send Origin on a websocket handshake; a missing Origin is a non-browser client.
    Checking it stops another website from opening a socket to the local dashboard.
    """
    if origin is None:
        return True
    return origin in extra_origins or (host is not None and origin in (f"http://{host}", f"https://{host}"))


async def handle_request(connection: Connection, hub: Hub, message: protocol.RequestMessage) -> None:
    method = METHODS.get(message.method)
    if method is None:
        connection.send(protocol.error(message.id, protocol.ErrorCode.UNKNOWN_METHOD, f"unknown method: {message.method}"))
        return
    try:
        result = await method(message.params, hub)
    except MethodError as exc:
        connection.send(protocol.error(message.id, exc.code, exc.message))
        return
    except Exception:  # noqa: BLE001 - a failing method must answer, not kill the socket
        connection.send(protocol.error(message.id, protocol.ErrorCode.INTERNAL, f"{message.method} failed"))
        return
    connection.send(protocol.response(message.id, result))


def handle_frame(connection: Connection, hub: Hub, text: str, tasks: set) -> None:
    """Dispatch one incoming frame. Requests run as tasks so a slow method does not block the socket."""
    try:
        message = protocol.CLIENT_MESSAGE.validate_python(json.loads(text))
    except (json.JSONDecodeError, ValidationError):
        connection.send(protocol.error(None, protocol.ErrorCode.BAD_MESSAGE, "not a valid protocol message"))
        return
    if isinstance(message, protocol.RequestMessage):
        task = asyncio.create_task(handle_request(connection, hub, message))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    elif isinstance(message, protocol.SubscribeMessage):
        connection.topics.add(message.topic)
    elif isinstance(message, protocol.UnsubscribeMessage):
        connection.topics.discard(message.topic)
    else:
        connection.send(protocol.pong())


async def websocket_endpoint(websocket: WebSocket) -> None:
    """One browser connection: origin check, hello, then frames until it disconnects."""
    state = websocket.app.state
    if not origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host"), state.extra_origins):
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return
    await websocket.accept()
    hub: Hub = state.hub
    connection = Connection(websocket)
    hub.add(connection)
    writer = asyncio.create_task(connection.writer())
    tasks: set = set()
    connection.send(protocol.hello(SERVER_VERSION))
    try:
        while True:
            handle_frame(connection, hub, await websocket.receive_text(), tasks)
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(connection)
        writer.cancel()
        for task in tasks:
            task.cancel()


class SessionEnded(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "protocol": protocol.PROTOCOL_VERSION})


async def session_ended(payload: SessionEnded) -> JSONResponse:
    """The SessionEnd hook reports a closed session, so the sessions page stops showing it live at once.

    A JSON body is required: a cross-site form post cannot send one without a preflight this app never allows.
    """
    await asyncio.to_thread(record_session_ended, payload.session_id)
    return JSONResponse({"ok": True})


async def spa(request: Request, path: str) -> FileResponse:
    """A file from the build as it is; any other path is a client-side route and gets index.html."""
    static_dir: Path = request.app.state.static_dir
    candidate = (static_dir / path).resolve()
    if path and candidate.is_file() and candidate.is_relative_to(static_dir.resolve()):
        return FileResponse(candidate)
    return FileResponse(static_dir / "index.html")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """The websocket hub, shared by every connection."""
    app.state.hub = Hub()
    yield


def create_app(static_dir: Path = STATIC_DIR, extra_origins: tuple[str, ...] = VITE_DEV_ORIGINS) -> FastAPI:
    """Build the dashboard app."""
    app = FastAPI(title="synapptic dashboard", version=SERVER_VERSION, lifespan=lifespan)
    app.state.static_dir = static_dir
    app.state.extra_origins = extra_origins

    app.add_api_route("/api/health", health, methods=["GET"])
    app.add_api_route("/api/session-ended", session_ended, methods=["POST"])
    app.add_api_websocket_route("/api/ws", websocket_endpoint)

    if (static_dir / "index.html").exists():
        if (static_dir / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
        app.add_api_route("/{path:path}", spa, methods=["GET"])

    return app
