"""
C-22 · SKILL_TOOL
Gives the agent the ability to:
  - list   : show all skills in the skills directory
  - read   : read a skill's SKILL.md content
  - create : write a new skill from scratch (auto-generates SKILL.md + references/)
  - use    : inject a skill's content into the agent's next prompt

If the agent needs a skill that doesn't exist, it calls create to generate it
and then continues with the task using the new skill immediately.

CBD CONTRACT
═══════════════════════════════════════════════════════
IN:  operation  str   list|read|create|exists
     name       str?  skill name (slug, e.g. "web-scraping")
     content    str?  full SKILL.md content for create operation
     description str? one-line description for create

OUT: {"ok": bool, "result": str, "skill_name": str, "path": str}

ERROR CODES:
  SKILL_NOT_FOUND    read/use on a skill that doesn't exist
  SKILL_EXISTS       create on a skill that already exists (use overwrite=true)
  SKILL_WRITE_FAIL   filesystem error writing skill
  SKILL_INVALID_NAME name contains invalid characters
═══════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from logger import trace, warn

# ── Config ────────────────────────────────────────────────────────────────────
SKILLS_ROOT = Path(
    os.environ.get("JUAN_SKILLS_DIR", "/app/.claude/skills")
).resolve()

_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9\-]{0,48}[a-z0-9]$')

# ── Error ─────────────────────────────────────────────────────────────────────
class SkillError(Exception):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code

# ── Helpers ───────────────────────────────────────────────────────────────────
def _validate_name(name: str) -> str:
    slug = name.lower().strip().replace(" ", "-").replace("_", "-")
    if not _NAME_RE.match(slug):
        raise SkillError("SKILL_INVALID_NAME",
            f"Skill name '{name}' is invalid. Use lowercase letters, numbers, hyphens only.")
    return slug

def _skill_path(name: str) -> Path:
    return SKILLS_ROOT / name

def _skill_md_path(name: str) -> Path:
    return _skill_path(name) / "SKILL.md"

# ── Operations ────────────────────────────────────────────────────────────────
def _list(**_) -> dict:
    SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    skills = []
    for d in sorted(SKILLS_ROOT.iterdir()):
        if d.is_dir() and (d / "SKILL.md").exists():
            md = (d / "SKILL.md").read_text(encoding="utf-8")
            # Extract description from frontmatter
            desc = ""
            for line in md.splitlines():
                if line.startswith("description:"):
                    desc = line[12:].strip().split(".")[0][:80]
                    break
            skills.append(f"  {d.name:<28} {desc}")
    if not skills:
        result = "No skills installed yet.\nCreate one with: skill(operation='create', name='my-skill', description='...')"
    else:
        result = f"Installed skills ({len(skills)}):\n" + "\n".join(skills)
    trace("SKILL_TOOL", "skill_listed", skill_count=len(skills))
    return {"ok": True, "result": result, "skill_name": "", "path": str(SKILLS_ROOT)}

def _read(name: str = "", **_) -> dict:
    slug = _validate_name(name)
    p    = _skill_md_path(slug)
    if not p.exists():
        raise SkillError("SKILL_NOT_FOUND",
            f"Skill '{slug}' not found. Run skill(operation='list') to see available skills, "
            f"or skill(operation='create', name='{slug}', ...) to create it.")
    content = p.read_text(encoding="utf-8")
    trace("SKILL_TOOL", "skill_read", skill_name=slug)
    return {"ok": True, "result": content, "skill_name": slug, "path": str(p)}

def _exists(name: str = "", **_) -> dict:
    slug   = _validate_name(name)
    p      = _skill_md_path(slug)
    exists = p.exists()
    return {"ok": True, "result": "exists" if exists else "not_found",
            "skill_name": slug, "path": str(p)}

def _create(
    name: str        = "",
    content: str     = "",
    description: str = "",
    overwrite: bool  = False,
    **_
) -> dict:
    slug = _validate_name(name)
    p    = _skill_md_path(slug)

    if p.exists() and not overwrite:
        raise SkillError("SKILL_EXISTS",
            f"Skill '{slug}' already exists. Use overwrite=true to replace it, "
            f"or read it first with skill(operation='read', name='{slug}').")

    SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
    skill_dir = _skill_path(slug)
    skill_dir.mkdir(parents=True, exist_ok=True)

    # If content provided, use it. Otherwise auto-generate a template.
    if not content.strip():
        content = _auto_generate(slug, description)

    # Ensure frontmatter has name and description
    if not content.startswith("---"):
        header = (
            f"---\nname: {slug}\n"
            f"description: {description or slug + ' skill'}\n---\n\n"
        )
        content = header + content

    try:
        tmp = str(p) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, str(p))
    except OSError as e:
        raise SkillError("SKILL_WRITE_FAIL", str(e))

    trace("SKILL_TOOL", "skill_created",
          skill_name=slug, char_count=len(content),
          auto_generated=not bool(content.strip()))

    return {
        "ok":         True,
        "result":     f"Skill '{slug}' created at {p}\n\nContent preview:\n{content[:400]}",
        "skill_name": slug,
        "path":       str(p),
    }

def _auto_generate(name: str, description: str) -> str:
    """Generate a minimal valid SKILL.md template."""
    return f"""---
