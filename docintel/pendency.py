"""Pendency management: the queue of open questions blocking a credit decision.

Agents raise `open_items`; the orchestrator turns them into PendencyQuery
objects routed to the customer, credit officer or ops. Queries auto-resolve
when a document of the required type arrives, and every state change is
written to the audit log.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from .audit import AuditLog
from .models import PendencyQuery, QueryStatus, now


class PendencyManager:
    def __init__(self, audit: AuditLog):
        self.audit = audit
        self._queries: dict[str, PendencyQuery] = {}

    def raise_query(self, application_id: str, raised_by: str, item: dict[str, Any]) -> PendencyQuery:
        # De-duplicate: same app + same question that is still open
        for q in self._queries.values():
            if (
                q.application_id == application_id
                and q.question == item["question"]
                and q.status in (QueryStatus.OPEN, QueryStatus.RESPONDED)
            ):
                return q
        q = PendencyQuery(application_id=application_id, raised_by=raised_by, **item)
        self._queries[q.id] = q
        self.audit.record(application_id, raised_by, "query_raised", q.model_dump(mode="json"))
        return q

    def respond(self, query_id: str, actor: str, message: str, document_id: str | None = None) -> PendencyQuery:
        q = self._queries[query_id]
        q.responses.append({"actor": actor, "message": message, "document_id": document_id, "ts": now().isoformat()})
        q.status = QueryStatus.RESPONDED
        self.audit.record(q.application_id, actor, "query_responded", {"query_id": q.id, "message": message})
        return q

    def resolve(self, query_id: str, actor: str, note: str, waive: bool = False) -> PendencyQuery:
        q = self._queries[query_id]
        q.status = QueryStatus.WAIVED if waive else QueryStatus.RESOLVED
        q.resolved_at = now()
        q.resolution_note = note
        self.audit.record(q.application_id, actor, "query_waived" if waive else "query_resolved",
                          {"query_id": q.id, "note": note})
        return q

    def auto_resolve_on_document(self, application_id: str, doc_type: str, document_id: str) -> list[PendencyQuery]:
        resolved = []
        for q in self.for_application(application_id, open_only=True):
            if q.required_doc_type and doc_type in q.required_doc_type.split("|"):
                resolved.append(self.resolve(q.id, "system", f"Satisfied by uploaded {doc_type} ({document_id})"))
        return resolved

    def for_application(self, application_id: str, open_only: bool = False) -> list[PendencyQuery]:
        qs = [q for q in self._queries.values() if q.application_id == application_id]
        if open_only:
            qs = [q for q in qs if q.status in (QueryStatus.OPEN, QueryStatus.RESPONDED)]
        return sorted(qs, key=lambda q: q.created_at)

    def get(self, query_id: str) -> PendencyQuery:
        return self._queries[query_id]

    def all(self) -> list[PendencyQuery]:
        return list(self._queries.values())

    def sla_report(self, application_id: str | None = None) -> dict[str, Any]:
        qs = [q for q in self._queries.values() if application_id in (None, q.application_id)]
        open_qs = [q for q in qs if q.status in (QueryStatus.OPEN, QueryStatus.RESPONDED)]
        t = now()
        breached = [q.id for q in open_qs if t - q.created_at > timedelta(hours=q.sla_hours)]
        by_assignee: dict[str, int] = {}
        for q in open_qs:
            by_assignee[q.assignee] = by_assignee.get(q.assignee, 0) + 1
        return {
            "total": len(qs),
            "open": len(open_qs),
            "blocking_open": sum(1 for q in open_qs if q.blocking),
            "sla_breached": breached,
            "open_by_assignee": by_assignee,
        }
