# /// script
# requires-python = ">=3.10,<3.15"
# dependencies = [
#     "agent-client-protocol",
# ]
# ///
import asyncio
import json
from typing import Any
from uuid import uuid4

from acp import (
    Agent,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    run_agent,
    text_block,
    update_agent_message,
)
from acp.interfaces import Client
from acp.schema import (
    ClientCapabilities,
    HttpMcpServer,
    Implementation,
    McpServerStdio,
    SseMcpServer,
    TextContentBlock,
    ImageContentBlock,
    AudioContentBlock,
    ResourceContentBlock,
    EmbeddedResourceContentBlock,
)


class SchemaCheckAgent(Agent):
    _conn: Client

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        return InitializeResponse(protocol_version=protocol_version)

    async def new_session(
        self,
        cwd: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | McpServerStdio],
        **kwargs: Any,
    ) -> NewSessionResponse:
        return NewSessionResponse(session_id=uuid4().hex)

    async def prompt(
        self,
        prompt: list[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ],
        session_id: str,
        **kwargs: Any,
    ) -> PromptResponse:
        # Collect all text from the prompt blocks
        user_input = ""
        for block in prompt:
            text = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
            user_input += text

        await self._send(session_id, f"Checking: {user_input!r}\n")

        # Try to parse as JSON and report findings
        try:
            data = json.loads(user_input)
            await self._send(session_id, "✅ Valid JSON!\n")
            await self._send(session_id, f"Type: {type(data).__name__}\n")

            if isinstance(data, dict):
                await self._send(session_id, f"Keys ({len(data)}): {', '.join(data.keys())}\n")
            elif isinstance(data, list):
                await self._send(session_id, f"Items: {len(data)}\n")
            else:
                await self._send(session_id, f"Value: {data}\n")

        except json.JSONDecodeError as e:
            await self._send(session_id, f"❌ Invalid JSON: {e.msg} at line {e.lineno}, col {e.colno}\n")

        return PromptResponse(stop_reason="end_turn")

    async def _send(self, session_id: str, text: str) -> None:
        await self._conn.session_update(
            session_id=session_id,
            update=update_agent_message(text_block(text)),
        )


async def main() -> None:
    await run_agent(SchemaCheckAgent())


if __name__ == "__main__":
    asyncio.run(main())
