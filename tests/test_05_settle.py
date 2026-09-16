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
