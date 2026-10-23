# Obligate: Friction Log

Friction I ran into while building Obligate for the Alexa+ track of the Build, Ship, Shape: Amazon Developer Hackathon 2026.

## 1. Connecting a self-hosted MCP server to Alexa+
- **Task:** Register Obligate's Streamable HTTP MCP server with Alexa+ and test it by voice.
- **Steps taken:** Read the hackathon Resources page; followed its Alexa+ links (MCP spec transports page, Agent Skills page); searched for Alexa+ MCP registration docs.
- **Expected:** A page explaining where to register the server URL, the HTTPS/auth requirements, and a way to test (console or simulator).
- **Actual:** The Resources page links to the MCP spec but gives no Alexa+-specific connection or testing steps.
- **Severity:** High. It blocks seeing the real end-user experience.
- **Workaround:** Tested with a generic MCP client, and built a browser voice simulation (`/voice`) that calls the MCP endpoint over JSON-RPC.
- **Suggestion:** Publish a one-page "Connect your MCP server to Alexa+" quickstart, with a test console that shows which tool Alexa+ chose and the arguments it sent.

## 2. MCP Python SDK 2.x breaking tutorial imports
- **Task:** Start an MCP server from a standard tutorial.
- **Steps taken:** `pip install "mcp[cli]"`, then `from mcp.server.fastmcp import FastMCP`.
- **Expected:** A working import, as in most tutorials.
- **Actual:** `ModuleNotFoundError`. FastMCP was renamed to `MCPServer` in SDK 2.x, and the host/transport-security signatures changed.
- **Severity:** Medium (about 20 minutes lost).
- **Workaround:** Import `MCPServer` with a fallback to `FastMCP`, so the code supports both 1.x and 2.x.
- **Suggestion:** Hackathon resources and Amazon sample repos should pin an SDK version or include a 2.x example.

## 3. Running the server locally on macOS
- **Task:** Run the server and open the voice console in the browser.
- **Steps taken:** `python -m obligate.server`, then opened `http://localhost:8000/voice`.
- **Expected:** The server starts and the page loads.
- **Actual:** `python` pointed to the system Python 3.9 (`ModuleNotFoundError`; the MCP SDK needs 3.10+), and the server was stopped by Ctrl+C in the same terminal, giving `ERR_CONNECTION_REFUSED`.
- **Severity:** Low to medium.
- **Workaround:** Used `python3` (3.14), kept the server in its own terminal tab, and opened `http://127.0.0.1:8000`.
- **Suggestion:** MCP quickstarts should state the minimum Python version and show the exact URL to open.

## 4. Exposing a local MCP server through a tunnel
- **Task:** Make the server reachable from outside localhost for a cloud assistant.
- **Expected:** Requests through the tunnel hostname are accepted.
- **Actual:** The SDK enables DNS-rebinding protection for localhost by default and rejects other Host headers unless explicitly allowed.
- **Severity:** Low to medium. The default is safe, but the fix is hard to discover.
- **Workaround:** Added an `OBLIGATE_ALLOWED_HOSTS` setting that passes `TransportSecuritySettings(allowed_hosts=...)`.
- **Suggestion:** Alexa+ docs should include a "local development with a tunnel" section showing the `allowed_hosts` setting.

## Feature requests
- **Alexa+ MCP test console** (Critical): send an utterance and see the tool Alexa+ picked, its arguments and the spoken response.
- **Proactive notifications from MCP servers** (Important): let a server ask Alexa+ to announce "Raj, the renewal contract is due today" at a chosen time.
- **Speaker-attributed meeting transcripts for MCP tools** (Important): knowing who said what makes ownership detection reliable.
- **Echo Show visual responses from MCP tools** (Nice-to-have): a way for a tool to return a card or list for screen devices.
