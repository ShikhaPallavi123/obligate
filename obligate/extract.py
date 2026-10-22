"""Find the commitments hiding in meeting talk.

Two engines:
  * rules  - deterministic, offline, zero-cost. Always available.
  * bedrock - Amazon Bedrock (Converse API) for messier transcripts.
             Enabled with OBLIGATE_LLM=bedrock. Falls back to rules on any error.

Both produce the same `Commitment` shape; risk scoring is applied afterwards
by `risk.score()` so every obligation is judged the same way.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date

from .dates import parse_due

log = logging.getLogger("obligate.extract")


@dataclass
class Commitment:
    task: str
    owner: str | None
    kind: str                 # "commitment" (I'll...), "request" (Raj, can you...), "team" (we need to...)
    source_quote: str
    speaker: str | None = None
    due_date: date | None = None
    due_phrase: str | None = None
    confidence: float = 0.8
    hedged: bool = False
    extra: dict = field(default_factory=dict)


# --- patterns -----------------------------------------------------------

SELF = (r"(?:i'll|i will|i'm going to|im going to|i am going to|i can|let me|i'll make sure to|"
        r"i'll make sure|i promise to|i commit to|i'll take care of|i'll own|i'll handle|i'll get)")
HEDGES = r"\b(try to|maybe|probably|hopefully|if i can|if i get time|should be able to|i think i can|might)\b"
TEAM = (r"\b(we(?:'ll| will)?\s+(?:also\s+|really\s+|still\s+|definitely\s+)?(?:need to|should|have to|must)|we'll|we will|"
        r"we've got to|someone (?:should|needs to|has to|must)|somebody (?:should|needs to|has to|must))\b")
NAME = r"([A-Z][a-z]+(?: [A-Z][a-z]+)?)"
NOT_NAMES = {"I", "We", "You", "They", "It", "That", "This", "So", "And", "But", "Ok", "Okay", "Yes",
             "No", "Also", "Then", "Great", "Sure", "Alexa", "Let", "Please", "Thanks", "Can", "Could",
             "Will", "Who", "What", "When", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday", "Everyone", "Someone", "Somebody"}
ACK = r"^(yes|yeah|yep|sure|ok|okay|will do|on it|got it|absolutely|sounds good|leave it with me|i can do that|i'll do it|done)\b"
TASK_VERB_HINT = (r"\b(send|share|review|draft|write|file|submit|sign|update|fix|schedule|book|call|email|"
                  r"follow up|check|confirm|prepare|finish|complete|deliver|renew|pay|approve|loop|get|"
                  r"set up|deploy|ship|test|audit|investigate|reach out|ping|put together|create|build|"
                  r"look into|circulate|post|order|cancel|notify|escalate|reply|respond)\b")

LINE_RE = re.compile(r"^\s*(?:\[[^\]]*\]\s*)?([A-Z][\w .'-]{0,40}?)\s*:\s*(.+)$")


def _split_lines(transcript: str) -> list[tuple[str | None, str]]:
    out: list[tuple[str | None, str]] = []
    for raw in transcript.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        m = LINE_RE.match(raw)
        if m and m[1].split()[0] not in {"Note", "Action", "Re", "Subject"}:
            out.append((m[1].strip(), m[2].strip()))
        else:
            out.append((None, raw))
    return out


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|;\s+", text)
    return [p.strip() for p in parts if p.strip()]


VAGUE_TASK = re.compile(r"^(?:get|do|handle|sort|look at|take care of)\s+(?:that|it|this|those)\b", re.I)
LEADING_FILLER = re.compile(r"^(?:(?:and|so|also|then|okay|ok|great|but|alright|right|um|uh|hmm)[,]?\s+)+", re.I)


def _clean_task(task: str, due_phrase: str | None) -> str:
    t = task.strip(" .,!?:;-")
    t = re.sub(r",?\s+(?:it's|it is|which|since|because|as)\b.*$", "", t, flags=re.I)
    t = re.sub(r",?\s+(?:maybe|probably|hopefully|i think|if possible)$", "", t, flags=re.I)
    if due_phrase:
        t = re.sub(rf"\s*(?:by|on|before|until|due|no later than)?\s*{re.escape(due_phrase)}\b", "",
                   t, flags=re.I).strip(" .,!?:;-")
    t = re.sub(r"^(?:to|that|and|also|just|then)\s+", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t)
    return t[:1].upper() + t[1:] if t else t


def _after(pattern: str, sentence: str) -> str | None:
    m = re.search(pattern, sentence, flags=re.I)
    return sentence[m.end():] if m else None


def extract_rules(transcript: str, meeting_date: date) -> list[Commitment]:
    results: list[Commitment] = []
    lines = _split_lines(transcript)
    pending_request: Commitment | None = None

    for speaker, text in lines:
        # A bare "yes / on it" right after "Raj, can you..." upgrades the request to a commitment.
        if pending_request and speaker and re.match(ACK, text.strip(), flags=re.I):
            if pending_request.owner and speaker.split()[0].lower() == pending_request.owner.split()[0].lower():
                pending_request.kind = "commitment"
                pending_request.confidence = 0.9
                pending_request.source_quote += f" / {speaker}: {text}"
                if not pending_request.due_date:
                    pending_request.due_date, pending_request.due_phrase = parse_due(text, meeting_date)
            pending_request = None
            continue
        pending_request = None if speaker else pending_request

        for s in _sentences(text):
            s = LEADING_FILLER.sub("", s)
            low = s.lower()
            due, phrase = parse_due(s, meeting_date)
            hedged = bool(re.search(HEDGES, low))

            # 1. "I'll send the contract by Friday"
            rest = _after(rf"\b{SELF}\b", s)
            if rest and re.search(TASK_VERB_HINT, s, flags=re.I) and not re.match(r"^\s*(?:be|think|see|say|guess|bet)\b", rest, flags=re.I):
                task = _clean_task(re.sub(r"^\s*(?:try to|make sure to|make sure|go ahead and)\s+", "", rest, flags=re.I), phrase)
                if len(task.split()) >= 2:
                    results.append(Commitment(task=task, owner=speaker, kind="commitment",
                                              source_quote=f"{speaker}: {s}" if speaker else s,
                                              speaker=speaker, due_date=due, due_phrase=phrase,
                                              confidence=0.6 if hedged else (0.9 if speaker else 0.7),
                                              hedged=hedged))
                    continue

            # 2. "Raj, can you send the deck?"  /  "Can you send the deck, Raj?"
            m = re.match(rf"^{NAME},?\s+(?:can|could|would|will) you\s+(?:please\s+)?(.+)$", s) or \
                re.match(rf"^(?:can|could|would|will) you\s+(?:please\s+)?(.+?),\s*{NAME}\??$", s, flags=re.I)
            if m:
                if m.re.pattern.startswith("^(?:can"):
                    task_txt, owner = m[1], m[2]
                else:
                    owner, task_txt = m[1], m[2]
                if owner.split()[0] not in NOT_NAMES:
                    c = Commitment(task=_clean_task(task_txt, phrase), owner=owner, kind="request",
                                   source_quote=s if not speaker else f"{speaker}: {s}", speaker=speaker,
                                   due_date=due, due_phrase=phrase, confidence=0.65, hedged=hedged)
                    results.append(c)
                    pending_request = c
                    continue

            # 3. "Priya will draft the policy" / "Priya to draft the policy" / "Action: Priya - draft..."
            m = re.search(rf"\b{NAME}\s+(?:will|is going to|'ll|is gonna|agreed to|to)\s+(.+)$", s)
            if m and m[1].split()[0] not in NOT_NAMES and re.search(TASK_VERB_HINT, m[2], flags=re.I):
                results.append(Commitment(task=_clean_task(m[2], phrase), owner=m[1], kind="commitment",
                                          source_quote=s if not speaker else f"{speaker}: {s}", speaker=speaker,
                                          due_date=due, due_phrase=phrase, confidence=0.75, hedged=hedged))
                continue

            # 4. "We need to renew the SOC 2 report" - nobody owns it. This is the dangerous one.
            rest = _after(TEAM, s)
            if rest and re.search(TASK_VERB_HINT, rest, flags=re.I):
                results.append(Commitment(task=_clean_task(rest, phrase), owner=None, kind="team",
                                          source_quote=s if not speaker else f"{speaker}: {s}", speaker=speaker,
                                          due_date=due, due_phrase=phrase, confidence=0.6, hedged=hedged))
                continue
    return _dedupe([c for c in results if c.task and not VAGUE_TASK.match(c.task)])


def _dedupe(items: list[Commitment]) -> list[Commitment]:
    seen: set[tuple] = set()
    out = []
    for c in items:
        key = ((c.owner or "").lower(), re.sub(r"\W+", " ", c.task.lower())[:40])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# --- Amazon Bedrock -----------------------------------------------------

BEDROCK_PROMPT = """You extract commitments from a meeting transcript.
Meeting date: {meeting_date}. Resolve relative dates ("Friday", "next week") against it.

