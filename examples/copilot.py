# /// script
# requires-python = ">=3.10,<3.15"
# dependencies = [
#     "agent-client-protocol",
# ]
# ///
"""
Interactive ACP client for GitHub Copilot CLI.

Usage:
    uv run examples/copilot.py
    uv run examples/copilot.py --yolo        # auto-approve permission prompts
    uv run examples/copilot.py --copilot /path/to/copilot

Mirrors examples/gemini.py but targets `copilot --acp --stdio` instead of
`gemini --experimental-acp`.
"""
from __future__ import annotations

import argparse
import asyncio
import asyncio.subprocess
import contextlib
import json
import os
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from acp import (
    PROTOCOL_VERSION,
    Client,
    RequestError,
    connect_to_agent,
    text_block,
)
from acp.core import ClientSideConnection
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    AvailableCommandsUpdate,
    ClientCapabilities,
    CreateTerminalResponse,
    CurrentModeUpdate,
    DeniedOutcome,
    EmbeddedResourceContentBlock,
    EnvVariable,
    FileEditToolCallContent,
    FileSystemCapabilities,
    KillTerminalResponse,
    PermissionOption,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    ResourceContentBlock,
    TerminalOutputResponse,
    TerminalToolCallContent,
    TextContentBlock,
    ToolCall,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)


class CopilotClient(Client):
    """ACP client implementation that drives GitHub Copilot CLI."""

    def __init__(self, auto_approve: bool) -> None:
        self._auto_approve = auto_approve

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------

    async def request_permission(
        self, options: list[PermissionOption], session_id: str, tool_call: ToolCall, **kwargs: Any
    ) -> RequestPermissionResponse:
        if self._auto_approve:
            option = _pick_preferred_option(options)
            if option is None:
                return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
            return RequestPermissionResponse(outcome=AllowedOutcome(option_id=option.option_id, outcome="selected"))

        title = tool_call.title or "<permission>"
        if not options:
            print(f"\n🔐 Permission requested: {title} (no options — cancelling)")
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

        print(f"\n🔐 Copilot requests permission: {title}")
        for idx, opt in enumerate(options, start=1):
            print(f"  {idx}. {opt.name} ({opt.kind})")

        loop = asyncio.get_running_loop()
        while True:
            choice = await loop.run_in_executor(None, lambda: input("Select option (number): ").strip())
            if not choice:
                continue
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    opt = options[idx]
                    return RequestPermissionResponse(
                        outcome=AllowedOutcome(option_id=opt.option_id, outcome="selected")
                    )
            print("Invalid selection, try again.")

    # ------------------------------------------------------------------
    # File system (Copilot CLI uses these to read/write code)
    # ------------------------------------------------------------------

    async def write_text_file(
        self, content: str, path: str, session_id: str, **kwargs: Any
    ) -> WriteTextFileResponse | None:
        pathlib_path = Path(path)
        if not pathlib_path.is_absolute():
            raise RequestError.invalid_params({"path": str(pathlib_path), "reason": "path must be absolute"})
        pathlib_path.parent.mkdir(parents=True, exist_ok=True)
        pathlib_path.write_text(content, encoding="utf-8")
        print(f"[fs] Wrote {pathlib_path} ({len(content)} bytes)")
        return WriteTextFileResponse()

    async def read_text_file(
        self, path: str, session_id: str, limit: int | None = None, line: int | None = None, **kwargs: Any
    ) -> ReadTextFileResponse:
        pathlib_path = Path(path)
        if not pathlib_path.is_absolute():
            raise RequestError.invalid_params({"path": str(pathlib_path), "reason": "path must be absolute"})
        text = pathlib_path.read_text(encoding="utf-8")
        print(f"[fs] Read {pathlib_path} ({len(text)} bytes)")
        if line is not None or limit is not None:
            text = _slice_text(text, line, limit)
        return ReadTextFileResponse(content=text)

    # ------------------------------------------------------------------
    # Terminal (Copilot CLI uses these to run commands)
    # ------------------------------------------------------------------

    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[EnvVariable] | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse:
        print(f"[terminal] createTerminal: {command} {args or []} (cwd={cwd})")
        return CreateTerminalResponse(terminal_id="term-1")

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> TerminalOutputResponse:
        return TerminalOutputResponse(output="", truncated=False)

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> ReleaseTerminalResponse | None:
        return ReleaseTerminalResponse()

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> WaitForTerminalExitResponse:
        return WaitForTerminalExitResponse()

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> KillTerminalResponse | None:
        return KillTerminalResponse()

    # ------------------------------------------------------------------
    # Session updates (this is how Copilot streams its response back)
    # ------------------------------------------------------------------

    async def session_update(
        self,
        session_id: str,
        update: UserMessageChunk
        | AgentMessageChunk
        | AgentThoughtChunk
        | ToolCallStart
        | ToolCallProgress
        | AgentPlanUpdate
        | AvailableCommandsUpdate
        | CurrentModeUpdate,
        **kwargs: Any,
    ) -> None:
        if isinstance(update, AgentMessageChunk):
            _print_text_content(update.content)
        elif isinstance(update, AgentThoughtChunk):
            print("\n[thinking]")
            _print_text_content(update.content)
        elif isinstance(update, UserMessageChunk):
            print("\n[user_message]")
            _print_text_content(update.content)
        elif isinstance(update, AgentPlanUpdate):
            print("\n[plan]")
            for entry in update.entries:
                print(f"  - {entry.status.upper():<10} {entry.content}")
        elif isinstance(update, ToolCallStart):
            print(f"\n🔧 {update.title} ({update.status or 'pending'})")
        elif isinstance(update, ToolCallProgress):
            status = update.status or "in_progress"
            print(f"\n🔧 Tool call `{update.tool_call_id}` → {status}")
            if update.content:
                for item in update.content:
                    if isinstance(item, FileEditToolCallContent):
                        print(f"  diff: {item.path}")
                    elif isinstance(item, TerminalToolCallContent):
                        print(f"  terminal: {item.terminal_id}")
                    elif isinstance(item, dict):
                        print(f"  content: {json.dumps(item, indent=2)}")
        else:
            print(f"\n[update] {update}")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _pick_preferred_option(options: Iterable[PermissionOption]) -> PermissionOption | None:
    best: PermissionOption | None = None
    for option in options:
        if option.kind in {"allow_once", "allow_always"}:
            return option
        best = best or option
    return best


