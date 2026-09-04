"""GSTIN structural validation.

A GSTIN is 15 characters: 2-digit state code, 10-char PAN, 1 entity digit,
'Z', then a mod-36 check character. Validating the check character locally
rules out most OCR mangling and all invented numbers without a network call,
which is why it is enough to lift an invoice from tier C to tier B.
"""
import re

CODEPOINTS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

STATE_CODES = {
    "27": "Maharashtra", "29": "Karnataka", "32": "Kerala",
    "33": "Tamil Nadu", "36": "Telangana", "24": "Gujarat",
    "07": "Delhi", "06": "Haryana", "09": "Uttar Pradesh",
    "19": "West Bengal", "23": "Madhya Pradesh", "08": "Rajasthan",
}


def checksum_char(first14: str) -> str:
    """Compute the mod-36 check character for the first 14 characters."""
    factor, total = 2, 0
    for ch in reversed(first14):
        cp = CODEPOINTS.index(ch)
        addend = factor * cp
        factor = 1 if factor == 2 else 2
        total += (addend // 36) + (addend % 36)
    return CODEPOINTS[(36 - (total % 36)) % 36]


def validate(gstin: str | None) -> dict:
    """Returns {valid, reason, state} - never raises, so it is safe to call
    on whatever the extractor produced."""
    if not gstin:
        return {"valid": False, "reason": "missing", "state": None}
    g = gstin.strip().upper().replace(" ", "")
    if len(g) != 15:
        return {"valid": False, "reason": f"length {len(g)}, expected 15", "state": None}
    if not GSTIN_RE.match(g):
        return {"valid": False, "reason": "does not match GSTIN structure", "state": None}
    if any(c not in CODEPOINTS for c in g):
        return {"valid": False, "reason": "invalid characters", "state": None}
    expected = checksum_char(g[:14])
    if expected != g[14]:
        return {"valid": False,
                "reason": f"checksum mismatch (expected {expected}, got {g[14]})",
                "state": None}
    return {"valid": True, "reason": "checksum ok", "state": STATE_CODES.get(g[:2])}


def make_valid(first14: str) -> str:
    """Build a checksum-valid GSTIN. Used by the seed data generator."""
    first14 = first14.upper()
    return first14 + checksum_char(first14)
