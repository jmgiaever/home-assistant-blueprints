# Cabin auto-lock policy blueprint — design

**Status:** approved in conversation 2026-09-15/16, awaiting written review
**Repo:** `jmgiaever/home-assistant-blueprints` → `automation/cabin_auto_lock.yaml`
**First instance:** "Hytta" (Joachim's cabin, Giæverhytta site), Home Assistant 2026.8.3 on the LAV918 Pi 5

## 1. Goal

Joachim and Lukas should not have to enter the door code every time they enter the cabin during a stay.
Today the TTLock auto-locks the door 30 s after every unlock, so every trip to the car, the main cabin or the
woodshed costs a fingerprint or a code. The existing workaround (switch auto-lock off by hand, an automation
re-arms it after 45 min without motion) is manual on the way in and blind to phones on the way out.

The deliverable is a **reusable Home Assistant automation blueprint** that encodes the policy below, so the
same behaviour can be applied to other locks, cabins and people by filling in inputs.

## 2. Verified context (2026-09-15/16, read from the live device and the 2026-08-21 backup)

| Item | Fact |
|---|---|
| Lock | `lock.joachim_cabin` ("Hytta"), TTLock SN9274 with keypad + fingerprint, reached through a TTLock Wi-Fi gateway (`binary_sensor.hytta_status` = gateway online) |
| Integration | `jbergler/hass-ttlock` v0.15.0 (HACS). Webhook registered, so lock events arrive in near real time; polling every 15 min otherwise |
| Lock entities | `switch.joachim_cabin_auto_lock` (attribute `seconds`), `binary_sensor.joachim_cabin_passage_mode`, `sensor.joachim_cabin_last_trigger`, `sensor.joachim_cabin_last_operator`, `sensor.joachim_cabin_battery` |
| Services | `ttlock.configure_autolock` (`enabled`, `seconds` 0–60), `ttlock.configure_passage_mode`, `lock.lock`, `lock.unlock`. The switch's own `turn_on` hard-codes 10 s, so the blueprint always uses `configure_autolock` |
| Event strings | `sensor.*_last_trigger` takes the integration's `Event.EVENTS` descriptions: `unlock by fingerprint`, `unlock by passcode`, `unlock by app`, `unlock by gateway`, `unlock by IC card`, `lock by lock key`, `lock by passcode`, `lock by app`, `Auto Lock`, `auto unlock at passage mode`, `open from inside`, … (full table in §4.2). The sensor does **not** set `force_update`, so an identical repeated string produces no state event |
| Simulated auto-lock | On every unlock webhook the integration schedules an HA-side "Auto Lock" after the auto-lock delay *as known at that moment*. Disabling auto-lock afterwards does not cancel it |
| Passage mode | `auto_lock_delay()` returns `None` while passage mode is active: the integration assumes no auto-lock during a passage window |
| Current automation | "Turn back on auto-lock": kitchen + living-room motion cleared for 45 min AND auto-lock off → `configure_autolock` 30 s. Auto-lock is switched off manually |
| Motion | Two Shelly Wave Motion (Z-Wave): `binary_sensor.kitchen_motion_detection_location_provided`, `binary_sensor.living_room_motion_detection_location_provided`; a Heiman sensor in the bedroom (`binary_sensor.bedroom_motion_sensor_motion_detection`) |
| Presence | UniFi Wi-Fi trackers only, no companion app. `person.lav918` (Joachim: ThinkPad, four Pixel-10 entries, three Pixel-Watch entries), `person.lukas_alexander` (Pixel 8). On 2026-08-21 neither phone was on the cabin Wi-Fi during 8 h of fingerprint/passcode activity, so presence is a helper signal, not the deciding one |
| Usage (Aug 11–21) | 18 unlocks: 11 passcode, 4 fingerprint, 3 gateway/app; operators Joachim, Joachim L, Lukas, Magnus, joachim@giaever.no. Every unlock was followed by `Auto Lock` within a minute |
| Site | Timezone Europe/Oslo. Zones exist for the buildings (Hovedhytta, Joachim, Kristina, Ola Magnus, Johanne, Garasjen, Stabburet) but nothing GPS-based feeds them |

## 3. Scope and non-goals

**In scope:** one automation blueprint, its tests, README section, deployment to Hytta, acceptance on the device.

**Not in scope (see §11):** configuring passage mode or passcodes from HA, auto-unlock on approach, door-open
detection (no door sensor), push notifications (no companion app), installing the companion app, a dashboard.

## 4. Behaviour

### 4.1 States

The state is the lock's own auto-lock setting combined with the bolt. No occupancy helper exists.

| State | On the lock | Experience |
|---|---|---|
| **Occupied** | auto-lock **off** (`switch.*_auto_lock` = off), bolt unlocked | every unlock stays unlocked; walk in and out freely. HA never unlocks the door by itself except R5 |
| **Resting** | auto-lock **off**, bolt **locked** | the household is here but quiet (in bed, or over at the main cabin with phones on the site Wi-Fi): the door is locked for safety. The next entry is a fingerprint or code, which returns to Occupied. Anyone who unlocks meanwhile leaves the door unlocked, as in Occupied |
| **Vacant** | auto-lock **armed** at *S* seconds (default 30), bolt locked | nobody is here: a guest code or courier unlock re-locks itself |

Resting and Vacant feel the same to the household (the next entry costs one credential either way); they differ in
whether auto-lock is armed, which matters for other people's unlocks and for what the dashboard shows. HA never
unlocks a resting door on motion (D12): that keeps a PIR false trigger from opening the cabin.

### 4.2 Event classification

Applied to the new value of the last-trigger sensor, case-insensitive:

| Class | Rule | Matches (from the integration's table) | Deliberately excluded |
|---|---|---|---|
| `UNLOCK` | contains `unlock` and none of `fail`, `try to`, `expired`, `passage` | unlock by app / passcode / IC card / fingerprint / wrist strap / Mechanical key / gateway / unlock key / hotel card / key fob, unlock with QR code success, unlocked due to the high temperature, 3D Face Unlock Success | `auto unlock at passage mode` (firmware, not a person), `Unlock with QR code failed…`, `Try to unlock with a deleted card`, `3D Face Unlock Failed (Locked)` |
| `EXPLICIT_LOCK` | starts with `lock by`, `lock with` or `locked via`, and does not contain `fail` | lock by app / fingerprint / passcode / IC card / Mechanical key / lock key, Lock with QR code success, Locked via 3D Face | `Auto Lock`, `Dead lock with…`, `Double locked`, `System locked (…)`, `Lock with QR code failed…` |
| `AUTO_LOCK` | equals `auto lock` | Auto Lock (real webhook record or the integration's simulated one) | — |

Transitions **from** `unavailable`/`unknown`/none are ignored: they are stale values coming back after an outage,
not new events.

### 4.3 Rules

`vacate()` = lock the bolt if the lock is not `locked` → wait ≤ 10 s for `locked` → `configure_autolock(enabled: true, seconds: S)` → wait ≤ 10 s for the auto-lock switch to be `on` → on failure, one retry after 10 s (see §4.7) → on second failure, notify.
`rest()` = lock the bolt if the lock is not `locked` → wait ≤ 10 s for `locked` → same retry and notification; auto-lock is left untouched (off).

Motion sensors come in two groups: **activity sensors** (living areas) push the timer; **resting sensors** (bedrooms) never push it and instead count as presence when the timer settles.

| # | Name | Triggers | Conditions | Actions |
|---|---|---|---|---|
| **R0** | Track activity | any **activity** motion sensor changes between `on` and `off` (resting sensors excluded); any tracked person changes between two known states | both old and new state are known (not `unavailable`/`unknown`/none), so boot-time and outage transitions never push *H* | motion: `H := max(H, now + M)`; person: `H := max(H, now + P)` |
| **R1** | Occupy | last-trigger changes to an `UNLOCK` value; **or** the lock entity changes to `unlocked` from `locked`/`locking`/`unlocking` | trusted-operator list empty, or last-operator value is in it (trimmed, case-insensitive); a non-empty list without an operator sensor denies everything (README says so) | `H := max(H, now + M)`; if the auto-lock switch is `on` → `configure_autolock(enabled: false)` |
| **R2** | Vacate now | last-trigger changes to an `EXPLICIT_LOCK` value | — | `vacate()` |
| **R3** | Settle on timer | time reaches *H*; passage sensor turns `off`; lock entity returns from `unavailable`; safety net every 5 min; HA start (+2 min grace) | `now ≥ H`; no activity sensor is `on`; passage sensor is not `on`; auto-lock switch is `off` **or** the lock is not `locked` | if the lock entity is `unavailable`: persistent notification "settle pending, lock unreachable" and stop (re-evaluated when the lock returns and by the safety net); else if no tracked person is `home` **and** no resting sensor is `on` or changed within the last M → **Vacant**: `vacate()`; otherwise → **Resting**: `rest()` |
| **R4** | Vacate at night | time equals the night time | night enabled; auto-lock switch `off` **or** lock not `locked` | `vacate()` (passage mode does **not** suppress this) |
| **R5** | Undo stale countdown | last-trigger changes to `AUTO_LOCK` | feature enabled; auto-lock switch is `off`; previous value was `UNLOCK` and changed < 90 s ago | `lock.unlock` once |

Why R1 also listens to the lock entity: the last-trigger sensor emits no event when the same string repeats. After a
timer or night vacate performed by HA the string may still read `unlock by fingerprint`; the next fingerprint would
then be invisible to a string trigger, while the lock entity still goes `locked → unlocked`. HA's only own unlock is
R5, which leads to the same Occupied state, so this cannot ping-pong.

Why R2 listens only to strings: HA's own `lock.lock` (R3/R4) changes the lock entity, and the lock may echo it as
`lock by app`; a string-triggered R2 re-running `vacate()` is idempotent, a lock-entity-triggered R2 would be
indistinguishable from a human lock.

### 4.4 Vacancy timing and the helper

*H* ("vacancy not before") is an `input_datetime` helper with date and time, one per instance, **required**. It is the
later of *last activity-sensor event + M* and *last presence event + P* (and *last unlock + M*), maintained by R0/R1
with a monotonic `max`. Because both constraints only ever move forward in time, one value encodes both delays exactly.

- The settle happens at *H* only if the live states agree (R3 conditions). If an activity sensor is still `on` at *H*,
  its next `off` pushes *H* again and the time trigger re-arms. Presence does **not** block the settle any more; it
  decides the outcome (Resting vs Vacant).
- Outcome at *H*: **Vacant** when no tracked person is `home` and every resting sensor is `off` and unchanged for at
  least M (a sleeping person triggers a bedroom PIR rarely, so the last change, not only the current state, counts);
  **Resting** otherwise. The safety net re-evaluates every 5 min, so a Resting door becomes Vacant (auto-lock armed)
  once the phones have left and the resting window has expired, without any new event.
- Defaults: M = 45 min (today's value), P = 20 min (longer than the few-minute Wi-Fi flaps seen in August).
- No activity sensors configured → *H* is driven by presence changes and unlocks only (README warns that Wi-Fi presence
  alone was blind for hours on 2026-08-21). No persons and no resting sensors → every settle is Vacant.
- `unavailable`/`unknown` sensors and persons count as *not active* (no motion / not home): a dead sensor fails towards
  **locked**, never towards an open door. After a restart a resting sensor's `last_changed` is the boot time, so for
  up to M after a boot the outcome may be Resting where Vacant was due; the safety net corrects it.
- HA start: R3 waits 2 min (Z-Wave and UniFi settle) before evaluating. If *H* is already in the past and the
  conditions hold, the door settles then; otherwise the restored *H* fires at its original time.

### 4.5 Passage mode = guest hours

The lock's own passage-mode schedule (set in the TTLock app) is for people HA cannot see. The blueprint never
configures passage mode; it only reads the passage-mode sensor (optional input).

- While passage mode is `on`: R3 is suppressed (condition), R1/R2/R5 unchanged. A household member's first unlock still
  switches auto-lock off, and "unlocked until the vacate rules apply" holds because R3 resumes when the window ends.
- Window end (`passage → off`) is an R3 trigger. With guests gone at 15:00 and a 16:00 window end, the door locks at
  16:00; with guests still moving inside, M minutes after the last motion.
- Whether the firmware also locks by itself at window end is verified on the device (V2); R3 does not depend on it.
- R4 (night) is **not** suppressed by passage mode: guest hours end at the night time.

### 4.6 Night

Optional (default off). At the configured time, `vacate()` regardless of motion, presence or passage mode, from
Occupied or Resting alike (a Resting door only gets its auto-lock armed). The next morning the first fingerprint or
code re-occupies (R1). There is deliberately no motion-based morning unlock: a PIR
false trigger would open an empty cabin.

### 4.7 Failure handling

TTLock actions go cloud → gateway → lock and can fail (gateway offline, cloud hiccup). Success is observed through
state, not return values: after `configure_autolock` the coordinator updates the switch immediately, after `lock.lock`
the lock entity turns `locked`.

- Each `vacate()` step: call → `wait_template` for the expected state (≤ 10 s) → if not reached, wait 10 s and retry once
  **only if no newer lock event arrived** (the last-trigger sensor's `last_changed` is unchanged since the run started)
  → if still not reached, `persistent_notification.create` (fixed `notification_id` per instance) plus the optional
  notify action. A later successful `vacate()` dismisses that notification (`persistent_notification.dismiss`).
- If the lock entity is `unavailable` when a vacate is due, no call is made: the same notification is posted and the
  vacate is re-evaluated when the lock returns from `unavailable` and by the 5-minute safety-net trigger (§6).
- R1 failure: one retry, no notification. Worst case the user types the code once more.
- R5 failure: no retry, no notification (the user is at the door).

### 4.8 Restart behaviour

Motion and person *states* survive a restart (Z-Wave JS runs in its own snap; persons and the TTLock sensors are
restore-entities). HA re-stamps `last_changed` at startup, which is why timing lives in the helper (§4.4), not in
`for:` triggers. The Occupied/Vacant state lives in the lock and needs no resync.

### 4.9 Worked scenarios (also the acceptance and test storyboard)

1. **Arrival, phones invisible.** 14:15:00 `unlock by fingerprint` → R1: auto-lock off (~3 s), H = 15:00.
   14:15:30 `Auto Lock` (simulated and/or real) → R5: `lock.unlock` → `unlock by gateway` → R1 (no-op, H pushed).
   Door free. Motion keeps pushing H all day.
2. **Afternoon at Hovedhytta, phones on site Wi-Fi.** No living-area motion since 13:00 → H = 13:45 → persons `home`
   → **Resting**: bolt locked, auto-lock off. Return at 17:00 → fingerprint → R1 → Occupied. If instead the phones
   leave the site at 23:05 → R0: H = 23:25 → nobody home, bedroom quiet → **Vacant** (arm).
3. **Leaving for the week.** Keypad lock key → `lock by lock key` → R2 → locked + armed within seconds.
   Forgot to lock: last motion 10:00, phones gone 10:05 → H = max(10:45, 10:25) → locks 10:45.
4. **Guest hours 13–16.** Cleaner enters 13:30 (passage), leaves 14:30; H = 15:15 but passage `on` → no lock;
   16:00 passage `off` → R3 → lock 16:00.
5. **Night on at 23:00.** 23:00 `vacate()` despite motion. 07:30 fingerprint → R1 → occupied.
6. **Restart mid-timer.** As in 3, HA restarts 10:20; H restored = 10:45 → locks 10:45.
7. **Explicit lock while occupied, then return.** 21:00 `lock by lock key` → R2. 21:05 fingerprint → R1 → occupied.
8. **Guest without phone sits still 45 min.** H passes, nobody `home`, bedroom quiet → Vacant; the guest can leave
   (inside handle) and needs a code to come back. Same as today.
9. **Evening in bed, phones on Wi-Fi.** Living areas quiet from 22:30, bedroom motion 22:35 → H = 23:15 → someone
   `home` → **Resting**: door locked, auto-lock off. 07:10 first exit: bolt locked, so the return is a fingerprint →
   R1 → Occupied.
10. **Evening in bed, phones invisible (the Aug 21 case).** As 9, but persons `not_home`; the bedroom changed at 22:35,
    less than M before 23:15 → **Resting**, not Vacant. Had the bedroom been quiet for more than M as well → **Vacant**
    (armed). Either way the door is locked and the morning costs one fingerprint.

## 5. Blueprint interface

Grouped with blueprint input sections (HA ≥ 2024.6).

| Section / input | Selector | Default | Notes |
|---|---|---|---|
| **Lock** — `lock_entity` | entity, domain `lock`, integration `ttlock` | required | |
| `last_trigger_sensor` | entity, domain `sensor`, integration `ttlock` | required | the `*_last_trigger` sensor |
| `auto_lock_switch` | entity, domain `switch`, integration `ttlock` | required | the `*_auto_lock` switch |
| `last_operator_sensor` | entity, domain `sensor`, integration `ttlock` | none | needed only with a trusted list |
| `passage_mode_sensor` | entity, domain `binary_sensor`, integration `ttlock` | none | absent = treated as off |
| **Timers** — `vacancy_helper` | entity, domain `input_datetime` | required | must have date **and** time; README shows the 30-second creation |
| `motion_delay` (M) | number 1–720 min | 45 | |
| `presence_delay` (P) | number 1–720 min | 20 | |
| `armed_seconds` (S) | number 1–60 s | 30 | value used when re-arming |
| **Occupancy sources** — `motion_sensors` | entity, `binary_sensor`, device_class motion/occupancy, multiple | [] | activity sensors (living areas): push *H* |
| `resting_sensors` | entity, `binary_sensor`, device_class motion/occupancy, multiple | [] | bedrooms: never push *H*; count as presence for M after their last change |
| `presence_entities` | entity, domains `person`, `device_tracker`, multiple | [] | `home` = present |
| `trusted_operators` | text, multiple | [] | empty = anyone |
| **Night** — `night_enabled` | boolean | false | |
| `night_time` | time | 23:00 | |
| **Safety** — `stale_countdown_unlock` | boolean | true | R5 |
| `stale_countdown_window` | number 10–300 s | 90 | |
| `notify_actions` | action | [] | run after a failed vacate, in addition to the persistent notification |

Helper contract: written with `input_datetime.set_datetime` (`datetime:` ISO string, HA local time), read with
`states(helper) | as_datetime`. If the helper is `unavailable`/`unknown`, R0/R1 treat the current value as *now*.

## 6. Implementation notes

- One automation, `mode: queued`, `max: 100` (R0 fires on every motion change; a burst during a wait must never be
  dropped by the default queue limit of 10). Runs are short in the normal case (a few seconds); the worst case is a
  vacate with both steps failing and retried, about 1 min. Ordering is preserved; a queued R1 behind a failing vacate
  is delayed at most that long, and the retry guard (§4.7) skips the retry when the newer unlock has already changed
  the last-trigger sensor.
- Triggers carry `id`s (`activity_motion`, `activity_presence`, `unlock_string`, `unlock_state`, `explicit_lock`,
  `vacancy_time`, `passage_off`, `lock_available`, `safety_net`, `ha_start`, `night`, `auto_lock`); a top-level
  `choose` on `trigger.id` dispatches. `safety_net` is a `time_pattern` every 5 minutes that evaluates the R3
  conditions; it is a local no-op unless a vacate is overdue, and it also covers V4.
- Classification lives in `variables:` as booleans computed from `trigger.to_state.state | lower` with `is search()`,
  so the patterns exist once, are unit-tested through the automation, and show up in the automation trace for
  troubleshooting.
- `time` trigger with `at: !input vacancy_helper` re-arms whenever the helper changes; `at: !input night_time` for R4.
- R3's HA-start path: `trigger: homeassistant, event: start` → `delay: 00:02:00` → same condition block.
- Every TTLock call uses `continue_on_error: true` and is followed by a `wait_template` on the observable state.
- The blueprint contains no entity ids, names or site specifics; everything comes from inputs.

## 7. Repository, tests, CI

- Existing repo layout is flat: `automation/<name>.yaml`, `script/<name>.yaml`. The blueprint goes to
  `automation/cabin_auto_lock.yaml`; the README gets a section with the My-Home-Assistant import button, the behaviour
  table from §4, and the helper setup. License stays GPL-3.0 (the repo's).
- New in the repo: `pyproject.toml` (uv-managed), `tests/`, `.github/workflows/test.yml`.
- Tests run a real Home Assistant core through `pytest-homeassistant-custom-component`, pinned to the release matching
  the cabin's HA (2026.8.3, nearest 2026.8.x if no exact match). A fixture copies the blueprint into the test config
  dir and sets up `automation:` with `use_blueprint:` and the inputs; tests drive the last-trigger strings, motion,
  person and passage states, the helper and time, and assert the exact service calls (`ttlock.configure_autolock`,
  `lock.lock`, `lock.unlock`, `input_datetime.set_datetime`, `persistent_notification.create`).
- Test cases: T1 classification parametrized over the integration's **complete** `Event.EVENTS` table (every string
  is asserted as `UNLOCK`, `EXPLICIT_LOCK`, `AUTO_LOCK` or ignored), so a wording change is caught when the pinned
  integration version is bumped; T2 occupy via lock entity `unlocked` after an
  HA-performed vacate; T3 trusted list allow/deny; T4 explicit lock strings vs `Auto Lock`/`Dead lock`; T5 H = max of
  motion+M, presence+P; T6 settle at H only when the conditions hold, re-arm after push; T7 activity-only and presence-only
  configurations; T8 unavailable sensors count as inactive; T9 passage suppresses R3, window end locks; T10 night beats
  passage; T11 R5 inside/outside the window and only when auto-lock is off; T12 retry guard and notification; T13 HA
  start grace and restored H; T14 no self-trigger on HA's own lock/unlock; T15 stale values from `unavailable` ignored;
  T16 boot/outage transitions (`unknown`/`unavailable` ↔ known) neither push H nor occupy; T17 lock unavailable at H →
  notification, then vacate when the lock returns; T18 settle → Resting when a person is home (bolt locked, auto-lock
  untouched); T19 settle → Resting on a resting-sensor change within M with persons away; T20 settle → Vacant only when
  persons are away and resting sensors have been quiet for M; T21 Resting → Vacant via the safety net once presence and
  the resting window expire; T22 resting sensors never push H.
- Mutation pass before whole-branch review, each must turn at least one test red: AND → OR in R3; drop the `Auto Lock`
  exclusion; drop the passage gate; replace `max` with assignment in R0; remove the lock-entity trigger from R1;
  remove the retry guard; swap the Resting/Vacant outcome; count resting sensors as activity.

## 8. Deployment to Hytta and acceptance

Deploy over the persistent SSH master; config lives in the docker volume
`/var/snap/docker/common/var-lib-docker/volumes/homeassistant-config/_data`.

1. Create helper `input_datetime.hytta_vacancy_not_before` (date + time) in Settings → Helpers.
2. Copy the blueprint to `/config/blueprints/automation/jmgiaever/cabin_auto_lock.yaml` (or import from the raw URL
   once the branch is merged).
3. Create automation "Hytta auto-lock policy" with: `lock.joachim_cabin`, `sensor.joachim_cabin_last_trigger`,
   `switch.joachim_cabin_auto_lock`, `sensor.joachim_cabin_last_operator`, `binary_sensor.joachim_cabin_passage_mode`,
   activity motion = kitchen + living room, resting = bedroom (`binary_sensor.bedroom_motion_sensor_motion_detection`),
   presence = `person.lav918` + `person.lukas_alexander`, M = 45, P = 20, S = 30, night off, R5 on.
4. Back up `automations.yaml`, remove "Turn back on auto-lock" (it arms auto-lock after 45 min without motion even when the
   household is home, which contradicts the Resting outcome), `automation.reload`, confirm both changes in the UI.
5. Acceptance with M temporarily 2 min and P 1 min, all timestamps from the HA logbook/trace:
   A1 fingerprint unlock → auto-lock switch off: latency. A2 does the door physically re-lock ~30 s after the arrival
   unlock (V1)? If yes, R5 unlocks it: latency. A3 keypad lock key → lock `locked` + switch `on` with `seconds` 30.
   A4 leave (phones to flight mode, no motion) → Vacant at H: expected vs actual, auto-lock switch `on`. A5 HA restart
   mid-timer → lock at the original H. A6 if a passage window is configured in the TTLock app: lock at window end.
   A7 phones on Wi-Fi, living areas quiet → Resting at H: bolt locked, auto-lock switch still `off`. A8 phones in
   flight mode, bedroom motion inside the window, living areas quiet → Resting, not Vacant; then bedroom quiet past the
   window → the safety net arms auto-lock. A9 restore M = 45, P = 20 and confirm the instance values.
6. Report the measured numbers; anything not measured is reported as not verified.

Tip for the household, outside this design: enrolling Lukas's fingerprint makes arrival code-free as well.

## 9. Decisions record

| # | Decision | Reason |
|---|---|---|
| D1 | Occupancy begins with a successful **unlock event**, not with phone presence | Lock events are reliable and instant; Wi-Fi presence was blind for 8 h on 2026-08-21; no door is ever opened by a false "home" |
| D2 | **Anyone** who unlocks counts; a trusted-operator list is an optional input | Family site, guests get the same convenience (user choice); the input keeps the blueprint reusable for stricter cabins |
| D3 | The **lock's auto-lock setting is the state**; no occupancy helper | Restart-safe, visible in the existing switch and in Google Home, and every action is idempotent so double fires are harmless |
| D4 | The timer settles the door M after the last living-area motion (and P after the last presence change); the outcome is **Resting** when someone is home or a resting sensor changed within M, **Vacant** otherwise | A quiet evening in bed must lock the door even with phones on the Wi-Fi; presence and the bedroom decide only whether auto-lock is armed, and the bedroom covers the phone blind spot seen in August |
| D5 | An explicit lock beats every timer | A deliberate gesture at the keypad, app, dashboard or Google Home is the clearest "we are leaving" |
| D6 | Night lock optional, default off, beats passage mode, no morning motion-unlock | Household security wins over guest hours; a PIR false trigger must never open an empty cabin; one credential per morning is the accepted cost |
| D7 | Timers survive restarts through one **required** `input_datetime` helper holding "vacancy not before" as a monotonic max | User asked for restart-proof timers; a single value encodes both delays; one code path is easier to test than helper-or-fallback |
| D8 | Passage mode is **guest hours**: read, never configured; suppresses only the timer lock | Separates the firmware schedule for guests from the HA policy for the household; window end is handled by the vacancy rule so firmware behaviour does not matter |
| D9 | R5 re-unlocks once if `Auto Lock` follows within 90 s of occupying (default on) | The lock's countdown is already running when HA disables auto-lock, and the integration simulates an Auto Lock regardless; harmless when unnecessary, verified on the device |
| D10 | Failure handling: observe state, one guarded retry after 20 s, then persistent notification + optional action; fail towards locked | No companion app for push; a locked door is the safe failure; the guard prevents a stale retry from undoing a newer event |
| D11 | Occupy triggers on strings **or** the lock entity turning `unlocked`; explicit lock triggers on strings only; R5 on the `Auto Lock` string | The sensor has no `force_update`, so repeated strings are invisible; HA's own `lock.lock` must not look like a human lock; HA's only own unlock (R5) leads to the same state |
| D12 | HA never unlocks except R5 | Auto-unlock on approach was rejected: outdoor APs see phones tens of metres away and a false "home" would open the door |
| D13 | Existing repo `jmgiaever/home-assistant-blueprints`, flat `automation/` layout, GPL-3.0 | User's existing blueprint home; importable by raw URL; keeps the snap repo about the snap |
| D14 | Tests run a real HA core pinned to the cabin's version, plus a mutation pass | User rule: a test that passes is not evidence; the blueprint controls a physical door |
| D15 | `mode: queued` | Preserves event order without a state machine; runs are short; the retry guard covers the one case where a queued run would apply stale intent |
| D16 | Unavailable/unknown sensors count as inactive; 2 min startup grace | Fail towards locked; Z-Wave and UniFi need a moment after boot |
| D17 | The old "Turn back on auto-lock" automation is removed (backed up) at deploy | It re-arms on motion alone, contradicting D4 |
| D18 | Notification hook is an `action` selector | Lets each instance choose TTS, a script or nothing without the blueprint knowing about notify platforms |
| D19 | Classification by string patterns with explicit exclusions rather than an allow-list of exact strings | New firmware strings of the same shape (e.g. new unlock methods) keep working; exclusions are the dangerous cases and are enumerated |
| D20 | Resting is encoded as auto-lock off + bolt locked; no helper | Fully derivable from the lock like the other states, and a wake-up unlock could be added later without a migration |
| D21 | Resting sensors (bedrooms) never push the timer and count as presence for M after their last change; no unlock on motion | Bedroom motion means people are in bed, not active; unlocking on motion was rejected because a PIR false trigger could open the cabin while the family is at the main cabin with phones on the site Wi-Fi |
| D22 | Waits of 10 s, one retry after 10 s, `mode: queued` with `max: 100`, a 5-minute safety-net trigger | Keeps the worst-case run near one minute so a queued occupy is never far behind, never drops a motion burst, and guarantees an overdue settle is retried without depending on the time trigger re-arming |

## 10. To verify on the device during acceptance

| # | Question | Impact if the answer is "no" |
|---|---|---|
| V1 | Does disabling auto-lock cancel a countdown already running on the lock? | None: R5 covers it either way; only the acceptance numbers differ |
| V2 | Does the firmware lock by itself when a passage window ends? | None: R3 locks at window end when the cabin is empty |
| V3 | Which strings does the lock echo for HA-initiated `lock.lock` / `lock.unlock` (`lock by app`? `unlock by gateway`)? | Confirms the self-trigger analysis in D11; both possibilities are handled |
| V4 | Is the `time` trigger on the `input_datetime` re-armed reliably when R0 pushes H many times an hour? | If not, add a minute-rate template trigger on `now() >= H` as a fallback |

## 11. Limitations and future work (not in this design)

- Events that happen while HA is unreachable are lost (the webhook cannot be delivered; the 15-min poll restores the
  lock state but not the reason). Consequence: one extra code, never an open door.
- Companion app on the phones would add GPS/zone presence and push notifications; the blueprint needs no change
  (persons are already the input).
- A door sensor would enable "door left open" alerts.
- A "wake-up unlock" (first living-area motion while Resting unlocks the bolt so the morning exit costs nothing) was
  considered and rejected for now (D21). If wanted later it needs a presence-confirmed guard and a time window, and
  no state migration (D20).
- Managing guest hours (passage schedules) or passcodes from HA is a separate blueprint if ever wanted.
- Upstream: a small PR to `hass-ttlock` setting `force_update` on the last-trigger and last-operator sensors would make
  repeated identical events visible; the blueprint must not depend on it (D11), so it is a separate follow-up.
