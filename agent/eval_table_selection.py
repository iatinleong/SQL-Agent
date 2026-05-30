"""評測：LLM 根據需求文字從 table_summaries/ 選出正確表格的準確度。

Ground truth：每個 case 的 SQL 中實際用到的表格（限 table_summaries/ 有收錄者）。
LLM 預測：將所有 table summary 與需求文字一起送給 LLM，要求回傳 JSON 表格清單。
指標：Precision / Recall / F1 per case，以及整體平均。
"""

from __future__ import annotations

import json
import re

from .config import ALL_CASES_PATH, get_model_pricing, openai_client

TABLE_SELECTION_MODEL = "gpt-5.4"  # eval-only，不走 config
from .experiment_logger import log_experiment
from .reader import normalize_requirement
from .schema_summarizer import load_raw_schema_as_text, load_table_summaries

SEP = "─" * 65
WIDE_SEP = "═" * 65

# ── System prompt ─────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
你是一位熟悉金融資料庫的 SQL 分析師。

我將提供你一份報表需求以及資料庫中所有可用表格的業務說明。
請根據需求，仔細思考與判斷撰寫這份報表的 Oracle SQL 時必須用到哪些表格。

回傳格式：只回傳一個 JSON 陣列，包含表格名稱字串，不需要任何解釋文字。
範例：["M_AC_ACCOUNT", "M_AT_STOCK_TXN", "M_PT_SALES"]
"""


def _build_user_prompt(req_text: str, table_summaries: dict[str, str]) -> str:
    table_block = "\n\n".join(
        f"【{name}】\n{summary}"
        for name, summary in sorted(table_summaries.items())
    )
    return f"""\
【可用表格說明】
{table_block}

【報表需求】
{req_text}


