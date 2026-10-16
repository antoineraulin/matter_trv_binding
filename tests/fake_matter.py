"""Fake MatterClient for tests. Mirrors only the command protocol the integration uses."""
from typing import Any, Callable


class FakeMatterClient:
    def __init__(self) -> None:
        self.attributes: dict[int, dict[str, Any]] = {}  # node_id -> "ep/cluster/attr" -> value
        self.connected = False
        self.commands: list[tuple[str, dict]] = []
        self.event_subscribers: list[Callable] = []
        self.fail_nodes: set[int] = set()          # reads/writes on these raise
        self.acl_replaces_all = False             # safety-test mode: set_acl_entry wipes the list
        self.connect_fails = 0                    # >0: connect() raises this many times

    async def connect(self) -> None:
        if self.connect_fails:
            self.connect_fails -= 1
            raise OSError("connection refused")
        self.connected = True

    async def start_listening(self, init_ready: Callable | None = None) -> None:
        if init_ready:
            init_ready()

    async def close(self) -> None:
        self.connected = False

    async def get_server_info(self) -> dict:
        return {"fabric_id": 1, "schema_version": 19, "sdk_version": "fake-1.4.0"}

    def subscribe(self, callback: Callable) -> Callable:
        self.event_subscribers.append(callback)
        return lambda: self.event_subscribers.remove(callback)

    def emit_event(self, event_type: str, data: dict) -> None:
        for cb in list(self.event_subscribers):
            cb(event_type, data)

    def _check_node(self, node_id: int) -> None:
        if node_id in self.fail_nodes:
            raise ConnectionError(f"node {node_id} unreachable")

    async def send_command(self, command: str, **kwargs: Any) -> Any:
        self.commands.append((command, kwargs))
        node_id: int = kwargs["node_id"]
        if command == "read_attribute":
            self._check_node(node_id)
            return {kwargs["attribute_path"]: self.attributes.get(node_id, {}).get(kwargs["attribute_path"])}
        if command == "write_attribute":
            self._check_node(node_id)
            self.attributes.setdefault(node_id, {})[kwargs["attribute_path"]] = kwargs["value"]
            return [{"Path": kwargs["attribute_path"], "Status": 0}]
        if command == "set_acl_entry":
            self._check_node(node_id)
            # Real contract: `entry` is an ARRAY of entries (server calls entries.map).
            entry = kwargs["entry"]
            if not isinstance(entry, list):
                raise TypeError("entries.map is not a function")
            acl = self.attributes.setdefault(node_id, {}).setdefault("0/31/0", [])
            if self.acl_replaces_all:
                acl[:] = list(entry)
            else:
                acl.extend(entry)
            return {"Status": 0}
        if command == "set_node_binding":
            self._check_node(node_id)
            # Real contract: every binding must carry node/group/endpoint/cluster;
            # a missing key reaches GroupId(undefined) and throws.
            for binding in kwargs["bindings"]:
                if not {"node", "group", "endpoint", "cluster"}.issubset(binding):
                    raise TypeError("GroupId(undefined)")
            self.attributes.setdefault(node_id, {})[f"{kwargs['endpoint']}/30/0"] = kwargs["bindings"]
            return {"Status": 0}
        raise ValueError(f"unknown command {command}")
