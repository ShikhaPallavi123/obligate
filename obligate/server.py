"""Obligate - an MCP server that turns spoken commitments into tracked obligations.

Transport: Streamable HTTP at /mcp (MCP spec 2025-11-25 or later), ready for Alexa+.
Every tool returns a short `speech` string written to be read aloud by a voice
assistant, plus structured data for screens and agents.

Run:  python -m obligate.server          (http://localhost:8000/mcp, dashboard at /)
"""
from __future__ import annotations

import difflib
import html
import logging
import os
from datetime import date, timedelta

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer
except ImportError:  # SDK 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer
from mcp.types import ToolAnnotations

from .dates import parse_due
from .extract import extract
from .risk import compliance_tags
from .store import Store

log = logging.getLogger("obligate")

INSTRUCTIONS = """Obligate tracks the promises people make in meetings.
- After a meeting, call capture_meeting with the transcript (lines like "Name: what they said").
- When the user says "remind me I owe..." or "I promised to...", call add_obligation.
- "What do I owe?" / "what's due?" -> list_obligations. "What could go wrong?" -> risk_report.
- "Give me my briefing" -> briefing. "I sent the contract" -> complete_obligation.
- Nobody-owns-it items are the most dangerous: offer to assign_obligation them.
Read the `speech` field aloud; it is already written for voice."""

mcp = MCPServer(
    name="Obligate",
    title="Obligate - meeting commitment tracker",
    description="Turns spoken commitments in meetings into tracked obligations and flags compliance risks.",
    instructions=INSTRUCTIONS,
    version="1.0.0",
)
store = Store()

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)


# --- helpers ------------------------------------------------------------

def _today() -> date:
    override = os.environ.get("OBLIGATE_TODAY")  # lets demos run "as of" a fixed date
    return date.fromisoformat(override) if override else date.today()


def _say_due(d: str | None) -> str:
    if not d:
        return "no deadline yet"
    due = date.fromisoformat(d)
    days = (due - _today()).days
    if days < 0:
        return f"{-days} day{'s' if days != -1 else ''} overdue"
    if days == 0:
        return "due today"
    if days == 1:
        return "due tomorrow"
    if days < 7:
        return f"due {due.strftime('%A')}"
    return f"due {due.strftime('%B')} {due.day}"


def _verb(task: str) -> str:
    first = task.split()[0] if task else ""
    return task if first.isupper() and len(first) > 1 else task[:1].lower() + task[1:]


def _say_item(o: dict, with_owner: bool = True) -> str:
    """'Raj needs to send the renewal contract to legal, due tomorrow'"""
    t = f"{_verb(o['task'])}, {_say_due(o['due_date'])}"
    if not with_owner:
        return t
    return f"{o['owner']} needs to {t}" if o["owner"] else f"nobody owns: {t}"


def _why(o: dict) -> str:
    """The most useful reason not already implied by the due date."""
    for r in o["risk_reasons"]:
        if not r.startswith(("overdue", "due within", "no deadline")):
            return r
    return o["risk_reasons"][0] if o["risk_reasons"] else ""


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _join(parts: list[str], sep: str = ", ") -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return sep.join(parts[:-1]) + f"{sep}and " + parts[-1]


def _brief(o: dict) -> dict:
    keys = ["id", "task", "owner", "due_date", "status", "risk_level", "risk_reasons",
            "compliance_tags", "kind", "source_quote", "meeting_title"]
    return {k: o.get(k) for k in keys}


def _resolve(ref: str) -> dict | None:
    """Find an obligation by id ("OB-1A2B3C") or by what the user said ("the SOC 2 thing")."""
    hit = store.get(ref.strip())
    if hit:
        return hit
    opens = store.all("open")
    if not opens:
        return None
    q = ref.lower()
    scored = []
    for o in opens:
        text = o["task"].lower()
        overlap = len(set(q.split()) & set(text.split()))
        scored.append((overlap + difflib.SequenceMatcher(None, q, text).ratio(), o))
    best_score, best = max(scored, key=lambda x: x[0])
    return best if best_score >= 0.6 else None


# --- tools --------------------------------------------------------------

