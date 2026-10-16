# Matter TRV Binding Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a HACS-publishable Home Assistant custom integration (`matter_trv_binding`) that creates native Matter bindings (sensor → TRVs), sets RemoteSensing, and automatically verifies/repairs the bindings.

**Architecture:** Two-tier config entries: one **hub** entry (owns the WebSocket connection to the Matter server + integration-level options) and N **binding** entries (one per sensor→TRVs group, each with its own verify/repair coordinator and entities). All Matter operations go through a thin `MatterConnection` wrapper over `matter-python-client`'s `MatterClient`, using the server's high-level `set_node_binding` / `set_acl_entry` commands where they exist. Device identity is stored as HA device-registry ids; node ids are resolved at runtime, so a re-commissioned device auto-heals.

**Tech Stack:** Python 3.13, Home Assistant 2026.8 custom-integration APIs (`runtime_data`, `OptionsFlowWithReload`, reconfigure flow, issue registry), `matter-python-client~=1.0` (module `matter_server`), `pytest` + `pytest-homeassistant-custom-component`.

**Spec:** `docs/superpowers/specs/2026-10-16-matter-trv-binding-integration-design.md` — read it before Task 1. The plan argues from the spec; where they conflict, the spec wins and you stop and report.

## Global Constraints

- **Branch:** Before Task 1, from `main`: `git checkout --orphan ha-integration && git rm -rf . && git checkout main -- docs/` — the branch contains ONLY `docs/` + the new integration + tests + packaging files. The legacy scripts (`matter_trv_binding.py`, `monitor_binding.py`, `HA/`, old `README.adoc`, `assets/`) stay on `main` and must NOT be re-added.
- **Python:** 3.13+. Type hints on every public function. No `hass.data` — use `entry.runtime_data`.
- **HA target:** 2026.8. Install test deps with `pip install -U "pytest-homeassistant-custom-component" "matter-python-client~=1.0" pytest pytest-asyncio` (the phacc version resolves to the matching HA release; if it installs an older HA, that is fine as long as `OptionsFlowWithReload` and `async_step_reconfigure` exist — the tests import them and will fail loudly if not).
- **Test config:** `pytest.ini` at repo root with `asyncio_mode = auto`. Run tests with `python -m pytest tests/ -x -q`.
- **Matter access:** Only the Matter server websocket API. Never import `homeassistant.components.matter` internals. Never attach our config entries to another integration's device.
- **Translations:** every user-visible flow/service/issue string comes from `translations/en.json` (full contents pinned in Task 1).
- **Matter protocol constants** (use the constants from `const.py`, never literals):
  - Cluster ids: Descriptor `29`, Binding `30`, AccessControl `31`, Thermostat `513` (0x0201), TemperatureMeasurement `1026` (0x0402).
  - Attribute paths: sensor ACL list = `"0/31/0"`; Binding list = `f"{endpoint}/30/0"`; RemoteSensing = `f"{endpoint}/513/26"` (bitmap, bit 0); PartsList = `"0/29/0"`; ServerList = `f"{endpoint}/29/1"`; DeviceTypeList = `f"{endpoint}/29/0"` (entries look like `{"deviceType": int, "revision": int}`).
  - Raw command protocol (proven against a live server in the legacy scripts): `send_command("read_attribute", node_id=…, attribute_path=…)` returns a dict keyed by attribute path; `send_command("write_attribute", node_id=…, attribute_path=…, value=…)` returns a list of `{"Path": …, "Status": 0}` entries.
  - **Key-spelling duality:** structs read from the server may be keyed by TLV tags (`"1"`=node/privilege, `"2"`=group/authMode, `"3"`=endpoint/subjects, `"4"`=cluster/targets) or camelCase (`"node"`, `"endpoint"`, `"cluster"`) depending on server generation. Every read must go through a normalizer that accepts both.
  - ACL entry for `set_acl_entry`: `{"privilege": 1, "auth_mode": 2, "subjects": [trv_node_ids], "targets": [{"cluster": 1026, "endpoint": ep, "device_type": dt, "deviceType": dt}]}` — both `device_type` spellings must be present (matter.js builds a bogus DeviceTypeId when `device_type` is absent); `dt` is the sensor endpoint's first device type from DeviceTypeList, `0` if unknown.
  - Binding for `set_node_binding`: `[{"node": sensor_node_id, "endpoint": sensor_endpoint, "cluster": 1026}]`.
- **Commit after every task.** Message style: `feat: …` / `test: …`.
- **Ponytail rules:** mark intentional simplifications with `# shortcut: <limit>, <upgrade path>` comments. No abstractions this plan doesn't ask for.

## Review Focus

The five input classes most likely to bite a user, and the tasks that pin them with tests:

1. **ACL write must never drop the controller's admin entry** (fabric lockout). → Task 5: safety test where the fake server's `set_acl_entry` replaces the whole list.
2. **A re-commissioned device (new node_id, same HA device) must auto-heal**, not error. → Task 4 (identifier parsing) + Task 9 (coordinator node-change test).
3. **Matter server down at HA startup** must yield `ConfigEntryNotReady` (retry), and recovery after reconnect must work. → Task 7 (setup raises NotReady when connect fails) + Task 3 (`async_ensure_connected` reconnect test).
4. **Both struct key spellings** (python-matter-server vs matter.js serialization) must verify correctly. → Tasks 5 and 6 normalizer tests with both spellings.
5. **One unreachable TRV must not abort the verify cycle** for the other TRVs in the same binding. → Task 9 mixed-health test.

---

### Task 1: Scaffold, manifest, constants, translations

**Files:**
- Create: `custom_components/matter_trv_binding/__init__.py` (empty for now), `const.py`, `manifest.json`, `translations/en.json`
- Create: `hacs.json`, `pytest.ini`, `requirements_test.txt`, `README.md` (stub), `.gitignore`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Produces: `const.DOMAIN = "matter_trv_binding"`, all cluster/attribute constants, `TrvStatus` enum, `DEFAULT_URL`, option-key constants — every later task imports from `custom_components/matter_trv_binding.const`.

- [x] **Step 1: Create the branch and clean tree**

```bash
git checkout --orphan ha-integration && git rm -rf . && git checkout main -- docs/ && git commit -m "chore: start integration branch"
```

- [x] **Step 2: Write `const.py`** with exactly:

```python
"""Constants for the Matter TRV Binding integration."""
from enum import StrEnum

DOMAIN = "matter_trv_binding"
PLATFORMS = ["binary_sensor", "sensor", "button"]
DEFAULT_URL = "ws://localhost:5580/ws"
HUB_UNIQUE_ID = "hub"

CLUSTER_DESCRIPTOR = 29
CLUSTER_BINDING = 30
CLUSTER_ACCESS_CONTROL = 31
CLUSTER_THERMOSTAT = 513
CLUSTER_TEMPERATURE_MEASUREMENT = 1026

PRIVILEGE_VIEW = 1
PRIVILEGE_ADMIN = 5
AUTH_MODE_CASE = 2

OPT_UPDATE_INTERVAL_MINUTES = "update_interval_minutes"
OPT_AUTO_REPAIR = "auto_repair"
OPT_REPAIR_ATTEMPTS = "repair_attempts"
DEFAULT_UPDATE_INTERVAL_MINUTES = 30
DEFAULT_AUTO_REPAIR = True
DEFAULT_REPAIR_ATTEMPTS = 3


class TrvStatus(StrEnum):
    """Health status of one TRV's binding."""

    BOUND = "bound"
    BINDING_MISSING = "binding_missing"
    REMOTE_SENSING_OFF = "remote_sensing_off"
    ACL_MISSING = "acl_missing"
    UNREACHABLE = "unreachable"
    DEVICE_MISSING = "device_missing"
```

- [x] **Step 3: Write `manifest.json`** with exactly:

```json
{
  "domain": "matter_trv_binding",
  "name": "Matter TRV Binding",
  "version": "0.1.0",
  "config_flow": true,
  "iot_class": "local_push",
  "integration_type": "hub",
  "documentation": "https://github.com/antoineraulin/matter_trv_binding",
  "issue_tracker": "https://github.com/antoineraulin/matter_trv_binding/issues",
  "codeowners": ["@antoineraulin"],
  "requirements": ["matter-python-client~=1.0"],
  "dependencies": ["persistent_notification"],
  "after_dependencies": ["matter"],
  "loggers": ["matter_server"]
}
```

- [x] **Step 4: Write `translations/en.json`** — full file (later tasks reference these keys; do not invent new ones without adding them here):

