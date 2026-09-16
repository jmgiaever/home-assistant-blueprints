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


async def test_t12_stale_notification_is_dismissed_when_the_door_heals_out_of_band(hass: HomeAssistant, fake: FakeTTLock, policy, clock, notifications) -> None:
    await policy()
    await occupied(hass, fake)
    fake.fail("lock.lock", times=2)
    fake.event("lock by lock key")
    await clock(40, step=5)
    assert len(notifications["create"]) == 1                # could not secure the door
    assert hass.states.get(SWITCH).state == "on"           # the arm step succeeded
    fake.set_lock_state("locked")                          # someone locked it by hand
    fake.reset_calls()
    fake.event("lock by app")                              # the echo / next explicit lock: nothing left to do
    await hass.async_block_till_done()
    assert fake.calls == []
    assert [c.data["notification_id"] for c in notifications["dismiss"]] == ["cabin_auto_lock_lock_hytta"]


async def test_t12_safety_net_retries_an_unsecured_door(hass, fake: FakeTTLock, policy, clock, notifications) -> None:
    await policy()
    await occupied(hass, fake)
    fake.fail("lock.lock", times=2)
    fake.event("lock by lock key")
    await clock(40, step=5)
    assert len(fake.calls_of("lock.lock")) == 2
    assert len(notifications["create"]) == 1
    await clock(3 * 60)                                    # crosses the next 5-minute safety-net tick, not the one after
    assert len(fake.calls_of("lock.lock")) == 3
    assert hass.states.get(LOCK).state == "locked"
    assert [c.data["notification_id"] for c in notifications["dismiss"]] == ["cabin_auto_lock_lock_hytta"]


async def test_t12_a_queued_settle_never_bolts_a_door_unlocked_meanwhile(hass, fake: FakeTTLock, policy, clock) -> None:
    """A settle queued behind a slow vacate must not act on a stale snapshot (final review I4)."""
    await policy()
    fake.set_passage(True)                                 # keeps the timer from settling on its own
    await hass.async_block_till_done()
    await occupied(hass, fake)
    fake.fail("lock.lock", times=2)
    fake.event("lock by lock key")                        # run A: vacate, parked on its waits
    await clock(5, step=5)
    fake.set_passage(False)                                # run B (passage_off settle) queued behind A
    await clock(10, step=5)
    fake.unlocked_by("unlock by fingerprint", "Joachim")  # the household comes back while A is still parked
    await clock(40, step=5)
    assert hass.states.get(LOCK).state == "unlocked"
    # The newer event supersedes both A's own retry (the pre-existing guard: D10) and B's whole
    # settle (this fix): exactly one lock.lock call is ever made.
    assert len(fake.calls_of("lock.lock")) == 1
    assert hass.states.get(SWITCH).state == "off"


async def test_t17_unavailable_switch_posts_settle_pending_and_makes_no_call(hass: HomeAssistant, fake: FakeTTLock, policy, notifications) -> None:
    await policy()
    await occupied(hass, fake)
    hass.states.async_set(SWITCH, "unavailable")
    await hass.async_block_till_done()
    fake.event("lock by lock key")
    await hass.async_block_till_done()
    assert fake.calls == []
    assert len(notifications["create"]) == 1
    assert notifications["create"][0].data["title"] == "Cabin auto-lock: settle pending"
