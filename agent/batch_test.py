"""批次評測：Phase 1 場景分類 + Phase 2 向量檢索。

命中定義：用 test case 自己的需求文字去檢索，看自身能否出現在 Top-5（自身排名）。
"""

from __future__ import annotations

import json

from .classifier import classify_intent
from .config import ALL_CASES_PATH
from .experiment_logger import log_experiment
from .pool_filter import resolve_secondary_scene
from .reader import normalize_requirement
from .retriever import retrieve

SEP = "─" * 65
WIDE_SEP = "═" * 65

TEST_CASES: list[tuple[str, str]] = [
    ("113", "精準行銷與專案名單篩選"),
    ("116", "交易動能趨勢與異動偵測"),
    ("119", "財管商品業績與例行月報"),
    ("127", "人員異動與客戶移轉管理"),
    ("145", "庫存與損益明細報表"),
    ("135", "財管商品業績與例行月報"),
    ("146", "庫存與損益明細報表"),
    ("149", "人員異動與客戶移轉管理"),
    ("159", "靜止戶與未實動名單"),
    ("192", "市佔率與交易排名分析"),
]

_cases_cache: list[dict] | None = None


def _all_cases() -> list[dict]:
    global _cases_cache
    if _cases_cache is None:
        with open(ALL_CASES_PATH, encoding="utf-8") as f:
            _cases_cache = json.load(f)
    return _cases_cache


def _find_case(folder_id: str) -> dict | None:
    return next((c for c in _all_cases() if str(c.get("資料夾")) == folder_id), None)


def _req_summary(case: dict) -> str:
    req = case.get("需求", {})
    return req.get("需求摘要", "") if isinstance(req, dict) else ""


def run_batch_test() -> list[dict]:
    all_cases = _all_cases()
    results: list[dict] = []

    for folder_id, expected_scene in TEST_CASES:
        print(SEP)
        case = _find_case(folder_id)
        if case is None:
            print(f"  [案例 {folder_id}] 找不到，跳過")
            continue

        req_text = normalize_requirement(case.get("需求", {}))
        print(f"  案例 [{folder_id}]  期望場景：{expected_scene}")
        print(f"  需求：{_req_summary(case)[:60]}")

        # ── Phase 1 ──────────────────────────────────────────────────────
        classification = classify_intent(case.get("需求", req_text))
        predicted = classification.主要場景
        p1_hit = predicted == expected_scene
        secondary = resolve_secondary_scene(classification)
        print(f"  P1：{'✓' if p1_hit else '✗'}  {predicted}（次要：{secondary or '無'}）")

        # ── Phase 2：全庫向量檢索 Top-5 ──────────────────────────────────
        hits = retrieve(req_text, all_cases, top_k=5)

        self_rank: int | None = None
        print("  Top-5：")
        for hit in hits:
            hit_case = _find_case(hit.case_id)
            summary = _req_summary(hit_case)[:45] if hit_case else ""
            is_self = hit.case_id == folder_id
            marker = "  ← ★ 自身" if is_self else ""
            print(f"    #{hit.rank}  [{hit.case_id}]  {hit.score:.4f}  {summary}{marker}")
            if is_self:
                self_rank = hit.rank

        p3_hit = self_rank is not None
        print(f"  P3：{'✓ 排名 #' + str(self_rank) if p3_hit else '✗ 未命中 Top-5'}")

        results.append({
            "folder_id": folder_id,
            "expected_scene": expected_scene,
            "predicted_scene": predicted,
            "p1_hit": p1_hit,
            "p3_hit": p3_hit,
            "self_rank": self_rank,
        })

    # ── 摘要 ─────────────────────────────────────────────────────────────
    print(f"\n{WIDE_SEP}")
    p1_correct = sum(1 for r in results if r["p1_hit"])
    p3_correct = sum(1 for r in results if r["p3_hit"])
    ranks = [r["self_rank"] for r in results if r["self_rank"] is not None]
    avg_rank = sum(ranks) / len(ranks) if ranks else None

    print(f"  P1 準確率：{p1_correct}/{len(results)}")
    print(f"  P3 命中率（Top-5）：{p3_correct}/{len(results)}")
    if avg_rank is not None:
        print(f"  P3 平均命中排名：{avg_rank:.1f}")

    print(f"\n  {'案例':>5}  {'期望場景':<16}  P1  P3")
    print(f"  {'─'*5}  {'─'*16}  ──  ──")
    for r in results:
        p3 = f"#{r['self_rank']}" if r["p3_hit"] else "✗"
        print(f"  {r['folder_id']:>5}  {r['expected_scene']:<16}  {'✓' if r['p1_hit'] else '✗'}   {p3}")
    print(WIDE_SEP)

    return results


def main() -> None:
    with log_experiment("batch_test") as log:
        results = run_batch_test()
        log["results"] = results


if __name__ == "__main__":
    main()
