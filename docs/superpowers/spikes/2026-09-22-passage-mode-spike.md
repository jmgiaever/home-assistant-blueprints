# Spike: passage-window occupancy on the Hytta TTLock (2026-09-22)

**Question:** can the lock's own passage-mode schedule replace "disarm auto-lock on arrival" (spec D3), so that a
Home Assistant outage can never leave the door disarmed indefinitely?

**Setup:** hass-ttlock v0.15.0, `ttlock.configure_passage_mode` called through the HA REST API from the laptop;
`automation.hytta_auto_lock_policy` OFF 22:25:35–23:21:06 CEST; the user at the door; evidence from the lock's own
records (`ttlock.list_records`, lock clock), HA's recorder and the HA log. Times are CEST.

| # | Check | Result |
|---|---|---|
| 1 | Firmware relocks at the window end with the gateway unplugged | **PASS** — window 22:28–22:32 written 22:28:03; passcode unlock 22:28:13 held open past the 30 s countdown; record `AUTOMATIC_LOCK_TIMEOUT` at 22:32:00 (lock clock), gateway unplugged since ~22:29:30; the relock is silent |
| 2 | A window written right after an unlock cancels the running countdown | **NO** — window written 2 s after the 23:15:11 unlock (HTTP 200, sensor on); outside handle locked ~40 s later. R5 stays |
| 3 | Window across midnight | skipped (user's choice) → the design clamps windows at 23:59 |
| 4 | Write reliability while the lock is idle | running: `automation.spike_ttlock_idle_write_probe…` (hourly `enabled: false` write at :07; remove after 2026-09-24) |

**Side findings**

- HA's `locked`/"Auto Lock" 30 s after an unlock is the integration's simulation (`coordinator._handle_auto_lock`), not
  an observation. The lock does not upload countdown relocks (none within 20 min); the window-end relock was uploaded
  3 min late. The blueprint must keep its own clock and never treat that state as proof.
- After a gateway power cycle the cloud answered errcode -3002 "gateway offline" for 20+ min while the gateway pinged on
  the LAN; a config-entry reload fixed it at once. Treat -3002 as "retry later"; a reload is a valid fallback.
- The phone's TTLock app relays lock events over Bluetooth when near the door (the 22:55 "remote" unlock was a
  `BLUETOOTH_UNLOCK`), so an app-driven unlock proves nothing about the gateway.

**Recommendation:** proceed with the redesign (keep auto-lock armed; occupancy = a passage window [arrival, H] on
today's weekday; extend only when it lags H by ≥ 5 min; explicit lock / night / settle end it; every failure leans
towards locked). Spec amendment next.

## Raw log
spike log: 2026-09-22
22:25:37 automation off for the spike; lock locked, switch on s=30, passage off
22:28:05 check1: passage window 22:28-22:32 written
22:46:05 check2 attempt1: write at unlock+1s failed -3002 gateway offline; countdown relocked 22:43:27
### Findings as recorded during the spike
- CHECK 1 PASS: window 22:28–22:32 written 22:28:03 (HTTP 200, passage sensor on at once). Passcode unlock 22:28:13 (lock clock) / 22:28:17 (HA). No 30 s relock: HA saw `unlocked` continuously 22:28:20–22:33:22. Gateway unplugged ~22:29:30. Lock record AUTOMATIC_LOCK_TIMEOUT at 22:32:00 (lock clock) = exactly the window end, uploaded 22:35:09. Relock is silent (no voice prompt). User found the door locked at ~22:33:40 and unlocked it (record 22:33:43); that post-window unlock relocked by countdown (door locked at 22:42 per user).
- Gateway after replug (~22:33:50): cloud saw it briefly (records 22:35:09), then `binary_sensor.hytta_status` off at 22:36:51 and every configure_passage_mode since fails: RequestFailed errcode -3002 "The gateway is offline" (HTTP 500 from HA). Gateway answers ping on the LAN the whole time. Hypothesis: the phone's TTLock app relayed the 22:43:02 unlock webhook (phone at the door).
- CHECK 2 attempt 1 INVALID: watcher saw the unlock at 22:43:03.17 and wrote the window at +0.01 s, but the write failed (-3002); relock at 22:43:27 was the plain countdown. Redo when the gateway is back; user asked for 190 s spacing on cloud retries.
22:58:20 ttlock entry reloaded 22:57:39 -> gateway sensor on 22:57:40, write OK 22:57:54; check2 watcher re-armed
- CHECK 2 attempt 2 (22:59): watcher saw the unlock at 22:59:05.43 (lock clock 22:58:59), window 22:59–23:02 written at 22:59:06 (HTTP 200, +1.2 s), passage sensor on. HA showed locked/"Auto Lock" at 22:59:29 — CORRECTION: that is the integration's simulated auto-lock (coordinator._handle_auto_lock schedules locked at unlock lock_ts + 30 s; 22:58:59 + 30 = 22:59:29; likewise 22:42:57 + 30 = 22:43:27 earlier). Not evidence. Lock records show NO AUTOMATIC_LOCK_TIMEOUT (type 45) after 22:32:00 as of 23:04, although the door was found locked at 22:42 by hand; countdown relock records may upload lazily (the 22:32:00 one arrived 22:35:09). Verdict pending the user's ear/handle and a records sync via the app.
- Note: the app "remote" unlock at 22:55:22 was a BLUETOOTH_UNLOCK (phone next to the lock), so it did not prove the gateway online. A ttlock config-entry reload at 22:57:39 immediately gave gateway sensor on + a successful write; unknown whether the cloud had recovered by itself just before.
- CHECK 2 attempt 3 (23:15) VERIFIED BY HANDLE: lock-key lock 23:14:30 (record LOCK_BUTTON, pushed in 4 s); passcode unlock lock clock 23:15:11 / HA 23:15:15; window 23:15–23:18 written 23:15:17 (HTTP 200, +2 s, passage sensor on); user tried the outside handle ~40 s after the unlock: LOCKED. => a passage window written right after an unlock does NOT cancel the running 30 s countdown. R5 (one re-unlock on the stale countdown) stays in the design. HA's locked/"Auto Lock" 23:15:41 = the integration's simulation (unlock lock_ts 23:15:11 + 30 s), coincidentally right.
- Lock record behaviour: PASSWORD_UNLOCK / LOCK_BUTTON / BLUETOOTH_UNLOCK are pushed within 3–4 s; AUTOMATIC_LOCK_TIMEOUT records are NOT pushed in real time (none listed for the handle-proven countdown relocks at ~22:59:29 and ~23:15:41 as of 23:19); the 22:32:00 window-end relock record appeared 3 min later (22:35:09). HA cannot observe firmware relocks promptly; the blueprint keeps its own clock.
- 23:19 passage-off write (clears the spent Tue 23:15–23:18 window).
23:21:08 RESTORE: automation on; lock locked, armed 30 s, passage off
23:21:41 check4 probe automation applied (hourly at :07, remove after 2026-09-24)
23:36:18 records poll ended: still no AUTOMATIC_LOCK_TIMEOUT record for the 22:59 / 23:15 countdown relocks (20 min after)
