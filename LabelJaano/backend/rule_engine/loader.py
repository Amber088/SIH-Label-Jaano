"""
Pack loading and selection.

Loads every ``*.json`` pack from the rulepacks directory, then for a given product
category builds the merged :class:`RuleSet` that applies:

    RuleSet = base pack(s)  +  every category pack whose applies_when matches

Merge rules:
  * base packs first, then category packs;
  * a category pack may **override** a base declaration by reusing its ``id``;
  * the font-height table comes from the first pack that defines one (the base pack);
  * scoring weights come from the first pack that defines them.

The rulepacks directory defaults to ``<project_root>/rulepacks`` but can be
overridden with the ``LABEL_JAANO_RULEPACKS`` env var or the ``rulepacks_dir`` arg —
handy for tests and for pointing at a gazette-updated pack set later.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .models import Pack, RuleSet

# <project_root>/backend/rule_engine/loader.py  ->  parents[2] == <project_root>
_DEFAULT_RULEPACKS = Path(__file__).resolve().parents[2] / "rulepacks"


def rulepacks_dir(override: Optional[str | os.PathLike] = None) -> Path:
    if override:
        return Path(override)
    env = os.environ.get("LABEL_JAANO_RULEPACKS")
    if env:
        return Path(env)
    return _DEFAULT_RULEPACKS


def load_packs(rulepacks_dir_path: Optional[str | os.PathLike] = None) -> list[Pack]:
    d = rulepacks_dir(rulepacks_dir_path)
    if not d.exists():
        raise FileNotFoundError(f"rulepacks directory not found: {d}")
    packs: list[Pack] = []
    for path in sorted(d.glob("*.json")):
        with open(path, "r", encoding="utf-8") as fh:
            packs.append(Pack.from_dict(json.load(fh)))
    if not packs:
        raise FileNotFoundError(f"no *.json rule packs found in {d}")
    return packs


def load_pack_dicts(rulepacks_dir_path: Optional[str | os.PathLike] = None) -> dict[str, dict]:
    """Return the raw parsed JSON of every pack, keyed by ``pack_id``.

    Unlike :func:`load_packs`, this keeps the packs as plain dicts so the API can
    serve the full, unmodified rule text (every check + regex) for a "view the gov
    rules" endpoint — useful for auditing exactly what the engine enforces.
    """
    d = rulepacks_dir(rulepacks_dir_path)
    if not d.exists():
        raise FileNotFoundError(f"rulepacks directory not found: {d}")
    out: dict[str, dict] = {}
    for path in sorted(d.glob("*.json")):
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        out[raw.get("pack_id", path.stem)] = raw
    if not out:
        raise FileNotFoundError(f"no *.json rule packs found in {d}")
    return out


def build_ruleset(category: str,
                  packs: Optional[list[Pack]] = None,
                  rulepacks_dir_path: Optional[str | os.PathLike] = None) -> RuleSet:
    """Select and merge the packs that apply to ``category`` into one RuleSet."""
    if packs is None:
        packs = load_packs(rulepacks_dir_path)

    applicable = [p for p in packs if p.applies_to(category)]
    # base packs first so category packs can override by id
    applicable.sort(key=lambda p: 0 if p.scope == "base" else 1)

    merged: dict[str, "Pack"] = {}  # declaration id -> declaration (later wins)
    order: list[str] = []
    font_table = None
    weights = None
    ref_merged: dict = {}           # reference-standard id -> ReferenceStandard (later wins)
    ref_order: list[str] = []

    for pack in applicable:
        if font_table is None and pack.font_height_table:
            font_table = pack.font_height_table
        if weights is None and pack.scoring.get("weights"):
            weights = pack.scoring["weights"]
        for decl in pack.declarations:
            if decl.id not in merged:
                order.append(decl.id)
            merged[decl.id] = decl
        for ref in pack.reference_standards:
            if ref.id not in ref_merged:
                ref_order.append(ref.id)
            ref_merged[ref.id] = ref

    return RuleSet(
        declarations=[merged[i] for i in order],
        font_height_table=font_table,
        packs_applied=[p.pack_id for p in applicable],
        weights=weights or {"critical": 3, "major": 2, "minor": 1},
        reference_standards=[ref_merged[i] for i in ref_order],
    )
