"""Matter connection layer.

``_create_client`` is the only import seam for ``matter_server``: tests patch the
module-level name to hand back a fake client, so it must stay exactly that name.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from enum import Enum
from typing import Any, Callable

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from matter_server.client.exceptions import ServerVersionTooOld

_INIT_READY_TIMEOUT = 30  # seconds to wait for the real client to finish connecting
_MIN_RETRY_DELAY = 5
_MAX_RETRY_DELAY = 300


class MatterConnectionError(Exception):
    """Raised when the connection to the Matter server fails."""


class MatterServerTooOldError(MatterConnectionError):
    """The server's schema is below the client's floor."""


class MatterCommandError(Exception):
    """Raised when a Matter command reports a non-zero status."""


def _create_client(hass, url) -> Any:
    """Create a real MatterClient bound to HA's shared aiohttp session."""
    from matter_server.client import MatterClient

    return MatterClient(url, async_get_clientsession(hass))


class MatterConnection:
    """Thin wrapper around the Matter client with reconnect and event subscription."""

    def __init__(self, url: str, client_factory: Callable[[str], Any]) -> None:
        self._url = url
        self._client_factory = client_factory
        self._client: Any | None = None
        self._listen_task: asyncio.Task | None = None
        self._connected = False
        self._closed = False
        self._retry_delay = _MIN_RETRY_DELAY
        self.server_info: dict = {}
        # Subscription intents (normalized callbacks) live on the wrapper, not the
        # client, so a reconnect can re-attach them to the new client object.
        self._subscriptions: list[Callable] = []
        self._unsubscribers: dict[Callable, Callable] = {}

    def _is_client_connected(self) -> bool:
        """Consult the client itself; the fake exposes ``connected``, the real one nests it."""
        if self._client is None:
            return False
        if hasattr(self._client, "connected"):
            return bool(self._client.connected)
        connection = getattr(self._client, "connection", None)
        if connection is not None:
            return bool(getattr(connection, "connected", False))
        return self._connected

    async def _start_listening(self, client: Any) -> None:
        # The fake's start_listening returns immediately; the real one connects then
        # loops forever, so it must run as a task until it signals readiness.
        if hasattr(client, "get_server_info"):
            await client.start_listening()
            return
        init_ready = asyncio.Event()
        self._listen_task = asyncio.ensure_future(client.start_listening(init_ready))
        try:
            await asyncio.wait_for(init_ready.wait(), timeout=_INIT_READY_TIMEOUT)
        except asyncio.TimeoutError:
            pass  # single attempt; the listen loop keeps running in the background,
            # a dead connection surfaces on the next command

    async def _get_server_info(self, client: Any) -> dict:
        if hasattr(client, "get_server_info"):
            info = await client.get_server_info()
        else:
            info = client.server_info
        if info is None:
            return {}
        if dataclasses.is_dataclass(info):
            return dataclasses.asdict(info)
        return info

    async def async_connect(self) -> dict:
        """Connect, start listening and return the server-info dict."""
        try:
            client = self._client_factory(self._url)
            await client.connect()
            await self._start_listening(client)
            info = await self._get_server_info(client)
        except ServerVersionTooOld as err:
            # The client's own refusal: precise, unlike any schema heuristic.
            self._connected = False
            raise MatterServerTooOldError(str(err)) from err
        except Exception as err:  # noqa: BLE001 - any failure is a connection failure
            self._connected = False
            raise MatterConnectionError(str(err)) from err
        self._client = client
        self._connected = True
        self._closed = False
        self._retry_delay = _MIN_RETRY_DELAY
        self.server_info = info
        self._reattach_subscriptions()
        return info

    async def async_ensure_connected(self) -> None:
        """No-op when connected, otherwise reconnect immediately.

        A wrapper that was explicitly closed never revives on its own: a binding
        coordinator must not reopen a connection the hub has torn down.
        """
        if self._closed:
            raise MatterConnectionError("connection was closed")
        if self._is_client_connected():
            self._connected = True
            return
        self._connected = False
        try:
            await self.async_connect()
        except MatterConnectionError:
            self._retry_delay = min(self._retry_delay * 2, _MAX_RETRY_DELAY)
            raise

    async def async_close(self) -> None:
        if self._listen_task is not None:
            self._listen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listen_task
            self._listen_task = None
        if self._client is not None:
            if hasattr(self._client, "close"):
                await self._client.close()
            elif hasattr(self._client, "disconnect"):
                await self._client.disconnect()
        self._connected = False
        self._closed = True

    async def send_command(self, command: str, **kwargs: Any) -> Any:
        if self._client is None:
            raise MatterConnectionError("not connected")
        try:
            return await self._client.send_command(command=command, **kwargs)
        except MatterConnectionError:
            raise
        except Exception as err:  # noqa: BLE001 - any client error invalidates the connection
            self._connected = False
            raise MatterConnectionError(str(err)) from err

    async def read_attribute(self, node_id: int, attribute_path: str) -> Any | None:
        result = await self.send_command(
            "read_attribute", node_id=node_id, attribute_path=attribute_path
        )
        return (result or {}).get(attribute_path)

    async def write_attribute(self, node_id: int, attribute_path: str, value: Any) -> None:
        result = await self.send_command(
            "write_attribute", node_id=node_id, attribute_path=attribute_path, value=value
        )
        for entry in result or []:
            if entry.get("Status", 0) != 0:
                raise MatterCommandError(
                    f"write_attribute {attribute_path} failed: {entry.get('Status')}"
                )

    def _attach(self, normalized: Callable) -> None:
        """Attach one already-normalized callback to the current client, if any."""
        if self._client is None:
            return
        if hasattr(self._client, "subscribe_events"):
            self._unsubscribers[normalized] = self._client.subscribe_events(normalized)
        else:
            self._unsubscribers[normalized] = self._client.subscribe(normalized)

    def _reattach_subscriptions(self) -> None:
        """Re-point every recorded intent at the freshly created client."""
        self._unsubscribers.clear()
        for normalized in self._subscriptions:
            self._attach(normalized)

    def subscribe(self, callback: Callable[[str, Any], None]) -> Callable[[], None]:
        """Subscribe to server events; returns a plain unsubscribe callable.

        Safe before connect: the intent is recorded and attached once a client exists.
        """
        def _normalized(event_type: Any, data: Any) -> None:
            # Real event types are a non-str Enum and its payloads may be dataclasses.
            if isinstance(event_type, Enum):
                event_type = event_type.value
            if dataclasses.is_dataclass(data):
                data = dataclasses.asdict(data)
            callback(event_type, data)

        self._subscriptions.append(_normalized)
        self._attach(_normalized)

        def _unsubscribe() -> None:
            if _normalized not in self._subscriptions:
                return
            self._subscriptions.remove(_normalized)
            unsub = self._unsubscribers.pop(_normalized, None)
            if unsub is not None:
                unsub()

        return _unsubscribe
