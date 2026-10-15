"""Integration layer for external verification APIs.

In production each interface is backed by a vendor (NSDL/Protean PAN,
DigiLocker/UIDAI, account aggregator, penny-drop, EPFO, bureaus, e-sign).
Mocks are deterministic so the pipeline and tests run offline. Swap in a
real implementation by registering it on the `Connectors` object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class PanVerifier(Protocol):
    def verify(self, pan: str, name: str) -> dict: ...


class PennyDrop(Protocol):
    def verify(self, account_number: str, ifsc: str) -> dict: ...


class EmploymentVerifier(Protocol):
    def lookup(self, uan: str | None, pan: str | None) -> dict: ...


class BureauSoftPull(Protocol):
    def soft_pull(self, pan: str, name: str, mobile: str | None) -> dict: ...


class MockPan:
    def verify(self, pan: str, name: str) -> dict:
        return {"status": "valid", "name_on_record": name, "source": "mock"}


class MockPennyDrop:
    def __init__(self, registered_names: dict[str, str] | None = None):
        self.names = registered_names or {}

    def verify(self, account_number: str, ifsc: str) -> dict:
        return {"status": "active", "beneficiary_name": self.names.get(account_number), "source": "mock"}


class MockEmployment:
    def __init__(self, records: dict[str, dict] | None = None):
        self.records = records or {}

    def lookup(self, uan: str | None, pan: str | None) -> dict:
        return self.records.get(uan or "") or self.records.get(pan or "") or {"status": "not_found", "source": "mock"}


class MockBureau:
    def soft_pull(self, pan: str, name: str, mobile: str | None) -> dict:
        # Deterministic pseudo-score so demos are reproducible
        score = 650 + (sum(map(ord, pan or "x")) % 200)
        return {"score": score, "active_loans": 1, "dpd_90_last_12m": 0, "enquiries_last_6m": 2, "source": "mock"}


@dataclass
class Connectors:
    pan: PanVerifier = field(default_factory=MockPan)
    penny_drop: PennyDrop = field(default_factory=MockPennyDrop)
    employment: EmploymentVerifier = field(default_factory=MockEmployment)
    bureau: BureauSoftPull = field(default_factory=MockBureau)
