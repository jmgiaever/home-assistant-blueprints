"""T2 an unlock that repeats the previous string is still seen through the lock entity (D11)."""

from homeassistant.core import HomeAssistant

from .fakes import LOCK, SWITCH, FakeTTLock


async def test_t2_repeated_string_occupies_via_lock_entity(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    # Yesterday's last event was a fingerprint unlock; HA locked the door itself later (no echo).
    fake.event("unlock by fingerprint", operator="Joachim")
    await hass.async_block_till_done()
    fake.set_switch(on=True, seconds=30)               # HA re-armed it later (vacant), string unchanged
    fake.set_lock_state("locked")
    await hass.async_block_till_done()
    fake.reset_calls()
    fake.unlocked_by("unlock by fingerprint", "Joachim")   # same string: no sensor event, lock entity changes
    await hass.async_block_till_done()
    assert fake.calls_of("ttlock.configure_autolock") == [{"entity_id": [LOCK], "enabled": False}]
    assert hass.states.get(SWITCH).state == "off"


async def test_t2_one_physical_unlock_makes_one_call(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy()
    fake.unlocked_by("unlock by passcode", "Lukas")     # lock entity AND string change: two triggers
    await hass.async_block_till_done()
    assert len(fake.calls_of("ttlock.configure_autolock")) == 1


async def test_t2_lock_entity_unlock_respects_trusted_list(hass: HomeAssistant, fake: FakeTTLock, policy) -> None:
    await policy(trusted_operators=["Joachim"])
    fake.unlocked_by("unlock by passcode", "Magnus")
    await hass.async_block_till_done()
    assert fake.calls == []
