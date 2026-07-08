"""169B WS inner blob bucket fingerprints (session-scoped LE32 headers)."""
from __future__ import annotations

import hashlib

# Official A/B/C/D buckets — LE32 header observed per session (not global across logins).
# Re-harvest via CDP or bundle export after re-login.
BUCKET_INNER_FP: dict[str, dict[str, int]] = {
    "A": {"le32_0": 2184623637, "le32_4": 2567683221},
    "B": {"le32_0": 3836255967, "le32_4": 2398594532},
    "C": {"le32_0": 1697468172, "le32_4": 630129842},
    "D": {"le32_0": 3357022190, "le32_4": 2712474244},
}

# Init sync inner (get_message_by_init): session-scoped inbox seed — NOT a send class.
# Header e7782e60ef32f06c, fp 6b4edee7… — stored as __init_sync__ in session cache.
INIT_SYNC_INNER_FP: dict[str, int] = {
    "le32_0": 1613658343,
    "le32_4": 1827681007,
}

# Empirical inner groups from live harvest pool (sha256 prefix → group name)
EMPIRICAL_INNER_FP: dict[str, str] = {
    "ec682565c0de88c3": "E",  # short text 1-8, 61, 79-80 transitional
    "7f0a9225d0d8ca5c": "B",
    "333f8adcf864ef84": "A",
    "18dd21e8572bfee2": "C",
    "c6bdf259d5c245b4": "D",
    "a8acd6bdec02a8d6": "F",  # long-text 82-119 cross-length reuse
    "09edf72361984f9b": "G",  # 120+ cluster
}


def classify_inner_bucket(inner: bytes) -> str | None:
    if len(inner) < 8:
        return None
    le0 = int.from_bytes(inner[0:4], "little")
    le4 = int.from_bytes(inner[4:8], "little")
    for name, fp in BUCKET_INNER_FP.items():
        if le0 == fp["le32_0"] and le4 == fp["le32_4"]:
            return name
    if le0 == INIT_SYNC_INNER_FP["le32_0"] and le4 == INIT_SYNC_INNER_FP["le32_4"]:
        return None  # INIT_SYNC is not an A–G send bucket
    prefix = hashlib.sha256(inner).hexdigest()[:16]
    return EMPIRICAL_INNER_FP.get(prefix)


def inner_group_id(inner: bytes) -> str:
    """Stable group key for session inner cache."""
    bucket = classify_inner_bucket(inner)
    if bucket:
        return f"bucket_{bucket}"
    return hashlib.sha256(inner).hexdigest()[:16]
