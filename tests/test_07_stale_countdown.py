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
