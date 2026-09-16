"""hass-ttlock v0.15.0 Event.EVENTS: id -> (action, description), and the class we expect."""

EVENTS: dict[int, tuple[str, str]] = {
    1: ("unlock", "unlock by app"),
    4: ("unlock", "unlock by passcode"),
    7: ("unlock", "unlock by IC card"),
    8: ("unlock", "unlock by fingerprint"),
    9: ("unlock", "unlock by wrist strap"),
    10: ("unlock", "unlock by Mechanical key"),
    11: ("lock", "lock by app"),
    12: ("unlock", "unlock by gateway"),
    29: ("unknown", "apply some force on the Lock"),
    30: ("close", "Door sensor closed"),
    31: ("open", "Door sensor open"),
    32: ("open", "open from inside"),
    33: ("lock", "lock by fingerprint"),
    34: ("lock", "lock by passcode"),
    35: ("lock", "lock by IC card"),
    36: ("lock", "lock by Mechanical key"),
    37: ("unknown", "Remote Control"),
    42: ("unknown", "received new local mail"),
    43: ("unknown", "received new other cities' mail"),
    44: ("unknown", "Tamper alert"),
    45: ("lock", "Auto Lock"),
    46: ("unlock", "unlock by unlock key"),
    47: ("lock", "lock by lock key"),
    48: ("unknown", "System locked ( Caused by, for example: Using INVALID Passcode/Fingerprint/Card several times)"),
    49: ("unlock", "unlock by hotel card"),
    50: ("unlock", "unlocked due to the high temperature"),
    51: ("unknown", "Try to unlock with a deleted card"),
    52: ("unknown", "Dead lock with APP"),
    53: ("unknown", "Dead lock with passcode"),
    54: ("unknown", "The car left (for parking lock)"),
    55: ("unlock", "unlock with key fob"),
    57: ("unlock", "unlock with QR code success"),
    58: ("unknown", "Unlock with QR code failed, it's expired"),
    59: ("unknown", "Double locked"),
    60: ("unknown", "Cancel double lock"),
    61: ("lock", "Lock with QR code success"),
    62: ("unknown", "Lock with QR code failed, the lock is double locked"),
    63: ("unlock", "auto unlock at passage mode"),
    67: ("unlock", "3D Face Unlock Success"),
    68: ("unknown", "3D Face Unlock Failed (Locked)"),
    69: ("lock", "Locked via 3D Face"),
    71: ("unknown", "3D Face Recognition Failed (Expired)"),
}

UNLOCK_IDS = {1, 4, 7, 8, 9, 10, 12, 46, 49, 50, 55, 57, 67}
EXPLICIT_LOCK_IDS = {11, 33, 34, 35, 36, 47, 61, 69}
AUTO_LOCK_IDS = {45}


def expected_class(event_id: int) -> str:
    """The class the blueprint must assign (spec §4.2)."""
    if event_id in UNLOCK_IDS:
        return "UNLOCK"
    if event_id in EXPLICIT_LOCK_IDS:
        return "EXPLICIT_LOCK"
    if event_id in AUTO_LOCK_IDS:
        return "AUTO_LOCK"
    return "IGNORED"