@mcp.tool(title="Capture meeting commitments", annotations=WRITE)
def capture_meeting(transcript: str, meeting_title: str = "Meeting", meeting_date: str | None = None) -> dict:
    """Extract every commitment from a meeting transcript and start tracking it.

    Args:
        transcript: The meeting text. Best as one line per turn, "Speaker: what they said".
        meeting_title: Short name for the meeting, e.g. "Vendor review".
        meeting_date: ISO date the meeting happened (defaults to today). Relative dates like
            "by Friday" are resolved against it.
    """
    mdate = date.fromisoformat(meeting_date) if meeting_date else _today()
    found, engine = extract(transcript, mdate)
    created, skipped = [], 0
    for c in found:
        if store.find_open_duplicate(c.task, c.owner):
            skipped += 1
            continue
        oid = store.add(task=c.task, owner=c.owner, kind=c.kind, due_date=c.due_date, due_phrase=c.due_phrase,
                        hedged=c.hedged, confidence=c.confidence, source_quote=c.source_quote,
                        speaker=c.speaker, meeting_title=meeting_title, meeting_date=mdate, engine=engine)
        created.append(store.get(oid))

    high = [o for o in created if o["risk_level"] == "high"]
    unowned = [o for o in created if not o["owner"]]
    if not created:
        speech = "I didn't hear any new commitments in that meeting."
    else:
        speech = f"I captured {len(created)} commitment{'s' if len(created) != 1 else ''} from {meeting_title}."
        if high:
            speech += f" {len(high)} {'are' if len(high) != 1 else 'is'} high risk."
            worst = high[0]
            speech += f" Most urgent: {_say_item(worst)}. That one is {_why(worst)}." if _why(worst).startswith("compliance") \
                else f" Most urgent: {_say_item(worst)}."
        if unowned:
            speech += (f" {len(unowned)} {'have' if len(unowned) != 1 else 'has'} no owner. "
                       "Want me to assign them?")
    return {"speech": speech, "engine": engine, "captured": [_brief(o) for o in created],
            "duplicates_skipped": skipped}


@mcp.tool(title="Add an obligation", annotations=WRITE)
def add_obligation(task: str, owner: str | None = None, due: str | None = None) -> dict:
    """Track a single commitment someone made, e.g. "I promised Dana the budget by Monday".

    Args:
        task: What must be done, e.g. "Send Dana the Q4 budget".
        owner: Who owes it. Use the speaker's name for "I"/"me". Leave empty if nobody owns it yet.
        due: When it's due, in plain words ("Friday", "next week", "Nov 3") or ISO date.
    """
    due_date, phrase = parse_due(due, _today()) if due else (None, None)
    oid = store.add(task=task.strip().rstrip("."), owner=owner, kind="commitment", due_date=due_date,
                    due_phrase=phrase, source_quote=f"Added by voice: {task}", engine="manual")
    o = store.get(oid)
    speech = f"Got it. {_cap(_say_item(o))}."
    if o["compliance_tags"]:
        speech += f" I flagged it as {', '.join(o['compliance_tags'])} sensitive."
    if not due_date:
        speech += " When is it due?"
    return {"speech": speech, "obligation": _brief(o)}


@mcp.tool(title="List obligations", annotations=READ)
def list_obligations(owner: str | None = None, due_within_days: int | None = None,
                     risk_level: str | None = None, include_completed: bool = False) -> dict:
    """List tracked obligations, soonest first.

    Args:
        owner: Only this person's obligations (case-insensitive). Use "nobody" for unowned ones.
        due_within_days: Only items due within this many days (overdue items always included).
        risk_level: "high", "medium" or "low".
        include_completed: Also show finished items.
    """
    items = store.all(None if include_completed else "open")
    if owner:
        if owner.lower() in {"nobody", "none", "unowned", "no one", "unassigned"}:
            items = [o for o in items if not o["owner"]]
        else:
            items = [o for o in items if o["owner"] and o["owner"].lower().split()[0] == owner.lower().split()[0]]
    if due_within_days is not None:
        limit = _today() + timedelta(days=due_within_days)
        items = [o for o in items if o["due_date"] and date.fromisoformat(o["due_date"]) <= limit]
    if risk_level:
        items = [o for o in items if o["risk_level"] == risk_level.lower()]

    n = len(items)
    plural = "s" if n != 1 else ""
    if not items:
        speech = "Nothing on the list. You're all clear."
    else:
        top = [_say_item(o, with_owner=not owner) for o in items[:3]]
        if owner and owner.lower() in {"nobody", "none", "unowned", "no one", "unassigned"}:
            speech = f"{n} obligation{plural} {'have' if n != 1 else 'has'} no owner: "
        elif owner:
            speech = f"{owner.split()[0].title()} has {n} open obligation{plural}: "
        else:
            speech = f"The team has {n} open obligation{plural}. "
            top = [_cap(t) for t in top]
        speech += _join(top, "; ") + "."
        if len(items) > 3:
            speech += f" Plus {len(items) - 3} more."
    return {"speech": speech, "count": len(items), "obligations": [_brief(o) for o in items]}


