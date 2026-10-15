"""Document-type registry.

Adding a document type = adding one entry here: keywords for classification
and regexes for field extraction. No code changes. This is how the registry
scales from the ~25 types shipped here to 100+.
"""
from __future__ import annotations

AMT = r"(?:Rs\.?|INR|₹)?\s*([\d,]+(?:\.\d{1,2})?)"

# Extractors applied to every document regardless of type
COMMON_FIELDS = {
    "pan": r"\b([A-Z]{5}[0-9]{4}[A-Z])\b",
    "aadhaar": r"\b([2-9]\d{3}\s?\d{4}\s?\d{4})\b",
    "gstin": r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b",
    "ifsc": r"\b([A-Z]{4}0[A-Z0-9]{6})\b",
    "dob": r"(?:DOB|Date of Birth|Birth)\s*[:/]?\s*(\d{2}[/-]\d{2}[/-]\d{4})",
    "name": r"(?im)^\s*(?:Name|Customer Name|Account Holder(?: Name)?|Employee Name|Member Name)\s*[:]\s*([A-Za-z][A-Za-z .']{2,60}?)\s*$",
    "mobile": r"(?:Mobile|Phone|Mob)\s*(?:No\.?)?\s*[:]?\s*(?:\+91[\s-]?)?([6-9]\d{9})\b",
}

