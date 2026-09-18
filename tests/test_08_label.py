"""The optional input_select label: written on change with hooks, never read by the door rules."""

from .conftest import JOACHIM, KITCHEN, STATE
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


async def test_t20_label_reads_resting_while_in_a_zone_inside_the_home_zone(hass, fake: FakeTTLock, policy, clock, hooks) -> None:
    await policy()
    hass.states.async_set(JOACHIM, "Stabburet", {"in_zones": ["zone.stabburet", "zone.home"]})
    await hass.async_block_till_done()
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    await clock(46 * MIN)
    assert hass.states.get(STATE).state == "resting"
    assert len(hooks["on_resting"]) == 1 and hooks["on_vacant"] == []


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
