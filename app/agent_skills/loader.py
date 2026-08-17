"""Load replaceable SKILL.md + rules/*.mdc packs for Summary / OCR / Insight.

Agent orchestration stays in Python. These files supply LLM instructions only.
Deterministic enforcement (JSON lock, <mark> injection, OCR parse) stays in code.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.agent_skills.types import LoadedRule, LoadedSkill

logger = logging.getLogger("orchestrator.agent_skills")

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_CANDIDATES = (
    _REPO_ROOT / "skills",       # repo root / Docker /app/skills when COPY . or volume
    _APP_DIR / "skills",         # app/skills — present when only ./app is volume-mounted
)


def default_skills_root() -> Path:
    for candidate in _DEFAULT_CANDIDATES:
        if (
            (candidate / "summary" / "SKILL.md").is_file()
            or (candidate / "ocr" / "SKILL.md").is_file()
            or (candidate / "insight" / "SKILL.md").is_file()
        ):
            return candidate
    return _DEFAULT_CANDIDATES[0]


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    raw = text.lstrip("\ufeff")
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw.strip()
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip("\"'")
    return meta, match.group(2).strip()


def _as_bool(value: Optional[str], default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_pack_dir(
    agent: str,
    *,
    skills_root: Optional[Path] = None,
    pack_dir: Optional[Path] = None,
) -> Path:
    if pack_dir is not None:
        return Path(pack_dir)
    root = Path(skills_root) if skills_root is not None else default_skills_root()
    return root / agent


def _load_rules(rules_dir: Path) -> tuple[LoadedRule, ...]:
    if not rules_dir.is_dir():
        return ()
    loaded: list[LoadedRule] = []
    for path in sorted(rules_dir.glob("*.mdc")):
        meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        loaded.append(
            LoadedRule(
                path=path,
                description=meta.get("description") or path.stem,
                body=body,
                always_apply=_as_bool(meta.get("alwaysApply"), True),
            )
        )
    return tuple(loaded)


def load_skill_pack(
    agent: str,
    *,
    skills_root: Optional[Path] = None,
    pack_dir: Optional[Path] = None,
) -> LoadedSkill:
    """Load `{pack}/SKILL.md` + `{pack}/rules/*.mdc` for summary / ocr / insight."""
    directory = _resolve_pack_dir(
        agent, skills_root=skills_root, pack_dir=pack_dir
    )
    skill_path = directory / "SKILL.md"
    if not skill_path.is_file():
        raise FileNotFoundError(
            f"Skill pack missing SKILL.md for agent '{agent}' at {skill_path}"
        )
    meta, body = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
    rules = _load_rules(directory / "rules")
    skill = LoadedSkill(
        agent=agent,
        skill_id=meta.get("name") or directory.name,
        name=meta.get("name") or directory.name,
        description=meta.get("description") or "",
        skill_body=body,
        rules=rules,
        pack_dir=directory,
    )
    logger.info(
        "agent_skill_loaded",
        extra={
            "agent": agent,
            "skill_id": skill.skill_id,
            "pack_dir": str(directory),
            "rule_count": len(rules),
        },
    )
    return skill


@lru_cache(maxsize=16)
def _cached_pack(agent: str, pack_key: str) -> LoadedSkill:
    return load_skill_pack(agent, pack_dir=Path(pack_key) if pack_key else None)


def clear_skill_cache() -> None:
    _cached_pack.cache_clear()


def resolve_pack_dir_from_settings(agent: str, settings: Optional[object] = None) -> Path:
    """Pick pack directory from Settings / env-backed overrides."""
    if settings is None:
        from app.config import get_settings

        settings = get_settings()

    specific = {
        "summary": getattr(settings, "summary_skill_dir", None),
        "ocr": getattr(settings, "ocr_skill_dir", None),
        "insight": getattr(settings, "insight_skill_dir", None),
    }.get(agent)
    if specific:
        return Path(str(specific)).expanduser()

    root = getattr(settings, "agent_skills_root", None)
    if root:
        return Path(str(root)).expanduser() / agent

    return default_skills_root() / agent


def get_skill(agent: str, *, settings: Optional[object] = None) -> LoadedSkill:
    pack_dir = resolve_pack_dir_from_settings(agent, settings)
    return _cached_pack(agent, str(pack_dir.resolve()))
