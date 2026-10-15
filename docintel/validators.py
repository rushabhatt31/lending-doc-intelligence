"""Checksum and format validators for Indian KYC identifiers.

These catch typos and crude forgeries *before* any external API call:
a fabricated Aadhaar or GSTIN usually fails its check digit.
"""
from __future__ import annotations

import re

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

# PAN 4th character encodes holder type
PAN_HOLDER_TYPES = {
    "P": "individual", "C": "company", "H": "huf", "F": "firm", "A": "aop",
    "T": "trust", "B": "boi", "L": "local_authority", "J": "artificial_juridical", "G": "government",
}

# --- Verhoeff (Aadhaar check digit) ---
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_check_digit(number: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[c])


def aadhaar_valid(aadhaar: str) -> bool:
    digits = re.sub(r"\D", "", aadhaar)
    # Aadhaar never starts with 0 or 1
    return len(digits) == 12 and digits[0] not in "01" and verhoeff_valid(digits)


def mask_aadhaar(aadhaar: str) -> str:
    """UIDAI rules: never persist the full number outside an Aadhaar Data Vault."""
    digits = re.sub(r"\D", "", aadhaar)
    return f"XXXX XXXX {digits[-4:]}" if len(digits) == 12 else "XXXX XXXX XXXX"


def pan_valid(pan: str) -> bool:
    return bool(PAN_RE.match(pan or "")) and pan[3] in PAN_HOLDER_TYPES


def pan_holder_type(pan: str) -> str | None:
    return PAN_HOLDER_TYPES.get(pan[3]) if pan_valid(pan) else None


def ifsc_valid(ifsc: str) -> bool:
    return bool(IFSC_RE.match(ifsc or ""))


_GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = _GST_CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _GST_CHARS[(36 - total % 36) % 36]


def gstin_valid(gstin: str) -> bool:
    g = (gstin or "").upper()
    return bool(GSTIN_RE.match(g)) and gstin_check_char(g[:14]) == g[14]


def gstin_pan(gstin: str) -> str | None:
    return gstin[2:12] if gstin and len(gstin) == 15 else None
