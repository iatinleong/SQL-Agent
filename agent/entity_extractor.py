"""實體擷取：從報表需求文字偵測商品、業務概念、分公司，擴充候選池並生成 WHERE 提示。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import BASE_DIR

# ── 資料載入（module-level cache）────────────────────────────────────

def _load_json(filename: str) -> object:
    path = BASE_DIR / filename
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_product_catalog: list[dict] | None = None
_concept_routing: dict | None = None
_branch_mapping: dict[str, str] | None = None


def _get_product_catalog() -> list[dict]:
    global _product_catalog
    if _product_catalog is None:
        _product_catalog = _load_json("product_catalog.json") or []
    return _product_catalog


def _get_concept_routing() -> dict:
    global _concept_routing
    if _concept_routing is None:
        _concept_routing = _load_json("concept_routing.json") or {}
    return _concept_routing


def _get_branch_mapping() -> dict[str, str]:
    """branch_mapping.json: {"竹北分公司": "XYZB", ...}。非必要；不存在時仍輸出 BRANCH_NAME 提示。"""
    global _branch_mapping
    if _branch_mapping is None:
        loaded = _load_json("branch_mapping.json")
        _branch_mapping = loaded if isinstance(loaded, dict) else {}
    return _branch_mapping


# ── 分公司偵測 ─────────────────────────────────────────────────────

_CJK_RE = re.compile(r"[一-鿿]")
_BRANCH_SUFFIXES = ("分公司", "分行", "辦事處", "分部")


def _detect_branches(query: str) -> list[str]:
    """擷取 '竹北分公司' 這類名稱：取後綴前剛好 2 個漢字作為地名。"""
    results: list[str] = []
    seen: set[str] = set()
    for suffix in _BRANCH_SUFFIXES:
        start = 0
        while True:
            idx = query.find(suffix, start)
            if idx < 0:
                break
            if idx >= 2:
                city = query[idx - 2 : idx]
                if all(_CJK_RE.match(c) for c in city):
                    branch = city + suffix
                    if branch not in seen:
                        seen.add(branch)
                        results.append(branch)
            start = idx + 1
    return results


# ── 主函式 ─────────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    detected_products: list[str] = field(default_factory=list)
    detected_concepts: list[str] = field(default_factory=list)
    detected_branches: list[str] = field(default_factory=list)
    extra_tables: list[str] = field(default_factory=list)  # 追加進候選池
    codes: dict[str, str] = field(default_factory=dict)    # WHERE 提示
    enriched_entities: str = ""                             # 注入 Step A 的文字區塊


def extract_entities(query: str) -> ExtractionResult:
    result = ExtractionResult()
    extra_tables: set[str] = set()

    catalog = _get_product_catalog()
    concept_routing = _get_concept_routing()
    branch_mapping = _get_branch_mapping()

    # ── 1. 商品偵測 ──────────────────────────────────────────────
    product_lines: list[str] = []
    for entry in catalog:
        name = entry.get("name", "")
        aliases: list[str] = entry.get("aliases", [])
        codes: dict = entry.get("codes", {})
        tables: list[str] = entry.get("tables", [])

        matched = next((a for a in aliases if a in query), None)
        if matched is None:
            continue

        result.detected_products.append(name)
        extra_tables.update(tables)

        code_hints = ", ".join(f"{k}='{v}'" for k, v in codes.items())
        table_hint = ", ".join(tables)
        product_lines.append(
            f"  商品：{name}（由「{matched}」觸發）→ {code_hints}；可用表格：{table_hint}"
        )
        # 最高優先的 code 注入（若尚未設定同名 key）
        for k, v in codes.items():
            result.codes.setdefault(k, v)

    # ── 2. 業務概念偵測 ──────────────────────────────────────────
    concept_lines: list[str] = []
    for keyword, info in concept_routing.items():
        if keyword.lower() not in query.lower():
            continue
        tables: list[str] = info.get("tables", [])
        desc: str = info.get("desc", keyword)
        result.detected_concepts.append(keyword)
        extra_tables.update(tables)
        concept_lines.append(f"  概念「{keyword}」→ {desc}；可用表格：{', '.join(tables)}")

    # ── 3. 分公司偵測 ────────────────────────────────────────────
    branch_lines: list[str] = []
    for branch_name in _detect_branches(query):
        result.detected_branches.append(branch_name)
        if branch_name in branch_mapping:
            code = branch_mapping[branch_name]
            result.codes["BRANCH_CODE"] = code
            branch_lines.append(f"  分公司：{branch_name} → BRANCH_CODE='{code}'")
        else:
            result.codes.setdefault("BRANCH_NAME", branch_name)
            branch_lines.append(f"  分公司：{branch_name} → BRANCH_NAME='{branch_name}'（可直接用文字比對）")

    # ── 4. 組合 enriched_entities ───────────────────────────────
    sections: list[str] = []
    if product_lines:
        sections.append("【偵測到的商品】\n" + "\n".join(product_lines))
    if concept_lines:
        sections.append("【偵測到的業務概念】\n" + "\n".join(concept_lines))
    if branch_lines:
        sections.append("【偵測到的分公司】\n" + "\n".join(branch_lines))
    if result.codes:
        code_strs = [f"{k}='{v}'" for k, v in result.codes.items()]
        sections.append("【建議 WHERE 條件提示】\n  " + "，".join(code_strs))

    result.enriched_entities = "\n\n".join(sections)
    result.extra_tables = sorted(extra_tables)
    return result