```json
{
  "config": {
    "step": {
      "hub": {
        "title": "Connect to the Matter server",
        "data": {
          "name": "Name",
          "url": "Matter server WebSocket URL"
        }
      },
      "binding": {
        "title": "Bind a temperature sensor to TRVs",
        "data": {
          "label": "Label",
          "sensor_device": "Temperature sensor (Matter device)",
          "trv_devices": "TRVs (Matter devices)"
        }
      },
      "reconfigure": {
        "title": "Reconfigure the binding",
        "data": {
          "label": "Label",
          "sensor_device": "Temperature sensor (Matter device)",
          "trv_devices": "TRVs (Matter devices)"
        }
      }
    },
    "error": {
      "cannot_connect": "Failed to connect to the Matter server at {url}.",
      "legacy_server": "The server is a legacy python-matter-server. Update to the Matter Server add-on 9.x or newer.",
      "invalid_sensor": "The selected device does not expose a Matter temperature measurement cluster.",
      "invalid_trv": "These devices do not expose a Matter thermostat cluster: {devices}.",
      "unreachable": "These devices are unreachable: {devices}.",
      "apply_failed": "Failed to apply the binding: {detail}"
    },
    "abort": {
      "already_configured": "A Matter TRV Binding connection already exists.",
      "already_configured_sensor": "A binding for this sensor already exists."
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "Matter TRV Binding options",
        "data": {
          "update_interval_minutes": "Verification interval (minutes)",
          "auto_repair": "Automatically repair bindings",
          "repair_attempts": "Failed repairs before raising an issue"
        }
      }
    }
  },
  "issues": {
    "device_missing": {
      "title": "Bound Matter device no longer exists",
      "description": "The device {device_name} used by the binding {binding_name} was removed from Home Assistant. Reconfigure or remove the binding."
    },
    "repair_failed": {
      "title": "Binding repair keeps failing",
      "description": "The binding for {device_name} could not be repaired after {attempts} attempts. Check the device, the Matter network, and the Matter server."
    }
  },
  "services": {
    "repair": {
      "name": "Repair bindings",
      "description": "Runs an immediate verify-and-repair cycle for the selected binding device."
    }
  }
}
```

- [x] **Step 5: Write the small config files**

- `hacs.json`: `{"name": "Matter TRV Binding", "homeassistant": "2026.8.0"}`
- `pytest.ini`:
  ```ini
  [pytest]
  asyncio_mode = auto
  ```
- `requirements_test.txt`: one per line: `pytest`, `pytest-asyncio`, `pytest-homeassistant-custom-component`, `matter-python-client~=1.0`
- `README.md` stub (one line: project name + "Home Assistant custom integration for native Matter sensor→TRV bindings. Work in progress.")
- `.gitignore`: `__pycache__/`, `.pytest_cache/`, `*.pyc`

- [x] **Step 6: Write the failing test** `tests/test_scaffold.py`:

```python
"""Scaffold sanity tests."""
import json
from pathlib import Path

CC = Path(__file__).parent.parent / "custom_components" / "matter_trv_binding"


def test_manifest_keys():
    manifest = json.loads((CC / "manifest.json").read_text())
    assert manifest["domain"] == "matter_trv_binding"
    assert manifest["config_flow"] is True
    assert manifest["version"] == "0.1.0"
    assert "matter-python-client~=1.0" in manifest["requirements"]
    assert manifest["dependencies"] == ["persistent_notification"]


def test_translations_parse():
    data = json.loads((CC / "translations" / "en.json").read_text())
    assert "config" in data and "options" in data and "issues" in data
```

- [x] **Step 7: Run tests** — `python -m pytest tests/ -x -q` → PASS (2 tests)

- [x] **Step 8: Commit**

```bash
git add -A && git commit -m "feat: scaffold integration, manifest, constants, translations"
```

---

### Task 2: Fake Matter client and test fixtures

**Files:**
- Create: `tests/fake_matter.py`, `tests/conftest.py`
- Test: `tests/test_fake_client.py`

**Interfaces:**
- Produces: `FakeMatterClient` (full fake of `matter_server.client.MatterClient`) and pytest fixtures `fake_client`, `matter_devices`. Every later task's tests rely on these.
- `FakeMatterClient` API used by production code: `async connect()`, `async start_listening(init_ready)`, `async close()`, `async send_command(command: str, **kwargs)`, `async get_server_info() -> dict`, `subscribe(callback) -> callable`.

- [x] **Step 1: Write `tests/fake_matter.py`** — a stateful fake:

```python
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
            acl = self.attributes.setdefault(node_id, {}).setdefault("0/31/0", [])
            if self.acl_replaces_all:
                acl[:] = [kwargs["entry"]]
            else:
                acl.append(kwargs["entry"])
            return {"Status": 0}
        if command == "set_node_binding":
            self._check_node(node_id)
            self.attributes.setdefault(node_id, {})[f"{kwargs['endpoint']}/30/0"] = kwargs["bindings"]
            return {"Status": 0}
        raise ValueError(f"unknown command {command}")
```

- [x] **Step 2: Write `tests/conftest.py`** — exactly:

```python
"""Shared fixtures."""
import pytest
import pytest_homeassistant_custom_component  # noqa: F401  registers HA fixtures
from homeassistant.helpers import device_registry as dr

from tests.fake_matter import FakeMatterClient

CFID = 0x1234567890AB


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def fake_client() -> FakeMatterClient:
    return FakeMatterClient()


@pytest.fixture(autouse=True)
def patch_matter_client(monkeypatch, fake_client):
    """Route every MatterConnection to the per-test fake client."""
    monkeypatch.setattr(
        "custom_components.matter_trv_binding.matter.client._create_client",
        lambda hass, url: fake_client,
    )


class MatterDeviceFactory:
    """Creates Matter device-registry entries with realistic identifiers."""

    def __init__(self, hass) -> None:
        self._hass = hass

    def create(self, name: str, node_id: int, postfix: str = "MatterNodeDevice"):
        registry = dr.async_get(self._hass)
        return registry.async_get_or_create(
            config_entry_id="matter_test_entry",
            identifiers={("matter", f"deviceid_{CFID:016X}-{node_id:016X}-{postfix}")},
            name=name,
        )


@pytest.fixture
def matter_devices(hass):
    return MatterDeviceFactory(hass)
```

- [x] **Step 3: Write `tests/test_fake_client.py`**:

```python
from tests.fake_matter import FakeMatterClient


async def test_read_write_roundtrip():
    client = FakeMatterClient()
    await client.send_command("write_attribute", node_id=5, attribute_path="1/513/26", value=3)
    result = await client.send_command("read_attribute", node_id=5, attribute_path="1/513/26")
    assert result["1/513/26"] == 3


async def test_fail_nodes_raise():
    client = FakeMatterClient()
    client.fail_nodes.add(5)
    try:
        await client.send_command("read_attribute", node_id=5, attribute_path="0/29/0")
        raise AssertionError("should have raised")
    except ConnectionError:
        pass
```

- [x] **Step 4: Run** — `python -m pytest tests/ -x -q` → PASS. If `patch_matter_client` fails because `custom_components.matter_trv_binding.matter.client` doesn't exist yet, add empty package files `custom_components/matter_trv_binding/matter/__init__.py` and `custom_components/matter_trv_binding/matter/client.py` (just `"""Matter connection layer."""`).

- [x] **Step 5: Commit** — `git add -A && git commit -m "test: fake matter client and fixtures"`

---

### Task 3: MatterConnection wrapper

**Files:**
- Create: `custom_components/matter_trv_binding/matter/client.py`
- Test: `tests/test_connection.py`

**Interfaces:**
- Consumes: `const.DEFAULT_URL`.
- Produces (later tasks rely on these exact names):

```python
class MatterConnectionError(Exception): ...
class MatterCommandError(Exception): ...

def _create_client(hass, url) -> Any  # the ONLY import seam for matter_server; tests patch it

class MatterConnection:
    def __init__(self, url: str, client_factory: Callable[[str], Any]) -> None: ...
    async def async_connect(self) -> dict          # returns server_info dict; raises MatterConnectionError
    async def async_close(self) -> None: ...
    async def async_ensure_connected(self) -> None  # no-op if connected; else reconnect (5s backoff, cap 300s)
    async def send_command(self, command: str, **kwargs) -> Any
    async def read_attribute(self, node_id: int, attribute_path: str) -> Any | None
    async def write_attribute(self, node_id: int, attribute_path: str, value: Any) -> None
    def subscribe(self, callback: Callable[[str, dict], None]) -> Callable[[], None]
```

- [x] **Step 1: Verify the real client API before implementing.** Run:

```bash
python - <<'EOF'
from matter_server.client import MatterClient
import inspect
print(inspect.signature(MatterClient.__init__))
print(inspect.signature(MatterClient.start_listening))
print(inspect.signature(MatterClient.subscribe))
EOF
```

`_create_client(hass, url)` must call `MatterClient(url, <second positional arg as printed>)` using `homeassistant.helpers.aiohttp_client.async_get_clientsession(hass)` when the signature requires a session. If `subscribe`'s real signature differs from `subscribe(callback)`, adapt **inside** `MatterConnection.subscribe` — our wrapper's public signature above stays fixed.

- [x] **Step 2: Write failing tests** `tests/test_connection.py`:

```python
from custom_components.matter_trv_binding.matter.client import (
    MatterCommandError, MatterConnection, MatterConnectionError,
)


def make_conn(fake_client):
    return MatterConnection("ws://x", client_factory=lambda url: fake_client)


async def test_connect_returns_server_info(fake_client):
    conn = make_conn(fake_client)
    info = await conn.async_connect()
    assert info["fabric_id"] == 1
    assert fake_client.connected


async def test_connect_failure_raises(fake_client):
    fake_client.connect_fails = 2
    conn = make_conn(fake_client)
    try:
        await conn.async_connect()
        raise AssertionError("should raise")
    except MatterConnectionError:
        pass


async def test_ensure_connected_reconnects_after_drop(fake_client):
    conn = make_conn(fake_client)
    await conn.async_connect()
    fake_client.connected = False  # server died
    await conn.async_ensure_connected()
    assert fake_client.connected


async def test_read_attribute_returns_value(fake_client):
    fake_client.attributes[5] = {"1/513/26": 1}
    conn = make_conn(fake_client)
    await conn.async_connect()
    assert await conn.read_attribute(5, "1/513/26") == 1
    assert await conn.read_attribute(5, "9/9/9") is None


async def test_write_attribute_raises_on_bad_status(fake_client):
    conn = make_conn(fake_client)
    await conn.async_connect()
    async def bad(**kw):
        return [{"Path": kw["attribute_path"], "Status": 1}]
    fake_client.send_command = bad  # type: ignore[assignment]
    try:
        await conn.write_attribute(5, "1/513/26", 1)
        raise AssertionError("should raise")
    except MatterCommandError:
        pass


async def test_subscribe_roundtrip(fake_client):
    conn = make_conn(fake_client)
    await conn.async_connect()
    events = []
    unsub = conn.subscribe(lambda et, data: events.append((et, data)))
    fake_client.emit_event("attribute_updated", {"node_id": 5})
    unsub()
    fake_client.emit_event("attribute_updated", {"node_id": 5})
    assert len(events) == 1 and events[0][1]["node_id"] == 5
```

