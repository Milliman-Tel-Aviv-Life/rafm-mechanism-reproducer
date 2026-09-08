"""
Graft a page-range print of a RiskAgility FM Audit Report onto an existing
Hierarchy_<Client>_High.json / hierarchie_<client>_Low.json pair.

Usage:
    python scripts/merge_partial_hierarchy.py <ClientName> "<path to partial AuditReport.pdf>"

Why this exists: the full audit report PDF is huge, and the report generator
truncates long formula bodies. A fresh print of just the Formulas appendix
(chapter 8) carries complete bodies, but none of the chapters Stage 2 needs
for the model map (Summary, Input Manager, Code Manager, ...). This script
keeps every chapter of the existing pair and replaces only the second-level
sections the partial print covers (typically 7.1, 8.1 and 8.2).

Before writing, the existing pair is copied to docs/backup_<client>_<date>/.

Line-wrap repair: "Microsoft Print to PDF" wraps long code lines at ~100
characters, which can split the C++ arrow operator into "main-" / ">x". That
sequence never occurs on purpose, so "-\n>" is joined back into "->".
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_hierarchy_from_pdf import extract  # noqa: E402

DOCS = Path(__file__).resolve().parent.parent / "docs"


def _repair_wraps(text: str) -> str:
    return re.sub(r"-\n>", "->", text)


def _partie_for(numero: str, titre_by_numero: dict[str, str]) -> str:
    parts = numero.split(".")
    top = titre_by_numero.get(parts[0], "")
    if top == "Appendix" and len(parts) >= 2:
        return titre_by_numero.get(".".join(parts[:2]), top)
    return top


def merge(old: list[dict], partial: list[dict]) -> tuple[list[dict], list[str]]:
    covered = sorted(
        {n["numero"] for n in partial if n["niveau"] == 2},
        key=lambda s: tuple(int(x) for x in s.split(".")),
    )
    if not covered:
        raise SystemExit("The partial print contains no second-level section — nothing to graft.")

    def under(numero: str, section: str) -> bool:
        return numero == section or numero.startswith(section + ".")

    partial_by_section = {
        s: [n for n in partial if under(n["numero"], s)] for s in covered
    }
    for n in partial:
        if n["niveau"] == 1:
            # A depth-1 chapter heading that the print happens to include
            # (e.g. "8 Appendix"): keep the existing node instead.
            pass

    merged: list[dict] = []
    inserted: set[str] = set()
    for n in old:
        section = next((s for s in covered if under(n["numero"], s)), None)
        if section is None:
            merged.append(dict(n))
            continue
        if section not in inserted:
            merged.extend(dict(p) for p in partial_by_section[section])
            inserted.add(section)
        # every other old node of a covered section is dropped

    missing = [s for s in covered if s not in inserted]
    if missing:
        # Section exists in the print but not in the old pair: append at the
        # end so nothing is silently lost.
        for s in missing:
            merged.extend(dict(p) for p in partial_by_section[s])

    titre_by_numero = {n["numero"]: n["titre"] for n in merged}
    for n in merged:
        n["partie"] = _partie_for(n["numero"], titre_by_numero)
        if "contenu" in n:
            n["contenu"] = _repair_wraps(n["contenu"])
            n["contenu_len"] = len(n["contenu"])
            n["a_du_code"] = (
                n["partie"] == "Formulas"
                and bool(n["contenu"])
                and not n["contenu"].startswith("Description:")
            )
    return merged, covered


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <ClientName> <partial.pdf>", file=sys.stderr)
        sys.exit(1)
    client, pdf = sys.argv[1], Path(sys.argv[2])
    high_path = DOCS / f"Hierarchy_{client}_High.json"
    low_path = DOCS / f"hierarchie_{client.lower()}_Low.json"
    for p in (high_path, low_path, pdf):
        if not p.exists():
            print(f"File not found: {p}", file=sys.stderr)
            sys.exit(1)

    old_low = json.loads(low_path.read_text(encoding="utf-8"))
    partial, _ = extract(pdf, partial=True)
    if not partial:
        print("No nodes extracted from the partial print.", file=sys.stderr)
        sys.exit(1)

    merged_low, covered = merge(old_low, partial)
    merged_high = [
        {k: v for k, v in n.items() if k not in ("contenu", "contenu_len", "a_du_code")}
        for n in merged_low
    ]

    backup = DOCS / f"backup_{client.lower()}_{date.today():%Y%m%d}"
    backup.mkdir(exist_ok=True)
    shutil.copy2(high_path, backup / high_path.name)
    shutil.copy2(low_path, backup / low_path.name)

    high_path.write_text(json.dumps(merged_high, ensure_ascii=False, indent=2), encoding="utf-8")
    low_path.write_text(json.dumps(merged_low, ensure_ascii=False, indent=2), encoding="utf-8")

    old_code = sum(1 for n in old_low if n.get("a_du_code"))
    new_code = sum(1 for n in merged_low if n.get("a_du_code"))
    print(f"Replaced sections {', '.join(covered)} from {pdf.name}")
    print(f"Nodes: {len(old_low)} -> {len(merged_low)}; formulas with code: {old_code} -> {new_code}")
    print(f"Backup of the previous pair: {backup}")
    print(f"Wrote {high_path}")
    print(f"Wrote {low_path}")


if __name__ == "__main__":
    main()
