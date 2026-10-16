# Matter TRV Binding

A Home Assistant custom integration that links a Matter temperature sensor to one or more Matter
thermostatic radiator valves (TRVs) using the **native Matter binding mechanism** — no cloud, no
vendor hub, and no automations required.

Writing the binding on the devices themselves means the TRV keeps following the sensor directly on
the Thread/Matter fabric, even when Home Assistant is offline.

## What it does

For each configured room, the integration:

- writes a **Matter binding** on every TRV so it reads temperature directly from the sensor node,
- writes an **Access Control List (ACL)** entry on the sensor so the TRVs are allowed to read it,
- enables **RemoteSensing** on each TRV so it uses the bound remote sensor instead of its internal one,
- **verifies** all of the above on a schedule and **repairs** any drift automatically (the binding,
  the ACL entry and the RemoteSensing flag are re-applied if they were lost),
- exposes the health of each TRV as entities and raises **repair issues** in Home Assistant when a
  bound device disappears or a repair keeps failing.

The result is a fully local sensor→TRV link that survives reboots and out-of-band changes.

## Requirements

- **Home Assistant 2026.8.0 or newer** (the `hacs.json` `homeassistant` value is authoritative).
- The official **Matter Server add-on 9.x or newer**, with at least one Matter fabric already set up
  and the target devices commissioned.
- The **Matter integration** must be configured in Home Assistant (`after_dependencies: [matter]`):
  the device pickers only offer Matter devices that Home Assistant already knows about.

> The legacy `python-matter-server` is **not** supported. When the WebSocket server reports a
> legacy schema the hub setup fails with a clear error asking you to upgrade to the Matter Server
> add-on 9.x.

## Installation

1. In HACS, open **⋮ → Custom repositories**.
2. Add `https://github.com/antoineraulin/matter_trv_binding` with category **Integration**.
3. Install **Matter TRV Binding**, then restart Home Assistant.
4. Add the integration from **Settings → Devices & Services → Add Integration**.

## Configuration

The integration uses two kinds of config entries.

### 1. Hub entry (once)

Add **Matter TRV Binding** and fill in the hub step:

| Field | Description |
| --- | --- |
| **Name** | A label for this connection (usually just `Matter`). |
| **Matter server WebSocket URL** | The WebSocket URL of your Matter Server add-on, e.g. `ws://localhost:5580/ws` (the default offered by the add-on). |

Only one hub entry can exist. It opens one shared connection to the Matter server; every binding
entry uses it.

### 2. Binding entries (one per room)

Add **Matter TRV Binding** again for each room and fill in the binding step:

| Field | Description |
| --- | --- |
| **Label** | A name for this binding (usually the room, e.g. `Bureau`). |
| **Temperature sensor (Matter device)** | The Matter temperature sensor whose readings drive the room. |
| **TRVs (Matter devices)** | One or more Matter TRVs that should follow that sensor. |

The two device pickers list the Matter devices Home Assistant already knows. You pick devices only —
the integration resolves each device's **node id, endpoint and clusters automatically**; you never
enter a node id or cluster by hand. A binding entry can be renamed or repointed later through its
**Reconfigure** action.

## Entities and services

Every binding entry creates one device (`Matter Binding — <label>`) carrying these entities:

| Entity | Platform | Description |
| --- | --- | --- |
| `<TRV name> status` | `sensor` | Health of that TRV's binding: `bound`, `binding_missing`, `remote_sensing_off`, `acl_missing`, `unreachable` or `device_missing`. |
| `<TRV name> binding` | `binary_sensor` | `on` when that TRV's binding is healthy. |
| `<label> Last check` | `sensor` | Timestamp of the last verify/repair cycle. |
| `<label> Repairs` | `sensor` | Cumulative number of automatic repairs applied to this binding. |
| `Check and repair` | `button` | Runs one verify/repair cycle immediately. |

A **service** is also available, targeting a binding device:

| Service | Target | Description |
| --- | --- | --- |
| `matter_trv_binding.repair` | Binding device | Runs an immediate verify-and-repair cycle for the selected binding. |

Hub options (configurable via **Configure** on the hub entry) tune the loop:

| Option | Default | Description |
| --- | --- | --- |
| **Verification interval (minutes)** | `30` | How often each binding is checked. |
| **Automatically repair bindings** | on | Re-apply drifted bindings, ACL entries and RemoteSensing flags. |
| **Failed repairs before raising an issue** | `3` | Consecutive failures before a `repair_failed` issue is raised. |

## Behavior notes

- **RemoteSensing diagnostic sensors flip.** Writing the RemoteSensing attribute on a TRV is exactly
  what makes Home Assistant's own Matter integration show the corresponding *RemoteSensing* diagnostic
  binary sensor turning on. This is expected: the integration is deliberately turning remote sensing
  on, and Home Assistant reflects that state.
- **ACL entry limits.** Some devices accept only about **4 ACL entries per fabric**. A node that
  already carries ACL entries for other fabrics or privileges can reject the ACL write; when that
  happens the repair is reported as failing rather than silently overwriting the list. If you hit
  this, **consolidate bindings onto such nodes** (fewer, larger bindings) instead of spreading many
  sensors across the same node.
- **Bindings live on the devices.** The actual Matter bindings and ACL entries are stored on the
  devices' own Matter stack, independent of Home Assistant. **Removing a binding entry, or
  uninstalling the integration, removes only *our* entities** — it does **not** clean up the
  bindings, ACL entries or RemoteSensing flags already written on the devices. No cleanup is
  implemented; if you want the old bindings gone you must remove them on the devices themselves.

## Troubleshooting

- **Repair issues** — Home Assistant raises a *Bound Matter device no longer exists* issue when a
  device referenced by a binding is removed from Home Assistant, and a *Binding repair keeps
  failing* issue when repairs keep failing. Both appear under **Settings → System → Repairs**. The
  first is fixable and offers to remove the stale binding entry.
- **Unreachable nodes** — a TRV or sensor that cannot be reached is reported with an `unreachable`
  status and its entities go unavailable; the binding is left untouched (writing to a dead node is
  never attempted). Check the device's Thread/Matter connectivity and the Matter server, then wait
  for the next cycle or press **Check and repair**.
- **Node-change notifications** — if a device re-commissions and gets a new Matter node id, the
  integration detects the change, re-applies the binding and posts a persistent notification
  (title *Matter TRV Binding*) describing the old and new node id.

## Brand assets

Brand assets (icon/logo) are **not shipped yet**; a placeholder is used in the Home Assistant UI
until they are added under `brand/`.

## License

See [LICENSE](LICENSE).
