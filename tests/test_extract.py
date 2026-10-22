"""Unit tests for commitment extraction, date parsing and risk scoring.  Run: python -m pytest -q"""
from datetime import date
from pathlib import Path

from obligate.dates import parse_due
from obligate.extract import extract_rules
from obligate.risk import score

THU = date(2026, 10, 22)  # a Thursday


def test_dates():
    assert parse_due("by Friday", THU)[0] == date(2026, 10, 23)
    assert parse_due("tomorrow", THU)[0] == date(2026, 10, 23)
    assert parse_due("next Tuesday", THU)[0] == date(2026, 10, 27)
    assert parse_due("on November 4th", THU)[0] == date(2026, 11, 4)
    assert parse_due("in two weeks", THU)[0] == date(2026, 11, 5)
    assert parse_due("end of month", THU)[0] == date(2026, 10, 31)
    assert parse_due("it's already late", THU)[0] < THU
    assert parse_due("sometime", THU) == (None, None)


def by_task(items, words):
    return next(c for c in items if words.lower() in c.task.lower())


def test_sample_meeting():
    t = (Path(__file__).parent.parent / "examples" / "vendor_review_meeting.txt").read_text()
    items = extract_rules(t, THU)
    assert len(items) == 8
    raj = by_task(items, "renewal contract")
    assert raj.owner == "Raj" and raj.due_date == date(2026, 10, 23) and raj.kind == "commitment"
    soc2 = by_task(items, "SOC 2")
    assert soc2.owner is None and soc2.kind == "team"
    gdpr = by_task(items, "GDPR")
    assert gdpr.owner == "Priya" and gdpr.kind == "commitment"  # "Yes, on it" confirmed the request
    assert by_task(items, "incident response").hedged
    assert by_task(items, "security review").owner == "Leo"
    assert by_task(items, "Q3 invoice").due_date < THU


def test_unconfirmed_request_stays_request():
    items = extract_rules("Maya: Sam, can you send the board deck by Monday?\nMaya: Moving on.", THU)
    assert items[0].owner == "Sam" and items[0].kind == "request"


def test_risk_unowned_compliance_is_high():
    level, reasons, tags = score("Renew the SOC 2 report", None, None, False, "team", THU)
    assert level == "high" and "audit" in tags and "nobody owns it" in reasons


def test_risk_owned_far_future_is_low():
    level, _, _ = score("Order team lunch", "Ana", date(2026, 11, 20), False, "commitment", THU)
    assert level == "low"


def test_bedrock_parsing_and_fallback(monkeypatch):
    import json
    import sys
    import types

    from obligate import extract as ex

    reply = [{"task": "Send the renewal contract to legal", "owner": "Raj", "kind": "commitment",
              "due_date": "2026-10-23", "due_phrase": "by Friday", "source_quote": "I'll send...", "hedged": False}]

    class FakeClient:
        def converse(self, **kw):
            assert kw["modelId"] and kw["messages"]
            return {"output": {"message": {"content": [{"text": "Here you go:\n" + json.dumps(reply)}]}}}

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda *a, **k: FakeClient()))
    monkeypatch.setenv("OBLIGATE_LLM", "bedrock")
    items, engine = ex.extract("Raj: I'll send the renewal contract to legal by Friday.", THU)
    assert engine == "bedrock" and items[0].owner == "Raj" and items[0].due_date == date(2026, 10, 23)

    class Broken:
        def converse(self, **kw):
            raise RuntimeError("no credentials")

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda *a, **k: Broken()))
    items, engine = ex.extract("Raj: I'll send the renewal contract to legal by Friday.", THU)
    assert engine == "rules" and items[0].owner == "Raj"
