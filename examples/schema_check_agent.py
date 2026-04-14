# /// script
# requires-python = ">=3.10,<3.15"
# dependencies = [
#     "agent-client-protocol",
# ]
# ///
import asyncio
import csv
import json
from collections import defaultdict
from pathlib import Path
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
    AudioContentBlock,
    ClientCapabilities,
    EmbeddedResourceContentBlock,
    HttpMcpServer,
    ImageContentBlock,
    Implementation,
    McpServerStdio,
    ResourceContentBlock,
    SseMcpServer,
    TextContentBlock,
)


def _extract_text(prompt: list) -> str:
    parts = []
    for block in prompt:
        text = block.get("text", "") if isinstance(block, dict) else getattr(block, "text", "")
        parts.append(text)
    return "".join(parts).strip()


def _infer_type(value: str) -> str:
    if value == "":
        return "empty"
    try:
        int(value)
        return "int"
    except ValueError:
        pass
    try:
        float(value)
        return "float"
    except ValueError:
        pass
    return "string"


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
        file_path = Path(_extract_text(prompt))

        await self._send(session_id, f"📂 Inspecting: {file_path}\n")

        if not file_path.exists():
            await self._send(session_id, f"❌ File not found: {file_path}\n")
            return PromptResponse(stop_reason="end_turn")

        ext = file_path.suffix.lower()
        if ext == ".json":
            await self._check_json(session_id, file_path)
        elif ext == ".csv":
            await self._check_csv(session_id, file_path)
        else:
            await self._send(session_id, f"⚠️  Unsupported file type '{ext}'. Supported: .json, .csv\n")

        return PromptResponse(stop_reason="end_turn")

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------

    async def _check_json(self, session_id: str, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            await self._send(session_id, f"❌ Invalid JSON syntax: {e.msg} (line {e.lineno}, col {e.colno})\n")
            return

        await self._send(session_id, f"✅ Valid JSON — root type: {type(data).__name__}\n\n")

        if isinstance(data, list):
            await self._check_json_array(session_id, data)
        elif isinstance(data, dict):
            await self._check_json_object(session_id, data)
        else:
            await self._send(session_id, f"ℹ️  Scalar value: {data!r}\n")

    async def _check_json_object(self, session_id: str, obj: dict) -> None:
        await self._send(session_id, f"Keys ({len(obj)}): {', '.join(obj.keys())}\n")
        nulls = [k for k, v in obj.items() if v is None]
        if nulls:
            await self._send(session_id, f"⚠️  Null values: {', '.join(nulls)}\n")

    async def _check_json_array(self, session_id: str, items: list) -> None:
        await self._send(session_id, f"Array length: {len(items)}\n\n")

        objects = [i for i in items if isinstance(i, dict)]
        if not objects:
            await self._send(session_id, "ℹ️  No objects in array to inspect.\n")
            return

        # Collect all keys and their types per row
        all_keys: set[str] = set()
        for obj in objects:
            all_keys.update(obj.keys())

        key_types: dict[str, set[str]] = defaultdict(set)
        key_missing: dict[str, int] = defaultdict(int)
        key_nulls: dict[str, int] = defaultdict(int)

        for obj in objects:
            for key in all_keys:
                if key not in obj:
                    key_missing[key] += 1
                else:
                    val = obj[key]
                    if val is None:
                        key_nulls[key] += 1
                    else:
                        key_types[key].add(type(val).__name__)

        issues_found = False

        for key in sorted(all_keys):
            types = key_types[key]
            missing = key_missing[key]
            nulls = key_nulls[key]
            row_issues = []

            if len(types) > 1:
                row_issues.append(f"mixed types: {', '.join(sorted(types))}")
            if missing:
                row_issues.append(f"missing in {missing}/{len(objects)} rows")
            if nulls:
                row_issues.append(f"null in {nulls}/{len(objects)} rows")

            if row_issues:
                issues_found = True
                await self._send(session_id, f"⚠️  '{key}': {'; '.join(row_issues)}\n")

        if not issues_found:
            await self._send(session_id, "✅ No schema issues found in array objects.\n")

    # ------------------------------------------------------------------
    # CSV
    # ------------------------------------------------------------------

    async def _check_csv(self, session_id: str, path: Path) -> None:
        rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
        if not rows:
            await self._send(session_id, "❌ File is empty.\n")
            return

        headers = rows[0]
        data_rows = rows[1:]
        expected_cols = len(headers)

        await self._send(session_id, f"✅ Parsed CSV — {len(data_rows)} data rows, {expected_cols} columns\n")
        await self._send(session_id, f"Headers: {', '.join(headers)}\n\n")

        # Check for duplicate headers
        if len(headers) != len(set(headers)):
            dupes = [h for h in headers if headers.count(h) > 1]
            await self._send(session_id, f"⚠️  Duplicate column names: {', '.join(set(dupes))}\n")

        # Check row lengths
        short = [(i + 2, len(r)) for i, r in enumerate(data_rows) if len(r) < expected_cols]
        long_ = [(i + 2, len(r)) for i, r in enumerate(data_rows) if len(r) > expected_cols]
        if short:
            rows_str = ", ".join(f"row {r[0]} ({r[1]} cols)" for r in short)
            await self._send(session_id, f"⚠️  Too few columns: {rows_str}\n")
        if long_:
            rows_str = ", ".join(f"row {r[0]} ({r[1]} cols)" for r in long_)
            await self._send(session_id, f"⚠️  Too many columns: {rows_str}\n")

        # Type inference per column
        issues_found = bool(short or long_)
        for col_idx, col_name in enumerate(headers):
            values = [r[col_idx] for r in data_rows if len(r) > col_idx]
            types = {_infer_type(v) for v in values}
            empties = sum(1 for v in values if v == "")
            col_issues = []

            non_empty_types = types - {"empty"}
            if len(non_empty_types) > 1:
                col_issues.append(f"mixed types: {', '.join(sorted(non_empty_types))}")
            if empties:
                col_issues.append(f"empty in {empties}/{len(values)} rows")

            if col_issues:
                issues_found = True
                await self._send(session_id, f"⚠️  '{col_name}': {'; '.join(col_issues)}\n")

        if not issues_found:
            await self._send(session_id, "✅ No schema issues found.\n")

    # ------------------------------------------------------------------

    async def _send(self, session_id: str, text: str) -> None:
        await self._conn.session_update(
            session_id=session_id,
            update=update_agent_message(text_block(text)),
        )


async def main() -> None:
    await run_agent(SchemaCheckAgent())


if __name__ == "__main__":
    asyncio.run(main())
