# Matter TRV Binding — Home Assistant Integration Design

Date: 2026-10-16
Status: Approved in brainstorming (sections 1–4) — pending spec review

## Purpose

Replace the standalone `matter_trv_binding.py` / `monitor_binding.py` scripts
(raw websocket + shell_command + file sensors) with a proper Home Assistant
custom integration, published on HACS, that:

- Establishes **native Matter bindings** between Matter-exposed temperature
  sensors and Matter TRVs (Eve Thermo, native Matter and
  HomeKit-upgraded-to-Matter generations), so TRVs regulate from the external
  sensor instead of their internal thermistor.
- Configures the required **Access Control List** entries on the sensor node and
  the **RemoteSensing** bit (Thermostat cluster 0x0201, attribute 0x001A) on
  each TRV.
- **Automatically verifies and repairs** bindings that drift (known Eve Thermo
  behavior: bindings/remote-sensing silently lost after matter network issues
  or sensor unavailability).
- Exposes health state as native HA entities with a full config flow,
  options flow, reconfigure flow, diagnostics, and repair issues.

Only the Matter server's own API is used — no HA matter-integration internals,
no shell commands, no file sensors.

## Non-goals

- Generic binding management (arbitrary cluster-to-cluster bindings). That is
  `cedricziel/ha-matter-binding-helper`'s territory; we are a focused,
  opinionated sensor→TRV integration.
- Group bindings / group ACLs. Per-TRV bindings only (what the original setup
  uses). The apply layer is per-TRV so group mode remains additive later.
- Support for non-TRV thermostats (may work; not guaranteed or tested).
- Custom Lovelace cards. Standard entities work with native dashboards.

## Matter server landscape (constraint)

`python-matter-server` is end-of-life (8.1.2, 2025-12-15). The HA add-on
`matter_server` 9.2.0 ships the rewritten **matterjs-server** (matter server
1.4.0); the maintained Python client is **`matter-python-client`** (module
`matter_server`, drop-in). Both server generations implement the high-level
commands we rely on. The integration targets the matterjs-server generation
(9.x add-on); legacy 8.x is not supported.

Key API facts used by this design:

- Connect: `ws://<host>:5580/ws` (or unix socket). Line-JSON protocol
  `{"message_id","command","args"}`; server pushes `server_info` on connect
  (`fabric_id`, `schema_version`, …). Multiple WS clients are allowed.
- `set_node_binding` — `{node_id, endpoint, bindings:[{node,group,endpoint,cluster}]}`
  writes the Binding attribute of an endpoint. **Preferred over raw
  `write_attribute`** (avoids fabricIndex handling and the camelCase-vs-TLV-tag
  key drift between server generations).
- `set_acl_entry` — `{node_id, entry}` sets the controller-fabric ACL entry
  (privilege VIEW=1, auth_mode CASE=2, subjects=TRV node ids,
  targets=[{cluster,endpoint,device_type}]). Server picks the fabric index.
  Note the matter.js quirk: ACL targets must carry both key spellings
  (`device_type` and the legacy camelCase) because matter.js builds a bogus
  `DeviceTypeId` when `device_type` is absent (pattern borrowed from
  ha-matter-binding-helper's `_acl_target`).
- RemoteSensing has no high-level command: plain
  `write_attribute(node_id, "1/513/26", value | 1)` after reading the current
  bitmap. Writing it will flip HA core's own RemoteSensing diagnostic
  binary_sensors (expected; document in README).
- HA device registry stores Matter devices with identifiers
  `("matter", f"deviceid_{compressed_fabric_id:016X}-{node_id:016X}-{postfix}")`
  where postfix is `MatterNodeDevice` for node-level devices or the decimal
  endpoint id for bridged devices. Node id is parseable from the identifier;
  the functional endpoint must be found by scanning the node's cluster tree
  (bridged sensors live on high endpoint ids, e.g. 359/361 on an HA Matter
  Hub node).
- Reusing the official Matter integration's client
  (`get_matter()` / `runtime_data.adapter.matter_client`) is **unsupported and
  rejected** — undocumented, single-fabric assumption, breaks silently.

## Architecture

Two-tier config entries (hub + bindings), the pattern used by core
integrations like `matter` itself.

### Hub entry (one)

Created first via config flow. Holds the connection and integration-level
options.

- Data: WebSocket URL (default `ws://localhost:5580/ws`; offer the add-on
  socket path as an alternative), optional friendly name.
- Options (options flow, reload-on-change): **poll interval** (default 30 min,
  min 5 min), **auto-repair on/off**, **repair attempts before raising an
  issue** (default 3).
- Runtime (on `entry.runtime_data`, typed `ConfigEntry[RuntimeData]` — no
  `hass.data`):
  - One reconnecting `MatterClient` (from `matter-python-client`) shared by
    all binding entries; reconnect with exponential backoff; on reconnect,
    signal all binding coordinators to re-verify (catches drift that happened
    while the connection was down).
  - Matter event listener (`node_added`, `attribute_updated`, `node_removed`)
    feeding the hybrid repair fast path.
  - Server info cache (schema/fabric) for format detection.
- Unload closes the client; binding entries are reloaded/unloaded via the
  hub's setup/shutdown (hub is a dependency: binding entries abort setup with
  `ConfigEntryNotReady`/failed setup if the hub entry is missing).

