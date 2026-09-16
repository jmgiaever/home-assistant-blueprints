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
