"""T5 the helper is the max of motion+M, presence+P, unlock+M; T22 resting sensors never push; T16 boot transitions never push."""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import BEDROOM, JOACHIM, KITCHEN, LIVING, helper_ts
from .fakes import FakeTTLock

MIN = 60
SITE = {"in_zones": ["zone.johanne", "zone.home"]}        # a building zone nested inside the home zone
SITE2 = {"in_zones": ["zone.stabburet", "zone.home"]}
AWAY = {"in_zones": ["zone.work"]}                        # a zone elsewhere


async def test_t5_motion_pushes_by_m(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    t0 = dt_util.utcnow().timestamp()
    hass.states.async_set(KITCHEN, "on")
    await hass.async_block_till_done()
    assert helper_ts(hass) == pytest.approx(t0 + 45 * MIN, abs=1)


async def test_t5_pushes_are_whole_seconds(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    """HA's time trigger fires on whole-second attributes, so H must be a whole second (final review I1)."""
    await policy()
    t0 = dt_util.utcnow().timestamp()
    hass.states.async_set(KITCHEN, "on")
    await hass.async_block_till_done()
    h = helper_ts(hass)
    assert h == int(h)
    assert t0 + 45 * MIN <= h < t0 + 45 * MIN + 1


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
