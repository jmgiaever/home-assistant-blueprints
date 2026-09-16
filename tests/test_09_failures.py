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
