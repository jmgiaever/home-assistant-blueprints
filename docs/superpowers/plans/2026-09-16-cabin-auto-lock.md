# Cabin Auto-Lock Blueprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `automation/cabin_auto_lock.yaml`, a reusable Home Assistant blueprint that keeps a TTLock door free while a cabin is in use and secures it when the cabin settles, with a real-HA test suite, a mutation check, and a verified deployment on the Hytta lock.

**Architecture:** One automation blueprint, `mode: queued`, whose triggers carry ids and whose actions form a fixed pipeline: push the vacancy timer → occupy → select a policy (explicit lock / night / settle) → apply the policy (strengthen-only, idempotent, with guarded retries) → undo a stale countdown → refresh the optional state label. The door state lives in the lock (auto-lock switch + bolt); timing lives in one `input_datetime` helper; the label is an optional `input_select` output. Tests run a real Home Assistant core (the cabin's exact version) against a fake TTLock whose services mirror into entity states.

**Tech Stack:** Home Assistant 2026.8.3 blueprint YAML (Jinja templates), Python 3.14, uv, pytest + `pytest-homeassistant-custom-component==0.13.357` (pins `homeassistant==2026.8.3`), freezegun (`freezer` fixture), GitHub Actions, SSH ControlMaster to the cabin Pi.

**Spec:** `docs/superpowers/specs/2026-09-16-cabin-auto-lock-design.md` (read it first; rule numbers R0–R5, decisions D1–D25, tests T1–T27 and acceptance A1–A9 below refer to it).

## Global Constraints

- Blueprint file: `automation/cabin_auto_lock.yaml` (repo layout is flat, spec §7). Blueprint path as installed on HA: `jmgiaever/cabin_auto_lock.yaml`.
- `homeassistant.min_version: "2025.4.0"` (variable scoping, `sequence:` action, `triggers:`/`actions:` syntax). The cabin runs 2026.8.3.
- Test harness pin: `pytest-homeassistant-custom-component==0.13.357` → `homeassistant==2026.8.3`; `requires-python = ">=3.14.2"`.
- Integration whose strings we classify: `jbergler/hass-ttlock` v0.15.0 (`Event.EVENTS` table copied into `tests/ttlock_events.py`).
- The blueprint contains no entity ids, names or site specifics (spec §6). Every value comes from inputs.
- Policies only strengthen: only R1 disarms, only R5 unlocks, `rest()` never re-configures an armed lock (D23).
- The door rules never read the state helper; it is written only (D20).
- Automation: `mode: queued`, `max: 100`, `max_exceeded: silent` (D22). Waits 10 s, one retry after 10 s, guarded by "no newer lock event" (§4.7).
- Start-up grace: settles require `(now() - as_datetime(this.last_changed)).total_seconds() >= 120` (§4.4/§6 as amended). No `delay` in the run.
- Every rule requires both `trigger.from_state` and `trigger.to_state` to exist for state triggers (entity-added and entity-removed events are not events, §6); the presence trigger keeps `not_from`/`not_to`, never an allow-list of states, because persons can be in named zones.
- License GPL-3.0 (repo's), branch `master`, commits in the form `feat(cabin-auto-lock): …` / `test(cabin-auto-lock): …` / `docs: …`.
- Never commit secrets. The cabin's config directory is `/var/snap/docker/common/var-lib-docker/volumes/homeassistant-config/_data`; the Pi host has no `python3`, use `docker exec -i homeassistant python3 -`.
- Git: work in a worktree on branch `feat/cabin-auto-lock` (created with superpowers:using-git-worktrees). Do not push to `master`; the user merges out.

---

## File structure

| Path | Responsibility |
|---|---|
| `automation/cabin_auto_lock.yaml` | The blueprint. Metadata + inputs, triggers, `variables:` (classification and live facts), `actions:` pipeline. Final form in Appendix A. |
| `pyproject.toml`, `.python-version`, `uv.lock` | uv-managed dev environment, pytest config (`asyncio_mode = "auto"`). |
| `.gitignore` | add Python artefacts. |
| `.github/workflows/test.yml` | CI: `uv sync`, `pytest`, mutation check. |
| `tests/ttlock_events.py` | The integration's event table (id → action, description) and the expected class per id. |
| `tests/fakes.py` | `FakeTTLock`: registers `lock.lock`, `lock.unlock`, `ttlock.configure_autolock`, mirrors calls into states, can fail N calls, simulates webhook events. |
| `tests/conftest.py` | Fixtures: temp config dir with the blueprint installed, `fake`, `hooks`, `notifications`, `clock` (fake-time stepping), `policy` (sets up helpers + automation with default inputs and overrides, tears down). |
| `tests/test_00_harness.py` … `tests/test_09_label.py` | One file per task, tests named after the spec's T-numbers. |
| `scripts/mutation_check.py` | Applies each spec §7 mutation to a copy of the blueprint and asserts the suite goes red. |
| `README.md` | New section: import button, behaviour table, helper setup, inputs. |

## Test harness conventions (used by every task)

- Entities used in tests (all fake, created by `FakeTTLock` and `conftest.py`): lock `lock.hytta`, switch `switch.hytta_auto_lock` (attribute `seconds`), `sensor.hytta_last_trigger`, `sensor.hytta_last_operator`, `binary_sensor.hytta_passage_mode`, helper `input_datetime.hytta_vacancy`, label `input_select.hytta_state`, activity sensors `binary_sensor.kitchen_motion` + `binary_sensor.living_motion`, resting sensor `binary_sensor.bedroom_motion`, persons `person.joachim` + `person.lukas`, automation `automation.hytta_auto_lock_policy`.
- `fake.calls` is a list of `(service_name, data)`; `fake.calls_of("lock.lock")` returns the data dicts. `data["entity_id"]` is a **list** (HA merges `target:` into data).
- After `await policy()` the scene is VACANT: lock `locked`, switch `on` with `seconds: 30`, helper = now, label `vacant`, persons `not_home`, sensors `off`, 3 minutes of fake time elapsed (start-up grace passed), call lists cleared.
- `await clock(seconds, step=60)` advances fake time and fires HA timers. Use `step=10` (or less) in tests that exercise the 10-second waits. `clock()` never waits for run tasks; a run parked in a `wait_template`/`delay` resumes on the next tick. Call `await hass.async_block_till_done()` only after an event whose run cannot be waiting on a timer (a healthy fake answers every command at once), never while a failure is being retried.
- HA's `homeassistant: start` trigger does **not** fire in tests (hass is already running when the automation is set up); the start grace is exercised through `this.last_changed` (T13).
- Fake-time: `time.monotonic` is frozen too, so `delay`/`wait_template` timeouts are advanced by `clock()`, never by real waiting.
- The harness runs Home Assistant in time zone **US/Pacific** (`dt_util.DEFAULT_TIME_ZONE`); `time` triggers such as the night time are local, so tests that depend on wall-clock times freeze to a local datetime, never to a `+00:00` string.
- `policy()` aligns the frozen clock to M0:30.123456 of the current 5-minute block so the safety net ticks at +4:30 after test start.

---

### Task 1: Test harness, CI and blueprint skeleton

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.github/workflows/test.yml`, `tests/__init__.py`, `tests/ttlock_events.py`, `tests/fakes.py`, `tests/conftest.py`, `tests/test_00_harness.py`, `automation/cabin_auto_lock.yaml`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `FakeTTLock` (constants `LOCK`, `SWITCH`, `TRIGGER`, `OPERATOR`, `PASSAGE`; methods `event(description, operator=None)`, `unlocked_by(description="unlock by fingerprint", operator="Joachim")`, `auto_locked()`, `set_lock_state(state)`, `set_switch(on, seconds=30)`, `set_passage(on)`, `fail(service, times=1)`, `calls_of(name) -> list[dict]`, `reset_calls()`), fixtures `fake`, `hooks` (dict of mock-service call lists `notify`, `on_occupied`, `on_resting`, `on_vacant`), `notifications` (dict `create`, `dismiss`), `clock(seconds, step=60)`, `policy(grace=True, **input_overrides)`, constants `HELPER`, `STATE`, `KITCHEN`, `LIVING`, `BEDROOM`, `JOACHIM`, `LUKAS`, `AUTOMATION`, helper `helper_ts(hass) -> float`, and `EVENTS`/`EXPECTED_CLASS` in `tests/ttlock_events.py`.
- Produces: the blueprint skeleton with all inputs, triggers and input-mapping variables, and an `actions:` list containing only marker comments. Later tasks insert blocks at the markers.

- [ ] **Step 1: Create the uv project and pytest config**

`pyproject.toml`:

```toml
[project]
name = "home-assistant-blueprints"
version = "0.0.0"
description = "Home Assistant blueprints and their tests"
requires-python = ">=3.14.2"
dependencies = []

[dependency-groups]
dev = [
  "pytest-homeassistant-custom-component==0.13.357",
]

[tool.uv]
package = false

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`.python-version`:

```
3.14
```

Append to `.gitignore`:

```
# ---> Python
.venv/
__pycache__/
.pytest_cache/
*.pyc
```

Run: `uv sync --group dev`
Expected: creates `.venv` with Python 3.14.x and `uv.lock`; `uv run python -c "import homeassistant.const as c; print(c.__version__)"` prints `2026.8.3`.

- [ ] **Step 2: Write the event table**

`tests/ttlock_events.py` (copied from `custom_components/ttlock/models.py`, hass-ttlock v0.15.0):

```python
"""hass-ttlock v0.15.0 Event.EVENTS: id -> (action, description), and the class we expect."""

EVENTS: dict[int, tuple[str, str]] = {
    1: ("unlock", "unlock by app"),
    4: ("unlock", "unlock by passcode"),
    7: ("unlock", "unlock by IC card"),
    8: ("unlock", "unlock by fingerprint"),
    9: ("unlock", "unlock by wrist strap"),
    10: ("unlock", "unlock by Mechanical key"),
    11: ("lock", "lock by app"),
    12: ("unlock", "unlock by gateway"),
    29: ("unknown", "apply some force on the Lock"),
    30: ("close", "Door sensor closed"),
    31: ("open", "Door sensor open"),
    32: ("open", "open from inside"),
    33: ("lock", "lock by fingerprint"),
    34: ("lock", "lock by passcode"),
    35: ("lock", "lock by IC card"),
    36: ("lock", "lock by Mechanical key"),
    37: ("unknown", "Remote Control"),
    42: ("unknown", "received new local mail"),
    43: ("unknown", "received new other cities' mail"),
    44: ("unknown", "Tamper alert"),
    45: ("lock", "Auto Lock"),
    46: ("unlock", "unlock by unlock key"),
    47: ("lock", "lock by lock key"),
    48: ("unknown", "System locked ( Caused by, for example: Using INVALID Passcode/Fingerprint/Card several times)"),
    49: ("unlock", "unlock by hotel card"),
    50: ("unlock", "unlocked due to the high temperature"),
    51: ("unknown", "Try to unlock with a deleted card"),
    52: ("unknown", "Dead lock with APP"),
    53: ("unknown", "Dead lock with passcode"),
    54: ("unknown", "The car left (for parking lock)"),
    55: ("unlock", "unlock with key fob"),
    57: ("unlock", "unlock with QR code success"),
    58: ("unknown", "Unlock with QR code failed, it's expired"),
    59: ("unknown", "Double locked"),
    60: ("unknown", "Cancel double lock"),
    61: ("lock", "Lock with QR code success"),
    62: ("unknown", "Lock with QR code failed, the lock is double locked"),
    63: ("unlock", "auto unlock at passage mode"),
    67: ("unlock", "3D Face Unlock Success"),
    68: ("unknown", "3D Face Unlock Failed (Locked)"),
    69: ("lock", "Locked via 3D Face"),
    71: ("unknown", "3D Face Recognition Failed (Expired)"),
}

UNLOCK_IDS = {1, 4, 7, 8, 9, 10, 12, 46, 49, 50, 55, 57, 67}
EXPLICIT_LOCK_IDS = {11, 33, 34, 35, 36, 47, 61, 69}
AUTO_LOCK_IDS = {45}


def expected_class(event_id: int) -> str:
    """The class the blueprint must assign (spec §4.2)."""
    if event_id in UNLOCK_IDS:
        return "UNLOCK"
    if event_id in EXPLICIT_LOCK_IDS:
        return "EXPLICIT_LOCK"
    if event_id in AUTO_LOCK_IDS:
        return "AUTO_LOCK"
    return "IGNORED"
```

- [ ] **Step 3: Write the fake TTLock**

`tests/fakes.py`:

```python
"""Test double for the TTLock integration: services mirror into entity states."""

from __future__ import annotations

from collections import Counter

from homeassistant.core import HomeAssistant, ServiceCall

LOCK = "lock.hytta"
SWITCH = "switch.hytta_auto_lock"
TRIGGER = "sensor.hytta_last_trigger"
OPERATOR = "sensor.hytta_last_operator"
PASSAGE = "binary_sensor.hytta_passage_mode"


class FakeTTLock:
    """Behaves like the lock + cloud: commands change state unless told to fail."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.calls: list[tuple[str, dict]] = []
        self._failures: Counter[str] = Counter()
        hass.states.async_set(LOCK, "locked")
        hass.states.async_set(SWITCH, "on", {"seconds": 30})
        hass.states.async_set(TRIGGER, "Unknown")
        hass.states.async_set(OPERATOR, "Unknown")
        hass.states.async_set(PASSAGE, "off")
        hass.services.async_register("lock", "lock", self._lock)
        hass.services.async_register("lock", "unlock", self._unlock)
        hass.services.async_register("ttlock", "configure_autolock", self._configure_autolock)

    # ---- commands the automation sends ----
    def _accept(self, name: str, call: ServiceCall) -> bool:
        self.calls.append((name, dict(call.data)))
        if self._failures[name] > 0:
            self._failures[name] -= 1
            return False
        return True

    async def _lock(self, call: ServiceCall) -> None:
        if self._accept("lock.lock", call):
            self.hass.states.async_set(LOCK, "locked")

    async def _unlock(self, call: ServiceCall) -> None:
        if self._accept("lock.unlock", call):
            self.hass.states.async_set(LOCK, "unlocked")

    async def _configure_autolock(self, call: ServiceCall) -> None:
        if self._accept("ttlock.configure_autolock", call):
            if call.data.get("enabled"):
                self.hass.states.async_set(SWITCH, "on", {"seconds": int(call.data.get("seconds", 10))})
            else:
                self.hass.states.async_set(SWITCH, "off", {"seconds": 0})

    def fail(self, service: str, times: int = 1) -> None:
        """Make the next `times` calls of `service` record but not change state."""
        self._failures[service] += times

    # ---- what the physical lock / cloud reports ----
    def event(self, description: str, operator: str | None = None) -> None:
        """A webhook record: the last-operator and last-trigger sensors update."""
        if operator is not None:
            self.hass.states.async_set(OPERATOR, operator)
        self.hass.states.async_set(TRIGGER, description)

    def unlocked_by(self, description: str = "unlock by fingerprint", operator: str = "Joachim") -> None:
        """Someone unlocked at the door: lock entity first, then the record (as the integration does)."""
        self.hass.states.async_set(LOCK, "unlocked")
        self.event(description, operator)

    def auto_locked(self) -> None:
        self.hass.states.async_set(LOCK, "locked")
        self.event("Auto Lock")

    def set_lock_state(self, state: str) -> None:
        self.hass.states.async_set(LOCK, state)

    def set_switch(self, on: bool, seconds: int = 30) -> None:
        self.hass.states.async_set(SWITCH, "on" if on else "off", {"seconds": seconds if on else 0})

    def set_passage(self, on: bool) -> None:
        self.hass.states.async_set(PASSAGE, "on" if on else "off")

    # ---- assertions ----
    def calls_of(self, name: str) -> list[dict]:
        return [data for n, data in self.calls if n == name]

    def reset_calls(self) -> None:
        self.calls.clear()
```

- [ ] **Step 4: Write conftest**

`tests/__init__.py`: empty file.

`tests/conftest.py`:

```python
"""Shared fixtures: a real HA core, the blueprint installed in a temp config dir, a fake lock."""

from __future__ import annotations

import asyncio
import pathlib
import shutil
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from .fakes import LOCK, OPERATOR, PASSAGE, SWITCH, TRIGGER, FakeTTLock

REPO = pathlib.Path(__file__).resolve().parent.parent
BLUEPRINT = REPO / "automation" / "cabin_auto_lock.yaml"
BLUEPRINT_PATH = "jmgiaever/cabin_auto_lock.yaml"

HELPER = "input_datetime.hytta_vacancy"
STATE = "input_select.hytta_state"
KITCHEN = "binary_sensor.kitchen_motion"
LIVING = "binary_sensor.living_motion"
BEDROOM = "binary_sensor.bedroom_motion"
JOACHIM = "person.joachim"
LUKAS = "person.lukas"
AUTOMATION = "automation.hytta_auto_lock_policy"

DEFAULT_INPUTS: dict = {
    "lock_entity": LOCK,
    "last_trigger_sensor": TRIGGER,
    "auto_lock_switch": SWITCH,
    "last_operator_sensor": OPERATOR,
    "passage_mode_sensor": PASSAGE,
    "vacancy_helper": HELPER,
    "motion_delay": 45,
    "presence_delay": 20,
    "armed_seconds": 30,
    "resting_lock_bolt": True,
    "resting_arm_auto_lock": True,
    "resting_armed_seconds": 30,
    "motion_sensors": [KITCHEN, LIVING],
    "resting_sensors": [BEDROOM],
    "presence_entities": [JOACHIM, LUKAS],
    "trusted_operators": [],
    "night_enabled": False,
    "night_time": "23:00:00",
    "stale_countdown_unlock": True,
    "stale_countdown_window": 90,
    "notify_actions": [{"action": "test.notify"}],
    "state_select": STATE,
    "on_occupied": [{"action": "test.on_occupied"}],
    "on_resting": [{"action": "test.on_resting"}],
    "on_vacant": [{"action": "test.on_vacant"}],
}


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """Temp HA config dir with the blueprint installed where HA looks for it."""
    dest = pathlib.Path(hass_tmp_config_dir) / "blueprints" / "automation" / "jmgiaever"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT, dest / "cabin_auto_lock.yaml")
    return hass_tmp_config_dir


@pytest.fixture
def fake(hass: HomeAssistant) -> FakeTTLock:
    return FakeTTLock(hass)


@pytest.fixture
def hooks(hass: HomeAssistant) -> dict[str, list]:
    return {name: async_mock_service(hass, "test", name) for name in ("notify", "on_occupied", "on_resting", "on_vacant")}


@pytest.fixture
def notifications(hass: HomeAssistant) -> dict[str, list]:
    return {
        "create": async_mock_service(hass, "persistent_notification", "create"),
        "dismiss": async_mock_service(hass, "persistent_notification", "dismiss"),
    }


@pytest.fixture
def clock(hass: HomeAssistant, freezer):
    """Advance fake time in steps, firing HA timers (time triggers, delays, wait timeouts) on the way."""

    async def advance(seconds: float, step: float = 60) -> None:
        # Never call hass.async_block_till_done() here: a run parked in a delay/wait_template
        # waits on a timer that only the next tick fires, and block_till_done would hang on it
        # (time.monotonic is frozen too). Yielding a few loop iterations per tick is enough
        # for due runs to progress to their next timer or to completion.
        remaining = float(seconds)
        while remaining > 0:
            d = min(step, remaining)
            freezer.tick(timedelta(seconds=d))
            async_fire_time_changed(hass, dt_util.utcnow())
            for _ in range(6):
                await asyncio.sleep(0)
            remaining -= d

    return advance


def helper_ts(hass: HomeAssistant) -> float:
    return float(hass.states.get(HELPER).attributes["timestamp"])


async def set_helper(hass: HomeAssistant, ts: float) -> None:
    await hass.services.async_call("input_datetime", "set_datetime", {"entity_id": HELPER, "timestamp": ts}, blocking=True)


@pytest.fixture
async def policy(hass: HomeAssistant, fake: FakeTTLock, hooks, notifications, clock):
    """Set up helpers + the blueprint automation. Scene after setup: VACANT, grace passed, calls cleared."""

    async def _setup(grace: bool = True, **overrides) -> None:
        inputs = {**DEFAULT_INPUTS, **overrides}
        for entity in (KITCHEN, LIVING, BEDROOM):
            hass.states.async_set(entity, "off")
        for person in (JOACHIM, LUKAS):
            hass.states.async_set(person, "not_home")
        assert await async_setup_component(
            hass, "input_datetime",
            {"input_datetime": {"hytta_vacancy": {"name": "Hytta vacancy", "has_date": True, "has_time": True}}},
        )
        assert await async_setup_component(
            hass, "input_select",
            {"input_select": {"hytta_state": {"options": ["occupied", "resting", "vacant"], "initial": "vacant"}}},
        )
        await set_helper(hass, dt_util.utcnow().timestamp())
        assert await async_setup_component(
            hass, "automation",
            {"automation": [{"id": "hytta", "alias": "Hytta auto-lock policy",
                             "use_blueprint": {"path": BLUEPRINT_PATH, "input": inputs}}]},
        )
        await hass.async_block_till_done()
        assert hass.states.get(AUTOMATION).state == "on"
        # Re-register the notification mocks now: if HA loaded the real persistent_notification
        # component while setting up automation, it replaced the handlers registered earlier.
        notifications["create"] = async_mock_service(hass, "persistent_notification", "create")
        notifications["dismiss"] = async_mock_service(hass, "persistent_notification", "dismiss")
        if grace:
            await clock(180)
        # A safety-net tick during the grace may have relabelled the cabin; the documented
        # scene after setup is 'vacant' (the door rules never read this helper).
        await hass.services.async_call("input_select", "select_option", {"entity_id": STATE, "option": "vacant"}, blocking=True)
        await hass.async_block_till_done()
        fake.reset_calls()
        for calls in hooks.values():
            calls.clear()
        notifications["create"].clear()
        notifications["dismiss"].clear()

    yield _setup

    if hass.states.get(AUTOMATION) is not None:
        await hass.services.async_call("automation", "turn_off", {"entity_id": AUTOMATION}, blocking=True)
        await hass.async_block_till_done()
```

- [ ] **Step 5: Write the failing harness test**

`tests/test_00_harness.py`:

```python
"""The harness itself: blueprint loads, automation is on, fake services are wired, no lingering timers."""

from homeassistant.core import HomeAssistant

from .conftest import AUTOMATION, HELPER, STATE
from .fakes import LOCK, SWITCH, FakeTTLock


async def test_blueprint_loads_and_scene_is_vacant(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    assert hass.states.get(AUTOMATION).state == "on"
    assert hass.states.get(LOCK).state == "locked"
    assert hass.states.get(SWITCH).state == "on"
    assert hass.states.get(SWITCH).attributes["seconds"] == 30
    assert hass.states.get(STATE).state == "vacant"
    assert hass.states.get(HELPER).attributes["timestamp"] > 0
    assert fake.calls == []


async def test_fake_lock_mirrors_commands(hass: HomeAssistant, fake: FakeTTLock) -> None:
    await hass.services.async_call("lock", "unlock", {"entity_id": LOCK}, blocking=True)
    assert hass.states.get(LOCK).state == "unlocked"
    await hass.services.async_call("ttlock", "configure_autolock", {"entity_id": LOCK, "enabled": False}, blocking=True)
    assert hass.states.get(SWITCH).state == "off"
    fake.fail("lock.lock", times=1)
    await hass.services.async_call("lock", "lock", {"entity_id": LOCK}, blocking=True)
    assert hass.states.get(LOCK).state == "unlocked"          # first call ignored
    await hass.services.async_call("lock", "lock", {"entity_id": LOCK}, blocking=True)
    assert hass.states.get(LOCK).state == "locked"
    assert [n for n, _ in fake.calls] == ["lock.unlock", "ttlock.configure_autolock", "lock.lock", "lock.lock"]
    assert fake.calls_of("lock.lock")[0]["entity_id"] in (LOCK, [LOCK])   # a direct call passes a string; automations pass a list
```

- [ ] **Step 6: Run it to see it fail**

Run: `uv run pytest tests/test_00_harness.py -q`
Expected: FAIL — `FileNotFoundError` copying `automation/cabin_auto_lock.yaml` (the blueprint does not exist yet). The second test passes on its own once the blueprint exists.

- [ ] **Step 7: Write the blueprint skeleton**

`automation/cabin_auto_lock.yaml` — the complete metadata, inputs, mode, triggers and input-mapping variables; `actions:` holds only markers. This is Appendix A minus the classification/fact variables and minus the action blocks (those arrive in Tasks 2–9).

```yaml
blueprint:
  name: Cabin auto-lock policy
  description: >-
    Keeps a TTLock door free while the cabin is in use and secures it when the cabin settles,
    so nobody enters the code more than once per visit.

    OCCUPIED = auto-lock off: every unlock stays unlocked. Any successful unlock occupies.
    RESTING = the household is here but quiet: the door follows the resting policy below.
    VACANT = nobody is here: bolt locked, auto-lock armed. An explicit lock (keypad lock key,
    app, dashboard, voice) vacates at once. A restart-proof timer (an input_datetime helper)
    settles the door a while after the last living-area motion. Passage mode counts as guest
    hours. Design and decisions: docs/superpowers/specs/2026-09-16-cabin-auto-lock-design.md
    in github.com/jmgiaever/home-assistant-blueprints.
  domain: automation
  author: Joachim M. G. Giæver
  homeassistant:
    min_version: "2025.4.0"
  input:
    lock:
      name: Lock
      icon: mdi:lock-smart
      input:
        lock_entity:
          name: Lock
          description: The TTLock lock entity.
          selector:
            entity:
              filter:
                - integration: ttlock
                  domain: lock
        last_trigger_sensor:
          name: Last trigger sensor
          description: The lock's "Last Trigger" sensor (values such as "unlock by fingerprint").
          selector:
            entity:
              filter:
                - integration: ttlock
                  domain: sensor
        auto_lock_switch:
          name: Auto-lock switch
          description: The lock's "Auto Lock" switch.
          selector:
            entity:
              filter:
                - integration: ttlock
                  domain: switch
        last_operator_sensor:
          name: Last operator sensor (optional)
          description: Only needed together with a trusted-operator list.
          default: ""
          selector:
            entity:
              filter:
                - integration: ttlock
                  domain: sensor
        passage_mode_sensor:
          name: Passage mode sensor (optional)
          description: While passage mode is on (guest hours) the timer never locks the door.
          default: ""
          selector:
            entity:
              filter:
                - integration: ttlock
                  domain: binary_sensor
    timers:
      name: Timers
      icon: mdi:timer-sand
      input:
        vacancy_helper:
          name: Vacancy timer helper
          description: >-
            An input_datetime helper WITH date and time, one per lock. It stores "not before" and
            survives restarts. Create it under Settings → Devices & services → Helpers.
          selector:
            entity:
              filter:
                - domain: input_datetime
        motion_delay:
          name: Motion delay (M)
          description: Minutes without living-area motion before the door settles.
          default: 45
          selector:
            number:
              min: 1
              max: 720
              unit_of_measurement: min
              mode: box
        presence_delay:
          name: Presence delay (P)
          description: Minutes after a presence change before the door may settle.
          default: 20
          selector:
            number:
              min: 1
              max: 720
              unit_of_measurement: min
              mode: box
        armed_seconds:
          name: Armed auto-lock seconds (S)
          description: Auto-lock countdown used when the cabin is vacant.
          default: 30
          selector:
            number:
              min: 1
              max: 60
              unit_of_measurement: s
              mode: box
    resting_policy:
      name: Resting policy
      icon: mdi:sleep
      description: What the door does when the household is here but quiet.
      input:
        resting_lock_bolt:
          name: Lock the bolt when resting
          default: true
          selector:
            boolean: {}
        resting_arm_auto_lock:
          name: Arm auto-lock when resting
          default: true
          selector:
            boolean: {}
        resting_armed_seconds:
          name: Resting auto-lock seconds
          description: Countdown used when resting arms auto-lock. Never re-configures an already armed lock.
          default: 30
          selector:
            number:
              min: 1
              max: 60
              unit_of_measurement: s
              mode: box
    occupancy:
      name: Occupancy sources
      icon: mdi:motion-sensor
      input:
        motion_sensors:
          name: Activity motion sensors
          description: Living-area sensors. Motion here keeps the door free.
          default: []
          selector:
            entity:
              multiple: true
              filter:
                - domain: binary_sensor
                  device_class:
                    - motion
                    - occupancy
        resting_sensors:
          name: Resting motion sensors
          description: Bedroom sensors. Motion here never keeps the door free; it only marks the cabin as resting rather than vacant.
          default: []
          selector:
            entity:
              multiple: true
              filter:
                - domain: binary_sensor
                  device_class:
                    - motion
                    - occupancy
        presence_entities:
          name: Presence
          description: Persons or device trackers. "home" postpones settling and marks the cabin as resting rather than vacant. Never keeps the door unlocked.
          default: []
          selector:
            entity:
              multiple: true
              filter:
                - domain:
                    - person
                    - device_tracker
        trusted_operators:
          name: Trusted operators (optional)
          description: Operator names as shown by the last-operator sensor. Empty means anyone who unlocks occupies the cabin.
          default: []
          selector:
            text:
              multiple: true
    night:
      name: Night
      icon: mdi:weather-night
      input:
        night_enabled:
          name: Lock at night
          default: false
          selector:
            boolean: {}
        night_time:
          name: Night time
          default: "23:00:00"
          selector:
            time: {}
    safety:
      name: Safety
      icon: mdi:shield-lock
      input:
        stale_countdown_unlock:
          name: Undo a stale auto-lock countdown
          description: If the lock still auto-locks right after someone occupied the cabin, unlock it once.
          default: true
          selector:
            boolean: {}
        stale_countdown_window:
          name: Stale countdown window
          default: 90
          selector:
            number:
              min: 10
              max: 300
              unit_of_measurement: s
              mode: box
        notify_actions:
          name: On failure
          description: Actions to run when the door could not be locked or armed (in addition to a persistent notification).
          default: []
          selector:
            action: {}
    state_output:
      name: State output (optional)
      icon: mdi:home-switch
      input:
        state_select:
          name: State helper
          description: An input_select with exactly the options occupied, resting and vacant. Written by this automation, never read by it.
          default: ""
          selector:
            entity:
              filter:
                - domain: input_select
        on_occupied:
          name: When the cabin becomes occupied
          default: []
          selector:
            action: {}
        on_resting:
          name: When the cabin becomes resting
          default: []
          selector:
            action: {}
        on_vacant:
          name: When the cabin becomes vacant
          default: []
          selector:
            action: {}

mode: queued
max: 100
max_exceeded: silent

trigger_variables:
  passage_mode_sensor: !input passage_mode_sensor

triggers:
  - id: activity_motion
    trigger: state
    entity_id: !input motion_sensors
    from: ["on", "off"]
    to: ["on", "off"]
  - id: activity_presence
    trigger: state
    entity_id: !input presence_entities
    not_from: ["unavailable", "unknown"]
    not_to: ["unavailable", "unknown"]
  - id: lock_event
    trigger: state
    entity_id: !input last_trigger_sensor
    not_from: ["unavailable", "unknown"]
    not_to: ["unavailable", "unknown"]
  - id: unlock_state
    trigger: state
    entity_id: !input lock_entity
    from: ["locked", "locking", "unlocking"]
    to: "unlocked"
  - id: vacancy_time
    trigger: time
    at: !input vacancy_helper
  - id: passage_off
    trigger: template
    value_template: "{{ passage_mode_sensor != '' and is_state(passage_mode_sensor, 'off') }}"
  - id: lock_available
    trigger: state
    entity_id: !input lock_entity
    from: "unavailable"
  - id: safety_net
    trigger: time_pattern
    minutes: "/5"
  - id: ha_start
    trigger: homeassistant
    event: start
  - id: night
    trigger: time
    at: !input night_time

variables:
  lock_entity: !input lock_entity
  last_trigger_sensor: !input last_trigger_sensor
  auto_lock_switch: !input auto_lock_switch
  last_operator_sensor: !input last_operator_sensor
  vacancy_helper: !input vacancy_helper
  motion_delay: !input motion_delay
  presence_delay: !input presence_delay
  armed_seconds: !input armed_seconds
  resting_lock_bolt: !input resting_lock_bolt
  resting_arm_auto_lock: !input resting_arm_auto_lock
  resting_armed_seconds: !input resting_armed_seconds
  motion_sensors: !input motion_sensors
  resting_sensors: !input resting_sensors
  presence_entities: !input presence_entities
  trusted_operators: !input trusted_operators
  night_enabled: !input night_enabled
  stale_countdown_unlock: !input stale_countdown_unlock
  stale_countdown_window: !input stale_countdown_window
  state_select: !input state_select
  notification_id: "cabin_auto_lock_{{ lock_entity | replace('.', '_') }}"
  # -- classification (Task 2) --
  # -- live facts (Task 3, Task 6) --

actions:
  # ---- timer push (R0, R1) — Task 3 ----
  # ---- occupy (R1) — Task 2 ----
  # ---- policy selection (R2, R3, R4) — Task 5, 6, 7 ----
  # ---- explicit lock ends activity (R2) — Task 5 ----
  # ---- apply policy — Task 5 ----
  # ---- stale countdown (R5) — Task 8 ----
  # ---- label — Task 9 ----
  - variables:
      skeleton: true
```

(The `variables` action is a no-op stand-in so `actions:` is a valid non-empty list; Task 2 deletes it.)

- [ ] **Step 8: Run the harness test to see it pass**

Run: `uv run pytest tests/test_00_harness.py -q`
Expected: `2 passed`. If the first test errors with "Lingering timer", the `policy` fixture teardown is not running: check that the fixture is `async` with `yield` as written.

- [ ] **Step 9: Add CI**

`.github/workflows/test.yml`:

```yaml
name: tests
on:
  push:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.14"
      - run: uv sync --group dev
      - run: uv run pytest -q
      - run: uv run python scripts/mutation_check.py
        if: hashFiles('scripts/mutation_check.py') != ''
```

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml .python-version uv.lock .gitignore .github/workflows/test.yml tests automation/cabin_auto_lock.yaml
git commit -m "test(cabin-auto-lock): real-HA test harness, fake TTLock, blueprint skeleton"
```

---

### Task 2: Event classification and R1 occupy (string trigger)

**Files:**
- Modify: `automation/cabin_auto_lock.yaml` (variables + `occupy` block; delete the `- variables: skeleton: true` stand-in)
- Test: `tests/test_01_classification.py`

**Interfaces:**
- Produces variables `event_is_fresh`, `event_value`, `previous_value`, `is_unlock_event`, `is_explicit_lock`, `is_auto_lock`, `previous_was_unlock`, `occupy_requested`, `operator_trusted` (all render to Python booleans/strings, never the strings "true"/"false").

- [ ] **Step 1: Write the failing tests (T1, T3, T15, T16)**

`tests/test_01_classification.py`:

```python
"""T1 classification over the whole event table, T3 trusted list, T15 stale values, T16 entity-added events."""

import pytest
from homeassistant.core import HomeAssistant

from .fakes import LOCK, OPERATOR, SWITCH, TRIGGER, FakeTTLock
from .ttlock_events import EVENTS, expected_class


@pytest.mark.parametrize("event_id", sorted(EVENTS))
async def test_t1_unlock_strings_occupy_from_vacant(hass: HomeAssistant, fake: FakeTTLock, policy, event_id) -> None:
    await policy()
    fake.event(EVENTS[event_id][1], operator="Joachim")
    await hass.async_block_till_done()
    disarm = [c for c in fake.calls_of("ttlock.configure_autolock") if c["enabled"] is False]
    if expected_class(event_id) == "UNLOCK":
        assert disarm == [{"entity_id": [LOCK], "enabled": False}]
        assert hass.states.get(SWITCH).state == "off"
    else:
        assert disarm == []
        assert hass.states.get(SWITCH).state == "on"


async def test_t1_classification_is_case_insensitive(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    fake.event("UNLOCK BY PASSCODE")
    await hass.async_block_till_done()
    assert len(fake.calls_of("ttlock.configure_autolock")) == 1


async def test_t1_repeated_unlock_while_occupied_makes_no_call(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    fake.event("unlock by fingerprint")
    await hass.async_block_till_done()
    fake.reset_calls()
    fake.event("unlock by passcode")            # a different string, switch already off
    await hass.async_block_till_done()
    assert fake.calls == []


async def test_t3_trusted_list_denies_guest(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy(trusted_operators=["Joachim", " lukas "])
    fake.event("unlock by passcode", operator="Magnus")
    await hass.async_block_till_done()
    assert fake.calls == []
    fake.event("unlock by fingerprint", operator="Lukas")
    await hass.async_block_till_done()
    assert len(fake.calls_of("ttlock.configure_autolock")) == 1


async def test_t3_trusted_list_without_operator_sensor_denies_everyone(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy(trusted_operators=["Joachim"], last_operator_sensor="")
    fake.event("unlock by fingerprint", operator="Joachim")
    await hass.async_block_till_done()
    assert fake.calls == []


async def test_t15_value_returning_from_unavailable_is_not_an_event(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    hass.states.async_set(TRIGGER, "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set(TRIGGER, "unlock by fingerprint")
    await hass.async_block_till_done()
    assert fake.calls == []


async def test_t16_entity_added_is_not_an_event(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    hass.states.async_remove(TRIGGER)
    await hass.async_block_till_done()
    hass.states.async_set(TRIGGER, "unlock by fingerprint")      # old_state is None
    await hass.async_block_till_done()
    assert fake.calls == []
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_01_classification.py -q`
Expected: the 13 UNLOCK cases of T1 (of 42 parametrized events), the case-insensitivity test and the "Lukas" half of T3 FAIL (no `configure_autolock` call); the rest pass vacuously.

- [ ] **Step 3: Add the classification variables and the occupy block**

In `variables:`, replace the line `# -- classification (Task 2) --` with:

```yaml
  event_is_fresh: "{{ (trigger.from_state is not none and trigger.to_state is not none) if trigger.from_state is defined else true }}"
  event_value: >-
    {{ (trigger.to_state.state | lower) if (trigger.id == 'lock_event' and trigger.to_state is not none) else '' }}
  previous_value: >-
    {{ (trigger.from_state.state | lower) if (trigger.id == 'lock_event' and trigger.from_state is not none) else '' }}
  is_unlock_event: "{{ 'unlock' in event_value and not (event_value is search('fail|try to|expired|passage')) }}"
  is_explicit_lock: "{{ (event_value is match('^(lock by|lock with|locked via)')) and 'fail' not in event_value }}"
  is_auto_lock: "{{ event_value == 'auto lock' }}"
  previous_was_unlock: "{{ 'unlock' in previous_value and not (previous_value is search('fail|try to|expired|passage')) }}"
  occupy_requested: "{{ (trigger.id == 'lock_event' and is_unlock_event and event_is_fresh) or trigger.id == 'unlock_state' }}"
  operator_trusted: >-
    {{ (trusted_operators | count == 0)
       or (last_operator_sensor != ''
           and (states(last_operator_sensor) | lower | trim) in (trusted_operators | map('lower') | map('trim') | list)) }}
```

In `actions:`, delete the `- variables: skeleton: true` stand-in and replace the marker `# ---- occupy (R1) — Task 2 ----` with:

```yaml
  # ---- occupy (R1) ----
  - if:
      - condition: template
        value_template: "{{ occupy_requested and operator_trusted and is_state(auto_lock_switch, 'on') }}"
    then:
      - action: ttlock.configure_autolock
        target:
          entity_id: "{{ lock_entity }}"
        data:
          enabled: false
        continue_on_error: true
      - wait_template: "{{ is_state(auto_lock_switch, 'off') }}"
        timeout: "00:00:10"
        continue_on_timeout: true
      - if:
          - condition: template
            value_template: "{{ not wait.completed }}"
        then:
          - delay: "00:00:10"
          - action: ttlock.configure_autolock
            target:
              entity_id: "{{ lock_entity }}"
            data:
              enabled: false
            continue_on_error: true
```

Note the `unlock_state` half of `occupy_requested` is already present; Task 4 only adds its test.

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_01_classification.py tests/test_00_harness.py -q`
Expected: all pass (`50 passed`: 42 parametrized + 6 + 2 harness). If a parametrized case for `"Lock with QR code failed, the lock is double locked"` occupies, the `match` regex lost its anchor.

- [ ] **Step 5: Commit**

```bash
git add automation/cabin_auto_lock.yaml tests/test_01_classification.py
git commit -m "feat(cabin-auto-lock): classify lock events and occupy on any unlock (R1)"
```

---

### Task 3: Vacancy timer push (R0, R1)

**Files:**
- Modify: `automation/cabin_auto_lock.yaml` (live-fact variables `h_ts`, `now_ts`; timer-push block)
- Test: `tests/test_02_timer.py`

**Interfaces:**
- Produces variables `h_ts` (float, helper's `timestamp` attribute or 0), `now_ts` (float), `helper_ok` (bool). Produces the invariant: the helper only ever moves forward, by M after activity or unlock and by P after a presence change.

- [ ] **Step 1: Write the failing tests (T5, T22, T16-push)**

`tests/test_02_timer.py`:

```python
"""T5 the helper is the max of motion+M, presence+P, unlock+M; T22 resting sensors never push; T16 boot transitions never push."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import BEDROOM, JOACHIM, KITCHEN, LIVING, helper_ts
from .fakes import FakeTTLock

MIN = 60


async def test_t5_motion_pushes_by_m(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    t0 = dt_util.utcnow().timestamp()
    hass.states.async_set(KITCHEN, "on")
    await hass.async_block_till_done()
    assert helper_ts(hass) == pytest.approx(t0 + 45 * MIN, abs=1)


async def test_t5_helper_is_monotonic_max(hass: HomeAssistant, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    t0 = dt_util.utcnow().timestamp()
    hass.states.async_set(LIVING, "on")
    await hass.async_block_till_done()                     # H = t0 + 45
    await clock(10 * MIN)
    hass.states.async_set(JOACHIM, "home")
    await hass.async_block_till_done()                     # candidate t0 + 30 < H: unchanged
    assert helper_ts(hass) == pytest.approx(t0 + 45 * MIN, abs=1)
    await clock(20 * MIN)                                  # now t0 + 30
    hass.states.async_set(JOACHIM, "not_home")
    await hass.async_block_till_done()                     # candidate t0 + 50 > H
    assert helper_ts(hass) == pytest.approx(t0 + 50 * MIN, abs=1)


async def test_t5_unlock_pushes_by_m(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    t0 = dt_util.utcnow().timestamp()
    fake.event("unlock by passcode", operator="Lukas")
    await hass.async_block_till_done()
    assert helper_ts(hass) == pytest.approx(t0 + 45 * MIN, abs=1)


async def test_t5_motion_off_also_pushes(hass: HomeAssistant, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    hass.states.async_set(KITCHEN, "on")
    await hass.async_block_till_done()
    await clock(5 * MIN)
    t1 = dt_util.utcnow().timestamp()
    hass.states.async_set(KITCHEN, "off")
    await hass.async_block_till_done()
    assert helper_ts(hass) == pytest.approx(t1 + 45 * MIN, abs=1)


async def test_t22_resting_sensor_never_pushes(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    before = helper_ts(hass)
    hass.states.async_set(BEDROOM, "on")
    await hass.async_block_till_done()
    hass.states.async_set(BEDROOM, "off")
    await hass.async_block_till_done()
    assert helper_ts(hass) == before


async def test_t16_boot_and_outage_transitions_never_push(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    before = helper_ts(hass)
    hass.states.async_set(KITCHEN, "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN, "off")                  # unavailable -> off
    await hass.async_block_till_done()
    hass.states.async_remove(JOACHIM)
    await hass.async_block_till_done()
    hass.states.async_set(JOACHIM, "home")                 # None -> home (entity added)
    await hass.async_block_till_done()
    hass.states.async_set(JOACHIM, "unknown")
    await hass.async_block_till_done()
    hass.states.async_set(JOACHIM, "not_home")             # unknown -> not_home
    await hass.async_block_till_done()
    assert helper_ts(hass) == before
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_02_timer.py -q`
Expected: the four T5 tests FAIL (helper unchanged); T22 and T16 pass vacuously.

- [ ] **Step 3: Add the live-fact variables and the push block**

In `variables:`, replace `# -- live facts (Task 3, Task 6) --` with:

```yaml
  h_ts: "{{ state_attr(vacancy_helper, 'timestamp') | float(0) }}"
  helper_ok: "{{ state_attr(vacancy_helper, 'timestamp') is not none }}"
  now_ts: "{{ now().timestamp() }}"
  # -- live facts (Task 6) --
```

In `actions:`, replace `# ---- timer push (R0, R1) — Task 3 ----` with:

```yaml
  # ---- timer push (R0, R1) ----
  - variables:
      push_delay_minutes: >-
        {%- if trigger.id == 'activity_motion' or occupy_requested -%}{{ motion_delay }}
        {%- elif trigger.id == 'activity_presence' -%}{{ presence_delay }}
        {%- else -%}0{%- endif -%}
      push_ts: "{{ now_ts + (push_delay_minutes | float(0)) * 60 }}"
  - if:
      - condition: template
        value_template: "{{ (push_delay_minutes | float(0)) > 0 and event_is_fresh and push_ts > h_ts }}"
    then:
      - action: input_datetime.set_datetime
        target:
          entity_id: "{{ vacancy_helper }}"
        data:
          timestamp: "{{ push_ts }}"
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_02_timer.py tests/test_01_classification.py -q`
Expected: all pass. If T16 fails on the `unknown -> not_home` step, the `activity_presence` trigger lost `not_from`.

- [ ] **Step 5: Commit**

```bash
git add automation/cabin_auto_lock.yaml tests/test_02_timer.py
git commit -m "feat(cabin-auto-lock): restart-proof vacancy timer as a monotonic max (R0)"
```

---

### Task 4: R1 via the lock entity turning unlocked (T2)

**Files:**
- Test: `tests/test_03_occupy.py`
- Modify: `automation/cabin_auto_lock.yaml` only if the test fails (the `unlock_state` trigger and `occupy_requested` already exist).

**Interfaces:**
- Consumes `occupy_requested` (Task 2). Produces nothing new; pins D11.

- [ ] **Step 1: Write the tests (T2, plus the double-trigger dedupe)**

`tests/test_03_occupy.py`:

```python
"""T2 an unlock that repeats the previous string is still seen through the lock entity (D11)."""

from homeassistant.core import HomeAssistant

from .fakes import LOCK, SWITCH, FakeTTLock


async def test_t2_repeated_string_occupies_via_lock_entity(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    # Yesterday's last event was a fingerprint unlock; HA locked the door itself later (no echo).
    fake.event("unlock by fingerprint", operator="Joachim")
    await hass.async_block_till_done()
    fake.set_switch(on=True, seconds=30)               # HA re-armed it later (vacant), string unchanged
    fake.set_lock_state("locked")
    await hass.async_block_till_done()
    fake.reset_calls()
    fake.unlocked_by("unlock by fingerprint", "Joachim")   # same string: no sensor event, lock entity changes
    await hass.async_block_till_done()
    assert fake.calls_of("ttlock.configure_autolock") == [{"entity_id": [LOCK], "enabled": False}]
    assert hass.states.get(SWITCH).state == "off"


async def test_t2_one_physical_unlock_makes_one_call(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    fake.unlocked_by("unlock by passcode", "Lukas")     # lock entity AND string change: two triggers
    await hass.async_block_till_done()
    assert len(fake.calls_of("ttlock.configure_autolock")) == 1


async def test_t2_lock_entity_unlock_respects_trusted_list(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy(trusted_operators=["Joachim"])
    fake.unlocked_by("unlock by passcode", "Magnus")
    await hass.async_block_till_done()
    assert fake.calls == []
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/test_03_occupy.py -q`
Expected: `3 passed`. If the first test fails, the `unlock_state` trigger's `from:` list is wrong; if the second reports 2 calls, queued mode is not in effect (check `mode: queued`).

- [ ] **Step 3: Commit**

```bash
git add tests/test_03_occupy.py automation/cabin_auto_lock.yaml
git commit -m "test(cabin-auto-lock): occupy via lock entity when the event string repeats (T2)"
```

---

### Task 5: Explicit lock (R2), the policy engine, retries and notifications

**Files:**
- Modify: `automation/cabin_auto_lock.yaml` (variables `event_marker`; blocks `policy selection`, `explicit lock ends activity`, `apply policy`)
- Test: `tests/test_04_explicit_lock.py`, `tests/test_09_failures.py`

**Interfaces:**
- Produces the `policy` variable (`vacant` | `resting` | `none`), the apply block driven by `want_lock`, `want_arm`, `want_seconds`, `need_lock`, `need_arm`, `secure_failed`, and the notification id `cabin_auto_lock_<lock_entity with . replaced by _>`. Task 6 and 7 extend the `policy` expression; the apply block is shared and not changed again.
- Consumes `h_ts`, `now_ts` (Task 3), `is_explicit_lock`, `event_is_fresh` (Task 2).

- [ ] **Step 1: Write the failing tests (T4, T14, T27-part, T12)**

`tests/test_04_explicit_lock.py`:

```python
"""T4 explicit lock strings vacate now; T14 HA's own lock echo is idempotent; T27 explicit lock ends the activity window."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import KITCHEN, helper_ts
from .fakes import LOCK, SWITCH, FakeTTLock
from .ttlock_events import EVENTS, expected_class


async def occupied(hass: HomeAssistant, fake: FakeTTLock) -> None:
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "off"
    fake.reset_calls()


@pytest.mark.parametrize("event_id", sorted(EVENTS))
async def test_t4_only_explicit_lock_strings_vacate(hass: HomeAssistant, fake: FakeTTLock, policy, event_id) -> None:
    await policy()
    await occupied(hass, fake)
    fake.event(EVENTS[event_id][1])
    await hass.async_block_till_done()
    if expected_class(event_id) == "EXPLICIT_LOCK":
        assert fake.calls_of("lock.lock") == [{"entity_id": [LOCK]}]
        assert fake.calls_of("ttlock.configure_autolock") == [{"entity_id": [LOCK], "enabled": True, "seconds": 30}]
        assert hass.states.get(LOCK).state == "locked"
        assert hass.states.get(SWITCH).attributes["seconds"] == 30
    else:
        assert fake.calls_of("lock.lock") == []
        assert [c for c in fake.calls_of("ttlock.configure_autolock") if c["enabled"] is True] == []


async def test_t4_explicit_lock_when_already_locked_only_arms(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    await occupied(hass, fake)
    fake.set_lock_state("locked")                         # someone locked from inside; auto-lock still off
    await hass.async_block_till_done()
    fake.reset_calls()
    fake.event("lock by app")
    await hass.async_block_till_done()
    assert fake.calls_of("lock.lock") == []
    assert fake.calls_of("ttlock.configure_autolock") == [{"entity_id": [LOCK], "enabled": True, "seconds": 30}]


async def test_t14_echo_of_own_lock_is_idempotent(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    await occupied(hass, fake)
    fake.event("lock by lock key")
    await hass.async_block_till_done()
    fake.reset_calls()
    fake.event("lock by app")                             # the cloud echoing HA's own lock command
    await hass.async_block_till_done()
    assert fake.calls == []


async def test_t27_explicit_lock_sets_helper_to_now(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    await occupied(hass, fake)
    hass.states.async_set(KITCHEN, "on")
    await hass.async_block_till_done()
    assert helper_ts(hass) > dt_util.utcnow().timestamp() + 40 * 60
    fake.event("lock by passcode")
    await hass.async_block_till_done()
    assert helper_ts(hass) == pytest.approx(dt_util.utcnow().timestamp(), abs=1)
```

`tests/test_09_failures.py`:

```python
"""T12 one guarded retry after 10 s, then a persistent notification plus the notify actions; success dismisses."""

from homeassistant.core import HomeAssistant

from .fakes import LOCK, SWITCH, FakeTTLock


async def occupied(hass: HomeAssistant, fake: FakeTTLock) -> None:
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    fake.reset_calls()


async def test_t12_one_failure_is_retried_silently(hass: HomeAssistant, fake: FakeTTLock, policy, clock, notifications, hooks) -> None:
    await policy()
    await occupied(hass, fake)
    fake.fail("lock.lock", times=1)
    fake.event("lock by lock key")
    await clock(25, step=5)                               # 10 s wait timeout + 10 s pause + retry
    assert len(fake.calls_of("lock.lock")) == 2
    assert hass.states.get(LOCK).state == "locked"
    assert hass.states.get(SWITCH).state == "on"
    assert notifications["create"] == []
    assert hooks["notify"] == []


async def test_t12_two_failures_notify_and_run_notify_actions(hass: HomeAssistant, fake: FakeTTLock, policy, clock, notifications, hooks) -> None:
    await policy()
    await occupied(hass, fake)
    fake.fail("lock.lock", times=2)
    fake.event("lock by lock key")
    await clock(40, step=5)
    assert len(fake.calls_of("lock.lock")) == 2
    assert len(notifications["create"]) == 1
    assert notifications["create"][0].data["notification_id"] == "cabin_auto_lock_lock_hytta"
    assert len(hooks["notify"]) == 1


async def test_t12_later_success_dismisses(hass: HomeAssistant, fake: FakeTTLock, policy, clock, notifications) -> None:
    await policy()
    await occupied(hass, fake)
    fake.fail("lock.lock", times=2)
    fake.event("lock by lock key")
    await clock(40, step=5)
    assert len(notifications["create"]) == 1
    fake.event("lock by app")                             # try again, lock now healthy
    await clock(5, step=5)
    assert hass.states.get(LOCK).state == "locked"
    assert [c.data["notification_id"] for c in notifications["dismiss"]] == ["cabin_auto_lock_lock_hytta"]


async def test_t12_retry_is_skipped_when_a_newer_event_arrived(hass: HomeAssistant, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    await occupied(hass, fake)
    fake.fail("lock.lock", times=1)
    fake.event("lock by lock key")
    await clock(12, step=4)                               # first attempt failed, waiting for the retry
    fake.unlocked_by("unlock by fingerprint", "Joachim") # newer lock event: the household came back
    await clock(30, step=5)
    assert len(fake.calls_of("lock.lock")) == 1           # no retry
    assert hass.states.get(SWITCH).state == "off"         # occupy won


async def test_t12_occupy_failure_is_retried_without_notification(hass: HomeAssistant, fake: FakeTTLock, policy, clock, notifications) -> None:
    await policy()
    fake.fail("ttlock.configure_autolock", times=1)
    fake.event("unlock by fingerprint", operator="Joachim")
    await clock(25, step=5)
    assert len(fake.calls_of("ttlock.configure_autolock")) == 2
    assert hass.states.get(SWITCH).state == "off"
    assert notifications["create"] == []
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_04_explicit_lock.py tests/test_09_failures.py -q`
Expected: the EXPLICIT_LOCK cases of T4, `only_arms`, T27 and every T12 test except `occupy_failure` FAIL (no `lock.lock`, no notification). `occupy_failure` already passes (Task 2 built that retry).

- [ ] **Step 3: Add `event_marker`, policy selection, the explicit-lock reset and the apply block**

In `variables:`, add after `now_ts`:

```yaml
  event_marker: "{{ expand(last_trigger_sensor) | map(attribute='last_changed') | map('string') | first | default('') }}"
```

In `actions:`, replace `# ---- policy selection (R2, R3, R4) — Task 5, 6, 7 ----` with:

```yaml
  # ---- policy selection (R2, R3, R4) ----
  - variables:
      policy: >-
        {%- if trigger.id == 'lock_event' and is_explicit_lock and event_is_fresh -%}vacant
        {%- else -%}none{%- endif -%}
```

Replace `# ---- explicit lock ends activity (R2) — Task 5 ----` with:

```yaml
  # ---- explicit lock ends activity (R2) ----
  - if:
      - condition: template
        value_template: "{{ trigger.id == 'lock_event' and is_explicit_lock and event_is_fresh and h_ts > now_ts }}"
    then:
      - action: input_datetime.set_datetime
        target:
          entity_id: "{{ vacancy_helper }}"
        data:
          timestamp: "{{ now_ts }}"
```

Replace `# ---- apply policy — Task 5 ----` with:

```yaml
  # ---- apply policy (strengthen-only, idempotent, guarded retry) ----
  - if:
      - condition: template
        value_template: "{{ policy != 'none' }}"
    then:
      - variables:
          want_lock: "{{ policy == 'vacant' or resting_lock_bolt }}"
          want_arm: "{{ policy == 'vacant' or resting_arm_auto_lock }}"
          want_seconds: "{{ (armed_seconds if policy == 'vacant' else resting_armed_seconds) | int }}"
          switch_seconds: "{{ state_attr(auto_lock_switch, 'seconds') | int(0) }}"
          need_lock: "{{ want_lock and not is_state(lock_entity, 'locked') }}"
          need_arm: >-
            {{ want_arm and (is_state(auto_lock_switch, 'off')
               or (policy == 'vacant' and is_state(auto_lock_switch, 'on') and switch_seconds != want_seconds)) }}
      - choose:
          - conditions:
              - condition: template
                value_template: "{{ (need_lock or need_arm) and is_state(lock_entity, 'unavailable') }}"
            sequence:
              - action: persistent_notification.create
                data:
                  notification_id: "{{ notification_id }}"
                  title: "Cabin auto-lock: settle pending"
                  message: "{{ lock_entity }} is unreachable. The door will be secured as soon as the lock is back."
          - conditions:
              - condition: template
                value_template: "{{ need_lock or need_arm }}"
            sequence:
              - if:
                  - condition: template
                    value_template: "{{ need_lock }}"
                then:
                  - action: lock.lock
                    target:
                      entity_id: "{{ lock_entity }}"
                    continue_on_error: true
                  - wait_template: "{{ is_state(lock_entity, 'locked') }}"
                    timeout: "00:00:10"
                    continue_on_timeout: true
                  - if:
                      - condition: template
                        value_template: "{{ not wait.completed }}"
                    then:
                      - delay: "00:00:10"
                      # retry only if no newer lock event arrived during the wait and the pause
                      - if:
                          - condition: template
                            value_template: "{{ (expand(last_trigger_sensor) | map(attribute='last_changed') | map('string') | first | default('')) == event_marker }}"
                        then:
                          - action: lock.lock
                            target:
                              entity_id: "{{ lock_entity }}"
                            continue_on_error: true
                          - wait_template: "{{ is_state(lock_entity, 'locked') }}"
                            timeout: "00:00:10"
                            continue_on_timeout: true
              - if:
                  - condition: template
                    value_template: "{{ need_arm }}"
                then:
                  - action: ttlock.configure_autolock
                    target:
                      entity_id: "{{ lock_entity }}"
                    data:
                      enabled: true
                      seconds: "{{ want_seconds }}"
                    continue_on_error: true
                  - wait_template: "{{ is_state(auto_lock_switch, 'on') }}"
                    timeout: "00:00:10"
                    continue_on_timeout: true
                  - if:
                      - condition: template
                        value_template: "{{ not wait.completed }}"
                    then:
                      - delay: "00:00:10"
                      - if:
                          - condition: template
                            value_template: "{{ (expand(last_trigger_sensor) | map(attribute='last_changed') | map('string') | first | default('')) == event_marker }}"
                        then:
                          - action: ttlock.configure_autolock
                            target:
                              entity_id: "{{ lock_entity }}"
                            data:
                              enabled: true
                              seconds: "{{ want_seconds }}"
                            continue_on_error: true
                          - wait_template: "{{ is_state(auto_lock_switch, 'on') }}"
                            timeout: "00:00:10"
                            continue_on_timeout: true
              - variables:
                  superseded: "{{ (expand(last_trigger_sensor) | map(attribute='last_changed') | map('string') | first | default('')) != event_marker }}"
                  secure_failed: >-
                    {{ not superseded and ((need_lock and not is_state(lock_entity, 'locked'))
                       or (need_arm and not is_state(auto_lock_switch, 'on'))) }}
              - choose:
                  - conditions:
                      - condition: template
                        value_template: "{{ secure_failed }}"
                    sequence:
                      - action: persistent_notification.create
                        data:
                          notification_id: "{{ notification_id }}"
                          title: "Cabin auto-lock: could not secure the door"
                          message: >-
                            {{ lock_entity }} could not be {{ 'locked' if need_lock else 'armed' }} after two attempts.
                            Check the TTLock gateway and cloud.
                      - sequence: !input notify_actions
                  - conditions:
                      - condition: template
                        value_template: "{{ not superseded }}"
                    sequence:
                      - action: persistent_notification.dismiss
                        data:
                          notification_id: "{{ notification_id }}"
```

A superseded run (a newer lock event arrived while it was waiting) neither retries nor notifies: the newer event's own run decides.

> **Amended during execution (Task 5 review, rulings in the SDD ledger):** the shipped apply block differs from the
> snippet above in two ways. (1) The outer `choose` has a single `conditions:` branch (the "settle pending" notification)
> and a `default:` branch holding the lock/arm sub-blocks, the `superseded`/`secure_failed` variables and the final
> create-or-dismiss `choose`, so a stale failure notification is dismissed by any applied policy that ends secure.
> (2) The "settle pending" condition is `(need_lock or need_arm) and (is_state(lock_entity, 'unavailable') or
> is_state(auto_lock_switch, 'unavailable'))`, with the message "… or its auto-lock switch is unreachable …".
> `automation/cabin_auto_lock.yaml` on the branch is the reference.

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_04_explicit_lock.py tests/test_09_failures.py -q`
Expected: all pass. If `retry_is_skipped` sees 2 `lock.lock` calls, the guard compares against a marker captured after the event; make sure `event_marker` is in the run-level `variables:`, not inside the apply block. If `two_failures` shows 3 calls, a wait timeout is shorter than the `clock` step; use `step=5`.

- [ ] **Step 5: Run the whole suite and commit**

Run: `uv run pytest -q`
Expected: all green.

```bash
git add automation/cabin_auto_lock.yaml tests/test_04_explicit_lock.py tests/test_09_failures.py
git commit -m "feat(cabin-auto-lock): explicit lock vacates now; strengthen-only policy engine with guarded retry (R2)"
```

---

### Task 6: Settle on the timer (R3): policies, classification, grace, passage, unavailable lock

**Files:**
- Modify: `automation/cabin_auto_lock.yaml` (live-fact variables `activity_on`, `passage_on`, `persons_home`, `resting_recent`, `classification`, `automation_age`, `settle_trigger`, `settle_due`; `policy` expression)
- Test: `tests/test_05_settle.py`

**Interfaces:**
- Produces `classification` (`resting` | `vacant`), `settle_due` (bool). Consumed by Task 9's label.

- [ ] **Step 1: Write the failing tests (T6, T7, T8, T9, T13, T17, T18, T24, T25, T26, T28)**

`tests/test_05_settle.py`:

```python
"""R3: the timer settles the door; presence and the bedroom pick the policy; nothing ever weakens the lock."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import BEDROOM, JOACHIM, KITCHEN, LIVING, LUKAS, helper_ts, set_helper
from .fakes import LOCK, PASSAGE, SWITCH, FakeTTLock

MIN = 60
VACATE = [("lock.lock", {"entity_id": [LOCK]}),
          ("ttlock.configure_autolock", {"entity_id": [LOCK], "enabled": True, "seconds": 30})]


async def occupied_with_motion(hass: HomeAssistant, fake: FakeTTLock) -> float:
    """Arrive, trigger living-area motion, return the time of that motion."""
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN, "on")
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN, "off")
    await hass.async_block_till_done()
    fake.reset_calls()
    return dt_util.utcnow().timestamp()


async def test_t6_settles_at_h_and_not_before(hass: HomeAssistant, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    await occupied_with_motion(hass, fake)
    await clock(44 * MIN)
    assert fake.calls == []
    await clock(2 * MIN)
    assert fake.calls == VACATE
    assert hass.states.get(LOCK).state == "locked" and hass.states.get(SWITCH).state == "on"


async def test_t6_activity_at_h_postpones_until_next_off(hass: HomeAssistant, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    await occupied_with_motion(hass, fake)
    await clock(40 * MIN)
    hass.states.async_set(LIVING, "on")                   # H pushed to +45 from now
    await hass.async_block_till_done()
    await clock(10 * MIN)
    assert fake.calls == []
    hass.states.async_set(LIVING, "off")
    await hass.async_block_till_done()
    await clock(46 * MIN)
    assert fake.calls == VACATE


async def test_t18_default_policy_locks_and_arms_whoever_is_home(hass: HomeAssistant, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    hass.states.async_set(JOACHIM, "home")
    await hass.async_block_till_done()
    await occupied_with_motion(hass, fake)
    await clock(46 * MIN)
    assert fake.calls == VACATE


async def test_t24_resting_policy_off_off_leaves_door_free_then_vacates_when_phones_leave(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy(resting_lock_bolt=False, resting_arm_auto_lock=False)
    hass.states.async_set(JOACHIM, "home")
    await hass.async_block_till_done()
    await occupied_with_motion(hass, fake)
    await clock(50 * MIN)
    assert fake.calls == []                               # resting: nothing on the lock
    assert hass.states.get(LOCK).state == "unlocked"
    hass.states.async_set(JOACHIM, "not_home")            # H = now + 20
    await hass.async_block_till_done()
    await clock(19 * MIN)
    assert fake.calls == []
    await clock(2 * MIN)
    assert fake.calls == VACATE


async def test_t24_resting_policy_lock_only(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy(resting_arm_auto_lock=False)
    hass.states.async_set(LUKAS, "home")
    await hass.async_block_till_done()
    await occupied_with_motion(hass, fake)
    await clock(46 * MIN)
    assert fake.calls == [("lock.lock", {"entity_id": [LOCK]})]
    assert hass.states.get(SWITCH).state == "off"


async def test_t24_resting_policy_arm_only(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy(resting_lock_bolt=False)
    hass.states.async_set(LUKAS, "home")
    await hass.async_block_till_done()
    await occupied_with_motion(hass, fake)
    await clock(46 * MIN)
    assert fake.calls == [("ttlock.configure_autolock", {"entity_id": [LOCK], "enabled": True, "seconds": 30})]
    assert hass.states.get(LOCK).state == "unlocked"


async def test_t26_resting_arms_at_its_own_seconds_only_when_off(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy(resting_armed_seconds=60)
    hass.states.async_set(LUKAS, "home")
    await hass.async_block_till_done()
    await occupied_with_motion(hass, fake)
    await clock(46 * MIN)
    assert fake.calls_of("ttlock.configure_autolock") == [{"entity_id": [LOCK], "enabled": True, "seconds": 60}]


@pytest.mark.parametrize("resting_policy", [
    {"resting_lock_bolt": False, "resting_arm_auto_lock": False, "resting_armed_seconds": 60},
    {"resting_lock_bolt": True, "resting_arm_auto_lock": True, "resting_armed_seconds": 60},
])
async def test_t25_explicit_lock_is_never_weakened_by_a_resting_refresh(hass, fake: FakeTTLock, policy, clock, resting_policy) -> None:
    await policy(**resting_policy)
    hass.states.async_set(LUKAS, "home")
    await hass.async_block_till_done()
    await occupied_with_motion(hass, fake)
    fake.event("lock by lock key")                        # armed at 30, locked
    await hass.async_block_till_done()
    fake.reset_calls()
    await clock(60 * MIN)                                 # many refreshes while classified resting
    assert fake.calls == []
    assert hass.states.get(SWITCH).attributes["seconds"] == 30


async def test_t19_bedroom_motion_makes_it_resting_when_phones_are_invisible(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy(resting_lock_bolt=False, resting_arm_auto_lock=False)
    await occupied_with_motion(hass, fake)
    await clock(30 * MIN)
    hass.states.async_set(BEDROOM, "on")                  # going to bed, 15 min before H
    await hass.async_block_till_done()
    hass.states.async_set(BEDROOM, "off")
    await hass.async_block_till_done()
    await clock(16 * MIN)
    assert fake.calls == []                               # resting (do-nothing policy), not vacant


async def test_t20_vacant_needs_bedroom_quiet_for_m(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy(resting_lock_bolt=False, resting_arm_auto_lock=False)
    hass.states.async_set(BEDROOM, "on")
    await hass.async_block_till_done()
    hass.states.async_set(BEDROOM, "off")                 # bedroom quiet from now, BEFORE the last living-area motion
    await hass.async_block_till_done()
    await occupied_with_motion(hass, fake)                # H = kitchen-off + 45; bedroom quiet slightly longer
    await clock(46 * MIN)                                 # at H the bedroom has been quiet for M: vacant
    assert fake.calls == VACATE


async def test_t22_bedroom_on_at_h_does_not_postpone_the_settle(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    await occupied_with_motion(hass, fake)
    hass.states.async_set(BEDROOM, "on")                  # someone in bed, still moving at H
    await hass.async_block_till_done()
    await clock(46 * MIN)
    assert fake.calls == VACATE                           # resting policy (default lock + arm), not postponed


async def test_t7_motion_only_configuration(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy(presence_entities=[], resting_sensors=[])
    await occupied_with_motion(hass, fake)
    await clock(46 * MIN)
    assert fake.calls == VACATE


async def test_t7_presence_only_configuration(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy(motion_sensors=[], resting_sensors=[])
    hass.states.async_set(JOACHIM, "home")
    await hass.async_block_till_done()
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    fake.reset_calls()
    await clock(50 * MIN)                                 # H passed (unlock + 45): resting (Joachim home) = lock + arm
    assert fake.calls == VACATE
    fake.reset_calls()
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    hass.states.async_set(JOACHIM, "not_home")
    await hass.async_block_till_done()
    fake.reset_calls()
    await clock(46 * MIN)
    assert fake.calls == VACATE


async def test_t8_unavailable_sensors_count_as_inactive(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    await occupied_with_motion(hass, fake)
    hass.states.async_set(KITCHEN, "unavailable")
    hass.states.async_set(LIVING, "unavailable")
    hass.states.async_set(BEDROOM, "unavailable")
    hass.states.async_set(JOACHIM, "unavailable")
    await hass.async_block_till_done()
    await clock(46 * MIN)
    assert fake.calls == VACATE


async def test_t9_passage_mode_suppresses_the_timer_until_the_window_ends(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    fake.set_passage(True)
    await hass.async_block_till_done()
    await occupied_with_motion(hass, fake)
    await clock(60 * MIN)
    assert fake.calls == []
    fake.set_passage(False)
    await hass.async_block_till_done()
    assert fake.calls == VACATE                           # locks at window end, no extra delay


async def test_t13_start_grace_then_safety_net(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy(grace=False)
    fake.set_lock_state("unlocked")                       # occupied before the restart
    fake.set_switch(on=False)
    await hass.async_block_till_done()                    # the unlock trigger's own run (it pushes H) completes here
    await set_helper(hass, dt_util.utcnow().timestamp() - 3600)   # ...and the restored helper says H is long past
    await hass.async_block_till_done()
    fake.reset_calls()
    await clock(110)
    assert fake.calls == []                               # inside the 2-minute grace
    await clock(7 * MIN)                                  # a safety-net tick after the grace
    assert fake.calls == VACATE


async def test_t17_lock_unavailable_at_h_notifies_then_settles_when_back(hass, fake: FakeTTLock, policy, clock, notifications) -> None:
    await policy()
    await occupied_with_motion(hass, fake)
    fake.set_lock_state("unavailable")
    await hass.async_block_till_done()
    await clock(46 * MIN)
    assert fake.calls == []
    assert notifications["create"][0].data["title"] == "Cabin auto-lock: settle pending"
    fake.set_lock_state("unlocked")                       # gateway back
    await hass.async_block_till_done()
    assert fake.calls == VACATE
    assert len(notifications["dismiss"]) == 1


async def test_t28_missing_helper_never_settles(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    await occupied_with_motion(hass, fake)
    hass.states.async_remove("input_datetime.hytta_vacancy")
    await hass.async_block_till_done()
    await clock(60 * MIN)
    assert fake.calls == []
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_05_settle.py -q`
Expected: every test that expects `VACATE` FAILS (no calls); the do-nothing ones pass vacuously.

- [ ] **Step 3: Add the settle facts and extend the policy expression**

In `variables:`, replace `# -- live facts (Task 6) --` with:

```yaml
  activity_on: "{{ expand(motion_sensors) | selectattr('state', 'eq', 'on') | list | count > 0 }}"
  passage_on: "{{ passage_mode_sensor != '' and is_state(passage_mode_sensor, 'on') }}"
  persons_home: "{{ expand(presence_entities) | selectattr('state', 'eq', 'home') | list | count > 0 }}"
  resting_recent: >-
    {%- set ns = namespace(recent=false) -%}
    {%- for s in expand(resting_sensors) -%}
      {%- if s.state == 'on' or (s.state == 'off' and (now() - s.last_changed).total_seconds() < motion_delay * 60) -%}
        {%- set ns.recent = true -%}
      {%- endif -%}
    {%- endfor -%}
    {{ ns.recent }}
  classification: "{{ 'resting' if (persons_home or resting_recent) else 'vacant' }}"
  automation_age: "{{ (now() - as_datetime(this.last_changed)).total_seconds() }}"
  settle_trigger: "{{ trigger.id in ['vacancy_time', 'passage_off', 'lock_available', 'safety_net'] }}"
  settle_due: >-
    {{ settle_trigger and helper_ok and now_ts >= h_ts and not activity_on and not passage_on
       and automation_age >= 120 }}
```

Replace the `policy` variable block (from Task 5) with:

```yaml
  - variables:
      policy: >-
        {%- if trigger.id == 'lock_event' and is_explicit_lock and event_is_fresh -%}vacant
        {%- elif settle_due -%}{{ classification }}
        {%- else -%}none{%- endif -%}
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run pytest tests/test_05_settle.py -q`
Expected: all pass. Diagnostics: T6 locking at 44 min → the `time` trigger fired on the helper's *first* value; make sure `occupied_with_motion` ran after `policy()` cleared calls. T13 locking inside the grace → `this.last_changed` is being parsed without `as_datetime`. T25 showing a `configure_autolock` with 60 → `need_arm` re-configures an armed lock.

- [ ] **Step 5: Full suite, commit**

Run: `uv run pytest -q`
Expected: green.

```bash
git add automation/cabin_auto_lock.yaml tests/test_05_settle.py
git commit -m "feat(cabin-auto-lock): settle on the vacancy timer with resting/vacant policies (R3)"
```

---

### Task 7: Night lock (R4)

**Files:**
- Modify: `automation/cabin_auto_lock.yaml` (`policy` expression)
- Test: `tests/test_06_night.py`

**Interfaces:**
- Consumes `night_enabled`, the `night` trigger, the apply block.

- [ ] **Step 1: Write the failing test (T10)**

`tests/test_06_night.py`:

```python
"""T10 night beats motion, presence and passage mode; off by default."""

from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import KITCHEN
from .fakes import LOCK, FakeTTLock

VACATE = [("lock.lock", {"entity_id": [LOCK]}),
          ("ttlock.configure_autolock", {"entity_id": [LOCK], "enabled": True, "seconds": 30})]


async def busy_evening(hass: HomeAssistant, fake: FakeTTLock) -> None:
    """Occupied, passage mode on, motion going: everything that would normally keep the door free."""
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    fake.set_passage(True)
    hass.states.async_set(KITCHEN, "on")
    await hass.async_block_till_done()
    fake.reset_calls()


async def test_t10_night_vacates_despite_everything(hass, fake: FakeTTLock, policy, clock, freezer) -> None:
    freezer.move_to(datetime(2026, 9, 16, 22, 50, tzinfo=dt_util.DEFAULT_TIME_ZONE))   # 22:50 LOCAL (the harness zone is US/Pacific), before setup
    await policy(night_enabled=True, night_time="23:00:00")   # setup + 3 min grace -> 22:53
    await busy_evening(hass, fake)
    await clock(8 * 60, step=30)                     # crosses 23:00
    assert fake.calls == VACATE


async def test_t10_night_is_off_by_default(hass, fake: FakeTTLock, policy, clock, freezer) -> None:
    freezer.move_to(datetime(2026, 9, 16, 22, 50, tzinfo=dt_util.DEFAULT_TIME_ZONE))
    await policy()
    await busy_evening(hass, fake)
    await clock(8 * 60, step=30)
    assert fake.calls == []
```

- [ ] **Step 2: Run to see it fail**

Run: `uv run pytest tests/test_06_night.py -q`
Expected: the first test FAILS (no calls), the second passes.

- [ ] **Step 3: Add the night branch**

Replace the `policy` variable block with:

```yaml
  - variables:
      policy: >-
        {%- if trigger.id == 'lock_event' and is_explicit_lock and event_is_fresh -%}vacant
        {%- elif trigger.id == 'night' and night_enabled -%}vacant
        {%- elif settle_due -%}{{ classification }}
        {%- else -%}none{%- endif -%}
```

- [ ] **Step 4: Run, then commit**

Run: `uv run pytest tests/test_06_night.py -q` → `2 passed`.

```bash
git add automation/cabin_auto_lock.yaml tests/test_06_night.py
git commit -m "feat(cabin-auto-lock): optional night lock that beats passage mode (R4)"
```

---

### Task 8: Undo a stale countdown (R5)

**Files:**
- Modify: `automation/cabin_auto_lock.yaml` (R5 block)
- Test: `tests/test_07_stale_countdown.py`

**Interfaces:**
- Consumes `is_auto_lock`, `previous_was_unlock`, `stale_countdown_unlock`, `stale_countdown_window`.

- [ ] **Step 1: Write the failing tests (T11)**

`tests/test_07_stale_countdown.py`:

```python
"""T11 an Auto Lock right after occupying is undone once; not outside the window, not when armed, not when disabled."""

from homeassistant.core import HomeAssistant

from .fakes import LOCK, SWITCH, FakeTTLock


async def arrive(hass: HomeAssistant, fake: FakeTTLock) -> None:
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).state == "off"
    fake.reset_calls()


async def test_t11_auto_lock_inside_window_is_undone(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    await arrive(hass, fake)
    await clock(30, step=30)
    fake.auto_locked()
    await hass.async_block_till_done()
    assert fake.calls_of("lock.unlock") == [{"entity_id": [LOCK]}]
    assert hass.states.get(LOCK).state == "unlocked"


async def test_t11_auto_lock_outside_window_is_left_alone(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    await arrive(hass, fake)
    await clock(120, step=30)
    fake.auto_locked()
    await hass.async_block_till_done()
    assert fake.calls_of("lock.unlock") == []


async def test_t11_no_unlock_when_auto_lock_is_still_armed(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    fake.fail("ttlock.configure_autolock", times=4)      # a physical unlock starts two runs, each tries twice
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await clock(60, step=5)
    assert hass.states.get(SWITCH).state == "on"
    fake.reset_calls()
    fake.auto_locked()
    await hass.async_block_till_done()
    assert fake.calls_of("lock.unlock") == []


async def test_t11_feature_can_be_disabled(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy(stale_countdown_unlock=False)
    await arrive(hass, fake)
    await clock(30, step=30)
    fake.auto_locked()
    await hass.async_block_till_done()
    assert fake.calls_of("lock.unlock") == []


async def test_t11_undo_does_not_ping_pong(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    await arrive(hass, fake)
    await clock(30, step=30)
    fake.auto_locked()
    await hass.async_block_till_done()
    fake.event("unlock by gateway")                       # the cloud echoing HA's own unlock
    await hass.async_block_till_done()
    assert len(fake.calls_of("lock.unlock")) == 1
    assert fake.calls_of("ttlock.configure_autolock") == []   # already off
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_07_stale_countdown.py -q`
Expected: `inside_window` and `ping_pong` FAIL (no unlock); the others pass vacuously.

- [ ] **Step 3: Add the R5 block**

Replace `# ---- stale countdown (R5) — Task 8 ----` with:

```yaml
  # ---- stale countdown (R5) ----
  - if:
      - condition: template
        value_template: >-
          {{ trigger.id == 'lock_event' and is_auto_lock and event_is_fresh and stale_countdown_unlock
             and is_state(auto_lock_switch, 'off') and previous_was_unlock
             and (now() - trigger.from_state.last_changed).total_seconds() < stale_countdown_window }}
    then:
      - action: lock.unlock
        target:
          entity_id: "{{ lock_entity }}"
        continue_on_error: true
```

- [ ] **Step 4: Run, then commit**

Run: `uv run pytest tests/test_07_stale_countdown.py -q` → `5 passed`.

```bash
git add automation/cabin_auto_lock.yaml tests/test_07_stale_countdown.py
git commit -m "feat(cabin-auto-lock): undo a stale auto-lock countdown once after occupying (R5)"
```

---

### Task 9: State label and hooks

**Files:**
- Modify: `automation/cabin_auto_lock.yaml` (label block)
- Test: `tests/test_08_label.py`

**Interfaces:**
- Consumes `persons_home`, `resting_recent` (Task 6), `state_select`, the `on_*` inputs. The label is `occupied` while `now < H`, else `resting`/`vacant`.

- [ ] **Step 1: Write the failing tests (T19, T20, T21, T23, T27)**

`tests/test_08_label.py`:

```python
"""The optional input_select label: written on change with hooks, never read by the door rules."""

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import JOACHIM, KITCHEN, STATE, set_helper
from .fakes import LOCK, FakeTTLock

MIN = 60
VACATE = [("lock.lock", {"entity_id": [LOCK]}),
          ("ttlock.configure_autolock", {"entity_id": [LOCK], "enabled": True, "seconds": 30})]


async def test_t19_occupied_on_unlock_then_resting_at_h_with_hooks(hass, fake: FakeTTLock, policy, clock, hooks) -> None:
    await policy()
    hass.states.async_set(JOACHIM, "home")
    await hass.async_block_till_done()
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    assert hass.states.get(STATE).state == "occupied"
    assert len(hooks["on_occupied"]) == 1
    hass.states.async_set(KITCHEN, "on")
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN, "off")
    await hass.async_block_till_done()
    assert len(hooks["on_occupied"]) == 1                 # unchanged label: hook not repeated
    await clock(46 * MIN)
    assert hass.states.get(STATE).state == "resting"
    assert len(hooks["on_resting"]) == 1 and hooks["on_vacant"] == []


async def test_t20_label_flips_resting_to_vacant_on_the_safety_net_without_lock_calls(hass, fake: FakeTTLock, policy, clock, hooks) -> None:
    await policy()
    hass.states.async_set(JOACHIM, "home")
    await hass.async_block_till_done()
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    await clock(46 * MIN)
    assert hass.states.get(STATE).state == "resting"
    fake.reset_calls()
    hass.states.async_set(JOACHIM, "not_home")            # H = now + 20: label 'occupied' meanwhile
    await hass.async_block_till_done()
    assert hass.states.get(STATE).state == "occupied"
    await clock(26 * MIN)
    assert hass.states.get(STATE).state == "vacant"
    assert len(hooks["on_vacant"]) == 1
    assert fake.calls == []                               # door already locked + armed


async def test_t27_explicit_lock_settles_the_label_at_once(hass, fake: FakeTTLock, policy, clock, hooks) -> None:
    await policy()
    await clock(46 * MIN)                                 # bedroom quiet for more than M, so the class is vacant
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    assert hass.states.get(STATE).state == "occupied"
    fake.event("lock by lock key")
    await hass.async_block_till_done()
    assert hass.states.get(STATE).state == "vacant"


async def test_t21_no_state_helper_means_no_writes_and_no_hooks(hass, fake: FakeTTLock, policy, clock, hooks) -> None:
    await policy(state_select="")
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN, "on")
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN, "off")
    await hass.async_block_till_done()
    fake.reset_calls()
    await clock(46 * MIN)
    assert fake.calls == VACATE                           # door behaviour unchanged
    assert hass.states.get(STATE).state == "vacant"       # never touched (initial value)
    assert all(calls == [] for calls in hooks.values())


async def test_t23_door_rules_never_read_the_state_helper(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN, "on")
    await hass.async_block_till_done()
    hass.states.async_set(KITCHEN, "off")
    await hass.async_block_till_done()
    hass.states.async_set(STATE, "bogus")                 # hand-edited / wrong option
    await hass.async_block_till_done()
    fake.reset_calls()
    await clock(46 * MIN)
    assert fake.calls == VACATE
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_08_label.py -q`
Expected: T19, T20, T27 FAIL (label stays `vacant`); T21 and T23 pass.

- [ ] **Step 3: Add the label block**

Replace `# ---- label — Task 9 ----` with:

```yaml
  # ---- label (projection; never read by the door rules) ----
  - if:
      - condition: template
        value_template: "{{ state_select != '' }}"
    then:
      - variables:
          label: >-
            {%- set h = state_attr(vacancy_helper, 'timestamp') | float(0) -%}
            {{ 'occupied' if now().timestamp() < h else ('resting' if (persons_home or resting_recent) else 'vacant') }}
      - if:
          - condition: template
            value_template: "{{ not is_state(state_select, label) }}"
        then:
          - action: input_select.select_option
            target:
              entity_id: "{{ state_select }}"
            data:
              option: "{{ label }}"
            continue_on_error: true
          - choose:
              - conditions: "{{ label == 'occupied' }}"
                sequence: !input on_occupied
              - conditions: "{{ label == 'resting' }}"
                sequence: !input on_resting
              - conditions: "{{ label == 'vacant' }}"
                sequence: !input on_vacant
```

- [ ] **Step 4: Run, full suite, commit**

Run: `uv run pytest tests/test_08_label.py -q` → `5 passed`; then `uv run pytest -q` → green.

```bash
git add automation/cabin_auto_lock.yaml tests/test_08_label.py
git commit -m "feat(cabin-auto-lock): occupied/resting/vacant label with hooks as an output"
```

---

### Task 10: README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Replace the README with the blueprint documentation**

```markdown
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
```

- [ ] **Step 2: Check the import URL resolves**

Run: `curl -sI https://raw.githubusercontent.com/jmgiaever/home-assistant-blueprints/master/automation/cabin_auto_lock.yaml | head -1`
Expected before merge: `HTTP/2 404` (the branch is not merged yet); note that in the task report. After merge it must be `200`.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the cabin auto-lock blueprint"
```

---

### Task 11: Mutation check

**Files:**
- Create: `scripts/mutation_check.py`

**Interfaces:**
- Produces a script that exits 0 only if every mutation makes at least one test fail, and that fails loudly if a mutation's anchor text no longer exists in the blueprint (so the list cannot rot).

- [ ] **Step 1: Write the script**

`scripts/mutation_check.py`:

```python
#!/usr/bin/env python3
"""Mutation check for automation/cabin_auto_lock.yaml (spec §7).

Each mutation is a (name, anchor, replacement). The anchor must occur exactly once in the blueprint.
The mutated blueprint is written to a temp copy of the repo and the test suite must go RED.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
BLUEPRINT = REPO / "automation" / "cabin_auto_lock.yaml"

MUTATIONS: list[tuple[str, str, str]] = [
    ("drop the now >= H check",
     "settle_trigger and helper_ok and now_ts >= h_ts and",
     "settle_trigger and helper_ok and"),
    ("treat Auto Lock as an explicit lock",
     "is_explicit_lock: \"{{ (event_value is match('^(lock by|lock with|locked via)')) and 'fail' not in event_value }}\"",
     "is_explicit_lock: \"{{ (event_value is match('^(lock by|lock with|locked via|auto lock)')) and 'fail' not in event_value }}\""),
    ("drop the passage gate",
     "and not activity_on and not passage_on",
     "and not activity_on"),
    ("replace max with assignment in R0",
     "and event_is_fresh and push_ts > h_ts }}",
     "and event_is_fresh }}"),
    ("remove the lock-entity trigger from R1",
     "or trigger.id == 'unlock_state' }}",
     "or false }}"),
    ("remove the retry guard",
     "| first | default('')) == event_marker }}",
     "| first | default('')) == event_marker or true }}"),
    ("swap the resting/vacant classification",
     "classification: \"{{ 'resting' if (persons_home or resting_recent) else 'vacant' }}\"",
     "classification: \"{{ 'vacant' if (persons_home or resting_recent) else 'resting' }}\""),
    ("count resting sensors as activity",
     "activity_on: \"{{ expand(motion_sensors) |",
     "activity_on: \"{{ expand(motion_sensors + resting_sensors) |"),
    ("make a door rule depend on the state helper",
     "need_lock: \"{{ want_lock and not is_state(lock_entity, 'locked') }}\"",
     "need_lock: \"{{ want_lock and not is_state(lock_entity, 'locked') and is_state(state_select, 'vacant') }}\""),
    ("let rest() re-configure an armed lock",
     "{{ want_arm and (is_state(auto_lock_switch, 'off')",
     "{{ want_arm and (true"),
    ("ignore resting_lock_bolt",
     "want_lock: \"{{ policy == 'vacant' or resting_lock_bolt }}\"",
     "want_lock: \"{{ true }}\""),
    ("drop H := now in R2",
     "is_explicit_lock and event_is_fresh and h_ts > now_ts }}",
     "is_explicit_lock and event_is_fresh and false }}"),
]


def run_suite(repo_copy: pathlib.Path) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests"],
        cwd=repo_copy, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode


def main() -> int:
    original = BLUEPRINT.read_text()
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        copy = pathlib.Path(tmp) / "repo"
        shutil.copytree(REPO, copy, ignore=shutil.ignore_patterns(".git", ".venv", ".pytest_cache", "__pycache__"))
        target = copy / "automation" / "cabin_auto_lock.yaml"
        for name, anchor, replacement in MUTATIONS:
            count = original.count(anchor)
            if count == 0:
                problems.append(f"ANCHOR  {name}: not found in the blueprint (list drifted)")
                continue
            target.write_text(original.replace(anchor, replacement))   # replaces every occurrence (the retry guard appears twice)
            rc = run_suite(copy)
            status = "killed" if rc != 0 else "SURVIVED"
            print(f"{status:9s} {name}")
            if rc == 0:
                problems.append(f"SURVIVED {name}")
        target.write_text(original)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"all {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it**

Run: `uv run python scripts/mutation_check.py`
Expected: twelve lines starting with `killed`, then `all 12 mutations killed`, exit code 0. Any `SURVIVED` line means a test in the spec's list is missing its teeth: add the assertion to the relevant test file (the spec names which test covers which mutation) before continuing. Any `ANCHOR` line means the blueprint text drifted from the mutation list: fix the anchor.

- [ ] **Step 3: Commit**

```bash
git add scripts/mutation_check.py
git commit -m "test(cabin-auto-lock): mutation check for the rules that matter"
```

---

### Task 12: Deploy to Hytta

Executed with the user present. Requires the SSH master `~/.ssh/cm/lav918` (`ssh -O check -S ~/.ssh/cm/lav918 giaever-online@192.168.254.254` must print `Master running`; otherwise open one: `ssh -M -S ~/.ssh/cm/lav918 -o ControlPersist=12h -fN giaever-online@192.168.254.254`, one YubiKey touch). Shell alias used below:

```bash
CAB='ssh -S ~/.ssh/cm/lav918 giaever-online@192.168.254.254'
C=/var/snap/docker/common/var-lib-docker/volumes/homeassistant-config/_data
```

**Files:** none in the repo (device only). Branch must contain Tasks 1–11 merged into the worktree branch.

- [ ] **Step 1: Ask the user to create the two helpers in the HA UI**

Settings → Devices & services → Helpers → Create helper:
- "Date and/or time" → *Date and time* → name `Hytta vacancy not before` → entity id `input_datetime.hytta_vacancy_not_before`.
- "Dropdown" → name `Hytta state` → options `occupied`, `resting`, `vacant` → entity id `input_select.hytta_state`.

Verify from the device:

```bash
$CAB "sudo docker exec -i homeassistant python3 -" <<'EOF'
import json
dt = json.load(open('/config/.storage/input_datetime'))['data']['items']
sel = json.load(open('/config/.storage/input_select'))['data']['items']
print([ (i['id'], i.get('has_date'), i.get('has_time')) for i in dt ])
print([ (i['id'], i.get('options')) for i in sel ])
EOF
```

Expected: `('hytta_vacancy_not_before', True, True)` and `('hytta_state', ['occupied', 'resting', 'vacant'])`.

- [ ] **Step 2: Install the blueprint file**

```bash
$CAB "sudo mkdir -p $C/blueprints/automation/jmgiaever && sudo tee $C/blueprints/automation/jmgiaever/cabin_auto_lock.yaml >/dev/null" < automation/cabin_auto_lock.yaml
$CAB "sudo sha256sum $C/blueprints/automation/jmgiaever/cabin_auto_lock.yaml"; sha256sum automation/cabin_auto_lock.yaml
```

Expected: identical digests.

- [ ] **Step 3: Back up and rewrite automations.yaml**

```bash
$CAB "sudo cp $C/automations.yaml $C/automations.yaml.bak-$(date +%Y%m%d-%H%M)"
$CAB "sudo docker exec -i homeassistant python3 -" <<'EOF'
import yaml
path = '/config/automations.yaml'
items = yaml.safe_load(open(path)) or []
items = [a for a in items if a.get('id') != '1783694754963']          # "Turn back on auto-lock" (spec D17)
items = [a for a in items if a.get('id') != 'cabin_auto_lock_hytta']
items.append({
    'id': 'cabin_auto_lock_hytta',
    'alias': 'Hytta auto-lock policy',
    'description': 'Blueprint jmgiaever/cabin_auto_lock.yaml — spec 2026-09-16',
    'use_blueprint': {
        'path': 'jmgiaever/cabin_auto_lock.yaml',
        'input': {
            'lock_entity': 'lock.joachim_cabin',
            'last_trigger_sensor': 'sensor.joachim_cabin_last_trigger',
            'auto_lock_switch': 'switch.joachim_cabin_auto_lock',
            'last_operator_sensor': 'sensor.joachim_cabin_last_operator',
            'passage_mode_sensor': 'binary_sensor.joachim_cabin_passage_mode',
            'vacancy_helper': 'input_datetime.hytta_vacancy_not_before',
            'motion_delay': 45, 'presence_delay': 20, 'armed_seconds': 30,
            'resting_lock_bolt': True, 'resting_arm_auto_lock': True, 'resting_armed_seconds': 30,
            'motion_sensors': ['binary_sensor.kitchen_motion_detection_location_provided',
                               'binary_sensor.living_room_motion_detection_location_provided'],
            'resting_sensors': ['binary_sensor.bedroom_motion_sensor_motion_detection'],
            'presence_entities': ['person.lav918', 'person.lukas_alexander'],
            'trusted_operators': [],
            'night_enabled': False, 'night_time': '23:00:00',
            'stale_countdown_unlock': True, 'stale_countdown_window': 90,
            'notify_actions': [],
            'state_select': 'input_select.hytta_state',
            'on_occupied': [], 'on_resting': [], 'on_vacant': [],
        },
    },
})
yaml.safe_dump(items, open(path, 'w'), sort_keys=False, allow_unicode=True)
print(len(items), [a['alias'] for a in items])
EOF
```

Expected: `1 ['Hytta auto-lock policy']`.

- [ ] **Step 4: Validate the configuration**

```bash
$CAB "sudo docker exec homeassistant python3 -m homeassistant --script check_config -c /config" 2>&1 | tail -5
```

Expected: ends with `Successful config (partial)` and no line mentioning `cabin_auto_lock` or `blueprint`. If it reports an error, restore the backup (`sudo cp automations.yaml.bak-… automations.yaml`) and stop.

- [ ] **Step 5: Reload automations**

Ask the user to reload: Developer tools → YAML → *Automations*. (Fallback: `$CAB "sudo docker restart -t 60 homeassistant"`, ~60 s. Always pass `-t 60`: the default 10 s stop timeout kills HA before its final restore-state dump, so helpers come back up to 15 min stale — measured in acceptance A5/A5b.)

- [ ] **Step 6: Verify the instance is live**

```bash
$CAB "sudo docker exec -i homeassistant python3 -" <<'EOF'
import json
reg = json.load(open('/config/.storage/core.entity_registry'))['data']['entities']
print([e['entity_id'] for e in reg if e['entity_id'].startswith('automation.')])
EOF
$CAB "sudo grep -iE 'cabin_auto_lock|blueprint|hytta_auto_lock' $C/home-assistant.log | tail -5"
```

Expected: `automation.hytta_auto_lock_policy` present, `automation.turn_back_on_auto_lock` absent, log shows `Initialized trigger Hytta auto-lock policy` and no errors.

- [ ] **Step 7: Record**

Append the digest, date and the helper/entity ids to the acceptance report (Task 13 step 1). No commit (device only).

---

### Task 13: Acceptance battery A1–A9

Executed with the user at the cabin. Measurements come from the live recorder (read-only) and the log; nothing is reported as passing without a timestamp.

- [ ] **Step 1: Create the report file and the measurement helper**

`docs/superpowers/acceptance/2026-09-16-cabin-auto-lock-hytta.md` (create the directory) starting with:

```markdown
# Cabin auto-lock — Hytta acceptance, <date>

Blueprint sha256: <digest>. HA 2026.8.3, hass-ttlock v0.15.0. Instance: automation.hytta_auto_lock_policy.
Temporary values for the battery: M = 2 min, P = 1 min (restored to 45/20 in A9).

| # | Check | Expected | Measured | Result |
|---|---|---|---|---|
```

Measurement script (run after each step; prints the last 12 state rows of the lock entities and the helpers):

```bash
$CAB "sudo docker exec -i homeassistant python3 -" <<'EOF'
import sqlite3, datetime
c = sqlite3.connect('file:/config/home-assistant_v2.db?mode=ro', uri=True)
ids = ['lock.joachim_cabin', 'switch.joachim_cabin_auto_lock', 'sensor.joachim_cabin_last_trigger',
       'input_datetime.hytta_vacancy_not_before', 'input_select.hytta_state']
rows = c.execute("""select m.entity_id, s.state, s.last_updated_ts from states s
                    join states_meta m on s.metadata_id = m.metadata_id
                    where m.entity_id in (%s) order by s.last_updated_ts desc limit 40""" % ','.join('?'*len(ids)), ids).fetchall()
for eid, st, ts in sorted(rows, key=lambda r: r[2]):
    print(datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).astimezone().strftime('%H:%M:%S'), eid.split('.')[1], st)
EOF
```

- [ ] **Step 2: Set the battery values**

Edit `automations.yaml` on the device as in Task 12 step 3 but with `'motion_delay': 2, 'presence_delay': 1`; reload automations; confirm via the registry that the automation is still `on`.

- [ ] **Step 3: A1 — occupy latency**

User: unlock by fingerprint. Run the measurement script. Record the timestamps of `sensor…last_trigger = unlock by fingerprint` and `switch…auto_lock = off`; latency = difference. Expected: under 15 s.

- [ ] **Step 4: A2 — stale countdown (V1)**

Watch the next 60 s after A1 without touching the door. Record whether `last_trigger` shows `Auto Lock` and whether `lock… = unlocked` follows it (R5), with timestamps. User confirms the outside handle works. Expected: either no Auto Lock (firmware cancelled) or Auto Lock followed by `unlock by gateway` within ~10 s.

- [ ] **Step 5: A3 — keypad lock key**

User: press the lock key on the keypad. Record `last_trigger` value (V3), `lock… = locked`, `switch… = on` with `seconds` 30 (read `state_attributes` for the switch row, or check the switch in the UI). Expected: all three within 15 s; `input_select.hytta_state` = `resting` (phones home) within 5 min.

- [ ] **Step 6: A4 — vacant at H**

User: unlock by fingerprint (occupy), walk through the living room, then put both phones in flight mode and stay out of the sensors' view. Expected: `lock… = locked` and `switch… = on` at H = last motion + 2 min (helper value ± 60 s), `hytta_state` = `vacant`. Record helper value vs. actual lock timestamp.

- [ ] **Step 7: A5 — restart mid-timer**

Repeat the occupy + motion, then within the 2-minute window run `$CAB "sudo docker restart -t 60 homeassistant"` (`-t 60`, see Task 12). Expected: after HA is back, the door locks at the *original* helper time or at the first safety-net tick after the 2-minute grace, whichever is later; record both times.

- [ ] **Step 8: A6 — passage window end (only if a passage window exists in the TTLock app)**

Ask the user whether a passage schedule is configured. If yes and a window end falls within the session, record whether the firmware locks by itself at window end (V2) and that the settle happened at the window end.

- [ ] **Step 9: A7 — resting with phones on Wi-Fi**

Phones out of flight mode (persons `home`), occupy, one pass through the living room, then stay still 2 minutes. Expected: locked + armed at H and `hytta_state` = `resting`.

- [ ] **Step 10: A8 — resting via the bedroom with phones invisible**

Phones in flight mode, occupy, living-room pass, bedroom motion, then quiet. Expected: locked + armed at H with `hytta_state` = `resting`; once the bedroom has been quiet for M and the next safety-net tick passes, `hytta_state` = `vacant` with **no** new lock/switch rows.

- [ ] **Step 11: A9 — restore**

Set `motion_delay: 45`, `presence_delay: 20`, reload, verify via the registry and one measurement-script run that the instance is on and the helper is sane. Phones out of flight mode.

- [ ] **Step 12: Fill in the report, commit it on the branch**

Every row gets a measured number or the word *not measured* with the reason. Commit:

```bash
git add docs/superpowers/acceptance/2026-09-16-cabin-auto-lock-hytta.md
git commit -m "docs: Hytta acceptance results for the cabin auto-lock blueprint"
```

---

### Task 14: Presence = inside the home zone (D27)

**Why:** acceptance 2026-09-17 (`docs/superpowers/acceptance/2026-09-17-cabin-auto-lock-hytta.md`, "Presence finding" 1 and 2).
Persons tracked by GPS read the building zones nested inside the home zone, so `state == 'home'` misses them, and every
zone hop pushed the timer. Spec D27, §4.3 R0, §4.4 and §5 `presence_entities` are the requirements.

**Files:**
- Modify: `automation/cabin_auto_lock.yaml` (variables `persons_home` → `persons_present`, new `presence_push`, R0 push, label, `presence_entities` description)
- Modify: `tests/test_02_timer.py`, `tests/test_05_settle.py`, `tests/test_08_label.py`
- Modify: `scripts/mutation_check.py` (rename anchor + two new mutations)
- Modify: `README.md` (presence row)

**Interfaces:**
- Consumes: `trigger.from_state` / `trigger.to_state` of the `activity_presence` state trigger; the `in_zones` attribute HA core puts on `person` and GPS `device_tracker` states (a list of zone entity ids containing the position, `zone.home` included when inside the home zone; `[]` when `not_home`; absent on router trackers).
- Produces: variables `persons_present` (bool) and `presence_push` (bool), used by `push_delay_minutes`, `classification` and the label.

- [ ] **Step 1: Write the failing tests**

In `tests/test_02_timer.py`, replace `test_t5_zone_transition_is_a_presence_change` with the four tests below (keep the imports; add `SITE`, `SITE2`, `AWAY` next to `MIN`):

```python
SITE = {"in_zones": ["zone.johanne", "zone.home"]}        # a building zone nested inside the home zone
SITE2 = {"in_zones": ["zone.stabburet", "zone.home"]}
AWAY = {"in_zones": ["zone.work"]}                        # a zone elsewhere


async def test_t5_move_from_home_into_a_nested_zone_pushes_by_p(hass: HomeAssistant, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    hass.states.async_set(JOACHIM, "home", {"in_zones": ["zone.home"]})
    await hass.async_block_till_done()
    await clock(30 * MIN)
    t1 = dt_util.utcnow().timestamp()
    hass.states.async_set(JOACHIM, "Hovedhytta", {"in_zones": ["zone.hovedhytta", "zone.home"]})
    await hass.async_block_till_done()
    assert helper_ts(hass) == pytest.approx(t1 + 20 * MIN, abs=1)


async def test_t5_move_between_two_nested_zones_pushes_by_p(hass: HomeAssistant, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    hass.states.async_set(JOACHIM, "Johanne", SITE)
    await hass.async_block_till_done()
    await clock(30 * MIN)
    t1 = dt_util.utcnow().timestamp()
    hass.states.async_set(JOACHIM, "Stabburet", SITE2)     # both states inside the home zone
    await hass.async_block_till_done()
    assert helper_ts(hass) == pytest.approx(t1 + 20 * MIN, abs=1)


async def test_t5_arrival_into_and_departure_from_a_nested_zone_push_by_p(hass: HomeAssistant, fake: FakeTTLock, policy, clock) -> None:
    await policy()
    t0 = dt_util.utcnow().timestamp()
    hass.states.async_set(JOACHIM, "Johanne", SITE)        # not_home -> inside the home zone
    await hass.async_block_till_done()
    assert helper_ts(hass) == pytest.approx(t0 + 20 * MIN, abs=1)
    await clock(30 * MIN)
    t1 = dt_util.utcnow().timestamp()
    hass.states.async_set(JOACHIM, "not_home", {"in_zones": []})
    await hass.async_block_till_done()
    assert helper_ts(hass) == pytest.approx(t1 + 20 * MIN, abs=1)


async def test_t5_moves_entirely_outside_the_site_never_push(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    before = helper_ts(hass)
    hass.states.async_set(JOACHIM, "Work", AWAY)           # not_home -> a zone elsewhere
    await hass.async_block_till_done()
    hass.states.async_set(JOACHIM, "Shop", {"in_zones": ["zone.shop"]})
    await hass.async_block_till_done()
    hass.states.async_set(JOACHIM, "not_home", {"in_zones": []})
    await hass.async_block_till_done()
    assert helper_ts(hass) == before
```

In `tests/test_05_settle.py`, after `test_t24_resting_policy_off_off_leaves_door_free_then_vacates_when_phones_leave`, add:

```python
async def test_t24_person_in_a_zone_inside_the_home_zone_is_present(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy(resting_lock_bolt=False, resting_arm_auto_lock=False)
    hass.states.async_set(JOACHIM, "Johanne", {"in_zones": ["zone.johanne", "zone.home"]})
    await hass.async_block_till_done()
    await occupied_with_motion(hass, fake)
    await clock(50 * MIN)
    assert fake.calls == []                               # resting: Joachim is on the site
    assert hass.states.get(LOCK).state == "unlocked"


async def test_t24_person_in_a_zone_elsewhere_is_not_present(hass, fake: FakeTTLock, policy, clock) -> None:
    await policy(resting_lock_bolt=False, resting_arm_auto_lock=False)
    hass.states.async_set(JOACHIM, "Work", {"in_zones": ["zone.work"]})
    await hass.async_block_till_done()
    await occupied_with_motion(hass, fake)
    await clock(50 * MIN)
    assert fake.calls == VACATE                           # vacant: a zone outside the home zone is away
```

In `tests/test_08_label.py`, after `test_t20_label_flips_resting_to_vacant_on_the_safety_net_without_lock_calls`, add:

```python
async def test_t20_label_reads_resting_while_in_a_zone_inside_the_home_zone(hass, fake: FakeTTLock, policy, clock, hooks) -> None:
    await policy()
    hass.states.async_set(JOACHIM, "Stabburet", {"in_zones": ["zone.stabburet", "zone.home"]})
    await hass.async_block_till_done()
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    await clock(46 * MIN)
    assert hass.states.get(STATE).state == "resting"
    assert len(hooks["on_resting"]) == 1 and hooks["on_vacant"] == []
```

- [ ] **Step 2: Run the new tests and see them fail**

Run: `uv run pytest tests/test_02_timer.py tests/test_05_settle.py tests/test_08_label.py -q`
Expected: `test_t5_move_between_two_nested_zones_pushes_by_p` passes by accident (any known change pushes today) and the
others FAIL: `..._moves_entirely_outside_the_site_never_push` (H moved), `..._is_present` (VACATE was called),
`..._reads_resting_...` (label `vacant`). Paste the failing assertion lines into the report.

- [ ] **Step 3: Implement**

In `automation/cabin_auto_lock.yaml`:

1. Replace the `persons_home` variable with:

```yaml
  persons_present: >-
    {%- set ns = namespace(present=false) -%}
    {%- for s in expand(presence_entities) -%}
      {%- if s.state == 'home' or 'zone.home' in (s.attributes.in_zones | default([])) -%}
        {%- set ns.present = true -%}
      {%- endif -%}
    {%- endfor -%}
    {{ ns.present }}
  presence_push: >-
    {%- set on_site = namespace(from=false, to=false) -%}
    {%- if trigger.id == 'activity_presence' and trigger.from_state is not none and trigger.to_state is not none -%}
      {%- set on_site.from = trigger.from_state.state == 'home' or 'zone.home' in (trigger.from_state.attributes.in_zones | default([])) -%}
      {%- set on_site.to = trigger.to_state.state == 'home' or 'zone.home' in (trigger.to_state.attributes.in_zones | default([])) -%}
    {%- endif -%}
    {{ on_site.from or on_site.to }}
```

2. `classification` and the label template: `persons_home` → `persons_present` (two places).
3. `push_delay_minutes`: `{%- elif trigger.id == 'activity_presence' -%}{{ presence_delay }}` → `{%- elif presence_push -%}{{ presence_delay }}`.
4. `presence_entities` input description: `Persons or device trackers. Present = "home" or any zone inside the home zone (HA lists it in the in_zones attribute). A presence change that starts or ends on the site postpones settling by P; presence marks the cabin as resting rather than vacant. Never keeps the door unlocked.`

- [ ] **Step 4: Run the whole suite and see it pass**

Run: `uv run pytest -q` (expected 154 passed) and `uv run ruff check .`

- [ ] **Step 5: Mutation check**

In `scripts/mutation_check.py`: update the "swap the resting/vacant classification" anchor/replacement to `persons_present`, and append:

```python
    ("count only the home state as present",
     "{%- if s.state == 'home' or 'zone.home' in (s.attributes.in_zones | default([])) -%}",
     "{%- if s.state == 'home' -%}"),
    ("push the timer on moves outside the site too",
     "{{ on_site.from or on_site.to }}",
     "{{ trigger.id == 'activity_presence' }}"),
```

Run: `uv run python scripts/mutation_check.py` → expected `all 14 mutations killed`.

- [ ] **Step 6: README**

Presence row: `persons or trackers; present = home or any zone inside the home zone; a change that starts or ends on the site postpones settling by P; presence marks resting; never keeps the door unlocked`.

- [ ] **Step 7: Commit**

```bash
git add automation/cabin_auto_lock.yaml tests scripts/mutation_check.py README.md
git commit -m "feat(cabin-auto-lock): presence = inside the home zone; off-site moves never push (D27)"
```

- [ ] **Step 8 (controller): redeploy and verify**

`deploy-hytta.sh prod --restart` (uses `docker restart -t 60`). Acceptance rows A10 (label `resting`, not `vacant`, at the first
settle/safety-net tick with Joachim in a building zone), A11 (a zone-to-zone hop pushes H by P with no lock call), A12 (a
`not_home` → elsewhere change pushes nothing; pending until it happens).

## Appendix A — the complete blueprint after Task 9

This is the reference the tasks converge on; if a task's snippet and this appendix disagree, the appendix wins.

```yaml
blueprint:
  name: Cabin auto-lock policy
  description: >-
    Keeps a TTLock door free while the cabin is in use and secures it when the cabin settles,
    so nobody enters the code more than once per visit.

    OCCUPIED = auto-lock off: every unlock stays unlocked. Any successful unlock occupies.
    RESTING = the household is here but quiet: the door follows the resting policy below.
    VACANT = nobody is here: bolt locked, auto-lock armed. An explicit lock (keypad lock key,
    app, dashboard, voice) vacates at once. A restart-proof timer (an input_datetime helper)
    settles the door a while after the last living-area motion. Passage mode counts as guest
    hours. Design and decisions: docs/superpowers/specs/2026-09-16-cabin-auto-lock-design.md
    in github.com/jmgiaever/home-assistant-blueprints.
  domain: automation
  author: Joachim M. G. Giæver
  homeassistant:
    min_version: "2025.4.0"
  input:
    # (identical to Task 1, Step 7)

mode: queued
max: 100
max_exceeded: silent

trigger_variables:
  passage_mode_sensor: !input passage_mode_sensor

triggers:
  # (identical to Task 1, Step 7)

variables:
  lock_entity: !input lock_entity
  last_trigger_sensor: !input last_trigger_sensor
  auto_lock_switch: !input auto_lock_switch
  last_operator_sensor: !input last_operator_sensor
  vacancy_helper: !input vacancy_helper
  motion_delay: !input motion_delay
  presence_delay: !input presence_delay
  armed_seconds: !input armed_seconds
  resting_lock_bolt: !input resting_lock_bolt
  resting_arm_auto_lock: !input resting_arm_auto_lock
  resting_armed_seconds: !input resting_armed_seconds
  motion_sensors: !input motion_sensors
  resting_sensors: !input resting_sensors
  presence_entities: !input presence_entities
  trusted_operators: !input trusted_operators
  night_enabled: !input night_enabled
  stale_countdown_unlock: !input stale_countdown_unlock
  stale_countdown_window: !input stale_countdown_window
  state_select: !input state_select
  notification_id: "cabin_auto_lock_{{ lock_entity | replace('.', '_') }}"
  event_is_fresh: "{{ (trigger.from_state is not none and trigger.to_state is not none) if trigger.from_state is defined else true }}"
  event_value: >-
    {{ (trigger.to_state.state | lower) if (trigger.id == 'lock_event' and trigger.to_state is not none) else '' }}
  previous_value: >-
    {{ (trigger.from_state.state | lower) if (trigger.id == 'lock_event' and trigger.from_state is not none) else '' }}
  is_unlock_event: "{{ 'unlock' in event_value and not (event_value is search('fail|try to|expired|passage')) }}"
  is_explicit_lock: "{{ (event_value is match('^(lock by|lock with|locked via)')) and 'fail' not in event_value }}"
  is_auto_lock: "{{ event_value == 'auto lock' }}"
  previous_was_unlock: "{{ 'unlock' in previous_value and not (previous_value is search('fail|try to|expired|passage')) }}"
  occupy_requested: "{{ (trigger.id == 'lock_event' and is_unlock_event and event_is_fresh) or trigger.id == 'unlock_state' }}"
  operator_trusted: >-
    {{ (trusted_operators | count == 0)
       or (last_operator_sensor != ''
           and (states(last_operator_sensor) | lower | trim) in (trusted_operators | map('lower') | map('trim') | list)) }}
  h_ts: "{{ state_attr(vacancy_helper, 'timestamp') | float(0) }}"
  helper_ok: "{{ state_attr(vacancy_helper, 'timestamp') is not none }}"
  now_ts: "{{ now().timestamp() }}"
  event_marker: "{{ expand(last_trigger_sensor) | map(attribute='last_changed') | map('string') | first | default('') }}"
  activity_on: "{{ expand(motion_sensors) | selectattr('state', 'eq', 'on') | list | count > 0 }}"
  passage_on: "{{ passage_mode_sensor != '' and is_state(passage_mode_sensor, 'on') }}"
  persons_home: "{{ expand(presence_entities) | selectattr('state', 'eq', 'home') | list | count > 0 }}"
  resting_recent: >-
    {%- set ns = namespace(recent=false) -%}
    {%- for s in expand(resting_sensors) -%}
      {%- if s.state == 'on' or (s.state == 'off' and (now() - s.last_changed).total_seconds() < motion_delay * 60) -%}
        {%- set ns.recent = true -%}
      {%- endif -%}
    {%- endfor -%}
    {{ ns.recent }}
  classification: "{{ 'resting' if (persons_home or resting_recent) else 'vacant' }}"
  automation_age: "{{ (now() - as_datetime(this.last_changed)).total_seconds() }}"
  settle_trigger: "{{ trigger.id in ['vacancy_time', 'passage_off', 'lock_available', 'safety_net'] }}"
  settle_due: >-
    {{ settle_trigger and helper_ok and now_ts >= h_ts and not activity_on and not passage_on
       and automation_age >= 120 }}

actions:
  # ---- timer push (R0, R1) ----            (Task 3, Step 3)
  # ---- occupy (R1) ----                    (Task 2, Step 3)
  # ---- policy selection (R2, R3, R4) ----  (Task 7, Step 3: the three-branch expression)
  # ---- explicit lock ends activity (R2) ---- (Task 5, Step 3)
  # ---- apply policy ----                   (Task 5, Step 3)
  # ---- stale countdown (R5) ----           (Task 8, Step 3)
  # ---- label ----                          (Task 9, Step 3)
```

## Appendix B — spec coverage map

| Spec item | Task |
|---|---|
| §4.2 classification, D19 | 2 (T1) |
| R1 (strings, lock entity, trusted list), D11 | 2, 4 |
| R0, helper max, D7 | 3 |
| R2, D5, D25 | 5 |
| §4.7 failure handling, D10, D22 | 5 (T12) |
| R3, D4, D8, D16, D23, D24, start grace, unavailable lock, missing helper | 6 |
| R4, D6 | 7 |
| R5, D9 | 8 |
| Label + hooks, D20, D21 | 9 |
| README, import button, helper setup | 10 |
| §7 mutation pass, D14 | 11 |
| §8 deployment, D17 | 12 |
| §8 acceptance A1–A9, §10 V1–V3 | 13 |
| V4 (time trigger re-arming) | covered by T6 (`activity_at_h_postpones`) and the safety net |
