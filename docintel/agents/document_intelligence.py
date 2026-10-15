"""Document Intelligence Agent: classify -> extract -> validate -> tamper-check.

Runs on every document at upload time so the checklist and pendency queue
update immediately, before the heavier analysis agents run.
"""
from __future__ import annotations

import re
from typing import Any

from ..doc_types import COMMON_FIELDS, DOC_TYPES, REQUIRED_DOCS
from ..models import AgentResult, Document, Severity
from ..utils import parse_amount
from .. import validators as v
from .base import AgentContext, BaseAgent

EDITING_TOOLS = ["photoshop", "gimp", "canva", "ilovepdf", "pdfescape", "sejda", "smallpdf", "pdf-xchange",
                 "foxit phantompdf", "nitro", "inkscape", "paint", "pixlr", "microsoft word", "libreoffice writer"]
# Documents a bank or government system generates; a word processor producing them is a red flag
SYSTEM_GENERATED = {"bank_statement", "form_16", "itr_v", "epfo_passbook", "gst_certificate", "aadhaar", "pan_card"}
AMOUNT_FIELDS = {"gross_salary", "net_salary", "pf_deduction", "tds", "amount_payable", "monthly_rent",
                 "gross_total_income", "emi", "sanctioned_amount"}
LLM_CONFIDENCE_FLOOR = 0.6