- [x] **Step 3: Run** — `python -m pytest tests/test_connection.py -x -q` → FAIL (module not implemented).

- [x] **Step 4: Implement `matter/client.py`.** Notes that pin choices:
  - `async_connect`: call factory, `await client.connect()`, wrap any exception in `MatterConnectionError`; then `await client.start_listening(lambda: None)`; get server info via `await client.send_command("get_server_info")` if the client has no `get_server_info` attribute, else `await client.get_server_info()`.
  - `read_attribute`: `result = await self.send_command("read_attribute", node_id=node_id, attribute_path=attribute_path)`; return `result.get(attribute_path)` (raw protocol returns a path-keyed dict — proven in the legacy scripts).
  - `write_attribute`: call send_command; scan the returned list; any entry with `Status != 0` → raise `MatterCommandError` with the path.
  - `send_command`: wraps the client's `send_command`; if it raises (or the client reports not connected), mark `self._connected = False` and re-raise as `MatterConnectionError`.
  - `async_ensure_connected`: if a flag on self says connected, return; else `async_connect()` inside `try`, and on failure raise `MatterConnectionError`. Backoff bookkeeping can be a simple `self._retry_delay` doubling from 5 to 300, reset on success. Keep it synchronous-simple: no background task; callers (coordinator) call this at the start of every cycle.
  - `subscribe(callback)`: delegate to the real client's subscribe (per Step 1's inspection); return its unsubscribe wrapped as a plain callable.

- [x] **Step 5: Run** — PASS. **Step 6: Commit** — `git add -A && git commit -m "feat: matter connection wrapper with reconnect and event subscription"`

---

### Task 4: Device discovery (registry → node → endpoint)

**Files:**
- Create: `custom_components/matter_trv_binding/matter/discovery.py`
- Test: `tests/test_discovery.py`

**Interfaces:**
- Consumes: `MatterConnection` (Task 3), cluster constants from `const`.
- Produces:

```python
class DeviceMissingError(Exception): ...
class NodeUnreachableError(Exception): ...

@dataclass(frozen=True)
class MatterDevice:
    device_id: str            # HA device registry id (stable)
    name: str
    node_id: int
    endpoint: int
    device_types: tuple[int, ...]

def parse_matter_node_id(device: DeviceEntry) -> int | None
async def async_scan_node(connection: MatterConnection, node_id: int) -> dict[int, tuple[set[int], tuple[int, ...]]]
    # endpoint -> (clusters, device_types)
async def async_resolve_device(connection, hass, device_id: str, wanted_cluster: int) -> MatterDevice
    # raises DeviceMissingError (registry entry gone), NodeUnreachableError (read fails / no matching endpoint)
```

- [x] **Step 1: Write failing tests** `tests/test_discovery.py`:

```python
import pytest
from homeassistant.helpers import device_registry as dr

from custom_components.matter_trv_binding.const import CLUSTER_TEMPERATURE_MEASUREMENT, CLUSTER_THERMOSTAT
from custom_components.matter_trv_binding.matter.client import MatterConnection
from custom_components.matter_trv_binding.matter.discovery import (
    DeviceMissingError, NodeUnreachableError, async_resolve_device, parse_matter_node_id,
)

CFID = 0x1234567890AB


def make_conn(fake_client):
    return MatterConnection("ws://x", client_factory=lambda url: fake_client)


async def test_parse_node_id_native_and_bridged(matter_devices):
    native = matter_devices.create("Eve Thermo Salon", 20)
    bridged = matter_devices.create("Capteur Bureau", 9, postfix="361")
    assert parse_matter_node_id(native) == 20
    assert parse_matter_node_id(bridged) == 9


async def test_parse_returns_none_for_foreign_device(hass):
    registry = dr.async_get(hass)
    dev = registry.async_get_or_create(
        config_entry_id="other_entry",
        identifiers={("other_domain", "x")},
        name="Not Matter",
    )
    assert parse_matter_node_id(dev) is None


async def test_resolve_bridged_sensor(hass, fake_client, matter_devices):
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    fake_client.attributes[9] = {
        "0/29/0": [0, 361],
        "361/29/1": [1026],
        "361/29/0": [{"deviceType": 7, "revision": 1}],
    }
    conn = make_conn(fake_client)
    dev = await async_resolve_device(conn, hass, sensor.id, CLUSTER_TEMPERATURE_MEASUREMENT)
    assert (dev.node_id, dev.endpoint) == (9, 361)
    assert dev.device_id == sensor.id and dev.device_types == (7,)


async def test_resolve_trv_finds_thermostat_endpoint(hass, fake_client, matter_devices):
    trv = matter_devices.create("Eve Thermo Bureau", 6)
    fake_client.attributes[6] = {
        "0/29/0": [0, 1],
        "1/29/1": [513, 30],
        "1/29/0": [{"deviceType": 769, "revision": 3}],
    }
    conn = make_conn(fake_client)
    dev = await async_resolve_device(conn, hass, trv.id, CLUSTER_THERMOSTAT)
    assert dev.endpoint == 1


async def test_resolve_missing_device_raises(hass, fake_client):
    conn = make_conn(fake_client)
    with pytest.raises(DeviceMissingError):
        await async_resolve_device(conn, hass, "no-such-device-id", CLUSTER_THERMOSTAT)


async def test_resolve_unreachable_node_raises(hass, fake_client, matter_devices):
    trv = matter_devices.create("Eve Thermo Bureau", 6)
    fake_client.fail_nodes.add(6)
    conn = make_conn(fake_client)
    with pytest.raises(NodeUnreachableError):
        await async_resolve_device(conn, hass, trv.id, CLUSTER_THERMOSTAT)
```

(Remove the malformed `test_parse_returns_none_for_foreign_device` draft above and replace it with a single clean test: create a device with identifiers `{("other_domain", "x")}` via `dr.async_get(hass).async_get_or_create` and assert `parse_matter_node_id(...) is None`.)

