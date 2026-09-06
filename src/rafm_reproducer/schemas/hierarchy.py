import json
import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel

# The node holding the model's object → class tree, used as the orientation map.
MODEL_TREE_NUMERO = "6.1"
_MODEL_TREE_HEADERS = {"Model Object", "Model Class", "Base Model Class"}
_MODEL_TREE_BUDGET = 2500


class HierarchyNode(BaseModel):
    numero: str
    niveau: int
    parent: str | None
    titre: str
    partie: str
    # present only in the Low JSON
    contenu: str | None = None
    contenu_len: int | None = None
    a_du_code: bool | None = None


def load_hierarchy(path: Path) -> list[HierarchyNode]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [HierarchyNode.model_validate(n) for n in raw]


def build_index(nodes: list[HierarchyNode]) -> dict[str, HierarchyNode]:
    """numero → node, O(1) lookup for validators."""
    return {n.numero: n for n in nodes}


def _collapse_runs(pairs: list[tuple[str, str]]) -> list[str]:
    """
    Fold numbered siblings sharing one class into a range, so that
    ret_age1..ret_age30 -> reserve_res costs one line instead of thirty.
    """
    out: list[str] = []
    i = 0
    while i < len(pairs):
        obj, cls = pairs[i]
        m = re.fullmatch(r"(.*?)(\d+)", obj)
        j = i
        if m:
            stem, start = m.group(1), int(m.group(2))
            expected = start
            while j + 1 < len(pairs):
                nxt = re.fullmatch(r"(.*?)(\d+)", pairs[j + 1][0])
                if not nxt or nxt.group(1) != stem or pairs[j + 1][1] != cls:
                    break
                if int(nxt.group(2)) != expected + 1:
                    break
                expected += 1
                j += 1
            if j - i >= 2:  # a run of 3+ is worth folding
                out.append(f"{stem}{{{start}..{expected}}} -> {cls}")
                i = j + 1
                continue
        out.append(f"{obj} -> {cls}")
        i += 1
    return out


def load_model_tree(low_path: Path) -> str | None:
    """
    Pull the model's object → class tree out of the Low JSON and de-duplicate it.

    The PDF renders it as a table, so the column headers reappear on every page
    and a class name repeats for each object using it. Left as-is it would
    reintroduce exactly the repetition this representation exists to remove.
    """
    raw = json.loads(low_path.read_text(encoding="utf-8"))
    node = next((n for n in raw if n.get("numero") == MODEL_TREE_NUMERO), None)
    if not node or not (node.get("contenu") or "").strip():
        return None

    lines = [
        ln.strip()
        for ln in node["contenu"].split("\n")
        if ln.strip() and ln.strip() not in _MODEL_TREE_HEADERS
    ]
    # Lines come in (object, class) pairs once the headers are gone.
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for obj, cls in zip(lines[::2], lines[1::2]):
        if (obj, cls) not in seen:
            seen.add((obj, cls))
            pairs.append((obj, cls))

    out = "\n".join(_collapse_runs(pairs))
    if len(out) > _MODEL_TREE_BUDGET:
        kept = out[:_MODEL_TREE_BUDGET].rsplit("\n", 1)[0]
        out = f"{kept}\n... ({len(pairs)} model objects in total)"
    return out


def to_compact_text(
    nodes: list[HierarchyNode],
    model_tree: str | None = None,
) -> str:
    """
    The complete title tree of the audit report, for the Stage 2 prompt.

    EVERY title the PDF contains is listed, at every depth — Stage 2 can only
    select from what it is shown, so nothing is filtered, sampled or summarised
    here. One line per node: indentation gives the depth at a glance, and the
    dotted numero is written in full so the model cites it verbatim instead of
    rebuilding it from the indentation.

    What is removed is repetition of *form*, never content: the chapter name is
    no longer repeated on all 12k lines (it is recoverable from the numero), and
    the model map that opens the block is de-duplicated.
    """
    out: list[str] = []

    if model_tree:
        out.append("# MODEL MAP (object -> class)")
        out.append(model_tree)
        out.append("")

    out.append("# CHAPTERS")
    for partie, count in Counter(n.partie for n in nodes).most_common():
        out.append(f"- {partie}: {count} titles")

    out.append("")
    out.append(f"# FULL TITLE TREE — every one of the {len(nodes)} titles in the report")
    out.append(
        "One line per title: <indent> <numero> <title>. The dotted numero IS the "
        "hierarchy — 8.2.1.1.1.4 is a child of 8.2.1.1.1, itself a child of "
        "8.2.1.1, and so on up to chapter 8. Indentation repeats that depth "
        "visually. Cite a numero exactly as written."
    )
    for n in nodes:
        out.append(f"{' ' * (n.niveau - 1)}{n.numero} {n.titre}")

    return "\n".join(out)
