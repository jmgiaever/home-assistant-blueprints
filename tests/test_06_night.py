"""T10 night beats motion, presence and passage mode; off by default."""

from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .conftest import KITCHEN
from .fakes import LOCK, FakeTTLock

VACATE = [("lock.lock", {"entity_id": [LOCK]}),
          ("ttlock.configure_autolock", {"entity_id": [LOCK], "enabled": True, "seconds": 30})]


async def busy_evening(hass: HomeAssistant, fake: FakeTTLock) -> None:
    """Occupied, passage mode on, motion going: everything that would normally keep the door free."""
    fake.unlocked_by("unlock by fingerprint", "Joachim")
    await hass.async_block_till_done()
    fake.set_passage(True)
    hass.states.async_set(KITCHEN, "on")
    await hass.async_block_till_done()
    fake.reset_calls()


async def test_t10_night_vacates_despite_everything(hass, fake: FakeTTLock, policy, clock, freezer) -> None:
    freezer.move_to(datetime(2026, 9, 16, 22, 50, tzinfo=dt_util.DEFAULT_TIME_ZONE))   # 22:50 local, before setup
    await policy(night_enabled=True, night_time="23:00:00")   # setup + 3 min grace -> 22:53
    await busy_evening(hass, fake)
    await clock(8 * 60, step=30)                     # crosses 23:00
    assert fake.calls == VACATE


async def test_t10_night_is_off_by_default(hass, fake: FakeTTLock, policy, clock, freezer) -> None:
    freezer.move_to(datetime(2026, 9, 16, 22, 50, tzinfo=dt_util.DEFAULT_TIME_ZONE))   # 22:50 local, before setup
    await policy()
    await busy_evening(hass, fake)
    await clock(8 * 60, step=30)
    assert fake.calls == []
