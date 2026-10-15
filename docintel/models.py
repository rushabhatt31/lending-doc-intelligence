"""Core data models shared by every agent."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now() -> datetime:
    return datetime.now(timezone.utc)


class Segment(str, Enum):
    RETAIL = "retail"
    SME = "sme"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_WEIGHT = {
    Severity.INFO: 0,
    Severity.LOW: 5,
    Severity.MEDIUM: 15,
    Severity.HIGH: 30,
    Severity.CRITICAL: 60,
}


class Document(BaseModel):
    id: str = Field(default_factory=lambda: _id("doc"))
    filename: str
    sha256: str
    text: str = ""
    # Structured payload for machine-readable inputs (CSV rows, GST JSON, financials JSON)
    data: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    doc_type: str | None = None
    classification_confidence: float = 0.0
    fields: dict[str, Any] = Field(default_factory=dict)
    uploaded_at: datetime = Field(default_factory=now)


class RiskSignal(BaseModel):
    code: str
    message: str
    severity: Severity
    source_agent: str
    document_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class AgentResult(BaseModel):
    agent: str
    status: str = "ok"  # ok | skipped | error
    summary: str = ""
    output: dict[str, Any] = Field(default_factory=dict)
    signals: list[RiskSignal] = Field(default_factory=list)
    # Things a human must supply or confirm -> become pendency queries
    open_items: list[dict[str, Any]] = Field(default_factory=list)
    duration_ms: int = 0


class QueryStatus(str, Enum):
    OPEN = "open"
    RESPONDED = "responded"
    RESOLVED = "resolved"
    WAIVED = "waived"


class PendencyQuery(BaseModel):
    """A credit-officer / customer query that blocks or conditions the decision."""

    id: str = Field(default_factory=lambda: _id("qry"))
    application_id: str
    raised_by: str  # agent name or officer id
    assignee: str  # "customer" | "credit_officer" | "ops"
    category: str  # e.g. missing_document, discrepancy, verification
    question: str
    required_doc_type: str | None = None
    blocking: bool = True
    status: QueryStatus = QueryStatus.OPEN
    sla_hours: int = 48
    created_at: datetime = Field(default_factory=now)
    responses: list[dict[str, Any]] = Field(default_factory=list)
    resolved_at: datetime | None = None
    resolution_note: str | None = None


class Applicant(BaseModel):
    name: str
    pan: str | None = None
    mobile: str | None = None
    email: str | None = None
    business_name: str | None = None
    gstin: str | None = None
    declared_monthly_income: float | None = None
    existing_emi: float = 0.0
    requested_amount: float | None = None
    requested_tenure_months: int | None = None
    sector: str | None = None


class LoanApplication(BaseModel):
    id: str = Field(default_factory=lambda: _id("app"))
    segment: Segment
    product: str = "personal_loan"
    applicant: Applicant
    documents: list[Document] = Field(default_factory=list)
    results: dict[str, AgentResult] = Field(default_factory=dict)
    decision: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now)
