"""需求讀取與正規化：統一處理 dict 或自由文字輸入。"""

from __future__ import annotations

from typing import Union


def normalize_requirement(requirement: Union[str, dict]) -> str:
    """
    將報表需求正規化為純文字。

    dict 格式時排除 `其他備註` 欄位，避免附件說明等雜訊干擾分類。
    """
    if isinstance(requirement, str):
        return requirement.strip()

    parts = []
    if summary := requirement.get("需求摘要"):
        parts.append(f"需求摘要：{summary}")
    if fields := requirement.get("欄位"):
        if isinstance(fields, list):
            parts.append(f"欄位：{', '.join(str(f) for f in fields)}")
        else:
            parts.append(f"欄位：{fields}")
    if filters := requirement.get("篩選條件"):
        if isinstance(filters, list):
            parts.append(f"篩選條件：{', '.join(str(f) for f in filters)}")
        else:
            parts.append(f"篩選條件：{filters}")
    # 刻意排除 其他備註

    return "\n".join(parts)
