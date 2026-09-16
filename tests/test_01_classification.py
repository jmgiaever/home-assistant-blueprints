"""T1 classification over the whole event table, T3 trusted list, T15 stale values, T16 entity-added events."""

import pytest
from homeassistant.core import HomeAssistant

from .conftest import helper_ts
from .fakes import LOCK, SWITCH, TRIGGER, FakeTTLock
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
    before = helper_ts(hass)
    fake.event("unlock by passcode", operator="Magnus")
    await hass.async_block_till_done()
    assert fake.calls == []
    assert helper_ts(hass) == before
    fake.event("unlock by fingerprint", operator="Lukas")
    await hass.async_block_till_done()
    assert len(fake.calls_of("ttlock.configure_autolock")) == 1
    assert helper_ts(hass) > before


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