Return ONLY a JSON array. Each item:
{{"task": "imperative description", "owner": "person name or null if nobody took it",
  "kind": "commitment" | "request" | "team",
  "due_date": "YYYY-MM-DD or null", "due_phrase": "words used or null",
  "source_quote": "the exact sentence", "hedged": true if the speaker was non-committal}}

Rules: "I'll"/"I will" by a speaker -> owner is that speaker. "X, can you..." with no answer -> kind "request".
"We need to"/"someone should" with nobody claiming it -> owner null, kind "team".
Skip small talk and things already done.

Transcript:
{transcript}"""


def extract_bedrock(transcript: str, meeting_date: date) -> list[Commitment]:
    import boto3  # imported lazily so the server runs without AWS installed

    model_id = os.environ.get("OBLIGATE_BEDROCK_MODEL", "us.amazon.nova-lite-v1:0")
    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    resp = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": BEDROCK_PROMPT.format(
            meeting_date=meeting_date.isoformat(), transcript=transcript)}]}],
        inferenceConfig={"maxTokens": 2000, "temperature": 0},
    )
    text = resp["output"]["message"]["content"][0]["text"]
    m = re.search(r"\[.*\]", text, flags=re.S)
    data = json.loads(m[0] if m else text)
    out = []
    for d in data:
        due = date.fromisoformat(d["due_date"]) if d.get("due_date") else None
        out.append(Commitment(task=d["task"], owner=d.get("owner"), kind=d.get("kind", "commitment"),
                              source_quote=d.get("source_quote", ""), due_date=due,
                              due_phrase=d.get("due_phrase"), hedged=bool(d.get("hedged")),
                              confidence=0.85, extra={"engine": "bedrock", "model": model_id}))
    return out


def extract(transcript: str, meeting_date: date) -> tuple[list[Commitment], str]:
    """Return (commitments, engine_used)."""
    if os.environ.get("OBLIGATE_LLM", "").lower() == "bedrock":
        try:
            return extract_bedrock(transcript, meeting_date), "bedrock"
        except Exception as e:  # never lose a meeting because the LLM hiccuped
            log.warning("Bedrock extraction failed, using rules: %s", e)
    return extract_rules(transcript, meeting_date), "rules"
