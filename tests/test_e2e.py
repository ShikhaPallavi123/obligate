"""End-to-end test: talk to a running Obligate server over Streamable HTTP, like Alexa+ does.

    python -m obligate.server            # terminal 1
    python tests/test_e2e.py             # terminal 2

Uses the MCP Python SDK client. Prints what Alexa+ would say for each step.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

URL = os.environ.get("OBLIGATE_URL", "http://127.0.0.1:8000/mcp")
TRANSCRIPT = (Path(__file__).parent.parent / "examples" / "vendor_review_meeting.txt").read_text()


def payload(result) -> dict:
    if getattr(result, "structured_content", None) or getattr(result, "structuredContent", None):
        sc = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    return json.loads(result.content[0].text)


async def main() -> int:
    try:
        from mcp.client.client import Client  # SDK 2.x

        ctx = Client(URL)
    except ImportError:  # SDK 1.x
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        raise SystemExit("Install mcp>=2 to run this test client, or use MCP Inspector.")

    async with ctx as c:
        tools = await c.list_tools()
        names = sorted(t.name for t in tools.tools)
        print("Tools:", ", ".join(names))
        expected = {"capture_meeting", "add_obligation", "list_obligations", "risk_report", "briefing",
                    "complete_obligation", "assign_obligation"}
        assert expected <= set(names), f"missing tools: {expected - set(names)}"

        steps = [
            ("capture_meeting", {"transcript": TRANSCRIPT, "meeting_title": "the vendor review",
                                 "meeting_date": "2026-10-22"}),
            ("risk_report", {}),
            ("assign_obligation", {"obligation": "SOC 2 report", "owner": "Priya", "due": "November 14"}),
            ("list_obligations", {"owner": "Raj"}),
            ("add_obligation", {"task": "Send Dana the signed NDA", "owner": "Maya", "due": "Monday"}),
            ("complete_obligation", {"obligation": "renewal contract to legal"}),
            ("briefing", {"owner": "Leo"}),
            ("list_obligations", {"owner": "nobody"}),
        ]
        for name, args in steps:
            res = await c.call_tool(name, args)
            assert not res.is_error if hasattr(res, "is_error") else not res.isError, res
            data = payload(res)
            print(f"\n> {name}({', '.join(f'{k}={v!r}' for k, v in args.items() if k != 'transcript')})")
            print("  Alexa:", data["speech"])
            if name == "capture_meeting":
                assert len(data["captured"]) >= 7, data
        res = await c.list_resources()
        print("\nResources:", [str(r.uri) for r in res.resources])
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
