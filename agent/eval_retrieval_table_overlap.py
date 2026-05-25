"""評測：Top-5 檢索案例（排除自身）的 table 聯集，與查詢案例 ground truth table 的重疊率。

目的：驗證 BGE-M3 檢索到的相似案例，是否能為 LLM 提供足夠的 table 線索。

資料來源：讀取已存在的 eval_retrieval JSON（不重跑向量檢索）。
邏輯：
  - 自身在 Top-5 中：排除自身，取剩餘 4 筆（等效「取 2~6 但只有到 5」）
  - 自身不在 Top-5 中：直接取全部 5 筆

指標：
  Union Recall = |truth ∩ union(所選案例 tables)| / |truth|
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ALL_CASES_PATH, BASE_DIR
from .eval_table_selection import _extract_truth_tables
from .experiment_logger import log_experiment
from .schema_summarizer import load_table_summaries

SEP = "─" * 65
WIDE_SEP = "═" * 65

EXPERIMENT_DIR: Path = BASE_DIR / "experiment"


def _load_latest_eval_retrieval() -> list[dict]:
    """找 experiment/ 下最新的 eval_retrieval_*.json（非 overlap），回傳 results list。"""
    candidates = sorted(
        [p for p in EXPERIMENT_DIR.glob("*_eval_retrieval.json")],
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            "找不到 eval_retrieval.json，請先執行：python -m agent --eval-retrieval"
        )
    path = candidates[0]
    print(f"  [載入] {path.name}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["results"]


def run_eval_retrieval_table_overlap(
    retrieval_json: str | None = None,
) -> list[dict]:
    # ── 載入檢索結果 ───────────────────────────────────────────────
    if retrieval_json:
        with open(retrieval_json, encoding="utf-8") as f:
            retrieval_results = json.load(f)["results"]
        print(f"  [載入] {retrieval_json}")
    else:
        retrieval_results = _load_latest_eval_retrieval()

    # ── 載入 all_cases ─────────────────────────────────────────────
    with open(ALL_CASES_PATH, encoding="utf-8") as f:
        all_cases = json.load(f)

    available = set(load_table_summaries().keys())
    case_map = {str(c.get("資料夾")): c for c in all_cases}

    # 建立 retrieval index：case_id → {self_rank, top5}
    retrieval_index = {r["case_id"]: r for r in retrieval_results}

    total = len(retrieval_results)
    results: list[dict] = []

    print(f"共 {total} 筆，表格庫 {len(available)} 張")
    print("邏輯：自身在 Top-5 → 排除自身取剩餘 4 筆；自身不在 Top-5 → 全取 5 筆\n")

    for r in retrieval_results:
        case_id = r["case_id"]
        scene = r.get("scene", "")
        req_summary = r.get("req_summary", "")[:50]
        self_rank = r.get("self_rank")          # None = 自身不在 top-5
        top5: list[dict] = r.get("top5", [])   # [{"case_id": ..., "score": ...}]

        case = case_map.get(case_id, {})
        truth = _extract_truth_tables(case, available)

        # ── 選取「其他案例」 ───────────────────────────────────────
        if self_rank is not None:
            # 自身在 Top-5：排除自身，取剩餘（最多 4 筆）
            others = [t for t in top5 if t["case_id"] != case_id]
            mode = f"自身排 #{self_rank}，排除後取 {len(others)} 筆"
        else:
            # 自身不在 Top-5：直接取全部 5 筆
            others = top5
            mode = f"自身未入 Top-5，取全部 {len(others)} 筆"

        # ── 計算 tables 聯集 ───────────────────────────────────────
        retrieved_union: set[str] = set()
        hit_table_info: list[dict] = []
        for t in others:
            oc = case_map.get(t["case_id"], {})
            tables = _extract_truth_tables(oc, available)
            retrieved_union |= tables
            hit_table_info.append({
                "case_id": t["case_id"],
                "score": t["score"],
                "tables": sorted(tables),
            })

        if truth:
            covered = truth & retrieved_union
            missed = truth - retrieved_union
            union_recall = len(covered) / len(truth)
        else:
            covered = set()
            missed = set()
            union_recall = None

        # ── 輸出 ───────────────────────────────────────────────────
        print(SEP)
        print(f"  案例 [{case_id}]  場景：{scene}")
        print(f"  需求：{req_summary}")
        print(f"  Ground truth ({len(truth)})：{', '.join(sorted(truth)) or '（無）'}")
        print(f"  {mode}")
        for info in hit_table_info:
            print(f"    [{info['case_id']}] {info['score']:.4f}  {', '.join(info['tables']) or '（無）'}")
        print(f"  聯集 ({len(retrieved_union)})：{', '.join(sorted(retrieved_union)) or '（無）'}")
        if truth:
            print(f"  覆蓋 ({len(covered)})：{', '.join(sorted(covered)) or '（無）'}")
            if missed:
                print(f"  未覆蓋：{', '.join(sorted(missed))}")
            print(f"  Union Recall：{union_recall:.2f}  ({len(covered)}/{len(truth)}){'  ✓ 完整' if union_recall == 1.0 else ''}")

        results.append({
            "case_id": case_id,
            "scene": scene,
            "req_summary": req_summary,
            "self_rank": self_rank,
            "others_count": len(others),
            "truth": sorted(truth),
            "retrieved_union": sorted(retrieved_union),
            "covered": sorted(covered),
            "missed": sorted(missed),
            "union_recall": round(union_recall, 4) if union_recall is not None else None,
            "other_hits": hit_table_info,
        })

    # ── 摘要 ─────────────────────────────────────────────────────────────
    valid = [r for r in results if r["truth"]]
    recalls = [r["union_recall"] for r in valid if r["union_recall"] is not None]
    avg_recall = sum(recalls) / len(recalls) if recalls else 0
    full_count = sum(1 for r in recalls if r == 1.0)

    buckets = {"1.00": 0, "0.75+": 0, "0.50+": 0, "<0.50": 0}
    for rv in recalls:
        if rv == 1.0:
            buckets["1.00"] += 1
        elif rv >= 0.75:
            buckets["0.75+"] += 1
        elif rv >= 0.50:
            buckets["0.50+"] += 1
        else:
            buckets["<0.50"] += 1

    print(f"\n{WIDE_SEP}")
    print(f"  有效案例（truth 非空）：{len(valid)}/{total}")
    print(f"  平均 Union Recall  ：{avg_recall:.3f}")
    print(f"  完整覆蓋（Recall=1）：{full_count}/{len(valid)}  ({full_count/len(valid)*100:.1f}%)")
    print(f"  Recall 分布：")
    print(f"    = 1.00  {buckets['1.00']:3d} 筆  ({buckets['1.00']/len(valid)*100:.1f}%)")
    print(f"    ≥ 0.75  {buckets['0.75+']:3d} 筆  ({buckets['0.75+']/len(valid)*100:.1f}%)")
    print(f"    ≥ 0.50  {buckets['0.50+']:3d} 筆  ({buckets['0.50+']/len(valid)*100:.1f}%)")
    print(f"    < 0.50  {buckets['<0.50']:3d} 筆  ({buckets['<0.50']/len(valid)*100:.1f}%)")

    worst = sorted(valid, key=lambda r: (r["union_recall"] or 0))[:5]
    print(f"\n  Union Recall 最低 5 筆：")
    for r in worst:
        print(f"    [{r['case_id']:>5}]  recall={r['union_recall']:.2f}  未覆蓋：{', '.join(r['missed'])}")
    print(WIDE_SEP)

    return results


def main(retrieval_json: str | None = None) -> None:
    with log_experiment("eval_retrieval_table_overlap") as log:
        results = run_eval_retrieval_table_overlap(retrieval_json=retrieval_json)
        log["results"] = results


if __name__ == "__main__":
    main()