def _slice_text(content: str, line: int | None, limit: int | None) -> str:
    lines = content.splitlines()
    start = max((line or 1) - 1, 0)
    end = min(start + limit, len(lines)) if limit else len(lines)
    return "\n".join(lines[start:end])


def _print_text_content(content: object) -> None:
    if isinstance(content, TextContentBlock):
        print(content.text, end="", flush=True)
    elif isinstance(content, ResourceContentBlock):
        print(f"{content.name or content.uri}")
    elif isinstance(content, EmbeddedResourceContentBlock):
        resource = content.resource
        text = getattr(resource, "text", None)
        if text:
            print(text)
        else:
            blob = getattr(resource, "blob", None)
            print(blob if blob else "<embedded resource>")
    elif isinstance(content, dict):
        text = content.get("text")
        if text:
            print(text, end="", flush=True)


def _resolve_copilot_cli(binary: str | None) -> str:
    """Find the `copilot` CLI binary."""
    if binary:
        return binary
    env_value = os.environ.get("ACP_COPILOT_BIN")
    if env_value:
        return env_value
    resolved = shutil.which("copilot")
    if resolved:
        return resolved
    raise FileNotFoundError(
        "Unable to locate `copilot` CLI. "
        "Install it with `npm install -g @githubnext/github-copilot-cli` or provide --copilot path."
    )


# ------------------------------------------------------------------
# Interactive loop
# ------------------------------------------------------------------

async def interactive_loop(conn: ClientSideConnection, session_id: str) -> None:
    print("\nType a message and press Enter to send to Copilot.")
    print("Commands: :cancel, :exit\n")

    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, lambda: input("> ").strip())
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not line:
            continue
        if line in {":exit", ":quit"}:
            break
        if line == ":cancel":
            await conn.cancel(session_id=session_id)
            continue

        print()  # blank line before Copilot's response
        try:
            await conn.prompt(
                session_id=session_id,
                prompt=[text_block(line)],
            )
            print()  # blank line after response
        except RequestError as err:
            _print_request_error("prompt", err)
        except Exception as exc:
            print(f"Prompt failed: {exc}", file=sys.stderr)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

async def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Interactive ACP client for GitHub Copilot CLI.")
    parser.add_argument("--copilot", help="Path to the copilot CLI binary (default: auto-detect)")
    parser.add_argument("--yolo", action="store_true", help="Auto-approve all permission prompts")
    args = parser.parse_args(argv[1:])

    try:
        copilot_path = _resolve_copilot_cli(args.copilot)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    cmd = [copilot_path, "--acp", "--stdio"]
    print(f"🚀 Starting Copilot CLI ACP server: {' '.join(cmd)}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,  # let Copilot's stderr flow through to the terminal
        )
    except FileNotFoundError as exc:
        print(f"Failed to start Copilot CLI: {exc}", file=sys.stderr)
        return 1

    if proc.stdin is None or proc.stdout is None:
        print("Copilot process did not expose stdio pipes.", file=sys.stderr)
        proc.terminate()
        with contextlib.suppress(ProcessLookupError):
            await proc.wait()
        return 1

    client_impl = CopilotClient(auto_approve=args.yolo)

    # connect_to_agent wires the NDJSON ACP framing over the process's stdio
    conn = connect_to_agent(client_impl, proc.stdin, proc.stdout)

    try:
        init_resp = await conn.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(
                fs=FileSystemCapabilities(read_text_file=True, write_text_file=True),
                terminal=True,
            ),
        )
    except RequestError as err:
        _print_request_error("initialize", err)
        await _shutdown(proc, conn)
        return 1
    except Exception as exc:
        print(f"initialize error: {exc}", file=sys.stderr)
        await _shutdown(proc, conn)
        return 1

    print(f"✅ Connected to GitHub Copilot CLI (ACP protocol v{init_resp.protocol_version})")

    try:
        session = await conn.new_session(cwd=os.getcwd(), mcp_servers=[])
    except RequestError as err:
        _print_request_error("new_session", err)
        await _shutdown(proc, conn)
        return 1
    except Exception as exc:
        print(f"new_session error: {exc}", file=sys.stderr)
        await _shutdown(proc, conn)
        return 1

    print(f"📝 Session started: {session.session_id}")

    try:
        await interactive_loop(conn, session.session_id)
    finally:
        await _shutdown(proc, conn)

    return 0


def _print_request_error(stage: str, err: RequestError) -> None:
    payload = err.to_error_obj()
    message = payload.get("message", "")
    code = payload.get("code")
    print(f"{stage} error ({code}): {message}", file=sys.stderr)
    data = payload.get("data")
    if data is not None:
        try:
            formatted = json.dumps(data, indent=2)
        except TypeError:
            formatted = str(data)
        print(formatted, file=sys.stderr)


async def _shutdown(proc: asyncio.subprocess.Process, conn: ClientSideConnection) -> None:
    with contextlib.suppress(Exception):
        await conn.close()
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv if argv is None else argv
    return asyncio.run(run(list(args)))


if __name__ == "__main__":
    raise SystemExit(main())
