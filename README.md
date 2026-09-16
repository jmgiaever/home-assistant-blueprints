# home-assistant-blueprints

Blueprints for my Home Assistant installations. Each blueprint is importable straight from this repository.

## Cabin auto-lock policy (`automation/cabin_auto_lock.yaml`)

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fjmgiaever%2Fhome-assistant-blueprints%2Fblob%2Fmaster%2Fautomation%2Fcabin_auto_lock.yaml)

Keeps a TTLock door free while a cabin is in use and secures it when the cabin settles, so nobody types the code
more than once per visit. Requires the [hass-ttlock](https://github.com/jbergler/hass-ttlock) integration (v0.15
or newer) and Home Assistant 2025.4 or newer.

| State | On the lock | When |
|---|---|---|
| **Occupied** | auto-lock off | any successful unlock (fingerprint, code, app). Every unlock now stays unlocked |
| **Resting** | per the resting policy (default: bolt locked, auto-lock armed) | M minutes after the last living-area motion while someone is home or the bedroom was active |
| **Vacant** | bolt locked, auto-lock armed | M minutes after the last living-area motion with nobody home and the bedroom quiet, or at once on an explicit lock (keypad lock key, app, dashboard, voice), or at the night time |

Policies only ever strengthen the lock. The blueprint never unlocks the door except to undo the lock's own
auto-lock countdown within 90 s of occupying. Passage mode (set in the TTLock app) counts as guest hours: the timer
never locks during a window, the window end does.

### Before you create the automation

1. **Create an `input_datetime` helper** with *date and time* (Settings → Devices & services → Helpers → Create helper
   → Date and/or time). One per lock. It stores "vacancy not before" and survives restarts.
2. Optional: **create an `input_select` helper** with exactly the options `occupied`, `resting`, `vacant` if you
   want other automations (heating, lights) to follow the cabin state. The blueprint writes it and never reads it.
3. Make sure the TTLock integration's webhook is registered (Settings → Integrations → TTLock → "Webhook registered"),
   otherwise lock events arrive only with the 15-minute poll.

### Inputs

| Section | Input | Default | Notes |
|---|---|---|---|
| Lock | lock, last-trigger sensor, auto-lock switch | required | the TTLock entities of one lock |
| Lock | last-operator sensor | none | only with a trusted-operator list |
| Lock | passage-mode sensor | none | guest hours |
| Timers | vacancy helper | required | the `input_datetime` from step 1 |
| Timers | motion delay M / presence delay P / armed seconds S | 45 min / 20 min / 30 s | |
| Resting policy | lock the bolt / arm auto-lock / resting seconds | on / on / 30 s | off/off leaves the door free while the household is around |
| Occupancy | activity motion sensors | none | living areas: keep the door free |
| Occupancy | resting motion sensors | none | bedrooms: mark the cabin as resting, never keep the door free |
| Occupancy | presence | none | persons or trackers; postpones settling and marks resting; never keeps the door unlocked |
| Occupancy | trusted operators | none | names as shown by the last-operator sensor; empty = anyone |
| Night | lock at night / night time | off / 23:00 | beats passage mode |
| Safety | undo stale countdown / window | on / 90 s | |
| Safety | on failure | none | actions after two failed attempts (a persistent notification is always created) |
| State output | state helper, on occupied / resting / vacant | none | the `input_select` from step 2 and hooks |

Design and decisions: `docs/superpowers/specs/2026-09-16-cabin-auto-lock-design.md`.

## Development

```
uv sync --group dev
uv run pytest -q
uv run python scripts/mutation_check.py
```

Tests run a real Home Assistant core pinned to the version on the target installation (see `pyproject.toml`).

## Other blueprints

`automation/motion_detected_lights.yaml`, `automation/lights_turned_on_by.yaml`, `automation/cast_lovelace_on_motion.yaml`,
`script/notify_user.yaml`, `script/create_device_class_groups.yaml` — older, undocumented.
