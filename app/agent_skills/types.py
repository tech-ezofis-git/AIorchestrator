"""Types for runtime-loaded agent skill packs (SKILL.md + rules/*.mdc)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LoadedRule:
    path: Path
    description: str
    body: str
    always_apply: bool = True


@dataclass(frozen=True)
class LoadedSkill:
    """Resolved skill pack used to build the LLM system prompt."""

    agent: str
    skill_id: str
    name: str
    description: str
    skill_body: str
    rules: tuple[LoadedRule, ...] = field(default_factory=tuple)
    pack_dir: Path = field(default_factory=Path)

    @property
    def system_prompt(self) -> str:
        parts = [self.skill_body.strip()]
        for rule in self.rules:
            if not rule.always_apply:
                continue
            title = rule.description.strip() or rule.path.stem
            parts.append(f"## Rule: {title}\n\n{rule.body.strip()}")
        return "\n\n".join(p for p in parts if p)
