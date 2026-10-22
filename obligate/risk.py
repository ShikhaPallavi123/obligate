"""Decide which obligations could turn into costly mistakes."""
from __future__ import annotations

import re
from datetime import date

COMPLIANCE_TERMS = {
    "contract": "contract", "nda": "contract", "msa": "contract", "sow": "contract", "sign": "signature",
    "signature": "signature", "renew": "renewal", "renewal": "renewal", "license": "licensing",
    "audit": "audit", "soc 2": "audit", "soc2": "audit", "sox": "audit", "iso": "audit",
    "gdpr": "privacy", "hipaa": "privacy", "ccpa": "privacy", "privacy": "privacy", "consent": "privacy",
    "customer data": "privacy", "patient": "privacy", "pii": "privacy",
    "security": "security", "breach": "security", "incident": "security", "vulnerability": "security",
    "regulator": "regulatory", "regulatory": "regulatory", "compliance": "regulatory", "filing": "regulatory",
    "file the": "regulatory", "tax": "financial", "invoice": "financial", "payment": "financial",
    "pay ": "financial", "refund": "financial", "payroll": "financial", "budget": "financial",
    "legal": "legal", "lawyer": "legal", "counsel": "legal", "policy": "policy", "sla": "customer promise",
    "deadline": "deadline", "penalty": "penalty", "fine": "penalty", "insurance": "insurance",
}


def compliance_tags(text: str) -> list[str]:
    low = f" {text.lower()} "
    tags = {cat for term, cat in COMPLIANCE_TERMS.items() if re.search(rf"\b{re.escape(term.strip())}\b", low)}
    return sorted(tags)


def score(task: str, owner: str | None, due: date | None, hedged: bool, kind: str,
          today: date, status: str = "open") -> tuple[str, list[str], list[str]]:
    """Return (level, reasons, compliance_tags). level is high | medium | low."""
    tags = compliance_tags(task)
    reasons: list[str] = []
    points = 0

    if status != "open":
        return "low", ["completed"], tags

    if not owner:
        reasons.append("nobody owns it")
        points += 3
    if kind == "request":
        reasons.append("asked, but never confirmed")
        points += 2
    if not due:
        reasons.append("no deadline set")
        points += 1
    else:
        days = (due - today).days
        if days < 0:
            reasons.append(f"overdue by {-days} day{'s' if days != -1 else ''}")
            points += 4
        elif days <= 2:
            reasons.append("due within 48 hours")
            points += 2
    if hedged:
        reasons.append("soft commitment (\"I'll try\")")
        points += 1
    if tags:
        reasons.append("compliance-sensitive (" + ", ".join(tags) + ")")
        points += 2

    level = "high" if points >= 4 or (tags and not owner) else "medium" if points >= 2 else "low"
    return level, reasons, tags