class DocumentIntelligenceAgent(BaseAgent):
    name = "document_intelligence"

    # ---------- classification ----------
    def classify(self, doc: Document, ctx: AgentContext | None = None) -> tuple[str | None, float, str]:
        structural = self._classify_structured(doc)
        if structural:
            return structural, 0.99, "structure"

        text = doc.text.lower()
        scores = []
        for dtype, spec in DOC_TYPES.items():
            hits = sum(1 for kw in spec["keywords"] if kw in text)
            if hits >= spec["min_hits"]:
                scores.append((hits / len(spec["keywords"]), hits, dtype))
        scores.sort(reverse=True)
        if scores:
            top_ratio, top_hits, top = scores[0]
            runner_up = scores[1][0] if len(scores) > 1 else 0
            margin = (top_ratio - runner_up) / top_ratio
            conf = round(min(0.99, (0.55 + 0.4 * top_ratio) * (0.7 + 0.3 * margin)), 2)
        else:
            top, conf = None, 0.0

        if conf < LLM_CONFIDENCE_FLOOR and ctx and ctx.llm.available:
            ans = ctx.llm.extract_json(
                "Classify this Indian lending document. Choose doc_type from: "
                + ", ".join(DOC_TYPES) + ', or "unknown". Return {"doc_type": str, "confidence": 0-1}.',
                doc.text,
            )
            if ans and ans.get("doc_type") in DOC_TYPES:
                ctx.audit.record(ctx.app.id, self.name, "llm_classification", {"document_id": doc.id, **ans})
                return ans["doc_type"], float(ans.get("confidence", 0.7)), "llm"
        return top, conf, "rules"

    @staticmethod
    def _classify_structured(doc: Document) -> str | None:
        d = doc.data
        if isinstance(d, list) and d and isinstance(d[0], dict):
            keys = {k.lower().strip() for k in d[0]}
            if "balance" in keys and ({"debit", "credit"} <= keys or {"withdrawal", "deposit"} <= keys):
                return "bank_statement"
        if isinstance(d, dict):
            keys = {k.lower() for k in d}
            if {"gstr1", "gstr3b"} & keys:
                return "gst_returns_data"
            if "financials" in keys or "years" in keys:
                return "financial_statements"
        return None

    # ---------- extraction ----------
    def extract(self, doc: Document) -> dict[str, Any]:
        fields: dict[str, Any] = {}
        patterns = dict(COMMON_FIELDS)
        patterns.update(DOC_TYPES.get(doc.doc_type or "", {}).get("fields", {}))
        for fname, pattern in patterns.items():
            m = re.search(pattern, doc.text)
            if m:
                val = m.group(1).strip()
                fields[fname] = parse_amount(val) if fname in AMOUNT_FIELDS else val

        # Aadhaar: validate, then never keep the full number (vault-only)
        if "aadhaar" in fields:
            raw = fields.pop("aadhaar")
            fields["aadhaar_masked"] = v.mask_aadhaar(raw)
            fields["aadhaar_checksum_valid"] = v.aadhaar_valid(raw)
            fields["aadhaar_last4"] = re.sub(r"\D", "", raw)[-4:]
        if "pan" in fields:
            fields["pan_valid"] = v.pan_valid(fields["pan"])
            fields["pan_holder_type"] = v.pan_holder_type(fields["pan"])
        if "gstin" in fields:
            fields["gstin_valid"] = v.gstin_valid(fields["gstin"])
        if "ifsc" in fields:
            fields["ifsc_valid"] = v.ifsc_valid(fields["ifsc"])
        if doc.doc_type == "gst_returns_data" and isinstance(doc.data, dict):
            fields["gstin"] = doc.data.get("gstin")
            fields["gstin_valid"] = v.gstin_valid(fields["gstin"] or "")
        return fields

    # ---------- tamper / forensics ----------
    def forensics(self, doc: Document) -> list:
        sigs = []
        meta = doc.metadata
        tool_fields = " ".join(str(meta.get(k) or "") for k in ("producer", "creator", "exif_software")).lower()
        tool = next((t for t in EDITING_TOOLS if t in tool_fields), None)
        if tool:
            sev = Severity.HIGH if doc.doc_type in SYSTEM_GENERATED else Severity.MEDIUM
            sigs.append(self.signal("DOC_EDITING_SOFTWARE", f"{doc.filename} was produced/edited with '{tool}'", sev, doc, tool=tool))
        if meta.get("created") and meta.get("modified") and meta["created"] != meta["modified"]:
            sigs.append(self.signal("DOC_MODIFIED_AFTER_CREATION", f"{doc.filename} modified after creation", Severity.LOW, doc,
                                    created=meta["created"], modified=meta["modified"]))
        fonts = {re.sub(r"^[A-Z]{6}\+", "", f).split("-")[0] for f in meta.get("fonts", [])}
        if doc.doc_type in SYSTEM_GENERATED and len(fonts) > 3:
            sigs.append(self.signal("DOC_FONT_INCONSISTENCY", f"{doc.filename} uses {len(fonts)} font families", Severity.MEDIUM, doc,
                                    fonts=sorted(fonts)))
        f = doc.fields
        if f.get("pan") and not f.get("pan_valid"):
            sigs.append(self.signal("PAN_FORMAT_INVALID", f"PAN {f['pan']} fails format check", Severity.HIGH, doc))
        if "aadhaar_checksum_valid" in f and not f["aadhaar_checksum_valid"] and doc.doc_type == "aadhaar":
            sigs.append(self.signal("AADHAAR_CHECKSUM_FAIL", "Aadhaar number fails Verhoeff checksum", Severity.HIGH, doc))
        if f.get("gstin") and not f.get("gstin_valid"):
            sigs.append(self.signal("GSTIN_CHECKSUM_FAIL", f"GSTIN {f['gstin']} fails checksum", Severity.HIGH, doc))
        if f.get("gross_salary") and f.get("net_salary") and f["net_salary"] > f["gross_salary"]:
            sigs.append(self.signal("SALARY_NET_GT_GROSS", "Net pay exceeds gross pay", Severity.HIGH, doc))
        return sigs

    # ---------- single-document entry point (used at upload) ----------
    def process_document(self, doc: Document, ctx: AgentContext | None = None) -> None:
        dtype, conf, method = self.classify(doc, ctx)
        doc.doc_type, doc.classification_confidence = dtype, conf
        doc.fields = self.extract(doc)
        doc.metadata["classification_method"] = method
        if ctx and ctx.llm.available and dtype in {"salary_slip", "form_16"} and "gross_salary" not in doc.fields:
            extra = ctx.llm.extract_json(
                'Extract {"employer": str, "gross_salary": number, "net_salary": number, "pay_month": str} '
                "from this document; use null when absent.", doc.text)
            if extra:
                doc.fields.update({k: val for k, val in extra.items() if val is not None and k not in doc.fields})
                doc.metadata["llm_fields"] = sorted(extra)

    # ---------- application-level run ----------
    def _run(self, ctx: AgentContext) -> AgentResult:
        res = AgentResult(agent=self.name)
        inventory = []
        for doc in ctx.app.documents:
            if doc.doc_type is None:
                self.process_document(doc, ctx)
            res.signals.extend(self.forensics(doc))
            inventory.append({"document_id": doc.id, "filename": doc.filename, "doc_type": doc.doc_type,
                              "confidence": doc.classification_confidence, "fields": doc.fields})
            if doc.doc_type is None:
                res.open_items.append({"assignee": "ops", "category": "classification", "blocking": False,
                                       "question": f"Could not classify '{doc.filename}'. Confirm document type."})
            elif doc.classification_confidence < LLM_CONFIDENCE_FLOOR:
                res.open_items.append({"assignee": "ops", "category": "classification", "blocking": False,
                                       "question": f"Low-confidence classification of '{doc.filename}' as {doc.doc_type} "
                                                   f"({doc.classification_confidence:.0%}). Confirm."})
            if doc.metadata.get("needs_ocr") and not doc.text.strip():
                res.open_items.append({"assignee": "customer", "category": "document_quality", "blocking": True,
                                       "question": f"'{doc.filename}' is an image without readable text. Upload a clearer or digital copy."})

        present = {d.doc_type for d in ctx.app.documents}
        missing = []
        for group in REQUIRED_DOCS[ctx.app.segment.value]:
            if not present & set(group):
                missing.append(group)
                res.open_items.append({
                    "assignee": "customer", "category": "missing_document", "blocking": True,
                    "required_doc_type": "|".join(group),  # any one satisfies it
                    "question": "Please upload " + " or ".join(g.replace("_", " ") for g in group) + ".",
                })

        res.output = {"documents": inventory, "missing_document_groups": missing,
                      "checklist_complete": not missing}
        res.summary = (f"{len(inventory)} documents classified; "
                       f"{'checklist complete' if not missing else f'{len(missing)} required document group(s) missing'}; "
                       f"{len(res.signals)} forensic signal(s)")
        return res
