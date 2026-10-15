from __future__ import annotations

import re
from datetime import date, datetime
from difflib import SequenceMatcher

_DATE_FORMATS = ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d/%m/%y", "%d-%b-%y"]


def parse_amount(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("₹", "").replace("INR", "").replace("Rs.", "").strip()
    neg = s.endswith("Dr") or s.startswith("-")
    s = re.sub(r"(Cr|Dr)$", "", s).strip().lstrip("-")
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def parse_date(value: str) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


_STOP = {"pvt", "private", "ltd", "limited", "llp", "india", "the", "and", "co", "mr", "mrs", "ms", "technologies", "services"}


def normalize_name(name: str) -> str:
    tokens = re.findall(r"[a-z]+", (name or "").lower())
    return " ".join(t for t in tokens if t not in _STOP)


def name_similarity(a: str, b: str) -> float:
    """Order-insensitive name match in [0,1]; handles 'Sharma Rahul' vs 'RAHUL SHARMA'."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb)
    # Token-level: every token in the shorter name must find a close token in the other.
    # Whole-string similarity is too lenient on surnames ("Karan Mehta" vs "Karan Malhotra").
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    token = min(max(SequenceMatcher(None, s, t).ratio() for t in long_) for s in short)
    # Initials: "R Sharma" vs "Rahul Sharma"
    initials = all(any(t2.startswith(t1) for t2 in tb) for t1 in ta) or all(any(t1.startswith(t2) for t1 in ta) for t2 in tb)
    return round(max(jaccard, token, 0.85 if initials else 0.0), 3)


def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def emi(principal: float, annual_rate_pct: float, months: int) -> float:
    r = annual_rate_pct / 1200
    if r == 0:
        return principal / months
    return principal * r * (1 + r) ** months / ((1 + r) ** months - 1)


def principal_for_emi(emi_amt: float, annual_rate_pct: float, months: int) -> float:
    r = annual_rate_pct / 1200
    if r == 0:
        return emi_amt * months
    return emi_amt * ((1 + r) ** months - 1) / (r * (1 + r) ** months)
