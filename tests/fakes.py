"""Test double for the TTLock integration: services mirror into entity states."""

from __future__ import annotations

from collections import Counter

from homeassistant.core import HomeAssistant, ServiceCall

LOCK = "lock.hytta"
SWITCH = "switch.hytta_auto_lock"
TRIGGER = "sensor.hytta_last_trigger"
OPERATOR = "sensor.hytta_last_operator"
PASSAGE = "binary_sensor.hytta_passage_mode"


class FakeTTLock:
    """Behaves like the lock + cloud: commands change state unless told to fail."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.calls: list[tuple[str, dict]] = []
        self._failures: Counter[str] = Counter()
        hass.states.async_set(LOCK, "locked")
        hass.states.async_set(SWITCH, "on", {"seconds": 30})
        hass.states.async_set(TRIGGER, "Unknown")
        hass.states.async_set(OPERATOR, "Unknown")
        hass.states.async_set(PASSAGE, "off")
        hass.services.async_register("lock", "lock", self._lock)
        hass.services.async_register("lock", "unlock", self._unlock)
        hass.services.async_register("ttlock", "configure_autolock", self._configure_autolock)

    # ---- commands the automation sends ----
    def _accept(self, name: str, call: ServiceCall) -> bool:
        self.calls.append((name, dict(call.data)))
        if self._failures[name] > 0:
            self._failures[name] -= 1
            return False
        return True

    async def _lock(self, call: ServiceCall) -> None:
        if self._accept("lock.lock", call):
            self.hass.states.async_set(LOCK, "locked")

    async def _unlock(self, call: ServiceCall) -> None:
        if self._accept("lock.unlock", call):
            self.hass.states.async_set(LOCK, "unlocked")

    async def _configure_autolock(self, call: ServiceCall) -> None:
        if self._accept("ttlock.configure_autolock", call):
            if call.data.get("enabled"):
                self.hass.states.async_set(SWITCH, "on", {"seconds": int(call.data.get("seconds", 10))})
            else:
                self.hass.states.async_set(SWITCH, "off", {"seconds": 0})

    def fail(self, service: str, times: int = 1) -> None:
        """Make the next `times` calls of `service` record but not change state."""
        self._failures[service] += times

    # ---- what the physical lock / cloud reports ----
    def event(self, description: str, operator: str | None = None) -> None:
        """A webhook record: the last-operator and last-trigger sensors update."""
        if operator is not None:
            self.hass.states.async_set(OPERATOR, operator)
        self.hass.states.async_set(TRIGGER, description)

    def unlocked_by(self, description: str = "unlock by fingerprint", operator: str = "Joachim") -> None:
        """Someone unlocked at the door: lock entity first, then the record (as the integration does)."""
        self.hass.states.async_set(LOCK, "unlocked")
        self.event(description, operator)

    def auto_locked(self) -> None:
        self.hass.states.async_set(LOCK, "locked")
        self.event("Auto Lock")

    def set_lock_state(self, state: str) -> None:
        self.hass.states.async_set(LOCK, state)

    def set_switch(self, on: bool, seconds: int = 30) -> None:
        self.hass.states.async_set(SWITCH, "on" if on else "off", {"seconds": seconds if on else 0})

    def set_passage(self, on: bool) -> None:
        self.hass.states.async_set(PASSAGE, "on" if on else "off")

    # ---- assertions ----
    def calls_of(self, name: str) -> list[dict]:
        return [data for n, data in self.calls if n == name]

    def reset_calls(self) -> None:
        self.calls.clear()