@mcp.tool(title="Risk report", annotations=READ)
def risk_report() -> dict:
    """Find the obligations most likely to become costly mistakes: overdue, unowned,
    unconfirmed, or touching contracts, audits, privacy, security, or money."""
    items = store.all("open")
    high = [o for o in items if o["risk_level"] == "high"]
    medium = [o for o in items if o["risk_level"] == "medium"]
    overdue = [o for o in items if o["days_until_due"] is not None and o["days_until_due"] < 0]
    unowned = [o for o in items if not o["owner"]]
    compliance = [o for o in items if o["compliance_tags"]]
    order = {"high": 0, "medium": 1, "low": 2}
    ranked = sorted(items, key=lambda o: (order[o["risk_level"]], o["days_until_due"] if o["days_until_due"] is not None else 99))

    if not high:
        speech = f"No high-risk items. {len(items)} open obligations, all on track." if items else "Nothing tracked yet."
    else:
        speech = f"{len(high)} high-risk item{'s' if len(high) != 1 else ''}. "
        speech += " ".join(f"{['First', 'Second', 'Third'][i]}, {_say_item(o)}. Why: {_why(o)}." for i, o in enumerate(high[:3]))
        if unowned:
            speech += f" {len(unowned)} still {'have' if len(unowned) != 1 else 'has'} no owner."
    return {"speech": speech,
            "summary": {"open": len(items), "high": len(high), "medium": len(medium), "overdue": len(overdue),
                        "unowned": len(unowned), "compliance_sensitive": len(compliance)},
            "ranked": [_brief(o) for o in ranked]}


@mcp.tool(title="Daily briefing", annotations=READ)
def briefing(owner: str | None = None) -> dict:
    """A spoken morning briefing: what's due today, what's overdue, and the biggest risk.

    Args:
        owner: Whose briefing. Omit for the whole team.
    """
    items = store.all("open")
    if owner:
        items = [o for o in items if o["owner"] and o["owner"].lower().split()[0] == owner.lower().split()[0]]
    overdue = [o for o in items if o["days_until_due"] is not None and o["days_until_due"] < 0]
    today = [o for o in items if o["days_until_due"] == 0]
    soon = [o for o in items if o["days_until_due"] is not None and 0 < o["days_until_due"] <= 3]
    name = owner.split()[0] if owner else "team"
    parts = [f"Good morning, {name}."]
    if overdue:
        parts.append(f"{len(overdue)} overdue: " + _join([_say_item(o, with_owner=not owner) for o in overdue[:2]], "; ") + ".")
    if today:
        parts.append("Due today: " + _join([_verb(o["task"]) for o in today[:3]], "; ") + ".")
    if soon:
        parts.append("Coming up: " + _join([_say_item(o, with_owner=not owner) for o in soon[:3]], "; ") + ".")
    if len(parts) == 1:
        parts.append("Nothing urgent. " + (f"You have {len(items)} open items." if items else "Your slate is clean."))
    return {"speech": " ".join(parts), "overdue": [_brief(o) for o in overdue],
            "due_today": [_brief(o) for o in today], "due_soon": [_brief(o) for o in soon]}


