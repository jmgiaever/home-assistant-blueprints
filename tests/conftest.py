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
            for _ in range(20):
                await asyncio.sleep(0)
            remaining -= d

    return advance


def helper_ts(hass: HomeAssistant) -> float:
    return float(hass.states.get(HELPER).attributes["timestamp"])


async def set_helper(hass: HomeAssistant, ts: float) -> None:
    await hass.services.async_call("input_datetime", "set_datetime", {"entity_id": HELPER, "timestamp": ts}, blocking=True)


@pytest.fixture
async def policy(hass: HomeAssistant, fake: FakeTTLock, hooks, notifications, clock, freezer):
    """Set up helpers + the blueprint automation. Scene after setup: VACANT, grace passed, calls cleared."""

    async def _setup(grace: bool = True, **overrides) -> None:
        # Deterministic position in the 5-minute safety-net cycle: start at M0:30.123456 of the current
        # 5-minute block (the frozen clock moves back by at most five minutes). The grace then ends at
        # +3:00, the first safety-net tick lands at +4:30, and the non-zero microsecond keeps an explicit
        # lock's H := now in the past for HA's whole-second time trigger.
        now = dt_util.now()
        freezer.move_to(now.replace(minute=(now.minute // 5) * 5, second=30, microsecond=123456))
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
