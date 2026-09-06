"""
Extract Hierarchy_<name>_High.json / hierarchie_<name>_Low.json from a
"Willis Towers Watson RiskAgility FM Audit Report" PDF export.

Usage:
    pip install -e ".[pdf]"
    python scripts/extract_hierarchy_from_pdf.py "<path to AuditReport.pdf>" <ClientName>

Writes:
    docs/Hierarchy_<ClientName>_High.json
    docs/hierarchie_<clientname>_Low.json

These report PDFs share one format across clients: every body page repeats a
fixed header line followed by a bare page-number line; sections are numbered
with a dotted outline (1, 1.3, 1.3.1, 6.3.1.1.1, ...) where the number and
title appear either on one line ("1.3 Job Details") or on two consecutive
lines (number alone, title on the next line). The Table of Contents uses the
same numbering but with dot-leaders + a page reference appended, and its
pages never carry the body's self-referential page-number line — that's how
we tell body pages from TOC/cover pages and skip the latter entirely.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import pymupdf
except ImportError:
    print(
        "pymupdf is required for this script but is not installed.\n"
        "Install it with:  pip install -e \".[pdf]\"",
        file=sys.stderr,
    )
    sys.exit(1)

HEADER_LINE = "Willis Towers Watson RiskAgility FM Audit Report"
# RAFM variable-metadata labels whose value is commonly a small integer that
# could otherwise collide with a valid next-sibling numero pattern.
_NUMERIC_METADATA_LABELS = {
    "Length:",
    "Number of Decimals:",
    "Category Order:",
    "Valid Range From:",
    "Valid Range To:",
    "Default Value:",
}
# The report template only ever generates these top-level chapters (confirmed
# across three different client reports' tables of contents). A depth-1
# candidate whose title isn't one of these is virtually always a stray
# numeric data-table cell (e.g. row indices in an External Sources table)
# colliding with the "next top-level sibling" numero pattern, not a real
# section — accepting it would silently misattribute everything that follows
# to a bogus node. Reject it (treat the line as content) instead.
_KNOWN_TOP_LEVEL_TITLES = {
    "Summary",
    "Input Manager",
    "Code Manager",
    "Wildcards",
    "Appendix",
    "Referenced Files",
    "Output Manager",
    "Run Manager",
}
_PAGE_NUM_RE = re.compile(r"^\d+$")
_INLINE_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+(\S.*)$")
_BARE_NUMERO_RE = re.compile(r"^\d+(?:\.\d+)*$")


def _numero_parts(numero: str) -> tuple[int, ...]:
    return tuple(int(p) for p in numero.split("."))


def _body_lines(doc) -> list[tuple[str, int]]:
    """
    Return (line_text, page_number) pairs for body pages only, with the
    repeated header + page-number preamble stripped. Cover page and every
    Table-of-Contents page are skipped because they lack the body's
    self-referential page-number line right after the header.
    """
    out: list[tuple[str, int]] = []
    skipped_pages = 0
    for page in doc:
        lines = [ln.strip() for ln in page.get_text().split("\n")]
        lines = [ln for ln in lines if ln]
        if not lines or lines[0] != HEADER_LINE:
            skipped_pages += 1
            continue
        if len(lines) < 2 or not _PAGE_NUM_RE.match(lines[1]):
            skipped_pages += 1
            continue
        page_num = page.number + 1
        for ln in lines[2:]:
            out.append((ln, page_num))
    if skipped_pages > doc.page_count * 0.5:
        print(
            f"WARNING: skipped {skipped_pages}/{doc.page_count} pages as "
            "non-body (cover/TOC) — that's unusually high, verify the output.",
            file=sys.stderr,
        )
    return out


def _valid_next(parts: tuple[int, ...], stack: list[str]) -> bool:
    if not stack:
        return parts == (1,)
    L = len(parts)
    if L == len(stack) + 1:
        top_parts = _numero_parts(stack[-1])
        return parts[:-1] == top_parts and parts[-1] == 1
    if 1 <= L <= len(stack):
        ref_parts = _numero_parts(stack[L - 1])
        return parts[:-1] == ref_parts[:-1] and parts[-1] == ref_parts[-1] + 1
    return False


def _parse_nodes(lines: list[tuple[str, int]]) -> tuple[list[dict], list[tuple[str, str]]]:
    raw_nodes: list[dict] = []
    stack: list[str] = []
    current: dict | None = None
    rejected_top_level: list[tuple[str, str]] = []

    def finalize():
        if current is not None:
            current["contenu"] = "\n".join(current["_content"]).strip()
            del current["_content"]
            raw_nodes.append(current)

    i = 0
    n = len(lines)
    while i < n:
        line, page_num = lines[i]

        # A known numeric metadata label is always immediately followed by
        # its own value, never by the start of a new section — guards
        # against stray field values (Length/Number of Decimals/...)
        # colliding with valid next-sibling numero patterns.
        preceded_by_label = i > 0 and lines[i - 1][0] in _NUMERIC_METADATA_LABELS

        numero = None
        title = None
        consumed = 1

        if not preceded_by_label:
            m = _INLINE_HEADING_RE.match(line)
            if m:
                candidate_numero, candidate_title = m.group(1), m.group(2)
                if _valid_next(_numero_parts(candidate_numero), stack):
                    numero, title = candidate_numero, candidate_title
            elif _BARE_NUMERO_RE.match(line):
                if _valid_next(_numero_parts(line), stack) and i + 1 < n:
                    numero, title = line, lines[i + 1][0]
                    consumed = 2

            if numero is not None and "." not in numero and title.strip() not in _KNOWN_TOP_LEVEL_TITLES:
                rejected_top_level.append((numero, title))
                numero, title, consumed = None, None, 1

        if numero is not None:
            finalize()
            parts = _numero_parts(numero)
            depth = len(parts)
            if depth == len(stack) + 1:
                stack.append(numero)
            else:
                stack = stack[: depth - 1] + [numero]
            current = {"numero": numero, "titre": title, "_content": []}
        elif current is not None:
            current["_content"].append(line)

        i += consumed

    finalize()
    return raw_nodes, rejected_top_level


def _enrich(raw_nodes: list[dict]) -> list[dict]:
    titre_by_numero = {n["numero"]: n["titre"] for n in raw_nodes}
    nodes: list[dict] = []
    for n in raw_nodes:
        parts = n["numero"].split(".")
        niveau = len(parts)
        parent = ".".join(parts[:-1]) if niveau > 1 else None

        top_numero = parts[0]
        top_titre = titre_by_numero.get(top_numero, "")
        if top_titre == "Appendix" and niveau >= 2:
            second_numero = ".".join(parts[:2])
            partie = titre_by_numero.get(second_numero, top_titre)
        else:
            partie = top_titre

        contenu = n["contenu"]
        a_du_code = partie == "Formulas" and bool(contenu) and not contenu.startswith("Description:")

        nodes.append(
            {
                "numero": n["numero"],
                "niveau": niveau,
                "parent": parent,
                "titre": n["titre"],
                "partie": partie,
                "contenu": contenu,
                "contenu_len": len(contenu),
                "a_du_code": a_du_code,
            }
        )
    return nodes


def extract(pdf_path: Path) -> tuple[list[dict], list[tuple[str, str]]]:
    doc = pymupdf.open(str(pdf_path))
    lines = _body_lines(doc)
    raw_nodes, rejected_top_level = _parse_nodes(lines)
    return _enrich(raw_nodes), rejected_top_level


def main():
    if len(sys.argv) != 3:
        print(f"Usage: python {sys.argv[0]} <input.pdf> <ClientName>", file=sys.stderr)
        sys.exit(1)

    pdf_path = Path(sys.argv[1])
    client_name = sys.argv[2]
    if not pdf_path.exists():
        print(f"File not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    nodes, rejected_top_level = extract(pdf_path)
    if rejected_top_level:
        distinct = sorted(set(t for _, t in rejected_top_level))
        print(
            f"Note: rejected {len(rejected_top_level)} depth-1 numero candidate(s) whose "
            "title wasn't a known top-level chapter (almost certainly stray table data, "
            "not real sections — but worth a glance):",
            file=sys.stderr,
        )
        for t in distinct[:15]:
            print(f"    {t!r}", file=sys.stderr)
        if len(distinct) > 15:
            print(f"    ... and {len(distinct) - 15} more distinct titles", file=sys.stderr)
    if not nodes:
        print("No nodes extracted — check the PDF format matches expectations.", file=sys.stderr)
        sys.exit(1)

    low_nodes = nodes
    high_nodes = [
        {k: v for k, v in n.items() if k not in ("contenu", "contenu_len", "a_du_code")}
        for n in nodes
    ]

    docs_dir = Path(__file__).resolve().parent.parent / "docs"
    docs_dir.mkdir(exist_ok=True)
    high_path = docs_dir / f"Hierarchy_{client_name}_High.json"
    low_path = docs_dir / f"hierarchie_{client_name.lower()}_Low.json"

    high_path.write_text(json.dumps(high_nodes, ensure_ascii=False, indent=2), encoding="utf-8")
    low_path.write_text(json.dumps(low_nodes, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter

    counts = Counter(n["partie"] for n in nodes)
    print(f"Extracted {len(nodes)} nodes from {pdf_path.name}")
    for partie, count in counts.most_common():
        print(f"  {partie}: {count}")
    print(f"Wrote {high_path}")
    print(f"Wrote {low_path}")


if __name__ == "__main__":
    main()
