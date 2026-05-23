"""Phase 1 後段：套用 0.4 gap 規則，從 all_cases.json 建立候選池。"""

from __future__ import annotations

import json
from typing import Optional

from .config import ALL_CASES_PATH
from .models import ClassificationResult


def resolve_secondary_scene(classification: ClassificationResult) -> Optional[str]:
    """
    若主要場景與次要場景信心分數差 >= 0.4，捨棄次要場景。
    避免主要場景明顯領先時，次要場景引入不相關案例稀釋候選池。
    """
    if classification.次要場景 is None:
        return None

    scores = {item.標籤: item.分數 for item in classification.各標籤置信度}
    primary_score = scores.get(classification.主要場景, 0.0)
    secondary_score = scores.get(classification.次要場景, 0.0)

    gap = primary_score - secondary_score
    return None if gap >= 0.4 else classification.次要場景


def build_candidate_pool(
    classification: ClassificationResult,
    exclude_id: Optional[str] = None,
) -> list[dict]:
    """
    從 all_cases.json 撈出符合場景的案例，自動排除當前評測案例（公正性遮蔽）。

    Returns:
        去重後的候選案例列表。
    """
    with open(ALL_CASES_PATH, encoding="utf-8") as f:
        all_cases = json.load(f)

    effective_secondary = resolve_secondary_scene(classification)
    target_scenes: set[str] = {classification.主要場景}
    if effective_secondary:
        target_scenes.add(effective_secondary)

    pool: list[dict] = []
    seen_ids: set[str] = set()

    for case in all_cases:
        case_id = str(case.get("資料夾", ""))
        if exclude_id is not None and case_id == str(exclude_id):
            continue
        scene_field = case.get("業務場景", {})
        scene_name = (
            scene_field.get("業務場景", "")
            if isinstance(scene_field, dict)
            else ""
        )
        if scene_name in target_scenes and case_id not in seen_ids:
            pool.append(case)
            seen_ids.add(case_id)

    return pool