請選出這份需求所需的表格，回傳 JSON 陣列。"""


# ── Ground truth 抽取 ─────────────────────────────────────────────

def _extract_truth_tables(case: dict, available: set[str]) -> set[str]:
    """從 SQL 抽出實際用到且在 available 中的表格名稱。

    處理兩種格式：
      - 純表名：M_AC_ACCOUNT
      - Schema 前綴：dm_s_view.M_AC_ACCOUNT  → 取最後一段比對
      - 自訂 schema：S_MELODYJJJIAN.CUSTOMER_GROUP_2026 → 整段比對
    """
    found: set[str] = set()
    for sql_part in (case.get("SQL") or []):
        sql_text = sql_part.get("內容", "")
        for m in re.finditer(
            r"\b([A-Z_][A-Z0-9_]*(?:\.[A-Z_][A-Z0-9_]*)*)\b",
            sql_text, re.IGNORECASE
        ):
            full = m.group(1).upper()
            if full in available:
                found.add(full)
            else:
                # 取最後一段（去掉 schema 前綴如 dm_s_view.）
                tail = full.rsplit(".", 1)[-1]
                if tail in available:
                    found.add(tail)
    return found


# ── LLM table selection ───────────────────────────────────────────

def select_tables(
    req_text: str, table_summaries: dict[str, str]
) -> tuple[list[str], int, int]:
    """回傳 (選出的表格清單, input_tokens, output_tokens)。"""
    response = openai_client.chat.completions.create(
        model=TABLE_SELECTION_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(req_text, table_summaries)},
        ],
        max_completion_tokens=8000,
    )
    in_tok  = response.usage.prompt_tokens     if response.usage else 0
    out_tok = response.usage.completion_tokens if response.usage else 0

    raw = response.choices[0].message.content.strip()
    m = re.search(r"\[.*?\]", raw, re.DOTALL)
    if not m:
        return [], in_tok, out_tok
    try:
        tables = [t.strip().upper() for t in json.loads(m.group(0))]
        return tables, in_tok, out_tok
    except json.JSONDecodeError:
        return [], in_tok, out_tok


# ── 評測主程式 ────────────────────────────────────────────────────

def run_eval_table_selection(use_raw_schema: bool = False) -> tuple[list[dict], dict]:
    with open(ALL_CASES_PATH, encoding="utf-8") as f:
        all_cases = json.load(f)

    summaries = load_table_summaries()
    if use_raw_schema:
        # 只取有 summary 的表（30 張），自訂貼標表不在 schema.csv 故自動保留 summary
        raw = load_raw_schema_as_text(table_names=list(summaries.keys()))
        table_summaries = {**summaries, **raw}  # raw 覆蓋同名的 summary
    else:
        table_summaries = summaries
    available = set(summaries.keys())  # ground truth 仍以 summaries 目錄為準

    total = len(all_cases)
    results: list[dict] = []
    total_in_tok = 0
    total_out_tok = 0

    price_in, price_out = get_model_pricing(TABLE_SELECTION_MODEL)
    mode_label = "raw schema" if use_raw_schema else "LLM summaries"
    print(f"共 {total} 筆，表格庫 {len(available)} 張，模型：{TABLE_SELECTION_MODEL}，模式：{mode_label}")
    print(f"費率：input ${price_in}/M  output ${price_out}/M\n")

    for case in all_cases:
        case_id = str(case.get("資料夾", ""))
        req = case.get("需求", {})
        req_text = normalize_requirement(req)
        req_summary = (req.get("需求摘要", "") if isinstance(req, dict) else "")[:50]
        scene = (case.get("業務場景") or {}).get("業務場景", "")

        truth = _extract_truth_tables(case, available)
        tables, in_tok, out_tok = select_tables(req_text, table_summaries)
        total_in_tok  += in_tok
        total_out_tok += out_tok
        predicted = set(tables)

        # 限定 predicted 在 available 範圍內（排除幻覺）
        predicted = predicted & available

        tp = truth & predicted
        precision = len(tp) / len(predicted) if predicted else 0.0
        recall = len(tp) / len(truth) if truth else 1.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
        exact = predicted == truth

        print(SEP)
        print(f"  案例 [{case_id}]  場景：{scene}")
        print(f"  需求：{req_summary}")
        print(f"  Ground truth ({len(truth)})：{', '.join(sorted(truth)) or '（無）'}")
        print(f"  LLM 選出 ({len(predicted)})：{', '.join(sorted(predicted)) or '（無）'}")
        missed = truth - predicted
        extra  = predicted - truth
        if missed:
            print(f"  漏選：{', '.join(sorted(missed))}")
        if extra:
            print(f"  多選：{', '.join(sorted(extra))}")
        print(f"  P={precision:.2f}  R={recall:.2f}  F1={f1:.2f}  {'✓ exact' if exact else ''}")
        case_cost = in_tok / 1_000_000 * price_in + out_tok / 1_000_000 * price_out
        print(f"  tokens: in={in_tok}  out={out_tok}  cost=${case_cost:.5f}")

        results.append({
            "case_id": case_id,
            "scene": scene,
            "req_summary": req_summary,
            "truth": sorted(truth),
            "predicted": sorted(predicted),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "exact_match": exact,
            "in_tokens": in_tok,
            "out_tokens": out_tok,
        })

    # ── 摘要 ─────────────────────────────────────────────────────
    valid = [r for r in results if r["truth"]]  # 排除 truth 為空的 case
    avg_p  = sum(r["precision"] for r in valid) / len(valid) if valid else 0
    avg_r  = sum(r["recall"]    for r in valid) / len(valid) if valid else 0
    avg_f1 = sum(r["f1"]        for r in valid) / len(valid) if valid else 0
    exact_n = sum(1 for r in valid if r["exact_match"])

    cost_in  = total_in_tok  / 1_000_000 * price_in
    cost_out = total_out_tok / 1_000_000 * price_out

    print(f"\n{WIDE_SEP}")
    print(f"  有效案例（truth 非空）：{len(valid)}/{total}")
    print(f"  Avg Precision : {avg_p:.3f}")
    print(f"  Avg Recall    : {avg_r:.3f}")
    print(f"  Avg F1        : {avg_f1:.3f}")
    print(f"  Exact match   : {exact_n}/{len(valid)}  ({exact_n/len(valid)*100:.1f}%)")
    print(f"  Input tokens  : {total_in_tok:,}  (${cost_in:.4f})")
    print(f"  Output tokens : {total_out_tok:,}  (${cost_out:.4f})")
    print(f"  總花費        : ${cost_in + cost_out:.4f}")
    print(WIDE_SEP)

    token_stats = {
        "model": TABLE_SELECTION_MODEL,
        "price_input_per_M": price_in,
        "price_output_per_M": price_out,
        "input_tokens": total_in_tok,
        "output_tokens": total_out_tok,
        "cost_input_usd": round(cost_in, 6),
        "cost_output_usd": round(cost_out, 6),
        "cost_total_usd": round(cost_in + cost_out, 6),
    }
    return results, token_stats


def main(use_raw_schema: bool = False) -> None:
    name = "eval_table_selection_raw" if use_raw_schema else "eval_table_selection"
    with log_experiment(name) as log:
        results, token_stats = run_eval_table_selection(use_raw_schema=use_raw_schema)
        log["results"] = results
        log["token_stats"] = token_stats


if __name__ == "__main__":
    main()
