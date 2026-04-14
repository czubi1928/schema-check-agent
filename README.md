# SchemaCheck — ACP Learning Project

A hands-on walkthrough of the [Agent Client Protocol (ACP)](https://agentclientprotocol.com) Python SDK,
built while following the [official quickstart](https://agentclientprotocol.github.io/python-sdk/quickstart/).

## What I Learned

### 1. What is ACP?

**Agent Client Protocol** is a standard communication protocol between **clients** (IDEs, terminals, scripts)
and **AI agents**. Think of it like HTTP — but designed specifically for AI agent ↔ tool communication.

### 2. The ACP Python SDK Quickstart (Steps 1–4 + Optional)

| Step         | What I did                                                                             | File                             |
|--------------|----------------------------------------------------------------------------------------|----------------------------------|
| **1**        | Installed `agent-client-protocol` via `uv`                                             | `pyproject.toml`                 |
| **2**        | Ran the ready-made `EchoAgent`                                                         | `examples/echo_agent.py`         |
| **3**        | Connected a Python client (instead of Zed/PyCharm AI Chat) using `spawn_agent_process` | `examples/client.py`             |
| **4**        | Built a real `SchemaCheckAgent` that inspects JSON & CSV files                         | `examples/schema_check_agent.py` |
| **Optional** | Replaced Gemini CLI with GitHub Copilot CLI as the ACP server                          | `examples/copilot.py`            |

### 3. Core ACP Concepts

**Building an agent** — subclass `acp.Agent`:

```python
class MyAgent(Agent):
    def on_connect(self, conn): self._conn = conn       # store the client handle

    async def initialize(...): ...                      # handshake

    async def new_session(...): ...                     # create a session

    async def prompt(...) -> PromptResponse:            # handle user messages
        await self._conn.session_update(...)            # stream results back
        return PromptResponse(stop_reason="end_turn")   # signal done
```

**Two ways to connect a client:**

| Method                                              | Who manages the process |
|-----------------------------------------------------|-------------------------|
| `spawn_agent_process(client, executable, script)`   | The SDK                 |
| `connect_to_agent(client, proc.stdin, proc.stdout)` | You                     |

**`ClientCapabilities`** — you declare what the client can do so the agent knows what to ask for
(file reads/writes, terminal access).

**`session_update`** — how an agent streams partial responses. Called many times per prompt, not just once at the end.

**`request_permission`** — before an agent does something impactful (edit a file, run a command),
it asks the client first. `--yolo` auto-approves.

### 4. The SchemaCheck Agent

Accepts a file path → inspects it → streams a schema report:

- **JSON**: valid syntax? mixed types across array objects? missing keys? null values?
- **CSV**: consistent column count? mixed types per column? empty cells?

### 5. The Big Picture

```
Your client (copilot.py / client.py)
        │
        ▼
   Agent process
   (copilot --acp --stdio  OR  schema_check_agent.py)
        │
        │  session_update  ←── streams responses
        │  request_permission ←── asks before acting
        ▼
   Back to your client
```

Any ACP-compatible agent (Copilot CLI, Gemini CLI, your own) speaks the same protocol —
swap the binary, keep the client code. That's the power of ACP.

---

## Project Structure

```
examples/
  echo_agent.py           # Step 2 — ready-made streaming echo agent
  client.py               # Step 3 — programmatic test client
  schema_check_agent.py   # Step 4 — real agent: inspects JSON & CSV files
  copilot.py              # Optional — interactive client for GitHub Copilot CLI
  data/
    users.json            # Sample JSON with intentional schema issues
    scores.csv            # Sample CSV with intentional schema issues
```

---

## Resources

| Resource                                  | URL                                                                            |
|-------------------------------------------|--------------------------------------------------------------------------------|
| ACP Python SDK — Quickstart               | https://agentclientprotocol.github.io/python-sdk/quickstart/                   |
| ACP Python SDK — GitHub repo              | https://github.com/agentclientprotocol/python-sdk                              |
| ACP Python SDK — `gemini.py` example      | https://github.com/agentclientprotocol/python-sdk/blob/main/examples/gemini.py |
| GitHub Copilot CLI — ACP server reference | https://docs.github.com/en/copilot/reference/copilot-cli-reference/acp-server  |
| JetBrains AI Assistant — ACP integration  | https://www.jetbrains.com/help/ai-assistant/acp.html                           |

---

## Running

```bash
# Test the schema-check agent with sample files
uv run examples/client.py

# Interactive session with GitHub Copilot CLI over ACP
uv run examples/copilot.py
uv run examples/copilot.py --yolo   # auto-approve permissions
```
