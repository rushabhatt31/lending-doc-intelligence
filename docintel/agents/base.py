from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from ..audit import AuditLog
from ..connectors import Connectors
from ..llm import LLM
from ..models import AgentResult, Document, LoanApplication, RiskSignal, Severity


@dataclass
class Config:
    assumed_interest_rate_pct: float = 14.0
    foir_cap_pct: float = 50.0
    recon_variance_tolerance_pct: float = 10.0
    name_match_threshold: float = 0.8
    velocity_window_days: int = 30


@dataclass
class AgentContext:
    app: LoanApplication
    audit: AuditLog
    llm: LLM
    connectors: Connectors
    identity_registry: Any  # fraud.IdentityRegistry
    config: Config = field(default_factory=Config)

    def docs(self, *types: str) -> list[Document]:
        return [d for d in self.app.documents if d.doc_type in types]

    def result(self, agent: str) -> AgentResult | None:
        return self.app.results.get(agent)


class BaseAgent:
    name: str = "base"
    segments: tuple[str, ...] = ("retail", "sme")

    def applies(self, ctx: AgentContext) -> bool:
        return ctx.app.segment.value in self.segments

    def run(self, ctx: AgentContext) -> AgentResult:
        start = time.perf_counter()
        if not self.applies(ctx):
            res = AgentResult(agent=self.name, status="skipped", summary=f"Not applicable to {ctx.app.segment.value}")
        else:
            try:
                res = self._run(ctx)
            except Exception as exc:  # an agent failing must not take down the pipeline
                res = AgentResult(
                    agent=self.name, status="error", summary=f"{type(exc).__name__}: {exc}",
                    output={"traceback": traceback.format_exc(limit=3)},
                    open_items=[{
                        "assignee": "ops", "category": "system_error", "blocking": True,
                        "question": f"{self.name} failed ({exc}); review manually.",
                    }],
                )
        res.duration_ms = int((time.perf_counter() - start) * 1000)
        ctx.audit.record(ctx.app.id, self.name, "agent_completed", {
            "status": res.status, "summary": res.summary,
            "signals": [s.code for s in res.signals], "duration_ms": res.duration_ms,
        })
        return res

    def _run(self, ctx: AgentContext) -> AgentResult:  # pragma: no cover
        raise NotImplementedError

    def signal(self, code: str, message: str, severity: Severity, doc: Document | None = None, **evidence) -> RiskSignal:
        return RiskSignal(code=code, message=message, severity=severity, source_agent=self.name,
                          document_id=doc.id if doc else None, evidence=evidence)
