"""Bank Statement Analyser Agent.

Parses statements (CSV / account-aggregator JSON rows, or text PDFs of any bank
layout), then computes ABB, monthly cash flows, categorised spend, EMI
obligations, bounces, and balance-continuity checks that expose edited statements.
"""
from __future__ import annotations

import calendar
import re
import statistics
from collections import defaultdict
from datetime import date

from ..models import AgentResult, Document, Severity
from ..utils import month_key, name_similarity, parse_amount, parse_date
from .base import AgentContext, BaseAgent

CATEGORY_RULES: list[tuple[str, str]] = [
    ("bounce_charge", r"(rtn|return|bounce|dishono).*(chg|chrg|charge|fee)|(chg|chrg|charge).*(rtn|return|bounce)"),
    ("bounce", r"\b(rtn|return|returned|bounce|insuff\w*|dishono\w*|unpaid)\b"),
    ("salary", r"\bsal(ary)?\b|payroll|salaries"),
    ("emi_loan", r"\bemi\b|\bloan\b|\bnach\b|\bach d|\becs\b|finance ltd|fin ?serv"),
    ("cash_deposit", r"cash dep|by cash|cdm|cash deposit"),
    ("cash_withdrawal", r"\batm\b|cash wdl|cash withdrawal|\bself\b"),
    ("tax", r"gst payment|\btds\b|income tax|cbdt|advance tax"),
    ("investment", r"mutual fund|\bsip\b|zerodha|groww|\bnps\b|\bppf\b|\bfd\b|fixed deposit"),
    ("utilities", r"electricity|bescom|tneb|airtel|\bjio\b|vodafone|bsnl|\bgas\b|water bill|broadband"),
    ("charges", r"charges|\bchrg\b|\bfee\b|sms alert|amc"),
    ("interest", r"\bint\.? ?(cr|pd|paid)|interest"),
    ("upi", r"\bupi\b"),
    ("transfer", r"\bneft\b|\bimps\b|\brtgs\b|\bift\b|\btrf\b|transfer"),
]
_DATE_START = re.compile(r"^\s*(\d{2}[/-]\d{2}[/-]\d{2,4}|\d{2}[- ][A-Za-z]{3}[- ]\d{2,4}|\d{4}-\d{2}-\d{2})\s+(.*)$")
_AMOUNT = re.compile(r"-?[\d,]+\.\d{2}(?:\s?(?:Cr|Dr))?")
ABB_DAYS = (5, 10, 15, 20, 25)
CREDIT_HINTS = re.compile(r"\b(by|cr|credit|salary|deposit|refund|interest)\b", re.I)


def categorise(narration: str) -> str:
    n = narration.lower()
    for cat, pattern in CATEGORY_RULES:
        if re.search(pattern, n):
            return cat
    return "other"


def _pick(row: dict, *names: str):
    for k, v in row.items():
        if k and k.lower().strip() in names:
            return v
    return None


def parse_rows(rows: list[dict]) -> list[dict]:
    txns = []
    for r in rows:
        d = parse_date(str(_pick(r, "date", "txn date", "transaction date", "value date") or ""))
        if not d:
            continue
        txns.append({
            "date": d,
            "narration": str(_pick(r, "narration", "description", "particulars", "remarks") or "").strip(),
            "debit": parse_amount(_pick(r, "debit", "withdrawal", "withdrawals", "dr")) or 0.0,
            "credit": parse_amount(_pick(r, "credit", "deposit", "deposits", "cr")) or 0.0,
            "balance": parse_amount(_pick(r, "balance", "closing balance")),
            "explicit": True,
        })
    return txns


def parse_text(text: str) -> tuple[list[dict], float | None]:
    """Layout-agnostic parser: date first, balance is the last amount, direction from balance delta."""
    opening = None
    m = re.search(r"Opening Balance\s*[:]?\s*(?:Rs\.?|INR)?\s*(-?[\d,]+\.\d{2})", text, re.I)
    if m:
        opening = parse_amount(m.group(1))
    txns, prev = [], opening
    for line in text.splitlines():
        m = _DATE_START.match(line)
        if not m:
            continue
        d = parse_date(m.group(1))
        amounts = _AMOUNT.findall(m.group(2))
        if not d or len(amounts) < 2:
            continue
        balance = parse_amount(amounts[-1])
        amount = abs(parse_amount(amounts[-2]) or 0.0)
        narration = m.group(2)[: m.group(2).find(amounts[0])].strip() if amounts else m.group(2)
        if prev is None:
            is_credit = bool(CREDIT_HINTS.search(narration))
        else:
            is_credit = balance >= prev
        txns.append({"date": d, "narration": narration, "debit": 0.0 if is_credit else amount,
                     "credit": amount if is_credit else 0.0, "balance": balance, "explicit": False})
        prev = balance
    return txns, opening


