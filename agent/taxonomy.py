"""Taxonomy 工具函式：載入 taxonomy.json 並提供 prompt 建構輔助。"""

from __future__ import annotations

import json

from .config import TAXONOMY_PATH


def load_taxonomy() -> list[dict]:
    with open(TAXONOMY_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["業務場景分類"]


def get_category_names(taxonomy: list[dict]) -> list[str]:
    return [item["類別名稱"] for item in taxonomy]


def build_taxonomy_section(taxonomy: list[dict]) -> str:
    lines = []
    for item in taxonomy:
        lines.append(f"【{item['類別名稱']}】")
        lines.append(f"說明：{item['類別說明']}")
        lines.append(f"典型特徵：{item['典型特徵']}")
        lines.append("")
    return "\n".join(lines)
