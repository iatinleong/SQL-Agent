"""追問處理：意圖分類 + SQL 改寫。

意圖類型：
  ADD_TABLE    需要引入目前 SQL 沒有的新表格（加年齡、加配息資料等）
  REMOVE_TABLE 移除某個表格或欄位
  MODIFY_SQL   只修改 SQL 邏輯（WHERE、聚合、排序、時間範圍等），不新增表格
  NEW_QUERY    完全不同的新需求，應重新走完整 Phase1+2+StepA+StepB 流程
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .config import CLASSIFICATION_MODEL, GENERATION_MODEL
from .generator import _chat, _load_schema_for_tables

INTENTS = ("ADD_TABLE", "REMOVE_TABLE", "MODIFY_SQL", "NEW_QUERY")

_CLASSIFY_SYSTEM = """\
你是一個 SQL 需求分析助理。根據目前 SQL 和使用者的追問，判斷追問意圖。
只輸出 JSON，不要任何其他文字。"""

_REFINE_SYSTEM = """\
你是一位 Oracle SQL 專家，熟悉台灣金融業報表邏輯。
根據使用者的修改指令，改寫已有的 SQL，並說明改法與最終設計思路。"""


@dataclass
class RefineResult:
    intent: str
    target_tables: list[str] = field(default_factory=list)
    modification_note: str = ""
    new_reasoning: str = ""
    new_sql: str = ""
    classify_tokens: dict[str, int] = field(default_factory=dict)
    refine_tokens: dict[str, int] = field(default_factory=dict)


def classify_followup(
    current_sql: str,
    new_query: str,
    available_tables: set[str],
    model: str = CLASSIFICATION_MODEL,
) -> dict:
    """判斷追問意圖，回傳 {intent, target_tables, explanation}。"""
    table_sample = ", ".join(sorted(available_tables)[:60])
    prompt = f"""\
目前 SQL（節錄前 1500 字元）：
{current_sql[:1500]}

使用者追問：{new_query}

可用表格（部分列舉）：{table_sample}

請判斷追問意圖，輸出 JSON：
{{
  "intent": "ADD_TABLE|REMOVE_TABLE|MODIFY_SQL|NEW_QUERY",
  "target_tables": [],
  "explanation": "一句話說明"
}}

意圖定義：
- ADD_TABLE：需要引入目前 SQL 沒有的新表格（如加客戶年齡、加配息、加市佔率）
- REMOVE_TABLE：要移除某個表格、欄位或 JOIN
- MODIFY_SQL：只修改 SQL 邏輯（WHERE 條件、GROUP BY、排序、時間範圍、閾值等），不新增表格
- NEW_QUERY：完全不同的新需求，無法在現有 SQL 基礎上修改"""

    resp = _chat(
        model,
        messages=[
            {"role": "system", "content": _CLASSIFY_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    raw = (resp.choices[0].message.content or "").strip().strip("```json").strip("```").strip()
    tokens = {
        "classify_in": resp.usage.prompt_tokens,
        "classify_out": resp.usage.completion_tokens,
    }
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"intent": "MODIFY_SQL", "target_tables": [], "explanation": raw}
    result["_tokens"] = tokens
    return result


def build_conversation_summary(conversation: list) -> str:
    """壓縮對話歷史為文字（避免 context 爆炸）。"""
    lines: list[str] = []
    for i, turn in enumerate(conversation, 1):
        sql_preview = turn.sql[:200] + "..." if len(turn.sql) > 200 else turn.sql
        lines.append(f"[Turn {i}] 需求：{turn.user_query}")
        if turn.modification:
            lines.append(f"         改法（{turn.intent}）：{turn.modification[:120]}")
        lines.append(f"         SQL（節錄）：{sql_preview}")
    return "\n".join(lines)


def refine(
    conversation_summary: str,
    current_sql: str,
    current_reasoning: str,
    new_query: str,
    classification: dict,
    model: str = GENERATION_MODEL,
) -> RefineResult:
    """改寫 SQL，回傳 RefineResult。"""
    intent = classification.get("intent", "MODIFY_SQL")
    target_tables: list[str] = classification.get("target_tables") or []
    classify_tokens = classification.get("_tokens", {})

    extra_schema = ""
    if intent == "ADD_TABLE" and target_tables:
        extra_schema = _load_schema_for_tables(target_tables)

    extra_block = f"\n\n【新增表格 Schema】\n{extra_schema}" if extra_schema else ""

    user_prompt = f"""\
【對話歷史摘要】
{conversation_summary}

【目前 SQL】
{current_sql}

【目前 SQL 思路】
{current_reasoning}

【使用者指令】
{new_query}
{extra_block}

請依以下格式輸出：

--- 改法 ---
（說明做了什麼改動，以及為何這樣改）

--- 最終思路 ---
（說明這份 SQL的完整設計決策，為何能符合使用者需求：選了哪些表格、JOIN 條件、時間篩選、聚合邏輯，使用者的需求的核心目標是什麼、這樣的寫法如何回應它、哪些設計決策是為了滿足哪個需求點）

--- 最終 SQL ---
（改寫後的完整 Oracle SQL）"""

    resp = _chat(
        model,
        messages=[
            {"role": "system", "content": _REFINE_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
    )
    raw = resp.choices[0].message.content or ""
    refine_tokens = {
        "refine_in": resp.usage.prompt_tokens,
        "refine_out": resp.usage.completion_tokens,
    }

    modification_note, new_reasoning, new_sql = "", "", ""
    if "--- 改法 ---" in raw:
        after = raw.split("--- 改法 ---", 1)[1]
        if "--- 最終思路 ---" in after:
            modification_note = after.split("--- 最終思路 ---", 1)[0].strip()
            after2 = after.split("--- 最終思路 ---", 1)[1]
            if "--- 最終 SQL ---" in after2:
                new_reasoning = after2.split("--- 最終 SQL ---", 1)[0].strip()
                new_sql = after2.split("--- 最終 SQL ---", 1)[1].strip()
            else:
                new_sql = after2.strip()
        elif "--- 最終 SQL ---" in after:
            modification_note = after.split("--- 最終 SQL ---", 1)[0].strip()
            new_sql = after.split("--- 最終 SQL ---", 1)[1].strip()
    else:
        new_sql = raw.strip()

    return RefineResult(
        intent=intent,
        target_tables=target_tables,
        modification_note=modification_note,
        new_reasoning=new_reasoning,
        new_sql=new_sql,
        classify_tokens=classify_tokens,
        refine_tokens=refine_tokens,
    )
