"""Server-rendered live dashboard. Refreshes every 3 seconds so it updates while you talk to Alexa."""
from __future__ import annotations

from datetime import date
from html import escape

CSS = """
:root{--ink:#14181f;--muted:#5d6672;--line:#e3e1db;--paper:#faf8f4;--card:#fff;
--high:#c2341d;--high-bg:#fdeeea;--med:#a86400;--med-bg:#fdf3e2;--low:#2f7a4b;--low-bg:#eaf5ee;--accent:#1f4fd1}
@media (prefers-color-scheme:dark){:root{--ink:#eceae5;--muted:#9aa2ad;--line:#2c3037;--paper:#121417;--card:#1a1d21;
--high:#ff7a62;--high-bg:#3a1d17;--med:#f0b04d;--med-bg:#35290f;--low:#6fd197;--low-bg:#15301f;--accent:#7ea1ff}}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);
font:15px/1.45 "Inter",system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:28px 32px 8px;display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
h1{font:700 28px/1 "Fraunces",Georgia,serif;margin:0;letter-spacing:-.01em}
h1 span{color:var(--accent)}.tag{color:var(--muted);font-size:14px}
.stats{display:flex;gap:12px;padding:16px 32px;flex-wrap:wrap}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;flex:1 1 92px;max-width:150px}
.stat b{display:block;font-size:26px;line-height:1.1}.stat small{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
.stat.alert b{color:var(--high)}
main{padding:8px 32px 40px}
.row{display:grid;grid-template-columns:96px 1fr 130px 150px;gap:16px;align-items:start;background:var(--card);
border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-bottom:10px;animation:in .4s ease}
@keyframes in{from{opacity:0;transform:translateY(4px)}to{opacity:1}}
.row.done{opacity:.45}.row.done .task{text-decoration:line-through}
.pill{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;border-radius:999px;padding:4px 10px}
.high{color:var(--high);background:var(--high-bg)}.medium{color:var(--med);background:var(--med-bg)}.low{color:var(--low);background:var(--low-bg)}
.task{font-weight:600;font-size:16px}.quote{color:var(--muted);font-size:13px;font-style:italic;margin-top:4px}
.why{margin-top:6px;display:flex;gap:6px;flex-wrap:wrap}.why span{font-size:12px;border:1px solid var(--line);border-radius:6px;padding:2px 7px;color:var(--muted)}
.owner{font-weight:600}.owner.none{color:var(--high)}.due{font-variant-numeric:tabular-nums}.due.late{color:var(--high);font-weight:700}
.id{color:var(--muted);font-size:12px;font-family:ui-monospace,Menlo,monospace}
.empty{padding:60px 0;text-align:center;color:var(--muted)}
.headrow{display:grid;grid-template-columns:96px 1fr 130px 150px;gap:16px;padding:0 16px 6px;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
@media (max-width:700px){header,.stats,main{padding-left:16px;padding-right:16px}.row{grid-template-columns:1fr}.headrow{display:none}}
"""


def _due(o: dict, today: date) -> tuple[str, str]:
    if not o["due_date"]:
        return "No deadline", ""
    d = date.fromisoformat(o["due_date"])
    n = (d - today).days
    label = d.strftime("%a %b ") + str(d.day)
    if o["status"] != "open":
        return label, ""
    if n < 0:
        return f"{label} · {-n}d late", "late"
    if n == 0:
        return "Today", "late"
    if n == 1:
        return "Tomorrow", ""
    return label, ""


def render(items: list[dict], today: date) -> str:
    open_ = [o for o in items if o["status"] == "open"]
    order = {"high": 0, "medium": 1, "low": 2}
    items = sorted(items, key=lambda o: (o["status"] != "open", order[o["risk_level"]],
                                         o["days_until_due"] if o["days_until_due"] is not None else 99))
    stats = [
        ("Open", len(open_), False),
        ("High risk", sum(o["risk_level"] == "high" for o in open_), True),
        ("No owner", sum(not o["owner"] for o in open_), True),
        ("Overdue", sum((o["days_until_due"] or 0) < 0 for o in open_), True),
        ("Compliance", sum(bool(o["compliance_tags"]) for o in open_), False),
        ("Done", sum(o["status"] == "done" for o in items), False),
    ]
    stat_html = "".join(f'<div class="stat{" alert" if alert and n else ""}"><b>{n}</b><small>{escape(k)}</small></div>'
                        for k, n, alert in stats)
    rows = []
    for o in items:
        due, cls = _due(o, today)
        why = "".join(f"<span>{escape(r)}</span>" for r in o["risk_reasons"] if r != "completed")
        quote = f'<div class="quote">“{escape(o["source_quote"])}”</div>' if o.get("source_quote") else ""
        level = "done" if o["status"] == "done" else o["risk_level"]
        pill_cls = "low" if o["status"] == "done" else o["risk_level"]
        rows.append(f"""<div class="row {'done' if o['status'] == 'done' else ''}">
  <div><span class="pill {pill_cls}">{escape(level)}</span><div class="id">{escape(o['id'])}</div></div>
  <div><div class="task">{escape(o['task'])}</div>{quote}<div class="why">{why}</div></div>
  <div class="owner {'none' if not o['owner'] else ''}">{escape(o['owner'] or 'Nobody')}</div>
  <div class="due {cls}">{escape(due)}</div></div>""")
    body = "".join(rows) or '<div class="empty">No obligations yet. Say “Alexa, capture my meeting with Obligate.”</div>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="3">
<title>Obligate</title><link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,700&family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<header><h1>Obligate<span>.</span></h1><div class="tag">Every promise from every meeting, tracked · {today.strftime('%A, %B ')}{today.day}</div></header>
<div class="stats">{stat_html}</div>
<main><div class="headrow"><div>Risk</div><div>Obligation</div><div>Owner</div><div>Due</div></div>{body}</main>
</body></html>"""
