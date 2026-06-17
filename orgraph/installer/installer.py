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
    get_mcp_entry,
    get_opencode_mcp_entry,
    is_detected,
)
from orgraph.installer.config import (
    _bake_repo_path,
    _remove_stale_project_scoped,
    claude_mcp_add,
    claude_mcp_remove,
    merge_claude_mcp,
    merge_json_mcp,
    merge_toml_mcp,
    remove_claude_mcp,
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


def _apply_mcp(agent: AgentTarget, mode: Mode, repo_path: Path | None = None) -> WriteResult | None:
    if agent.mcp is None:
        return None
    path, key = agent.mcp.path, agent.mcp.key

    # Use the currently-running orgraph binary so we don't re-fetch from PyPI
    entry = get_opencode_mcp_entry() if agent.id == "opencode" else get_mcp_entry()

    if key == "mcp_servers":  # TOML (Codex)
        action = merge_toml_mcp(path, repo_path) if mode == "install" else remove_toml_mcp(path)
    elif agent.id == "claude":
        # Use `claude mcp add/remove` so Claude Code owns the config format.
        # Also wipe stale project-scoped entries to prevent scope conflicts.
        bin_ = entry["command"] if isinstance(entry.get("command"), str) else str(entry.get("command", ""))
        args_ = [a for a in entry.get("args", []) if isinstance(a, str)]
        if mode == "install":
            _remove_stale_project_scoped(path)
            action = claude_mcp_add("orgraph", bin_, args_, scope="user")
        else:
            action = claude_mcp_remove("orgraph", scope="user")
    elif mode == "install":
        # Bake abs repo path into args for all other agents too (replaces "." placeholder)
        baked = _bake_repo_path(entry, repo_path) if repo_path else entry
        action = merge_json_mcp(path, key, baked)
    else:
        action = remove_json_mcp(path, key)
    return WriteResult(path, action)


def _apply_instructions(agent: AgentTarget, mode: Mode, repo_path: Path | None = None) -> WriteResult | None:
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
        _apply_mcp,  # type: ignore[arg-type]  # repo_path injected at call site
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


def _print_plan(agents: list[AgentTarget], integrations: list[Integration], repo_path: Path | None = None) -> None:
    print(f"\n  {_BOLD}Plan:{_RESET}\n")
    for agent in agents:
        print(f"  {_BOLD}{agent.display_name}{_RESET}")
        for integ in integrations:
            path = integ.plan_path(agent)
            ok = path is not None
            print(f"    {integ.label:<13} {_tick(ok)}  {path if ok else '(not supported)'}")
    print()


def _apply_all(mode: Mode, agents: list[AgentTarget], integrations: list[Integration], repo_path: Path | None = None) -> None:
    print()
    for agent in agents:
        print(f"  {_BOLD}{agent.display_name}{_RESET}")
        for integ in integrations:
            if integ.id == "mcp":
                result = _apply_mcp(agent, mode, repo_path)
            elif integ.id == "instructions":
                result = _apply_instructions(agent, mode, repo_path)
            else:
                result = integ.apply(agent, mode)
            if result is None:
                print(f"    {_DIM}– {integ.id}: not supported{_RESET}")
                continue
            ok = result.action in ("created", "updated", "removed", "unchanged")
            print(f"    {_tick(ok)} {integ.id} ({result.action}) → {result.path}")
        print()


def run(mode: Mode, repo_path: Path | None = None) -> None:
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

    _print_plan(chosen_agents, chosen_integrations, repo_path)

    if not questionary.confirm("Proceed?", default=install).ask():
        _exit("Cancelled.")

    _apply_all(mode, chosen_agents, chosen_integrations, repo_path)

    if install:
        repo_display = str(repo_path) if repo_path else "."
        print(
            f"  {_GREEN}Done!{_RESET}  Restart your agents to pick up the changes.\n\n"
            f"  Registered for repo: {repo_display}\n\n"
            "  First run:\n"
            f"    orgraph index {repo_display}    # one-time index (or skip — serve auto-indexes)\n"
        )
    else:
        print(f"  {_GREEN}Done!{_RESET}  orgraph configuration removed.\n")
