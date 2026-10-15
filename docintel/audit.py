"""Tamper-evident audit trail.

Every agent action, extracted value and human response is appended as a
hash-chained record: each entry stores the SHA-256 of the previous entry, so
editing or deleting any past line breaks `verify()`. This is what lets a
credit officer or auditor replay *why* the system said what it said.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from .models import now

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._entries: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if self.path and self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    self._entries.append(json.loads(line))

    @staticmethod
    def _digest(entry: dict[str, Any]) -> str:
        body = {k: v for k, v in entry.items() if k != "hash"}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()

    def record(self, application_id: str, actor: str, action: str, details: dict[str, Any] | None = None) -> dict:
        with self._lock:
            prev = self._entries[-1]["hash"] if self._entries else GENESIS
            entry = {
                "seq": len(self._entries),
                "ts": now().isoformat(),
                "application_id": application_id,
                "actor": actor,
                "action": action,
                "details": details or {},
                "prev_hash": prev,
            }
            entry["hash"] = self._digest(entry)
            self._entries.append(entry)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as fh:
                    fh.write(json.dumps(entry, default=str) + "\n")
            return entry

    def trail(self, application_id: str | None = None) -> list[dict[str, Any]]:
        if application_id is None:
            return list(self._entries)
        return [e for e in self._entries if e["application_id"] == application_id]

    def verify(self) -> dict[str, Any]:
        prev = GENESIS
        for e in self._entries:
            if e["prev_hash"] != prev or self._digest(e) != e["hash"]:
                return {"valid": False, "broken_at_seq": e["seq"]}
            prev = e["hash"]
        return {"valid": True, "entries": len(self._entries)}
