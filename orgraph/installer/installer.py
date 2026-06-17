"""Interactive installer/uninstaller for orgraph MCP across coding agents."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NoReturn, Sequence, TypeVar

import questionary

from orgraph.installer.agents import (
    AGENTS,
    CLAUDE_MD_BLOCK,
    Action,
    AgentTarget,
    Mode,
    is_detected,
)
from orgraph.installer.config import (
    merge_json_mcp,
    merge_toml_mcp,
    remove_instructions,
    remove_json_mcp,
    remove_toml_mcp,
    upsert_instructions,
)

_T = TypeVar("_T")

_GREEN = "\033[32m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_BOLD = "\033[1m"


@dataclass(frozen=True)
class WriteResult:
    path: Path
    action: Action


@dataclass(frozen=True)
class Integration:
    id: str
    label: str
    desc: str
    apply: Callable[[AgentTarget, Mode], WriteResult | None]
    plan_path: Callable[[AgentTarget], Path | None]


def _apply_mcp(agent: AgentTarget, mode: Mode) -> WriteResult | None:
    if agent.mcp is None:
        return None
    path, key, entry = agent.mcp.path, agent.mcp.key, agent.mcp.entry
    if key == "mcp_servers":  # TOML (Codex)
        action = merge_toml_mcp(path) if mode == "install" else remove_toml_mcp(path)
    elif mode == "install":
        action = merge_json_mcp(path, key, entry)
    else:
        action = remove_json_mcp(path, key)
    return WriteResult(path, action)


def _apply_instructions(agent: AgentTarget, mode: Mode) -> WriteResult | None:
    if agent.instructions_path is None:
        return None
    path = agent.instructions_path
    action = upsert_instructions(path, CLAUDE_MD_BLOCK) if mode == "install" else remove_instructions(path)
    return WriteResult(path, action)


_INTEGRATIONS: list[Integration] = [
    Integration(
        "mcp",
        "MCP server",
        "lets the agent call orgraph tools directly",
        _apply_mcp,
        lambda a: a.mcp.path if a.mcp else None,
    ),
    Integration(
        "instructions",
        "Instructions",
        "adds tool usage guidance to CLAUDE.md / AGENTS.md",
        _apply_instructions,
        lambda a: a.instructions_path,
    ),
]


def _tick(ok: bool) -> str:
    return f"{_GREEN}✓{_RESET}" if ok else f"{_DIM}–{_RESET}"


def _exit(msg: str) -> NoReturn:
    print(msg)
    sys.exit(0)


def _checkbox(prompt: str, items: Sequence[tuple[str, _T, bool]]) -> list[_T] | None:
    style = questionary.Style([
        ("pointer", "bold"),
        ("highlighted", "noreverse bold"),
        ("selected", "noreverse fg:ansigreen"),
    ])
    choices = [questionary.Choice(title=label, value=val, checked=checked) for label, val, checked in items]
    return questionary.checkbox(
        prompt, choices=choices, style=style, instruction="(↑↓ move · space select · a all · enter confirm)"
    ).ask()


def _print_plan(agents: list[AgentTarget], integrations: list[Integration]) -> None:
    print(f"\n  {_BOLD}Plan:{_RESET}\n")
    for agent in agents:
        print(f"  {_BOLD}{agent.display_name}{_RESET}")
        for integ in integrations:
            path = integ.plan_path(agent)
            ok = path is not None
            print(f"    {integ.label:<13} {_tick(ok)}  {path if ok else '(not supported)'}")
    print()


def _apply_all(mode: Mode, agents: list[AgentTarget], integrations: list[Integration]) -> None:
    print()
    for agent in agents:
        print(f"  {_BOLD}{agent.display_name}{_RESET}")
        for integ in integrations:
            result = integ.apply(agent, mode)
            if result is None:
                print(f"    {_DIM}– {integ.id}: not supported{_RESET}")
                continue
            ok = result.action in ("created", "updated", "removed", "unchanged")
            print(f"    {_tick(ok)} {integ.id} ({result.action}) → {result.path}")
        print()


def run(mode: Mode) -> None:
    """Interactively install or uninstall orgraph across coding agents."""
    install = mode == "install"
    print(f"\n  {_BOLD}{'orgraph Installer' if install else 'orgraph Uninstaller'}{_RESET}\n")
    print(
        "  orgraph gives coding agents a codebase knowledge graph — call chains,\n"
        "  topology clusters, dependency trees — on top of hybrid code search.\n"
    )

    agent_items = [
        (
            f"{a.display_name}{'  (detected)' if is_detected(a) else ''}",
            a,
            is_detected(a) and install,
        )
        for a in sorted(AGENTS, key=lambda a: not is_detected(a))
    ]
    chosen_agents = _checkbox(
        f"Select agents to {'configure' if install else 'remove configuration from'}:",
        agent_items,
    ) or _exit("Nothing selected. Exiting.")

    integ_items = [
        (f"{i.label:<13}  —  {i.desc}", i, True)
        for i in _INTEGRATIONS
    ]
    chosen_integrations = _checkbox(
        f"Select integrations to {'enable' if install else 'remove'}:",
        integ_items,
    ) or _exit("Nothing selected. Exiting.")

    _print_plan(chosen_agents, chosen_integrations)

    if not questionary.confirm("Proceed?", default=install).ask():
        _exit("Cancelled.")

    _apply_all(mode, chosen_agents, chosen_integrations)

    if install:
        print(
            f"  {_GREEN}Done!{_RESET}  Restart your agents to pick up the changes.\n\n"
            "  First run in any repo:\n"
            "    orgraph index .        # one-time index (or skip — serve auto-indexes)\n"
        )
    else:
        print(f"  {_GREEN}Done!{_RESET}  orgraph configuration removed.\n")
