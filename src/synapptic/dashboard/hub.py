"""Connections and topic subscriptions of the dashboard websocket.

Every connection owns a bounded outgoing queue drained by one writer task, so frames are never sent
concurrently on a socket and one slow browser cannot hold up a publish to everyone else: when its queue is
full the connection is closed (the client reconnects and refetches).
"""

import asyncio

from fastapi import WebSocket

from synapptic.dashboard import protocol

SEND_QUEUE_SIZE = 256
CLOSE_TOO_SLOW = 1013  # "try again later": the client fell too far behind
CLOSE = object()       # queued in place of the backlog when a connection must be closed


class Connection:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=SEND_QUEUE_SIZE)
        self.topics: set[str] = set()
        self.closed = False

    def send(self, message: dict) -> None:
        """Queue a message for the writer task. A full queue drops the backlog and closes the connection."""
        if self.closed:
            return
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.closed = True
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(CLOSE)

    async def writer(self) -> None:
        """Send queued messages in order; on CLOSE, close the socket as too slow and stop."""
        while True:
            message = await self.queue.get()
            if message is CLOSE:
                await self.websocket.close(code=CLOSE_TOO_SLOW)
                return
            await self.websocket.send_json(message)


class Hub:
    def __init__(self):
        self.connections: set[Connection] = set()

    def add(self, connection: Connection) -> None:
        self.connections.add(connection)

    def remove(self, connection: Connection) -> None:
        connection.closed = True
        self.connections.discard(connection)

    def publish(self, topic: str, data) -> int:
        """Push an event to every connection subscribed to the topic. Returns how many received it."""
        delivered = 0
        for connection in list(self.connections):
            if topic in connection.topics:
                connection.send(protocol.event(topic, data))
                delivered += 1
        return delivered
