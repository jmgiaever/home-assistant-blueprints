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