name: {name}
description: {description or name + ' — auto-generated skill. Edit SKILL.md to fill in the details.'}
---

# {name.replace('-', ' ').title()}

{description or 'Describe what this skill does and when to use it.'}

---

## When to use this skill

- Describe the trigger conditions here
- What user requests should activate this skill?

---

## Key instructions

1. Step one
2. Step two
3. Step three

---

## Examples

**Input:** Example user request

**Output:** What the response should look like

---

## Notes

- Any caveats or special considerations
- Environment requirements
"""

# ── Dispatch ──────────────────────────────────────────────────────────────────
_OPS = {"list": _list, "read": _read, "create": _create, "exists": _exists}

def skill_tool(
    operation:   str = "list",
    name:        str = "",
    content:     str = "",
    description: str = "",
    overwrite:   bool = False,
) -> dict:
    """C-22 · SKILL_TOOL entry point registered as 'skill'."""
    op = _OPS.get(operation)
    if op is None:
        return {"ok": False,
                "result": f"Unknown operation '{operation}'. Valid: {sorted(_OPS)}",
                "skill_name": name, "path": ""}
    try:
        return op(name=name, content=content,
                  description=description, overwrite=overwrite)
    except SkillError as e:
        return {"ok": False, "result": f"[{e.error_code}] {e}",
                "skill_name": name, "path": ""}
    except Exception as e:
        return {"ok": False, "result": f"[SKILL_WRITE_FAIL] {e}",
                "skill_name": name, "path": ""}

# ── Schema ────────────────────────────────────────────────────────────────────
SKILL_TOOL_SCHEMA = {
    "type": "custom",
    "name": "skill",
    "description": (
        "Manage agent skills. List available skills, read a skill's instructions, "
        "create a new skill from scratch, or check if a skill exists. "
        "If you need a skill that doesn't exist yet, use operation='create' to generate it "
        "and then immediately use its instructions to complete the task."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["list", "read", "create", "exists"],
                "description": (
                    "list: show all installed skills. "
                    "read: read a skill's SKILL.md content. "
                    "create: create a new skill (provide name + description, optionally content). "
                    "exists: check if a skill exists without reading it."
                ),
            },
            "name": {
                "type": "string",
                "description": "Skill name as a slug (e.g. 'web-scraping', 'data-analysis'). Required for read/create/exists.",
            },
            "description": {
                "type": "string",
                "description": "One-line description of the skill. Used when creating a new skill.",
            },
            "content": {
                "type": "string",
                "description": "Full SKILL.md content for create. If omitted, a template is auto-generated.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Set to true to overwrite an existing skill. Default: false.",
            },
        },
        "required": ["operation"],
    },
}
