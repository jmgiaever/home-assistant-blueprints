#!/usr/bin/env python3
"""Mutation check for automation/cabin_auto_lock.yaml (spec §7).

Each mutation is a (name, anchor, replacement). The anchor must occur exactly once in the blueprint.
The mutated blueprint is written to a temp copy of the repo and the test suite must go RED.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
BLUEPRINT = REPO / "automation" / "cabin_auto_lock.yaml"

MUTATIONS: list[tuple[str, str, str]] = [
    ("drop the now >= H check",
     "settle_trigger and helper_ok and now_ts >= h_ts and",
     "settle_trigger and helper_ok and"),
    ("treat Auto Lock as an explicit lock",
     "is_explicit_lock: \"{{ (event_value is match('^(lock by|lock with|locked via)')) and 'fail' not in event_value }}\"",
     "is_explicit_lock: \"{{ (event_value is match('^(lock by|lock with|locked via|auto lock)')) and 'fail' not in event_value }}\""),
    ("drop the passage gate",
     "and not activity_on and not passage_on",
     "and not activity_on"),
    ("replace max with assignment in R0",
     "and event_is_fresh and push_ts > h_ts }}",
     "and event_is_fresh }}"),
    ("remove the lock-entity trigger from R1",
     "or trigger.id == 'unlock_state' }}",
     "or false }}"),
    ("remove the retry guard",
     "| first | default('')) == event_marker }}",
     "| first | default('')) == event_marker or true }}"),
    ("swap the resting/vacant classification",
     "classification: \"{{ 'resting' if (persons_home or resting_recent) else 'vacant' }}\"",
     "classification: \"{{ 'vacant' if (persons_home or resting_recent) else 'resting' }}\""),
    ("count resting sensors as activity",
     "activity_on: \"{{ expand(motion_sensors) |",
     "activity_on: \"{{ expand(motion_sensors + resting_sensors) |"),
    ("make a door rule depend on the state helper",
     "need_lock: \"{{ want_lock and not is_state(lock_entity, 'locked') }}\"",
     "need_lock: \"{{ want_lock and not is_state(lock_entity, 'locked') and is_state(state_select, 'vacant') }}\""),
    ("let rest() re-configure an armed lock",
     "{{ want_arm and (is_state(auto_lock_switch, 'off')",
     "{{ want_arm and (true"),
    ("ignore resting_lock_bolt",
     "want_lock: \"{{ policy == 'vacant' or resting_lock_bolt }}\"",
     "want_lock: \"{{ true }}\""),
    ("drop H := now in R2",
     "is_explicit_lock and event_is_fresh and h_ts > now_ts }}",
     "is_explicit_lock and event_is_fresh and false }}"),
]


def run_suite(repo_copy: pathlib.Path) -> int:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", "tests"],
        cwd=repo_copy, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode


def main() -> int:
    original = BLUEPRINT.read_text()
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        copy = pathlib.Path(tmp) / "repo"
        shutil.copytree(REPO, copy, ignore=shutil.ignore_patterns(".git", ".venv", ".pytest_cache", "__pycache__"))
        target = copy / "automation" / "cabin_auto_lock.yaml"
        for name, anchor, replacement in MUTATIONS:
            count = original.count(anchor)
            if count == 0:
                problems.append(f"ANCHOR  {name}: not found in the blueprint (list drifted)")
                continue
            target.write_text(original.replace(anchor, replacement))   # replaces every occurrence (the retry guard appears twice)
            rc = run_suite(copy)
            status = "killed" if rc != 0 else "SURVIVED"
            print(f"{status:9s} {name}")
            if rc == 0:
                problems.append(f"SURVIVED {name}")
        target.write_text(original)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"all {len(MUTATIONS)} mutations killed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