DOC_TYPES: dict[str, dict] = {
    # ---------- Identity / KYC ----------
    "pan_card": {
        "category": "kyc_identity",
        "keywords": ["income tax department", "permanent account number", "govt. of india", "pan card", "signature"],
        "min_hits": 2,
        "fields": {"father_name": r"(?im)^\s*Father'?s? Name\s*[:]\s*(.+?)\s*$"},
    },
    "aadhaar": {
        "category": "kyc_identity",
        "keywords": ["aadhaar", "unique identification authority", "uidai", "enrolment", "mera aadhaar", "vid"],
        "min_hits": 2,
        "fields": {"gender": r"\b(MALE|FEMALE|Male|Female)\b", "address": r"(?is)Address\s*[:]\s*(.+?)(?:\n\s*\n|$)"},
    },
    "passport": {
        "category": "kyc_identity",
        "keywords": ["republic of india", "passport", "nationality", "place of issue", "p<ind", "date of expiry"],
        "min_hits": 3,
        "fields": {"passport_no": r"\b([A-Z][0-9]{7})\b", "expiry": r"Date of Expiry\s*[:]?\s*(\d{2}/\d{2}/\d{4})"},
    },
    "voter_id": {
        "category": "kyc_identity",
        "keywords": ["election commission", "elector", "epic", "identity card"],
        "min_hits": 2,
        "fields": {"epic_no": r"\b([A-Z]{3}[0-9]{7})\b"},
    },
    "driving_licence": {
        "category": "kyc_identity",
        "keywords": ["driving licence", "transport", "dl no", "valid till", "cov", "licensing authority"],
        "min_hits": 2,
        "fields": {"dl_no": r"\b([A-Z]{2}[0-9]{2}\s?[0-9]{11})\b"},
    },
    # ---------- Address ----------
    "utility_bill": {
        "category": "kyc_address",
        "keywords": ["electricity", "bill date", "due date", "consumer no", "units consumed", "amount payable"],
        "min_hits": 3,
        "fields": {"consumer_no": r"Consumer No\.?\s*[:]?\s*(\w+)", "amount_payable": r"Amount Payable\s*[:]?\s*" + AMT},
    },
    "rent_agreement": {
        "category": "kyc_address",
        "keywords": ["rent agreement", "lease", "lessor", "lessee", "landlord", "tenant", "monthly rent"],
        "min_hits": 3,
        "fields": {"monthly_rent": r"monthly rent of\s*" + AMT},
    },
    # ---------- Income (retail) ----------
    "salary_slip": {
        "category": "income",
        "keywords": ["payslip", "pay slip", "salary slip", "basic", "hra", "net pay", "gross earnings", "deductions", "provident fund"],
        "min_hits": 3,
        "fields": {
            "employer": r"(?im)^\s*(?:Employer|Company)\s*[:]\s*(.+?)\s*$",
            "pay_month": r"(?:Pay Period|Pay Month|Month)\s*[:]?\s*([A-Za-z]{3,9}[\s-]\d{4})",
            "gross_salary": r"Gross (?:Earnings|Salary|Pay)\s*[:]?\s*" + AMT,
            "net_salary": r"Net (?:Pay|Salary)\s*[:]?\s*" + AMT,
            "pf_deduction": r"(?:Provident Fund|PF)\s*[:]?\s*" + AMT,
        },
    },
    "form_16": {
        "category": "income",
        "keywords": ["form no. 16", "form 16", "tan of the deductor", "section 203", "assessment year", "tax deducted"],
        "min_hits": 2,
        "fields": {
            "employer": r"(?im)^\s*Name and address of the Employer\s*[:]\s*(.+?)\s*$",
            "tan": r"\b([A-Z]{4}\d{5}[A-Z])\b",
            "assessment_year": r"Assessment Year\s*[:]?\s*(\d{4}-\d{2,4})",
            "gross_salary": r"Gross Salary\s*[:]?\s*" + AMT,
            "tds": r"(?:Total )?Tax Deducted\s*[:]?\s*" + AMT,
        },
    },
    "epfo_passbook": {
        "category": "income",
        "keywords": ["provident fund organisation", "uan", "member passbook", "establishment id", "employee share", "employer share"],
        "min_hits": 3,
        "fields": {
            "uan": r"UAN\s*[:]?\s*(\d{12})",
            "employer": r"(?im)^\s*Establishment Name\s*[:]\s*(.+?)\s*$",
            "establishment_id": r"Establishment ID\s*[:]?\s*([A-Z]{5}\d{7,})",
        },
    },
    "itr_v": {
        "category": "income",
        "keywords": ["income tax return verification", "itr-v", "acknowledgement number", "gross total income", "assessment year"],
        "min_hits": 2,
        "fields": {
            "assessment_year": r"Assessment Year\s*[:]?\s*(\d{4}-\d{2,4})",
            "gross_total_income": r"Gross Total Income\s*[:]?\s*" + AMT,
        },
    },
    # ---------- Banking ----------
    "bank_statement": {
        "category": "banking",
        "keywords": ["statement of account", "account statement", "opening balance", "closing balance", "withdrawal", "deposit", "narration", "chq"],
        "min_hits": 3,
        "fields": {
            "account_number": r"(?:A/c|Account)\s*(?:No\.?|Number)\s*[:]\s*([\dX]{6,20})",
            "bank_name": r"(?im)^\s*Bank\s*[:]\s*(.+?)\s*$",
        },
    },
    "cancelled_cheque": {
        "category": "banking",
        "keywords": ["or bearer", "rupees", "a/c no", "cancelled", "pay", "micr"],
        "min_hits": 3,
        "fields": {"account_number": r"A/c No\.?\s*[:]?\s*(\d{9,18})"},
    },
    "loan_sanction_letter": {
        "category": "banking",
        "keywords": ["sanction letter", "sanctioned amount", "rate of interest", "tenure", "emi"],
        "min_hits": 3,
        "fields": {"emi": r"EMI\s*[:]?\s*" + AMT, "sanctioned_amount": r"Sanctioned Amount\s*[:]?\s*" + AMT},
    },
    # ---------- Business (SME) ----------
    "gst_certificate": {
        "category": "business",
        "keywords": ["form gst reg-06", "registration certificate", "goods and services tax", "legal name", "constitution of business", "trade name"],
        "min_hits": 3,
        "fields": {
            "legal_name": r"(?im)^\s*Legal Name\s*[:]\s*(.+?)\s*$",
            "constitution": r"(?im)^\s*Constitution of Business\s*[:]\s*(.+?)\s*$",
        },
    },
    "gstr_return": {
        "category": "business",
        "keywords": ["gstr-3b", "gstr-1", "outward taxable supplies", "input tax credit", "tax period"],
        "min_hits": 2,
        "fields": {},
    },
    "udyam_certificate": {
        "category": "business",
        "keywords": ["udyam registration", "ministry of micro, small", "msme", "enterprise", "type of enterprise"],
        "min_hits": 2,
        "fields": {"udyam_no": r"\b(UDYAM-[A-Z]{2}-\d{2}-\d{7})\b", "enterprise_type": r"Type of Enterprise\s*[:]?\s*(\w+)"},
    },
    "certificate_of_incorporation": {
        "category": "business",
        "keywords": ["certificate of incorporation", "registrar of companies", "corporate identity number", "companies act"],
        "min_hits": 2,
        "fields": {"cin": r"\b([LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6})\b"},
    },
    "partnership_deed": {
        "category": "business",
        "keywords": ["partnership deed", "partners", "firm", "profit sharing ratio", "deed of partnership"],
        "min_hits": 3,
        "fields": {},
    },
    "balance_sheet": {
        "category": "financials",
        "keywords": ["balance sheet", "shareholders' funds", "reserves and surplus", "non-current liabilities", "current liabilities", "total assets"],
        "min_hits": 3,
        "fields": {},
    },
    "profit_and_loss": {
        "category": "financials",
        "keywords": ["profit and loss", "statement of profit", "revenue from operations", "other income", "total expenses", "profit before tax"],
        "min_hits": 3,
        "fields": {},
    },
    # ---------- Machine-readable (detected from structure, not keywords) ----------
    "gst_returns_data": {"category": "business", "keywords": [], "min_hits": 99, "fields": {}},
    "financial_statements": {"category": "financials", "keywords": [], "min_hits": 99, "fields": {}},
}

# What each segment must submit. Each inner list = "any one of".
REQUIRED_DOCS = {
    "retail": [
        ["pan_card"],
        ["aadhaar", "passport", "voter_id", "driving_licence"],
        ["bank_statement"],
        ["salary_slip", "form_16"],
    ],
    "sme": [
        ["pan_card"],
        ["gst_certificate", "gst_returns_data", "gstr_return"],
        ["bank_statement"],
        ["financial_statements", "balance_sheet", "profit_and_loss"],
    ],
}
