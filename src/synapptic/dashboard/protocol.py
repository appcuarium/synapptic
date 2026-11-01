"""Wire protocol of the dashboard websocket at /api/ws. JSON text frames, one message per frame.

Client -> server
  {"type": "request", "id": "<client id>", "method": "config.get", "params": {}}
  {"type": "subscribe", "topic": "config"}
  {"type": "unsubscribe", "topic": "config"}
  {"type": "ping"}

Server -> client
  {"type": "hello", "protocol": 1, "server": "<version>"}          first frame after the socket is accepted
  {"type": "response", "id": "<request id>", "result": {...}}
  {"type": "error", "id": "<request id or null>", "code": "<ErrorCode>", "message": "..."}
  {"type": "event", "topic": "config", "data": {...}}               pushed to every subscriber of the topic
  {"type": "pong"}

The browser client (ui/src/lib/ws/protocol.ts) mirrors these shapes; bump PROTOCOL_VERSION on any breaking change.
"""

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION = 1


class ErrorCode:
    BAD_MESSAGE = "bad_message"          # not JSON, or not a known message shape
    UNKNOWN_METHOD = "unknown_method"
    INVALID_PARAMS = "invalid_params"
    INTERNAL = "internal"


class RequestMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["request"]
    id: str = Field(min_length=1, max_length=64)
    method: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class SubscribeMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["subscribe"]
    topic: str = Field(min_length=1, max_length=64)


class UnsubscribeMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["unsubscribe"]
    topic: str = Field(min_length=1, max_length=64)


class PingMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ping"]


ClientMessage = Annotated[
    Union[RequestMessage, SubscribeMessage, UnsubscribeMessage, PingMessage],
    Field(discriminator="type"),
]
CLIENT_MESSAGE = TypeAdapter(ClientMessage)


def hello(server_version: str) -> dict:
    return {"type": "hello", "protocol": PROTOCOL_VERSION, "server": server_version}


def response(request_id: str, result: Any) -> dict:
    return {"type": "response", "id": request_id, "result": result}


def error(request_id: str | None, code: str, message: str) -> dict:
    return {"type": "error", "id": request_id, "code": code, "message": message}


def event(topic: str, data: Any) -> dict:
    return {"type": "event", "topic": topic, "data": data}


def pong() -> dict:
    return {"type": "pong"}