@mcp.tool(title="Complete an obligation", annotations=WRITE)
def complete_obligation(obligation: str) -> dict:
    """Mark an obligation done.

    Args:
        obligation: Its id (e.g. "OB-1A2B3C") or a few words describing it ("the renewal contract").
    """
    o = _resolve(obligation)
    if not o:
        return {"speech": f"I couldn't find an open obligation matching {obligation}.", "matched": None}
    from datetime import datetime, timezone
    o = store.update(o["id"], status="done", completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    left = len(store.all("open"))
    return {"speech": f"Nice. Marked done: {_verb(o['task'])}. {left} open obligation{'s' if left != 1 else ''} left.",
            "obligation": _brief(o)}


@mcp.tool(title="Assign or reschedule an obligation", annotations=WRITE)
def assign_obligation(obligation: str, owner: str | None = None, due: str | None = None) -> dict:
    """Give an obligation an owner and/or a new deadline. Use this to fix "nobody owns it" risks.

    Args:
        obligation: Its id or a few words describing it ("the SOC 2 report").
        owner: The person taking it on.
        due: New deadline in plain words ("next Friday") or ISO date.
    """
    o = _resolve(obligation)
    if not o:
        return {"speech": f"I couldn't find an open obligation matching {obligation}.", "matched": None}
    fields: dict = {}
    if owner:
        fields["owner"] = owner
        if o["kind"] in ("team", "request"):
            fields["kind"] = "commitment"
    if due:
        d, phrase = parse_due(due, _today())
        if d:
            fields["due_date"], fields["due_phrase"] = d, phrase
    before = o["risk_level"]
    o = store.update(o["id"], **fields)
    speech = f"Done. {_cap(_say_item(o))}."
    if before != o["risk_level"]:
        speech += f" Risk dropped from {before} to {o['risk_level']}." if \
            ["low", "medium", "high"].index(o["risk_level"]) < ["low", "medium", "high"].index(before) else \
            f" Risk is now {o['risk_level']}."
    return {"speech": speech, "obligation": _brief(o)}


# --- resources ----------------------------------------------------------

@mcp.resource("obligate://obligations/open", name="open-obligations", mime_type="application/json",
              description="Every open obligation with its risk assessment.")
def open_obligations_resource() -> list[dict]:
    return [_brief(o) for o in store.all("open")]


# --- web routes: live dashboard for screens (Echo Show, laptop, demo video) ---

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "service": "obligate", "open": len(store.all("open"))})


@mcp.custom_route("/api/obligations", methods=["GET"])
async def api_obligations(_: Request) -> JSONResponse:
    return JSONResponse(store.all(None))


STATIC = os.path.join(os.path.dirname(__file__), "static")
SAMPLE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "examples", "vendor_review_meeting.txt")


@mcp.custom_route("/voice", methods=["GET"])
async def voice(_: Request) -> HTMLResponse:
    """Alexa+ simulation: speak to Obligate in the browser; it calls the MCP tools at /mcp."""
    with open(os.path.join(STATIC, "voice.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@mcp.custom_route("/api/sample-transcript", methods=["GET"])
async def sample_transcript(_: Request):
    from starlette.responses import PlainTextResponse
    with open(SAMPLE, encoding="utf-8") as f:
        return PlainTextResponse(f.read())


@mcp.custom_route("/api/reset", methods=["POST"])
async def reset(_: Request) -> JSONResponse:
    if os.environ.get("OBLIGATE_DEMO") != "1":
        return JSONResponse({"error": "reset is only available with OBLIGATE_DEMO=1"}, status_code=403)
    store.clear()
    return JSONResponse({"ok": True})


@mcp.custom_route("/", methods=["GET"])
async def dashboard(_: Request) -> HTMLResponse:
    from .dashboard import render
    return HTMLResponse(render(store.all(None), _today()))


def main() -> None:
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    logging.basicConfig(level=logging.INFO)
    host = os.environ.get("OBLIGATE_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", os.environ.get("OBLIGATE_PORT", "8000")))
    allowed = [h.strip() for h in os.environ.get("OBLIGATE_ALLOWED_HOSTS", "").split(",") if h.strip()]
    security = None
    if allowed:  # e.g. your ngrok / tunnel / API Gateway hostname, so Alexa+ can reach you
        security = TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*", *allowed, *[f"{h}:*" for h in allowed]],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", *[f"https://{h}" for h in allowed]])
    try:
        app = mcp.streamable_http_app(host=host, transport_security=security)
    except TypeError:  # SDK 1.x signature
        app = mcp.streamable_http_app()
    log.info("Obligate MCP server on http://%s:%s/mcp  (dashboard: http://%s:%s/)", host, port, host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
