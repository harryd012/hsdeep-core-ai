"""
Import the YAML SOP library (backend/data/sop-library/sop-NNN-*.yaml) into a
tenant's SOP repository.

Each file becomes one SOP with a single DRAFT version: the library marks every
procedure as draft, and a draft is never promoted to current, so SOPMatcher does
not recommend it until someone approves it in the SOP workflow.

Idempotent: keyed on (tenant_id, sop_number); an existing number is skipped,
never overwritten.

Usage (from backend/):
    python scripts/import_sop_library.py              # tenant from SEED_TENANT_ID
    python scripts/import_sop_library.py --tenant <uuid> [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from pathlib import Path

import yaml

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from dotenv import load_dotenv

load_dotenv(BACKEND / ".env")

from app.core.db import AsyncSessionLocal
from app.repositories.sop_repository import SOPRepository

LIBRARY = BACKEND / "data" / "sop-library"
CATEGORIES = {"availability", "performance", "capacity", "configuration", "security", "connectivity", "other"}
HEADER = re.compile(r"^#\s*([A-Za-z ]+):\s*(.+?)\s*$")


def _items(value) -> list[str]:
    """A YAML list, or a `|` block of "- item" lines, as a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [ln.strip().lstrip("-").strip() for ln in str(value).splitlines() if ln.strip().lstrip("-").strip()]


def parse_file(path: Path) -> dict:
    """One library file -> SOP fields plus its version's steps and causes."""
    text = path.read_text(encoding="utf-8")
    header: dict[str, str] = {}
    title = None
    for line in text.splitlines():
        if not line.startswith("#"):
            if line.strip() == "---":
                break
            continue
        m = re.match(r"^#\s*(SOP-\d{3}):\s*(.+?)\s*$", line)
        if m and title is None:
            title = m.group(2)
            continue
        h = HEADER.match(line)
        if h:
            header[h.group(1).strip().lower()] = h.group(2)
    doc = next(d for d in yaml.safe_load_all(text) if d)

    number = doc["sopId"]
    if not re.fullmatch(r"SOP-\d{3}", number) or not path.name.startswith(number.lower() + "-"):
        raise ValueError(f"{path.name}: sopId {number!r} does not match the file name")
    if title is None:
        raise ValueError(f"{path.name}: no '# {number}: <title>' header line")
    category = str(doc.get("category", "other")).strip().lower()
    if category not in CATEGORIES:
        raise ValueError(f"{path.name}: unknown category {category!r}")

    minutes = re.match(r"(\d+)", header.get("estimated duration", ""))
    tags = []
    for t in header.get("tags", "").split(","):
        t = t.strip().lower()[:50]
        if t and t not in tags:
            tags.append(t)

    steps = []
    for i, st in enumerate(doc.get("steps") or [], start=1):
        step = {"order": int(st.get("order", i)), "action": str(st["action"]).strip()}
        if st.get("description"):
            step["description"] = str(st["description"]).strip()
        if st.get("expectedResult"):
            step["expected_result"] = str(st["expectedResult"]).strip()
        if st.get("warnings"):
            step["warnings"] = _items(st["warnings"])
        if st.get("failureAction"):
            step["failure_action"] = str(st["failureAction"]).strip()
        steps.append(step)
    commands = [
        {k: v for k, v in {
            "command": str(c.get("command", "")).strip(),
            "expected_result": str(c.get("expectedResult", "")).strip() or None,
            "warnings": _items(c.get("warnings")) or None,
        }.items() if v}
        for c in doc.get("commandsAndExpectedResults") or []
        if isinstance(c, dict) and c.get("command")
    ]
    if commands:
        # Reference commands are not ordered procedure steps; keep them as one
        # clearly labelled final entry so nothing from the library is dropped.
        steps.append({"order": len(steps) + 1, "action": "Reference commands and expected results", "commands": commands})

    causes: list[str] = []
    for row in doc.get("troubleshootingMatrix") or []:
        for c in _items(row.get("possibleCauses") if isinstance(row, dict) else None):
            if c not in causes:
                causes.append(c)

    rollback = _items(doc.get("rollbackPrecautions"))
    return {
        "sop": {
            "sop_number": number,
            "name": title[:255],
            "description": None,
            "category": category,
            "tags": tags,
            "required_skills": [s.strip() for s in header.get("required skills", "").split(",") if s.strip()],
            "requires_approval": True,
            "estimated_resolution_minutes": int(minutes.group(1)) if minutes else None,
            "rollback_procedure": "\n".join(f"- {r}" for r in rollback) or None,
            "is_active": True,
        },
        "steps": steps,
        "possible_causes": causes,
        "change_summary": str(doc.get("changeSummary") or "Imported from the SOP library.").strip(),
    }


async def import_library(tenant_id: uuid.UUID, dry_run: bool = False) -> int:
    parsed = [parse_file(p) for p in sorted(LIBRARY.glob("sop-[0-9][0-9][0-9]-*.yaml"))]
    async with AsyncSessionLocal() as db:
        repo = SOPRepository(db)
        created = 0
        for item in parsed:
            number = item["sop"]["sop_number"]
            if await repo.get_by_number(tenant_id, number) is not None:
                print(f"[import_sop_library] {number} already exists - skipped.")
                continue
            print(f"[import_sop_library] {'Would create' if dry_run else 'Creating'} {number} | {item['sop']['name']} "
                  f"({len(item['steps'])} steps, draft v1).")
            if dry_run:
                continue
            sop = await repo.create(tenant_id=tenant_id, **item["sop"])
            await repo.create_version(
                tenant_id=tenant_id, sop_id=sop.id, version_number=1, steps=item["steps"],
                possible_causes=item["possible_causes"], change_summary=item["change_summary"], status="draft",
            )
            created += 1
        if not dry_run:
            await db.commit()
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", default=os.environ.get("SEED_TENANT_ID"), help="tenant id (default: SEED_TENANT_ID)")
    parser.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    args = parser.parse_args()
    if not args.tenant:
        print("[import_sop_library] Aborted: pass --tenant or set SEED_TENANT_ID in backend/.env")
        return 1
    created = asyncio.run(import_library(uuid.UUID(args.tenant), dry_run=args.dry_run))
    print(f"[import_sop_library] Done: {created} new SOP(s) created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