class BankStatementAgent(BaseAgent):
    name = "bank_statement_analyser"

    def _run(self, ctx: AgentContext) -> AgentResult:
        res = AgentResult(agent=self.name)
        docs = ctx.docs("bank_statement")
        if not docs:
            res.status, res.summary = "skipped", "No bank statement uploaded"
            return res

        txns: list[dict] = []
        opening = None
        for doc in docs:
            t, o = (parse_rows(doc.data), None) if isinstance(doc.data, list) else parse_text(doc.text)
            for x in t:
                x["document_id"] = doc.id
            txns.extend(t)
            opening = opening if opening is not None else o
            if not t:
                res.open_items.append({"assignee": "ops", "category": "parsing", "blocking": True,
                                       "question": f"No transactions parsed from '{doc.filename}'. Request AA data or a digital statement."})
            self._penny_drop(ctx, doc, res)

        txns.sort(key=lambda x: x["date"])
        for x in txns:
            x["category"] = categorise(x["narration"])
        if not txns:
            res.summary = "Statement uploaded but unreadable"
            return res

        res.signals.extend(self._continuity(txns, opening, docs))
        months = sorted({month_key(x["date"]) for x in txns})
        monthly = self._monthly(txns)
        abb, abb_monthly = self._abb(txns, opening)
        cats = self._categories(txns)
        bounces = [x for x in txns if x["category"] == "bounce"]
        emi_bounces = [x for x in bounces if re.search(r"nach|ach|ecs|emi|loan", x["narration"], re.I)]
        obligations = self._recurring(txns, "debit", {"emi_loan"})
        recurring_credits = self._recurring(txns, "credit", None, min_months=3)
        negative_days = sum(1 for x in txns if (x["balance"] or 0) < 0)

        required = 6 if ctx.app.segment.value == "retail" else 12
        if len(months) < required:
            res.open_items.append({
                "assignee": "customer", "category": "insufficient_period", "blocking": True,
                "question": f"Bank statement covers {len(months)} month(s); please share the last {required} months "
                            "(or consent via Account Aggregator).",
            })

        if emi_bounces:
            res.signals.append(self.signal("EMI_BOUNCE", f"{len(emi_bounces)} EMI/NACH return(s) in statement period",
                                           Severity.HIGH, None, dates=[str(x["date"]) for x in emi_bounces]))
        elif len(bounces) >= 3:
            res.signals.append(self.signal("FREQUENT_BOUNCES", f"{len(bounces)} cheque/payment returns", Severity.HIGH))
        elif bounces:
            res.signals.append(self.signal("BOUNCE", f"{len(bounces)} payment return(s)", Severity.MEDIUM))
        if negative_days:
            res.signals.append(self.signal("NEGATIVE_BALANCE", f"Balance negative on {negative_days} transaction(s)", Severity.MEDIUM))
        res.signals.extend(self._window_dressing(txns, monthly))

        avg_credit = statistics.mean(m["credits"] for m in monthly.values())
        res.output = {
            "period": {"from": str(txns[0]["date"]), "to": str(txns[-1]["date"]), "months": len(months)},
            "transaction_count": len(txns),
            "abb": round(abb, 2) if abb is not None else None,
            "abb_monthly": abb_monthly,
            "avg_monthly_credits": round(avg_credit, 2),
            "avg_monthly_debits": round(statistics.mean(m["debits"] for m in monthly.values()), 2),
            "monthly": monthly,
            "category_totals": cats,
            "bounce_count": len(bounces),
            "emi_bounce_count": len(emi_bounces),
            "emi_obligations": obligations,
            "total_monthly_emi_obligation": round(sum(o["typical_amount"] for o in obligations), 2),
            "recurring_credits": recurring_credits,
            "salary_credits": [{"date": str(x["date"]), "amount": x["credit"], "narration": x["narration"]}
                               for x in txns if x["category"] == "salary" and x["credit"] > 0],
            "account_holder": next((d.fields.get("name") for d in docs if d.fields.get("name")), None),
        }
        res.summary = (f"{len(txns)} txns over {len(months)} months; ABB ₹{res.output['abb']:,.0f}; "
                       f"{len(bounces)} bounce(s); EMI obligations ₹{res.output['total_monthly_emi_obligation']:,.0f}/month")
        return res

    # ---------- helpers ----------
    @staticmethod
    def _monthly(txns):
        out: dict[str, dict] = defaultdict(lambda: {"credits": 0.0, "debits": 0.0, "count": 0, "closing_balance": None})
        for x in txns:
            m = out[month_key(x["date"])]
            m["credits"] += x["credit"]
            m["debits"] += x["debit"]
            m["count"] += 1
            m["closing_balance"] = x["balance"]
        return {k: {kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in sorted(out.items())}

    @staticmethod
    def _abb(txns, opening):
        """ABB = mean of EOD balances on the 5th/10th/15th/20th/25th and last day of each month."""
        eod: dict[date, float] = {}
        for x in txns:
            if x["balance"] is not None:
                eod[x["date"]] = x["balance"]
        if not eod:
            return None, {}
        dates = sorted(eod)
        samples: dict[str, list[float]] = defaultdict(list)
        y, m = dates[0].year, dates[0].month
        end = dates[-1]
        while (y, m) <= (end.year, end.month):
            last = calendar.monthrange(y, m)[1]
            for day in (*ABB_DAYS, last):
                point = date(y, m, day)
                if point > end:
                    continue
                prior = [d for d in dates if d <= point]
                bal = eod[prior[-1]] if prior else opening
                if bal is not None:
                    samples[f"{y:04d}-{m:02d}"].append(bal)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        monthly = {k: round(statistics.mean(v), 2) for k, v in samples.items() if v}
        allv = [b for v in samples.values() for b in v]
        return (statistics.mean(allv) if allv else None), monthly

    @staticmethod
    def _categories(txns):
        out: dict[str, dict] = defaultdict(lambda: {"credit": 0.0, "debit": 0.0, "count": 0})
        for x in txns:
            c = out[x["category"]]
            c["credit"] += x["credit"]
            c["debit"] += x["debit"]
            c["count"] += 1
        return {k: {"credit": round(v["credit"], 2), "debit": round(v["debit"], 2), "count": v["count"]} for k, v in out.items()}

    @staticmethod
    def _recurring(txns, side, categories, min_months=2):
        groups: dict[str, list[dict]] = defaultdict(list)
        for x in txns:
            if x[side] <= 0 or (categories and x["category"] not in categories):
                continue
            key = re.sub(r"[\d/\-:#]+", " ", x["narration"].lower())
            key = " ".join(key.split()[:4])
            groups[key].append(x)
        out = []
        for key, items in groups.items():
            months = {month_key(i["date"]) for i in items}
            amounts = [i[side] for i in items]
            med = statistics.median(amounts)
            stable = all(abs(a - med) <= 0.15 * med for a in amounts)
            if len(months) >= min_months and stable:
                out.append({"pattern": key, "months": len(months), "typical_amount": round(med, 2),
                            "category": items[0]["category"], "last_date": str(items[-1]["date"])})
        return sorted(out, key=lambda o: -o["typical_amount"])

    def _continuity(self, txns, opening, docs):
        """prev_balance ± amount must equal balance. Edited statements usually break this."""
        breaks = []
        prev = opening
        for x in txns:
            if prev is not None and x["balance"] is not None:
                expected = round(prev + x["credit"] - x["debit"], 2)
                if abs(expected - x["balance"]) > 1.0:
                    breaks.append({"date": str(x["date"]), "narration": x["narration"][:40],
                                   "expected": expected, "stated": x["balance"]})
            prev = x["balance"]
        if not breaks:
            return []
        sev = Severity.CRITICAL if len(breaks) >= 3 else Severity.HIGH
        return [self.signal("STATEMENT_BALANCE_MISMATCH",
                            f"Running balance breaks in {len(breaks)} place(s); statement may be edited",
                            sev, docs[0], breaks=breaks[:10])]

    def _window_dressing(self, txns, monthly):
        sigs = []
        last_date = txns[-1]["date"]
        avg_credit = statistics.mean(m["credits"] for m in monthly.values()) or 1
        for x in txns:
            if (x["category"] == "cash_deposit" and (last_date - x["date"]).days <= 7
                    and x["credit"] >= 0.25 * avg_credit and x["credit"] % 1000 == 0):
                sigs.append(self.signal("WINDOW_DRESSING", f"Round cash deposit ₹{x['credit']:,.0f} just before statement end",
                                        Severity.MEDIUM, None, date=str(x["date"])))
        return sigs

    def _penny_drop(self, ctx: AgentContext, doc: Document, res: AgentResult):
        acct, ifsc = doc.fields.get("account_number"), doc.fields.get("ifsc")
        if not acct or not ifsc or "X" in acct:
            return
        pd = ctx.connectors.penny_drop.verify(acct, ifsc)
        ctx.audit.record(ctx.app.id, self.name, "penny_drop", {"account_last4": acct[-4:], "status": pd.get("status")})
        beneficiary = pd.get("beneficiary_name")
        if beneficiary and name_similarity(beneficiary, ctx.app.applicant.name) < ctx.config.name_match_threshold:
            res.signals.append(self.signal("BANK_ACCOUNT_NAME_MISMATCH",
                                           f"Account beneficiary '{beneficiary}' ≠ applicant", Severity.HIGH, doc))
