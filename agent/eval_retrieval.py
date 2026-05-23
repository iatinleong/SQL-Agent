"""全庫檢索準確度評測（無 LLM 花費）。

對全部 100 筆案例，各用自己的需求文字查詢，看自身排第幾。
命中 = 自身出現在 Top-5。
"""

from __future__ import annotations

import json

from .config import ALL_CASES_PATH
from .experiment_logger import log_experiment
from .reader import normalize_requirement
from .retriever import retrieve

SEP = "─" * 65
WIDE_SEP = "═" * 65


def run_eval_retrieval(top_k: int = 5) -> list[dict]:
    with open(ALL_CASES_PATH, encoding="utf-8") as f:
        all_cases = json.load(f)

    case_map = {str(c.get("資料夾")): c for c in all_cases}
    total = len(all_cases)
    results: list[dict] = []

    print(f"共 {total} 筆，開始全庫檢索評測（Top-{top_k}，無 LLM）\n")

    for case in all_cases:
        case_id = str(case.get("資料夾", ""))
        req = case.get("需求", {}) if isinstance(case.get("需求"), dict) else {}
        req_summary = req.get("需求摘要", "")
        scene = (case.get("業務場景") or {}).get("業務場景", "")
        req_text = normalize_requirement(case.get("需求", {}))

        hits = retrieve(req_text, all_cases, top_k=top_k)
        self_rank = next((h.rank for h in hits if h.case_id == case_id), None)

        print(SEP)
        print(f"  案例 [{case_id}]  場景：{scene}")
        print(f"  需求：{req_summary[:60]}")
        print(f"  Top-{top_k}：")
        for hit in hits:
            hc = case_map.get(hit.case_id, {})
            h_summary = (hc.get("需求") or {}).get("需求摘要", "")[:45]
            marker = "  ← ★ 自身" if hit.case_id == case_id else ""
            print(f"    #{hit.rank}  [{hit.case_id}]  {hit.score:.4f}  {h_summary}{marker}")

        hit = self_rank is not None
        print(f"  命中：{'✓ 排名 #' + str(self_rank) if hit else '✗ 未命中 Top-' + str(top_k)}")

        results.append({
            "case_id": case_id,
            "scene": scene,
            "req_summary": req_summary,
            "hit": hit,
            "self_rank": self_rank,
            "top5": [{"case_id": h.case_id, "score": round(h.score, 4)} for h in hits],
        })

    # ── 摘要 ─────────────────────────────────────────────────────────────
    hit_count = sum(1 for r in results if r["hit"])
    ranks = [r["self_rank"] for r in results if r["self_rank"] is not None]
    avg_rank = sum(ranks) / len(ranks) if ranks else None
    rank_dist = {k: ranks.count(k) for k in range(1, top_k + 1)}
    misses = [r for r in results if not r["hit"]]

    print(f"\n{WIDE_SEP}")
    print(f"  命中率（Top-{top_k}）：{hit_count}/{total}  ({hit_count/total*100:.1f}%)")
    if avg_rank is not None:
        print(f"  平均命中排名：{avg_rank:.2f}")
    print(f"  排名分布：" + "  ".join(f"#{k}:{rank_dist[k]}" for k in range(1, top_k + 1)))

    if misses:
        print(f"\n  未命中（{len(misses)} 筆）：")
        for m in misses:
            top1_id = m["top5"][0]["case_id"] if m["top5"] else "?"
            top1_c = case_map.get(top1_id, {})
            top1_summary = (top1_c.get("需求") or {}).get("需求摘要", "")[:35]
            print(f"    [{m['case_id']:>5}]  {m['req_summary'][:40]}  → top1=[{top1_id}] {top1_summary}")
    print(WIDE_SEP)

    return results


def main() -> None:
    with log_experiment("eval_retrieval") as log:
        results = run_eval_retrieval()
        log["results"] = results


if __name__ == "__main__":
    main()
