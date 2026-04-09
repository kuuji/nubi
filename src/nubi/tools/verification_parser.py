"""Parse verification commands from AGENTS.md or CLAUDE.md."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from nubi.agents.gate_result import GateCategory, GateDiscovery

VERIFICATION_FILES = ["AGENTS.md", "CLAUDE.md"]
SECTION_RE = re.compile(r"^##\s+Verification", re.IGNORECASE)
# Match: "1. `cmd` — desc" or "1. cmd — desc" (with or without backticks)
COMMAND_RE = re.compile(r"^\d+\.\s+`([^`]+)`|^\d+\.\s+(\S[^\n—–-]*\S)")

CATEGORY_KEYWORDS: list[tuple[str, GateCategory]] = [
    ("ruff format", GateCategory.FORMAT),
    ("ruff check", GateCategory.LINT),
    ("mypy", GateCategory.LINT),
    ("pytest", GateCategory.TEST),
    ("eslint", GateCategory.LINT),
    ("jest", GateCategory.TEST),
    ("radon", GateCategory.COMPLEXITY),
]


@dataclass
class VerificationCommand:
    raw_command: str
    category: GateCategory
    tool_name: str


def parse_verification_commands(workspace: str) -> list[VerificationCommand] | None:
    """Parse verification commands from AGENTS.md or CLAUDE.md.

    Returns None if no verification section found (caller should fall back).
    """
    for filename in VERIFICATION_FILES:
        path = os.path.join(workspace, filename)
        if not os.path.isfile(path):
            continue
        with open(path) as f:
            content = f.read()
        commands = _extract_commands(content)
        if commands is not None:
            return commands
    return None


def _extract_commands(content: str) -> list[VerificationCommand] | None:
    """Extract commands from a verification section in markdown."""
    lines = content.splitlines()
    in_section = False
    commands: list[VerificationCommand] = []

    for line in lines:
        if SECTION_RE.match(line):
            in_section = True
            continue
        if in_section and line.startswith("##"):
            break
        if in_section:
            match = COMMAND_RE.match(line.strip())
            if match:
                raw = (match.group(1) or match.group(2)).strip()
                # Strip trailing description after — or --
                raw = re.split(r"\s+[—–-]{1,2}\s+", raw)[0].strip()
                category = _categorize_command(raw)
                tool_name = _extract_tool_name(raw)
                if category and tool_name:
                    commands.append(
                        VerificationCommand(
                            raw_command=raw,
                            category=category,
                            tool_name=tool_name,
                        )
                    )

    return commands if in_section else None


def _categorize_command(cmd: str) -> GateCategory | None:
    """Categorize a command by matching keywords."""
    for keyword, category in CATEGORY_KEYWORDS:
        if keyword in cmd:
            return category
    return None


def _extract_tool_name(cmd: str) -> str:
    """Extract the tool name (first word) from a command."""
    return cmd.split()[0]


def to_gate_discoveries(commands: list[VerificationCommand]) -> list[GateDiscovery]:
    """Convert parsed verification commands to GateDiscovery objects."""
    return [
        GateDiscovery(
            name=cmd.tool_name,
            category=cmd.category,
            command=cmd.raw_command,
        )
        for cmd in commands
    ]