### Binding entries (one per sensor→TRVs group)

- Data: **HA device registry device ids** (stable UUIDs) of the sensor device
  and each TRV device, plus a user-optional label. Node ids are **never
  stored** — resolved at runtime (see Device identity).
- Runtime: its own `DataUpdateCoordinator` (poll at hub interval, hybrid
  triggers), binding apply/verify/repair state, per-TRV health.
- Each entry creates its own HA device (named e.g. "Matter Binding — Office")
  hosting all entities; source Matter devices are **linked, not attached**
  (entity `device_entry` / `via_device`-style linkage). Never attach our
  config entry to another integration's device — that pattern stops working
  in HA Core 2026.8 (architecture#1226).

### Module layout

```
custom_components/matter_trv_binding/
  __init__.py            # hub setup, client lifecycle, runtime_data
  matter/
    client.py            # connection manager (reconnect, server_info, events)
    discovery.py         # device-registry → node_id; endpoint/cluster scan
    bindings.py          # apply/verify/repair: set_node_binding + RemoteSensing
    acl.py               # ACL read/merge/write with admin-safety check
  config_flow.py         # hub flow + binding flow + reconfigure
  options_flow.py       # hub options (poll interval etc.) + binding options
  coordinator.py        # per-binding verify/repair DataUpdateCoordinator
  binary_sensor.py, sensor.py, button.py
  repairs.py, diagnostics.py, services.py
  translations/en.json (+ fr)
  manifest.json
```

## Config flow UX

1. **Hub flow** (`user`): pick a name, WebSocket URL (pre-filled with the
   add-on default), connect and validate (server_info handshake). Errors:
   cannot connect → retry with `ConfigEntryNotReady`; wrong server version
   (legacy 8.x) → explicit error message.
2. **Binding flow** (`user`):
   - Sensor: native `DeviceSelector` filtered to `matter` domain devices that
     expose TemperatureMeasurement (0x0402) — resolution happens after
     selection; devices whose node can't be scanned are shown with an error.
   - TRVs: multi-select `DeviceSelector` filtered to `matter` devices exposing
     Thermostat (0x0201).
   - Both selectors resolve node id + endpoint **automatically** from the
     device registry identifier plus a cluster-tree scan; the user never sees
     node/endpoint ids.
   - Optional label; defaults to sensor device name.
   - On submit: validate reachability (read node), then apply the full desired
     state once and verify; failure → shown in the flow, entry not created.
3. **Reconfigure flow** (`async_step_reconfigure`): change sensor or TRV list
   (device replacement). Stable unique id; `async_update_reload_and_abort`.
   Re-applies desired state; ACL rebuild drops removed TRVs.
4. **Hub options flow**: poll interval, auto-repair toggle, repair-attempts
   threshold; `OptionsFlowWithReload`.

## Device identity & node_id resilience

Identity chain: config entry stores **device registry ids** → registry lookup
yields the matter identifier → parse node_id → scan node endpoints for the
expected cluster → endpoint. Cache endpoint per (device id, node generation)
and invalidate on node events or verify failures.

| Situation | Detection | Behavior |
|---|---|---|
| Device removed & re-commissioned (new node_id, same HA device) | Registry identifier changed between verifies | **Auto-heal**: apply desired state to the new node (binding, ACL, RemoteSensing), plus a **one-time self-clearing info notice**: a `persistent_notification` created once per node_id change and dismissed on the next healthy verify (kept out of the repairs registry — the user cannot act on it, and repairs are for actionable issues only). |
| Device deleted from HA | Registry lookup fails | Repair issue (fixable → reconfigure flow), entities `unavailable`, auto-repair for that binding paused. |
| Device unreachable | Verify read fails/times out | Status `unreachable`, entities `unavailable` (not "unhealthy"), no repair writes against a dead node. |
| Sensor is a bridged endpoint (HA Matter Hub) | Endpoint scan finds TemperatureMeasurement on a bridged endpoint | Native case; no special handling. |
| Matter server restart | Client disconnect | Connection manager reconnects with backoff; coordinators re-verify after reconnect; entries never go `setup_error` for transient outages. |

## Verify / repair cycle (hybrid)

- **Poll** (coordinator, hub-configured interval): per TRV read Binding list,
  RemoteSensing bitmap, and the sensor ACL; compare to desired state.
- **Fast path**: subscriptions to TRV `LocalTemperature` updates and matter
  node events trigger an immediate (debounced) verify+repair on obvious
  reversion events.
- **Repair**: re-apply in order **binding → ACL → RemoteSensing**, each step
  idempotent (read current, write only if different), then re-verify. A TRV
  whose device is unreachable is skipped until it returns.
- **Escalation**: N consecutive failed repairs within a window (hub option,
  default 3) → repair issue raised; binary_sensor shows the problem; further
  repairs continue on schedule (the issue informs, it does not stop repair).
- **Manual**: `button.check_and_repair` per binding device; service
  `matter_trv_binding.repair` (device target) for automations.

### ACL safety

Before any ACL write: read the current list, verify the controller's admin/CASE
entry is present and preserved in the new list, then write. Never write a list
that could lock the fabric out of the sensor node. Consolidate all endpoints of
the same sensor node that share the same TRV set into one ACL entry (devices
limit ACLs to ~3-4 per fabric). Warn in flow/log when the total entry count
exceeds 4.

## Entities

Per binding entry (on its own device):

| Entity | Type | Behavior |
|---|---|---|
| `<trv> binding` | `binary_sensor` ×N | `on` = healthy: binding + remote sensing + ACL all match desired state |
| `<trv> status` | `sensor` (device_class-less enum text) | `bound / binding_missing / remote_sensing_off / unreachable / device_missing` |
| `last check` | `sensor` (timestamp) | last successful verify pass |
| `repairs` | `sensor` (counter, state_class total_increasing) | auto-repairs applied for this binding |
| `check & repair` | `button` | immediate verify + repair cycle |

Service: `matter_trv_binding.repair` (device selector target).

## Repairs / notifications

- Sensor or TRV device missing from the registry (fixable → reconfigure flow).
- Repeated repair failures (fixable → "check & repair" guidance).
- One-time info notice on node_id auto-heal (self-clearing on next healthy
  verify).
- Only issues a user can act on; severity per HA issue-registry rules.

## Diagnostics

`async_get_config_entry_diagnostics` + `async_get_device_diagnostics` for
binding entries: binding desired vs actual state, per-TRV health, server info,
node/endpoint ids and fabric **redacted**. Manifest `"dependencies": []`;
`after_dependencies: ["matter"]` (device registry presence, not API use).

## Packaging

- HACS: single integration at `custom_components/matter_trv_binding/`,
  `hacs.json` (`name`, `homeassistant` min version), brand assets, releases.
- `manifest.json`: `version`, `config_flow: true`, `iot_class: local_push`,
  `integration_type: service` (hub + services),
  `requirements: ["matter-python-client~=1.0"]`, `codeowners`,
  `documentation`, `issue_tracker`, `loggers: ["matter_server"]`.
- New empty git branch carries the integration + new README; old scripts and
  docs stay on the old branch.
- Quality-scale conventions: strict typing, `runtime_data`, translations
  (en + fr), `PARALLEL_UPDATES`, redacted diagnostics.

## Error handling

- Hub connect failure → `ConfigEntryNotReady` (HA exponential retry).
- Binding setup with unreachable node → `ConfigEntryNotReady`.
- Cluster write failure → per-TRV error state on status sensor; repair
  escalation policy handles notification.
- All verify/repair exceptions are caught per TRV; one failing TRV never
  aborts the rest of the binding's cycle.

## Testing

- pytest + `pytest-homeassistant-custom-component`; fake `MatterClient`
  (both server key-spelling variants) — no real hardware in CI.
- Config flows (hub + binding, mock device registry entries with realistic
  matter identifiers), reconfigure, options flows.
- Verify/repair logic: drift scenarios (missing binding, sensing bit off, ACL
  entry gone), unreachable nodes, node_id change auto-heal, ACL admin-safety
  check (must refuse to write a list that drops the admin entry).
- Unit tests: identifier parsing, endpoint/cluster scan, ACL consolidation.

## Explicit simplifications

- `shortcut:` group bindings unsupported; per-TRV only — add group mode in the
  apply layer later if needed.
- `shortcut:` legacy python-matter-server 8.x unsupported — target the
  matterjs-server generation only; revisit only if a user actually hits it.
- RemoteSensing written as raw attribute (no high-level server command
  exists); the write's key format is stable across both server generations.