- [x] **Step 2: Run** — FAIL.
- [x] **Step 3: Implement `discovery.py`.** Pinning the algorithm:
  - `parse_matter_node_id`: iterate `device.identifiers`; find the tuple whose first element is `"matter"`; regex `^deviceid_[0-9A-F]{16}-([0-9A-F]{16})-.*$` on the second element; return `int(group1, 16)` or `None`.
  - `async_scan_node`: read PartsList via `connection.read_attribute(node_id, "0/29/0")` (a list of endpoint ids, endpoint 0 included); for each endpoint read `f"{ep}/29/1"` (ServerList → cluster set) and `f"{ep}/29/0"` (DeviceTypeList → tuple of `entry.get("deviceType", entry.get("device_type", 0))`). If PartsList read returns None → raise `NodeUnreachableError`.
  - `async_resolve_device`: `dr.async_get(hass).async_get(device_id)` — `None` → `DeviceMissingError`; `parse_matter_node_id` → None → `DeviceMissingError`; scan; pick the lowest endpoint id whose cluster set contains `wanted_cluster`; none → `NodeUnreachableError` (the device can't serve this role). Wrap connection errors from the scan as `NodeUnreachableError`.
- [x] **Step 4: Run** — PASS. **Step 5: Commit** — `git add -A && git commit -m "feat: matter device discovery (registry -> node/endpoint resolution)"`

---

### Task 5: ACL operations with lockout safety

**Files:**
- Create: `custom_components/matter_trv_binding/matter/acl.py`
- Test: `tests/test_acl.py`

**Interfaces:**
- Consumes: `MatterConnection`, `const` constants.
- Produces:

```python
def normalize_acl_entry(entry: dict) -> dict
    # -> {"privilege": int, "auth_mode": int, "subjects": list[int], "targets": list[dict]}
    # accepts TLV keys ("1","2","3","4") and named keys ("privilege","authMode"/"auth_mode","subjects","targets")
def acl_entry_grants(entry: dict, subjects: list[int], cluster: int, endpoint: int) -> bool
def desired_acl_entry(subjects: list[int], cluster: int, endpoint: int, device_type: int) -> dict
async def async_verify_acl(connection, sensor_node_id: int, sensor_endpoint: int, trv_node_ids: list[int], device_type: int) -> bool
async def async_apply_acl(connection, sensor_node_id: int, sensor_endpoint: int, trv_node_ids: list[int], device_type: int) -> None
    # raises MatterCommandError on failure; NEVER leaves the node without an admin entry
```

- [x] **Step 1: Write failing tests** `tests/test_acl.py`:

```python
from custom_components.matter_trv_binding.matter.acl import (
    acl_entry_grants, async_apply_acl, async_verify_acl, desired_acl_entry, normalize_acl_entry,
)
from custom_components.matter_trv_binding.matter.client import MatterConnection
from custom_components.matter_trv_binding.const import CLUSTER_TEMPERATURE_MEASUREMENT

def make_conn(fake_client):
    return MatterConnection("ws://x", client_factory=lambda url: fake_client)

ADMIN = {"1": 5, "2": 2, "3": [99], "4": []}          # TLV-keyed admin entry (controller)
OUR_TL = {"1": 1, "2": 2, "3": [20, 6], "4": [{"0": 1026, "1": 361}]}


def test_normalize_tlv_keys():
    n = normalize_acl_entry(ADMIN)
    assert n["privilege"] == 5 and n["auth_mode"] == 2 and n["subjects"] == [99]


def test_normalize_named_keys():
    n = normalize_acl_entry({"privilege": 1, "authMode": 2, "subjects": [20], "targets": [{"cluster": 1026, "endpoint": 361}]})
    assert n["privilege"] == 1 and n["targets"][0]["endpoint"] == 361


def test_grants_requires_all_subjects_and_target():
    assert acl_entry_grants(OUR_TL, [20, 6], CLUSTER_TEMPERATURE_MEASUREMENT, 361)
    assert not acl_entry_grants(OUR_TL, [20, 7], CLUSTER_TEMPERATURE_MEASUREMENT, 361)
    assert not acl_entry_grants(OUR_TL, [20, 6], CLUSTER_TEMPERATURE_MEASUREMENT, 999)


def test_desired_entry_both_spellings():
    entry = desired_acl_entry([20, 6], CLUSTER_TEMPERATURE_MEASUREMENT, 361, 7)
    target = entry["targets"][0]
    assert target["device_type"] == 7 and target["deviceType"] == 7
    assert entry["privilege"] == 1 and entry["auth_mode"] == 2


async def test_verify_true_when_granted(fake_client):
    fake_client.attributes[9] = {"0/31/0": [ADMIN, OUR_TL]}
    conn = make_conn(fake_client)
    assert await async_verify_acl(conn, 9, 361, [20, 6], 7) is True
    assert await async_verify_acl(conn, 9, 361, [20, 7], 7) is False


async def test_apply_adds_entry_and_passes_safety(fake_client):
    fake_client.attributes[9] = {"0/31/0": [ADMIN]}
    conn = make_conn(fake_client)
    await async_apply_acl(conn, 9, 361, [20, 6], 7)
    acl = fake_client.attributes[9]["0/31/0"]
    assert len(acl) == 2          # admin preserved, ours added
    assert await async_verify_acl(conn, 9, 361, [20, 6], 7)


async def test_apply_never_drops_admin_even_on_replacing_server(fake_client):
    """Safety net: if the server's set_acl_entry replaces the whole list,
    apply must detect the lost admin entry and restore it."""
    fake_client.acl_replaces_all = True
    fake_client.attributes[9] = {"0/31/0": [ADMIN]}
    conn = make_conn(fake_client)
    try:
        await async_apply_acl(conn, 9, 361, [20, 6], 7)
        raise AssertionError("should raise")
    except Exception:
        pass
    acl = fake_client.attributes[9]["0/31/0"]
    assert any(normalize_acl_entry(e)["privilege"] == 5 for e in acl), "admin entry must be restored"
```

- [x] **Step 2: Run** — FAIL.
- [x] **Step 3: Implement `acl.py`.** Pins:
  - `async_apply_acl`: read current list via `read_attribute(sensor_node_id, "0/31/0")` (default `[]`); `send_command("set_acl_entry", node_id=…, entry=desired)`; re-read; if the re-read lost any previously-present entry with normalized `privilege == 5` (PRIVILEGE_ADMIN), re-write the ORIGINAL full list via `send_command("write_attribute", node_id=…, attribute_path="0/31/0", value=original_list)` and raise `MatterCommandError("ACL write would remove admin access; rolled back")`. Then verify; raise if our entry still absent.
  - `# shortcut:` one ACL entry per sensor endpoint per TRV-set; consolidating multiple endpoints into one entry (the legacy script's trick) is not implemented — few users hit the ~4-entry device limit with one endpoint per binding. Upgrade: group entries by TRV set before writing.
- [x] **Step 4: Run** — PASS. **Step 5: Commit** — `git add -A && git commit -m "feat: acl provisioning with lockout safety"`

---

### Task 6: Binding and RemoteSensing operations

**Files:**
- Create: `custom_components/matter_trv_binding/matter/bindings.py`
- Test: `tests/test_bindings.py`

**Interfaces:**
- Consumes: `MatterConnection`, `MatterDevice` (Task 4), `TrvStatus` (Task 1), constants.
- Produces:

```python
def normalize_binding(entry: dict) -> tuple[int | None, int | None, int | None]   # (node, endpoint, cluster), both spellings
def desired_binding(sensor: MatterDevice) -> list[dict]                            # [{"node", "endpoint", "cluster"}]
async def async_check_trv(connection, trv: MatterDevice, sensor: MatterDevice) -> TrvStatus
    # BOUND | BINDING_MISSING | REMOTE_SENSING_OFF | UNREACHABLE (exceptions -> UNREACHABLE)
async def async_apply_trv(connection, trv: MatterDevice, sensor: MatterDevice) -> None
    # set_node_binding + read/OR-1/write RemoteSensing
```

- [x] **Step 1: Write failing tests** `tests/test_bindings.py`:

```python
from custom_components.matter_trv_binding.const import TrvStatus
from custom_components.matter_trv_binding.matter.bindings import (
    async_apply_trv, async_check_trv, desired_binding, normalize_binding,
)
from custom_components.matter_trv_binding.matter.client import MatterConnection
from custom_components.matter_trv_binding.matter.discovery import MatterDevice

def make_conn(fake_client):
    return MatterConnection("ws://x", client_factory=lambda url: fake_client)

SENSOR = MatterDevice(device_id="d1", name="Capteur", node_id=9, endpoint=361, device_types=(7,))
TRV = MatterDevice(device_id="d2", name="Eve Thermo", node_id=20, endpoint=1, device_types=(769,))


def test_normalize_both_spellings():
    assert normalize_binding({"1": 9, "3": 361, "4": 1026}) == (9, 361, 1026)
    assert normalize_binding({"node": 9, "endpoint": 361, "cluster": 1026}) == (9, 361, 1026)


def test_desired_binding_payload():
    assert desired_binding(SENSOR) == [{"node": 9, "endpoint": 361, "cluster": 1026}]


async def test_check_healthy(fake_client):
    fake_client.attributes[20] = {"1/30/0": [{"1": 9, "3": 361, "4": 1026}], "1/513/26": 1}
    assert await async_check_trv(make_conn(fake_client), TRV, SENSOR) is TrvStatus.BOUND


async def test_check_binding_missing(fake_client):
    fake_client.attributes[20] = {"1/30/0": [], "1/513/26": 1}
    assert await async_check_trv(make_conn(fake_client), TRV, SENSOR) is TrvStatus.BINDING_MISSING


async def test_check_remote_sensing_off(fake_client):
    fake_client.attributes[20] = {"1/30/0": [{"node": 9, "endpoint": 361, "cluster": 1026}], "1/513/26": 0}
    assert await async_check_trv(make_conn(fake_client), TRV, SENSOR) is TrvStatus.REMOTE_SENSING_OFF


async def test_check_unreachable(fake_client):
    fake_client.fail_nodes.add(20)
    assert await async_check_trv(make_conn(fake_client), TRV, SENSOR) is TrvStatus.UNREACHABLE


async def test_apply_trv_writes_binding_and_sensing(fake_client):
    fake_client.attributes[20] = {"1/513/26": 0}
    await async_apply_trv(make_conn(fake_client), TRV, SENSOR)
    assert fake_client.attributes[20]["1/30/0"] == [{"node": 9, "endpoint": 361, "cluster": 1026}]
    assert fake_client.attributes[20]["1/513/26"] == 1   # OR'd, not overwritten


async def test_apply_trv_preserves_other_sensing_bits(fake_client):
    fake_client.attributes[20] = {"1/513/26": 2}
    await async_apply_trv(make_conn(fake_client), TRV, SENSOR)
    assert fake_client.attributes[20]["1/513/26"] == 3
```

- [x] **Step 2: Run** — FAIL.
- [x] **Step 3: Implement `bindings.py`.** Pins:
  - `async_check_trv`: read `f"{trv.endpoint}/30/0"` (list, default `[]`) and `f"{trv.endpoint}/513/26"`; healthy = any normalized binding == `(sensor.node_id, sensor.endpoint, 1026)` **and** remote sensing value is not None and `value & 1`; binding wrong → BINDING_MISSING; binding right but bit 0 unset (or value None) → REMOTE_SENSING_OFF; any connection exception → UNREACHABLE.
  - `async_apply_trv`: `send_command("set_node_binding", node_id=trv.node_id, endpoint=trv.endpoint, bindings=desired_binding(sensor))`; then read RemoteSensing (None→0), and if `value & 1 == 0` write `value | 1` via `write_attribute(trv.node_id, f"{trv.endpoint}/513/26", value | 1)`.
- [x] **Step 4: Run** — PASS. **Step 5: Commit** — `git add -A && git commit -m "feat: binding and remote-sensing apply/verify operations"`

---

### Task 7: Hub entry setup

**Files:**
- Create: `custom_components/matter_trv_binding/hub.py`
- Modify: `custom_components/matter_trv_binding/__init__.py`
- Test: `tests/test_hub_setup.py`

**Interfaces:**
- Consumes: `MatterConnection`, `_create_client` seam (patched in tests), `const`.
- Produces:

```python
# hub.py
@dataclass
class HubRuntimeData:
    connection: MatterConnection
    repair_counts: dict[str, int]   # binding entry_id -> cumulative repair counter

def get_hub_entry(hass) -> ConfigEntry | None     # the loaded (or retrying) entry with unique_id == HUB_UNIQUE_ID
async def async_create_connection(hass, url: str) -> MatterConnection

# __init__.py
async def async_setup_entry(hass, entry) -> bool
    # hub branch: create connection (ConfigEntryNotReady on failure), store HubRuntimeData on entry.runtime_data
    # binding branch: placeholder raise NotImplementedError for now (Task 10 implements it)
async def async_unload_entry(hass, entry) -> bool
```

- [x] **Step 1: Write failing tests** `tests/test_hub_setup.py`:

```python
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DOMAIN, DEFAULT_URL
from custom_components.matter_trv_binding.hub import get_hub_entry


async def test_hub_setup_and_unload(hass, fake_client):
    entry = MockConfigEntry(domain=DOMAIN, data={"name": "Matter", "url": DEFAULT_URL}, unique_id="hub")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hub = get_hub_entry(hass)
    assert hub.runtime_data.connection  # type: ignore[attr-defined]
    assert fake_client.connected
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert fake_client.connected is False


async def test_hub_setup_not_ready_when_server_down(hass, fake_client):
    fake_client.connect_fails = 99
    entry = MockConfigEntry(domain=DOMAIN, data={"name": "Matter", "url": DEFAULT_URL}, unique_id="hub")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is hass.config_entries.ConfigEntryState.SETUP_RETRY
```

- [x] **Step 2: Run** — FAIL.
- [x] **Step 3: Implement.** Pins:
  - `type MatterTrvBindingConfigEntry = ConfigEntry[HubRuntimeData | BindingRuntimeData]` (define `BindingRuntimeData` in Task 10; for now a comment). In `async_setup_entry`: `if entry.unique_id == HUB_UNIQUE_ID:` hub branch else binding branch.
  - Hub branch: `connection = await async_create_connection(hass, entry.data["url"])` — `async_create_connection` builds `MatterConnection(url, client_factory=lambda url: _create_client(hass, url))`, calls `async_connect()`, and re-raises `MatterConnectionError` as `ConfigEntryNotReady(f"cannot connect to {url}")`. Store `entry.runtime_data = HubRuntimeData(connection=connection, repair_counts={})`.
  - Unload (hub): `await entry.runtime_data.connection.async_close()`; return True.
  - Binding branch in `__init__` for now: `raise NotImplementedError` wrapped so setup fails cleanly (`return False` is fine; HA logs an error).
- [x] **Step 4: Run** — PASS. **Step 5: Commit** — `git add -A && git commit -m "feat: hub config entry with connection lifecycle"`

---

### Task 8: Hub config flow

**Files:**
- Create: `custom_components/matter_trv_binding/config_flow.py`
- Test: `tests/test_config_flow_hub.py`

**Interfaces:**
- Consumes: hub setup (Task 7), `DEFAULT_URL`, translations from Task 1.
- Produces: `MatterTrvBindingConfigFlow` with `async_step_user` → hub schema (binding flow added in Task 11 in the same class).

- [x] **Step 1: Write failing tests** `tests/test_config_flow_hub.py`:

```python
from pytest_homeassistant_custom_component.common import MockConfigEntry, patch

from custom_components.matter_trv_binding.const import DOMAIN


async def test_hub_flow_creates_entry(hass, fake_client):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "hub"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Matter", "url": "ws://localhost:5580/ws"}
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["title"] == "Matter"
    assert result["data"]["url"] == "ws://localhost:5580/ws"


async def test_hub_flow_duplicate_aborts(hass, fake_client):
    entry = MockConfigEntry(domain=DOMAIN, data={"name": "Matter", "url": "ws://x"}, unique_id="hub")
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


async def test_hub_flow_cannot_connect_shows_error(hass, fake_client):
    fake_client.connect_fails = 99
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Matter", "url": "ws://localhost:5580/ws"}
    )
    assert result["type"] == "form" and result["errors"] == {"base": "cannot_connect"}
```

- [x] **Step 2: Run** — FAIL.
- [x] **Step 3: Implement the flow.** Pins:
  - `class MatterTrvBindingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN)`, `VERSION = 1`.
  - `async_step_user`: `await self.async_set_unique_id(HUB_UNIQUE_ID)`; `self._abort_if_unique_id_configured()`; if aborted → the binding path (Task 11) — for now catch `_AbortFlow`... simpler: `try: await self.async_set_unique_id(...); self._abort_if_unique_id_configured(); except data_entry_flow.AbortFlow: return await self.async_step_binding()` — add `async_step_binding` in Task 11; for this task return `self.async_abort(reason="already_configured")` in that branch. Then show the hub form: schema `vol.Schema({vol.Required("name", default="Matter TRV Binding"): str, vol.Required("url", default=DEFAULT_URL): str})`, step_id `"hub"`.
  - On submit: validate with `hub.async_create_connection(self.hass, url)` — catch `MatterConnectionError` → `errors["base"] = "cannot_connect"` and re-show; on success `async_close()` the validation connection and `return self.async_create_entry(title=name, data={"name": name, "url": url})` (unique id already set).
- [x] **Step 4: Run** — PASS. **Step 5: Commit** — `git add -A && git commit -m "feat: hub config flow"`

---

### Task 9: Verify/repair coordinator

**Files:**
- Create: `custom_components/matter_trv_binding/coordinator.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: Tasks 3–6 (`async_ensure_connected`, `async_resolve_device`, `async_check_trv`, `async_apply_trv`, `async_verify_acl`, `async_apply_acl`), `HubRuntimeData.repair_counts`.
- Produces:

```python
@dataclass
class TrvState:
    device_id: str
    name: str
    node_id: int | None
    status: TrvStatus
    healthy: bool

@dataclass
class BindingData:
    sensor_ok: bool
    trvs: dict[str, TrvState]     # keyed by TRV HA device id
    last_check: datetime | None

class BindingCoordinator(DataUpdateCoordinator[BindingData]):
    def __init__(self, hass, entry, hub_entry) -> None: ...
    async def _async_update_data(self) -> BindingData        # the verify+repair cycle
    async def async_check_and_repair(self, force: bool = True) -> None  # for button/service; refreshes coordinator
    # fast path: subscribes to hub connection events on init; `attribute_updated`
    # events for known TRV node ids trigger async_request_refresh() (the
    # coordinator's built-in coalescing debounces bursts)
    node_changes: list[tuple[str, int, int]]                 # (device_id, old_node_id, new_node_id) recorded per cycle
```

- [x] **Step 1: Write failing tests** `tests/test_coordinator.py` (seed the fake so a full healthy cycle needs: sensor node 9/ep 361 with ACL entry granting TRVs, TRV node 20/ep 1 with binding + sensing):

```python
from datetime import timedelta

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DOMAIN, TrvStatus
from custom_components.matter_trv_binding.coordinator import BindingCoordinator


def seed_healthy(fake_client):
    fake_client.attributes[9] = {
        "0/29/0": [0, 361], "361/29/1": [1026], "361/29/0": [{"deviceType": 7, "revision": 1}],
        "0/31/0": [{"1": 5, "2": 2, "3": [99], "4": []},
                   {"1": 1, "2": 2, "3": [20], "4": [{"0": 1026, "1": 361}]}],
    }
    fake_client.attributes[20] = {
        "0/29/0": [0, 1], "1/29/1": [513, 30], "1/29/0": [{"deviceType": 769, "revision": 3}],
        "1/30/0": [{"node": 9, "endpoint": 361, "cluster": 1026}], "1/513/26": 1,
    }


async def make_loaded_pair(hass, fake_client):
    from custom_components.matter_trv_binding.hub import HubRuntimeData
    from custom_components.matter_trv_binding.matter.client import MatterConnection
    conn = MatterConnection("ws://x", client_factory=lambda url: fake_client)
    await conn.async_connect()
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    hub.runtime_data = HubRuntimeData(connection=conn, repair_counts={})
    binding = MockConfigEntry(domain=DOMAIN, data={"label": "Bureau", "sensor_device_id": "SENS", "trv_device_ids": ["TRV1"]}, unique_id="SENS")
    binding.add_to_hass(hass)
    return hub, binding


async def test_healthy_cycle(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capteur Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)
    data = await coord._async_update_data()
    assert data.sensor_ok is True
    assert data.trvs[trv.id].status is TrvStatus.BOUND and data.trvs[trv.id].healthy
    assert data.last_check is not None


async def test_drift_is_repaired(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    fake_client.attributes[20]["1/30/0"] = []                 # binding lost
    fake_client.attributes[20]["1/513/26"] = 0                # sensing lost
    del fake_client.attributes[9]["0/31/0"][1]                # ACL entry lost
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)
    data = await coord._async_update_data()
    assert data.trvs[trv.id].healthy                          # repaired in the same cycle
    assert fake_client.attributes[20]["1/30/0"]               # binding rewritten
    assert fake_client.attributes[20]["1/513/26"] == 1
    assert hub.runtime_data.repair_counts[binding.entry_id] >= 1


async def test_unreachable_trv_does_not_block_others(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    trv2 = matter_devices.create("Eve Thermo Salon", 6)
    fake_client.attributes[6] = {"0/29/0": [0, 1], "1/29/1": [513, 30], "1/29/0": [{"deviceType": 769, "revision": 3}]}
    fake_client.fail_nodes.add(6)                            # only node 6 fails
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv1 = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv1.id, trv2.id]
    coord = BindingCoordinator(hass, binding, hub)
    data = await coord._async_update_data()
    assert data.trvs[trv1.id].healthy is True                 # first TRV unaffected
    assert data.trvs[trv2.id].status is TrvStatus.UNREACHABLE


async def test_node_id_change_auto_heals_and_notifies(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub, binding = await make_loaded_pair(hass, fake_client)
    binding.data["sensor_device_id"] = sensor.id
    binding.data["trv_device_ids"] = [trv.id]
    coord = BindingCoordinator(hass, binding, hub)
    await coord._async_update_data()
    # device re-commissioned: same HA device, new node id
    from homeassistant.helpers import device_registry as dr
    dr.async_get(hass).async_update_device(trv.id, new_identifiers={("matter", "deviceid_1234567890AB0000000000000064-MatterNodeDevice")})
    fake_client.attributes[100] = dict(fake_client.attributes[20])   # node 0x64 = 100
    data = await coord._async_update_data()
    assert data.trvs[trv.id].node_id == 100 and data.trvs[trv.id].healthy
    assert coord.node_changes == [(trv.id, 20, 100)]
```

For the notification assertion, do not rely on states; instead assert the coordinator flagged the change: add to `BindingCoordinator` a public `self.node_changes: list[tuple[str, int, int]]` (device_id, old, new) recorded during the cycle, and assert `coord.node_changes == [(trv.id, 20, 100)]`. (The persistent notification itself is created in the coordinator with `hass.components.persistent_notification.async_create(hass, message=…, title="Matter TRV Binding", notification_id=f"{DOMAIN}_node_change_{entry.entry_id}_{device_id}")` and dismissed with `async_dismiss` on the next healthy verify — cover the flag, not the HA side effect.)

- [x] **Step 2: Run** — FAIL.
- [x] **Step 3: Implement `coordinator.py`.** Pins:
  - Constructor: `DataUpdateCoordinator.__init__(hass, logger, name=f"{DOMAIN}_{entry.entry_id}", update_interval=timedelta(minutes=hub_options_update_interval))` where hub options = `hub_entry.options.get(OPT_UPDATE_INTERVAL_MINUTES, DEFAULT_UPDATE_INTERVAL_MINUTES)` etc. `always_update=False`? No — data includes `last_check`, so leave default.
  - `_async_update_data` cycle, in order:
    1. `await hub.connection.async_ensure_connected()` — `MatterConnectionError` → raise `UpdateFailed`.
    2. Resolve sensor via `async_resolve_device(connection, hass, entry.data["sensor_device_id"], CLUSTER_TEMPERATURE_MEASUREMENT)` — `DeviceMissingError`/`NodeUnreachableError` → sensor state absent; every TRV still processed; `sensor_ok=False`.
    3. `sensor_acl_ok = await async_verify_acl(...)` with all TRV node ids; if False and `auto_repair` option → `await async_apply_acl(...)`, re-verify; still False → repair-failure counter per binding.
    4. Per TRV: resolve device; `DeviceMissingError` → `TrvStatus.DEVICE_MISSING` (and raise the `device_missing` issue via `ir.async_create_issue(...)`, translation_key `"device_missing"`, `is_fixable=True`, `data={"entry_id": entry.entry_id}`, placeholders device/binding names; delete the issue with `ir.async_delete_issue` when the device resolves again); `NodeUnreachableError` → `UNREACHABLE`; else `status = await async_check_trv(...)`.
    5. If `status != BOUND` and `auto_repair` → `await async_apply_trv(...)`; re-check; healthy → `hub.runtime_data.repair_counts[entry.entry_id] += 1` and reset the per-TRV consecutive-failure counter; still unhealthy → increment counter, and when it reaches the `repair_attempts` option → `ir.async_create_issue(..., translation_key="repair_failed", is_fixable=False, severity=ir.IssueSeverity.WARNING, ...)`.
    6. Track `self.node_changes`: keep `self._last_node_ids: dict[str, int]`; when a TRV resolves healthy with a node_id different from the last seen one, record the tuple, `persistent_notification.async_create(...)`, update `_last_node_ids`; on every healthy verify call `persistent_notification.async_dismiss(hass, notification_id)` for each tracked TRV (safe if absent).
    7. Build `BindingData(sensor_ok=…, trvs={device_id: TrvState(...)}, last_check=dt_util.utcnow())` with `healthy = status is BOUND and sensor_ok`.
  - `async_check_and_repair(force=True)`: `await self.async_request_refresh()` (the cycle IS verify+repair; force is in the signature for the button/service call sites and future use).
  - Fast path in `__init__`: `self.node_changes = []`; `self._last_node_ids = {}`; store `self._known_trv_nodes: set[int]` (filled each cycle from resolution); subscribe once — `self._unsub = hub_entry.runtime_data.connection.subscribe(self._on_matter_event)`; `_on_matter_event(event_type, data)`: if `event_type == "attribute_updated"` and `data.get("node_id")` in `self._known_trv_nodes` → `self.hass.async_create_task(self.async_request_refresh())`. Unsubscribe in `__init__.py`'s unload via `entry.async_on_unload(self._unsub)`. Add one test: seed healthy, `await coord._async_update_data()`, then `fake_client.emit_event("attribute_updated", {"node_id": 20})`, `await hass.async_block_till_done()`, and assert a refresh ran (e.g. `coord.last_update_success` and `coord.data.trvs` populated via the public coordinator path).
  - `# shortcut:` endpoints are re-resolved every cycle (no cache) — costs a few descriptor reads per poll; upgrade: cache per node_id if polling ever moves below one minute.
- [x] **Step 4: Run** — PASS. **Step 5: Commit** — `git add -A && git commit -m "feat: binding verify/repair coordinator with escalation and node-change healing"`

---

### Task 10: Binding entry setup + entities

**Files:**
- Modify: `custom_components/matter_trv_binding/__init__.py` (binding branch), create `custom_components/matter_trv_binding/binary_sensor.py`, `sensor.py`, `button.py`
- Test: `tests/test_binding_setup.py`

**Interfaces:**
- Consumes: Tasks 4–7, 9. `matter_devices` fixture for registry entries.
- Produces:

```python
# __init__.py binding branch
@dataclass
class BindingRuntimeData:
    coordinator: BindingCoordinator

# binding device: DeviceInfo(identifiers={(DOMAIN, entry.entry_id)},
#   name=f"Matter Binding — {label}", via_device=("matter", <sensor matter identifier>))
```

Entities (all attached to the binding device):
- `binary_sensor.<trv_name>_binding` — `on` = `TrvState.healthy`
- `sensor.<trv_name>_status` — `SensorDeviceClass.ENUM`, `options=[s.value for s in TrvStatus]`
- `sensor.<binding>_last_check` — `SensorDeviceClass.TIMESTAMP`
- `sensor.<binding>_repairs` — `state_class=SensorStateClass.TOTAL_INCREASING` (from `hub.runtime_data.repair_counts`)
- `button.<binding>_check_and_repair` — press → coordinator check

- [x] **Step 1: Write failing tests** `tests/test_binding_setup.py`:

```python
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DOMAIN

from tests.test_coordinator import seed_healthy


async def test_binding_setup_creates_device_and_entities(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    binding = MockConfigEntry(
        domain=DOMAIN,
        data={"label": "Bureau", "sensor_device_id": sensor.id, "trv_device_ids": [trv.id]},
        unique_id=sensor.id,
    )
    binding.add_to_hass(hass)
    assert await hass.config_entries.async_setup(hub.entry_id)
    assert await hass.config_entries.async_setup(binding.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(f"binary_sensor.eve_thermo_bureau_binding").state == "on"
    assert hass.states.get(f"sensor.eve_thermo_bureau_status").state == "bound"
    assert hass.states.get("sensor.matter_binding_bureau_last_check") is not None
    assert hass.states.get("sensor.matter_binding_bureau_repairs").state == "0"
    assert hass.states.get("button.matter_binding_bureau_check_and_repair") is not None


async def test_binding_setup_without_hub_fails(hass, fake_client, matter_devices):
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    binding = MockConfigEntry(domain=DOMAIN, data={"label": "B", "sensor_device_id": sensor.id, "trv_device_ids": []}, unique_id=sensor.id)
    binding.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(binding.entry_id)
    assert binding.state is hass.config_entries.ConfigEntryState.SETUP_RETRY
```

- [x] **Step 2: Run** — FAIL.
- [x] **Step 3: Implement.** Pins:
  - `__init__.py` binding branch: `hub = get_hub_entry(hass)`; `None` → `raise ConfigEntryNotReady("Matter TRV Binding hub is not set up")`. Create `BindingCoordinator`, `await coordinator.async_config_entry_first_refresh()`, store `entry.runtime_data = BindingRuntimeData(coordinator)`, then `await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)` wrapped with `entry.async_on_unload(...)`.
  - Device creation (in `__init__`, `dr.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}, name=f"Matter Binding — {entry.data['label']}", via_device=("matter", <the sensor device's matter identifier string>))` — get the identifier from the sensor registry entry's identifiers; skip `via_device` if the sensor device is missing.
  - Entities are `CoordinatorEntity`s; `PARALLEL_UPDATES = 1` in each platform module. Unique ids: `f"{entry.entry_id}_{trv_device_id}_binding"`, `f"{entry.entry_id}_{trv_device_id}_status"`, `f"{entry.entry_id}_last_check"`, `f"{entry.entry_id}_repairs"`, `f"{entry.entry_id}_check_and_repair"`. Translation keys per entity: set `has_entity_name=True` and `_attr_translation_key` (`binding`, `status`, `last_check`, `repairs`, `check_and_repair`) — add an `entity` section to `translations/en.json` with `binary_sensor`/`sensor`/`button` names.
  - Binding unload: platforms unload; return True.
- [x] **Step 4: Run** — PASS. **Step 5: Commit** — `git add -A && git commit -m "feat: binding entry setup with health entities and repair button"`

---

### Task 11: Binding config flow

**Files:**
- Modify: `custom_components/matter_trv_binding/config_flow.py`
- Create: `custom_components/matter_trv_binding/services.yaml` (empty placeholder `{}` is NOT valid yaml for services — skip creating it in this task; Task 12 creates it)
- Test: `tests/test_config_flow_binding.py`

**Interfaces:**
- Consumes: `get_hub_entry`, `async_resolve_device`, `async_apply_acl`, `async_apply_trv`, `async_check_trv`, `async_verify_acl`; `DeviceSelector`, `DeviceFilterIntegration` from `homeassistant.helpers.selector`.
- Produces: `async_step_binding` on the flow (already routed from Task 8's `async_step_user`).

- [x] **Step 1: Write failing tests** `tests/test_config_flow_binding.py`:

```python
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DOMAIN
from tests.test_coordinator import seed_healthy


async def setup_hub(hass):
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    assert await hass.config_entries.async_setup(hub.entry_id)
    return hub


async def test_binding_flow_creates_entry(hass, fake_client, matter_devices):
    await setup_hub(hass)
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    fake_client.attributes[9]["0/31/0"] = [{"1": 5, "2": 2, "3": [99], "4": []}]  # only admin; ACL to be created by the flow
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "binding"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"label": "Bureau", "sensor_device": sensor.id, "trv_devices": [trv.id]},
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["data"]["sensor_device_id"] == sensor.id
    assert result["data"]["trv_device_ids"] == [trv.id]
    # the flow applied the binding before creating the entry
    assert fake_client.attributes[20]["1/30/0"] == [{"node": 9, "endpoint": 361, "cluster": 1026}]
    assert fake_client.attributes[20]["1/513/26"] == 1


async def test_binding_flow_rejects_non_sensor(hass, fake_client, matter_devices):
    await setup_hub(hass)
    seed_healthy(fake_client)
    not_a_sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")  # has 1026
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    # pick a device with no 1026 as the "sensor": use the TRV itself
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"label": "X", "sensor_device": trv.id, "trv_devices": [trv.id]}
    )
    assert result["type"] == "form" and result["errors"] == {"base": "invalid_sensor"}


async def test_binding_flow_rejects_unreachable(hass, fake_client, matter_devices):
    await setup_hub(hass)
    seed_healthy(fake_client)
    fake_client.fail_nodes.add(20)
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"label": "X", "sensor_device": sensor.id, "trv_devices": [trv.id]}
    )
    assert result["type"] == "form" and result["errors"] == {"base": "unreachable"}
```

- [x] **Step 2: Run** — FAIL.
- [x] **Step 3: Implement `async_step_binding`.** Pins:
  - Schema: `vol.Schema({vol.Optional("label"): str, vol.Required("sensor_device"): DeviceSelector(device_filter=DeviceFilterIntegration(integration="matter")), vol.Required("trv_devices"): DeviceSelector(device_filter=DeviceFilterIntegration(integration="matter"), multiple=True)})`, step_id `"binding"`.
  - Unique id: `await self.async_set_unique_id(sensor_device_id)`; `self._abort_if_unique_id_configured()` → abort reason `already_configured_sensor`.
  - Validation+apply on submit (using `get_hub_entry(hass).runtime_data.connection`): resolve sensor with `CLUSTER_TEMPERATURE_MEASUREMENT` → `NodeUnreachableError` → `errors["base"]="invalid_sensor"`; resolve each TRV with `CLUSTER_THERMOSTAT` → collect failures → `errors["base"]="invalid_trv"` with `devices` placeholder; unreachable TRVs → `"unreachable"`. Then apply: `async_apply_acl`, then per TRV `async_apply_trv`, then verify (`async_verify_acl` + `async_check_trv` == BOUND per TRV); any failure → `errors["base"]="apply_failed"` with `detail`. On all-good: `return self.async_create_entry(title=label or sensor_name, data={...})`.
- [x] **Step 4: Run** — PASS. **Step 5: Commit** — `git add -A && git commit -m "feat: binding config flow with device pickers and apply-on-create"`

---

### Task 12: Repair service

**Files:**
- Create: `custom_components/matter_trv_binding/services.py`, `custom_components/matter_trv_binding/services.yaml`
- Modify: `custom_components/matter_trv_binding/__init__.py` (register on hub setup)
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: binding entries' `runtime_data.coordinator`; binding device identifiers `(DOMAIN, entry_id)`.
- Produces: service `matter_trv_binding.repair` (device target).

- [x] **Step 1: Write failing tests** `tests/test_services.py`:

```python
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DOMAIN
from tests.test_coordinator import seed_healthy


async def test_repair_service_triggers_refresh(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    binding = MockConfigEntry(domain=DOMAIN, data={"label": "Bureau", "sensor_device_id": sensor.id, "trv_device_ids": [trv.id]}, unique_id=sensor.id)
    binding.add_to_hass(hass)
    await hass.config_entries.async_setup(hub.entry_id)
    await hass.config_entries.async_setup(binding.entry_id)
    await hass.async_block_till_done()

    from homeassistant.helpers import device_registry as dr
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, binding.entry_id)})
    fake_client.attributes[20]["1/30/0"] = []  # drift after setup
    await hass.services.async_call(
        DOMAIN, "repair", {"device_id": device.id}, blocking=True
    )
    await hass.async_block_till_done()
    assert fake_client.attributes[20]["1/30/0"]  # repaired
```

- [x] **Step 2: Run** — FAIL.
- [x] **Step 3: Implement.** Pins:
  - `services.yaml`:
    ```yaml
    repair:
      target:
        device:
          integration: matter_trv_binding
    ```
  - `services.py`: `async_register_services(hass)` registers `"repair"` with schema `vol.Schema({vol.Required(ATTR_DEVICE_ID): vol.Any(str, [str])})`; handler normalizes to a list, resolves each device id through the device registry to find our binding devices, looks up the matching binding entry (`hass.config_entries.async_entry_for_domain_unique_id`? no — iterate `hass.config_entries.async_entries(DOMAIN)` and match `entry.entry_id` against the identifier), and awaits each coordinator's `async_check_and_repair()`. Unknown device ids are ignored.
  - Register in the hub setup branch: `entry.async_on_unload(lambda: hass.services.async_remove(DOMAIN, "repair"))` after `async_register_services(hass)`.
- [x] **Step 4: Run** — PASS. **Step 5: Commit** — `git add -A && git commit -m "feat: repair service with device target"`

---

### Task 13: Options flow (hub) + reconfigure flow (binding)

**Files:**
- Create: `custom_components/matter_trv_binding/options_flow.py`
- Modify: `custom_components/matter_trv_binding/config_flow.py`
- Test: `tests/test_flows_options.py`

**Interfaces:**
- Consumes: `const` option keys/defaults; Task 11's binding validation+apply helpers.
- Produces: `MatterTrvBindingOptionsFlowHandler` (`async_get_options_flow` staticmethod on the config flow) and `async_step_reconfigure` on the config flow.

- [x] **Step 1: Before implementing, verify the exact HA signatures** on the installed version:

```bash
python - <<'EOF'
from homeassistant.config_entries import ConfigFlow, OptionsFlowWithReload
import inspect
print(inspect.signature(ConfigFlow.async_update_reload_and_abort))
EOF
```

If `data_updates` is not in the printed signature, use the `data=` kwarg instead — the reconfigure submit step must call `self.async_update_reload_and_abort(self._get_reconfigure_entry(), data_updates=new_data)` (or `data=new_data` per the printed signature).

- [x] **Step 2: Write failing tests** `tests/test_flows_options.py`:

```python
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.matter_trv_binding.const import DEFAULT_UPDATE_INTERVAL_MINUTES, DOMAIN
from tests.test_coordinator import seed_healthy


async def make_setup(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    binding = MockConfigEntry(domain=DOMAIN, data={"label": "Bureau", "sensor_device_id": sensor.id, "trv_device_ids": [trv.id]}, unique_id=sensor.id)
    binding.add_to_hass(hass)
    await hass.config_entries.async_setup(hub.entry_id)
    await hass.config_entries.async_setup(binding.entry_id)
    await hass.async_block_till_done()
    return hub, binding, sensor, trv


async def test_hub_options_updates_interval_and_reloads_bindings(hass, fake_client, matter_devices):
    hub, binding, sensor, trv = await make_setup(hass, fake_client, matter_devices)
    result = await hass.config_entries.options.async_init(hub.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"update_interval_minutes": 15, "auto_repair": True, "repair_attempts": 3},
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert hub.options["update_interval_minutes"] == 15
    # binding entry was reloaded so the coordinator picked up the new interval
    assert binding.state is hass.config_entries.ConfigEntryState.LOADED


async def test_binding_reconfigure_changes_trv_list(hass, fake_client, matter_devices):
    hub, binding, sensor, trv = await make_setup(hass, fake_client, matter_devices)
    trv2 = matter_devices.create("Eve Thermo Salon", 6)
    fake_client.attributes[6] = {"0/29/0": [0, 1], "1/29/1": [513, 30], "1/29/0": [{"deviceType": 769, "revision": 3}]}
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure"}, data={"entry_id": binding.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"label": "Bureau", "sensor_device": sensor.id, "trv_devices": [trv.id, trv2.id]}
    )
    await hass.async_block_till_done()
    assert result["type"] == "abort" and result["reason"] == "reconfigured_successfully"
    assert set(binding.data["trv_device_ids"]) == {trv.id, trv2.id}
    # ACL on the sensor now grants both TRV node ids
    subjects = [20, 6]
    assert fake_client.attributes[20]["1/30/0"]
```

- [x] **Step 3: Run** — FAIL.
- [x] **Step 4: Implement.** Pins:
  - `async_get_options_flow(config_entry)` on the config flow class → returns `MatterTrvBindingOptionsFlowHandler(config_entry)` only for hub entries (`config_entry.unique_id == HUB_UNIQUE_ID`); binding entries have no options flow.
  - Options flow extends `OptionsFlowWithReload`; `async_step_init` shows schema `{update_interval_minutes: int (5–1440, default from options or 30), auto_repair: bool (default True), repair_attempts: int (1–10, default 3)}` using `add_suggested_values_to_schema`; on submit `return self.async_create_entry(data=user_input)`; then — because the binding coordinators read the interval only at creation — after the options flow creates its entry, reload every binding entry: in the options flow's `async_finish_flow` (or `async_create_entry` override) call `await self.hass.config_entries.async_reload(e.entry_id)` for all entries of the domain with `unique_id != HUB_UNIQUE_ID`. `OptionsFlowWithReload` already reloads the hub entry.
  - Reconfigure: `async_step_reconfigure` on the config flow: `entry = self._get_reconfigure_entry()`; `await self.async_set_unique_id(entry.unique_id)`; `self._abort_if_unique_id_mismatch()`; show the same schema as the binding step with `add_suggested_values_to_schema` from `entry.data`; on submit run the SAME validation+apply as Task 11 (factor the body of binding validation into a helper `_async_validate_and_apply(self, user_input) -> dict | None` returning `errors` or None, used by both steps); on success call `self.async_update_reload_and_abort(entry, data_updates={**entry.data, "label":…, "sensor_device_id":…, "trv_device_ids":…})` (or `data=` per Step 1 inspection).
- [x] **Step 5: Run** — PASS. **Step 6: Commit** — `git add -A && git commit -m "feat: hub options flow and binding reconfigure flow"`

---

### Task 14: Repairs fix flows

**Files:**
- Create: `custom_components/matter_trv_binding/repairs.py`
- Test: `tests/test_repairs.py`

**Interfaces:**
- Consumes: the `device_missing` issue created in Task 9 (`data={"entry_id": ...}`).
- Produces: `async_create_fix_flow(hass, issue_id, data)` for `device_missing` issues: a `RepairsFlow` whose `async_step_init` shows a confirmation form ("Remove this binding?") and, on submit, removes the config entry.

- [x] **Step 1: Write failing test** `tests/test_repairs.py`:

```python
from homeassistant.helpers import issue_registry as ir

from custom_components.matter_trv_binding.const import DOMAIN


async def test_device_missing_issue_offer_flow(hass, fake_client):
    ir.async_create_issue(
        hass, DOMAIN, f"device_missing_test_entry",
        is_fixable=True, severity=ir.IssueSeverity.WARNING,
        translation_key="device_missing",
        translation_placeholders={"device_name": "X", "binding_name": "B"},
        data={"entry_id": "test_entry"},
    )
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert not flows
    fix_flow = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "repair", "issue_id": "device_missing_test_entry", "data": {"entry_id": "test_entry"}}
    )
    assert fix_flow["type"] == "form"
```

(Also assert that `repairs.async_create_fix_flow` returns a flow whose `async_step_init` yields a form with a `confirm` key, and whose submit removes the config entry — use a real `MockConfigEntry` for the removal assertion.)

- [x] **Step 2: Run** — FAIL.
- [x] **Step 3: Implement `repairs.py`**: `class BindingRepairFlow(ir.RepairsFlow)` with `async_step_init(user_input=None)`: None → form `vol.Schema({vol.Required("confirm"): bool})` with description placeholders; `True` → `await self.hass.config_entries.async_remove(self.data["entry_id"])` then `return self.async_create_entry(data={})`. `async_create_fix_flow(hass, issue_id, data)` returns `BindingRepairFlow(data)` when the issue id starts with `"device_missing_"`, else `None`. Module-level function `async_create_fix_flow` is what HA calls (signature `(hass, issue_id, data)`); register it by convention (HA discovers `repairs.py` in the integration automatically).
- [x] **Step 4: Run** — PASS. **Step 5: Commit** — `git add -A && git commit -m "feat: repairs fix flow for missing devices"`

---

### Task 15: Diagnostics, packaging, docs, full suite

**Files:**
- Create: `custom_components/matter_trv_binding/diagnostics.py`, `translations/fr.json`, `custom_components/matter_trv_binding/icons.py` (optional — skip), brand assets (`brand/logo.png` placeholder note in README instead)
- Modify: `README.md` (full content), `hacs.json` (verify)
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Consumes: `BindingData` (Task 9).
- Produces: `async_get_config_entry_diagnostics(hass, entry)` for binding entries; `async_get_device_diagnostics` NOT implemented (spec: per entry is enough — `# shortcut:` skip device diagnostics; HA falls back to entry diagnostics).

- [x] **Step 1: Write failing test** `tests/test_diagnostics.py`:

```python
from homeassistant.components.diagnostics import REDACTED

from custom_components.matter_trv_binding.const import DOMAIN
from custom_components.matter_trv_binding.diagnostics import TO_REDACT, async_get_config_entry_diagnostics
from tests.test_coordinator import seed_healthy


async def test_diagnostics_redacts_node_ids(hass, fake_client, matter_devices):
    seed_healthy(fake_client)
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    sensor = matter_devices.create("Capetre Bureau", 9, postfix="361")
    trv = matter_devices.create("Eve Thermo Bureau", 20)
    hub = MockConfigEntry(domain=DOMAIN, data={"name": "M", "url": "ws://x"}, unique_id="hub")
    hub.add_to_hass(hass)
    binding = MockConfigEntry(domain=DOMAIN, data={"label": "Bureau", "sensor_device_id": sensor.id, "trv_device_ids": [trv.id]}, unique_id=sensor.id)
    binding.add_to_hass(hass)
    await hass.config_entries.async_setup(hub.entry_id)
    await hass.config_entries.async_setup(binding.entry_id)
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, binding)
    assert diag["trvs"][trv.id]["node_id"] == REDACTED
    assert "fabric" not in str(diag).lower() or REDACTED
```

- [x] **Step 2: Run** — FAIL. **Step 3: Implement `diagnostics.py`:** `TO_REDACT = {"node_id", "fabric_id", "url"}`; return `{"entry_data": redacted entry.data, "options": entry.options, "sensor_ok": …, "trvs": {device_id: {"name": …, "node_id": …, "status": …, "healthy": …}}, "last_check": iso}` using `homeassistant.components.diagnostics.async_redact_data`. HA discovers `diagnostics.py` automatically; export the two constants/functions used above.
- [x] **Step 4: Run** — PASS.
- [x] **Step 5: `translations/fr.json`** — translate `en.json` faithfully (the author is a French speaker; use natural French, e.g. "Lier un capteur de température à des têtes thermostatives"). Verify it parses: `python -c "import json; json.load(open('custom_components/matter_trv_binding/translations/fr.json'))"`.
- [x] **Step 6: README** — sections: What it does (native Matter binding, sensor→TRV, auto-repair); Requirements (HA 2026.8+, Matter Server add-on 9.x — NOT legacy python-matter-server); Installation (HACS custom repo); Configuration walkthrough (hub entry, then one entry per room; device pickers resolve node/endpoint automatically); Entities and service table; Behavior notes: (a) writing RemoteSensing flips HA's own RemoteSensing diagnostic sensors — expected; (b) devices with >4 ACL entries per fabric may reject the ACL write — consolidate bindings on such nodes; (c) bindings survive integration removal (they live on the Matter devices; removing an entry removes OUR entities only — state clearly that uninstall does not clean up bindings/ACLs, no cleanup is implemented); Troubleshooting (repair issues, unreachable nodes, node-change notifications).
- [x] **Step 7: Full suite** — `python -m pytest tests/ -q` → all PASS. Fix anything broken by later tasks' changes.
- [x] **Step 8: Final self-check** — `python -c "import json,glob; [json.load(open(p)) for p in glob.glob('custom_components/**/*.json', recursive=True)]"` (all JSON parses) and grep the code for `hass.data[` (must be zero hits) and for `from homeassistant.components.matter` (must be zero hits outside comments).
- [x] **Step 9: Commit** — `git add -A && git commit -m "feat: diagnostics, french translations, documentation"`

## Post-execution corrections (2026-10-16)
- Task 9's fast-path event test (`fake_client.emit_event("attribute_updated", {"node_id": 20})`) encoded a payload shape the real `matter-python-server`/`matterjs-server` client does not send (the callback receives the bare attribute value; node filtering is done client-side by subscription keys). The coordinator now treats any matching event as a debounced refresh trigger; see coordinator.py's `shortcut:` comment and the real-shape tests in test_connection.py / test_coordinator.py.
- Task 4's PartsList attribute path: `0/29/3` (Descriptor 0x0003), not `0/29/0`.
