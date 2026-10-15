"""REST API.

    uvicorn docintel.api:app --reload
    open http://localhost:8000/docs
"""
from __future__ import annotations

import os

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from .doc_types import DOC_TYPES, REQUIRED_DOCS
from .models import Applicant, Segment
from .orchestrator import LendingOrchestrator

app = FastAPI(
    title="Lending Document Intelligence",
    version="0.1.0",
    description="Agentic document intelligence for loan origination: KYC, bank statements, income, "
                "GST, financials and fraud, with pendency management and a tamper-evident audit trail.",
)
orch = LendingOrchestrator(audit_path=os.getenv("DOCINTEL_AUDIT_PATH"))


class CreateApplication(BaseModel):
    segment: Segment
    product: str = "personal_loan"
    applicant: Applicant


class QueryResponse(BaseModel):
    actor: str
    message: str


class QueryResolution(BaseModel):
    actor: str
    note: str
    waive: bool = False


class Question(BaseModel):
    question: str
    actor: str = "credit_officer"


def _app(app_id: str):
    if app_id not in orch.applications:
        raise HTTPException(404, f"Application {app_id} not found")
    return orch.applications[app_id]


@app.get("/health")
def health():
    return {"status": "ok", "llm_enabled": orch.llm.available, "applications": len(orch.applications)}


@app.get("/doc-types")
def doc_types():
    return {"types": {k: v["category"] for k, v in DOC_TYPES.items()}, "required": REQUIRED_DOCS}


@app.post("/applications", status_code=201)
def create_application(body: CreateApplication):
    a = orch.create_application(body.segment, body.applicant, body.product)
    return {"application_id": a.id}


@app.post("/applications/{app_id}/documents", status_code=201)
async def upload(app_id: str, files: list[UploadFile] = File(...)):
    _app(app_id)
    out = []
    for f in files:
        doc = orch.add_document(app_id, f.filename or "upload", await f.read())
        out.append({"document_id": doc.id, "filename": doc.filename, "doc_type": doc.doc_type,
                    "confidence": doc.classification_confidence, "fields": doc.fields})
    return {"documents": out, "open_queries": len(orch.pendency.for_application(app_id, open_only=True))}


@app.post("/applications/{app_id}/process")
def process(app_id: str):
    _app(app_id)
    orch.process(app_id)
    return orch.report(app_id)


@app.get("/applications/{app_id}")
def report(app_id: str):
    _app(app_id)
    return orch.report(app_id)


@app.get("/applications/{app_id}/results/{agent}")
def agent_result(app_id: str, agent: str):
    a = _app(app_id)
    if agent not in a.results:
        raise HTTPException(404, f"No result for {agent}; run /process first")
    return a.results[agent].model_dump(mode="json")


@app.get("/applications/{app_id}/queries")
def queries(app_id: str, open_only: bool = False):
    _app(app_id)
    return [q.model_dump(mode="json") for q in orch.pendency.for_application(app_id, open_only)]


@app.post("/queries/{query_id}/respond")
def respond(query_id: str, body: QueryResponse):
    try:
        return orch.pendency.respond(query_id, body.actor, body.message).model_dump(mode="json")
    except KeyError:
        raise HTTPException(404, "Query not found")


@app.post("/queries/{query_id}/resolve")
def resolve(query_id: str, body: QueryResolution):
    try:
        return orch.pendency.resolve(query_id, body.actor, body.note, body.waive).model_dump(mode="json")
    except KeyError:
        raise HTTPException(404, "Query not found")


@app.get("/pendency/sla")
def sla():
    return orch.pendency.sla_report()


@app.post("/applications/{app_id}/ask")
def ask(app_id: str, body: Question):
    _app(app_id)
    return orch.ask(app_id, body.question, body.actor)


@app.get("/applications/{app_id}/audit")
def audit(app_id: str):
    _app(app_id)
    return orch.audit.trail(app_id)


@app.get("/audit/verify")
def verify_audit():
    return orch.audit.verify()
