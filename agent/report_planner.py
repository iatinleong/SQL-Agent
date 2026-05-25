"""報表結構規劃：在生成 SQL 前，透過對話確認報表的每列粒度。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .config import CLASSIFICATION_MODEL
from .generator import _chat


@dataclass
class ReportPlan:
    status: str = "confirm"         # "ask" | "confirm"
    question: str = ""              # status="ask" 時，向使用者提的問題
    granularity: str = "其他"       # 帳戶/客戶/營業員/分公司/其他
    granularity_detail: str = ""    # 每列代表什麼（白話）
    tokens: dict = field(default_factory=dict)


_SYSTEM = """\
你是一位熟悉台灣金融業報表的顧問，擅長解讀業務員需求。
根據使用者的需求、歷史案例 SQL 與雙方的對話記錄，判斷這份報表每一列的粒度。
只輸出 JSON，不要其他文字。"""


def plan_report(
    requirement: str,
    case_sqls: list[str],
    qa_history: list[dict] | None = None,
    model: str = CLASSIFICATION_MODEL,
) -> ReportPlan:
    """
    qa_history：[{"q": "...", "a": "..."}, ...]，代表已確認的問答記錄。
    """
    sqls_text = "\n\n---\n\n".join(case_sqls[:5]) if case_sqls else "（無歷史案例）"

    qa_block = ""
    if qa_history:
        lines = [f"系統問：{item['q']}\n使用者答：{item['a']}" for item in qa_history]
        qa_block = "\n\n【雙方對話記錄（已確認的資訊，請以此為依據）】\n" + "\n\n".join(lines)

    prompt = f"""\
【使用者需求】
{requirement}

【相似歷史案例 SQL（了解這類需求通常怎麼寫）】
{sqls_text}{qa_block}

請判斷這份報表每一列代表什麼粒度。判斷原則：
- 結合需求與歷史案例，若能清楚判斷 → status="confirm"，直接輸出結果。
- 若真的無法判斷 → status="ask"，提一個最關鍵的問題（用業務員聽得懂的話）。
- 顯而易見的事情不需要問。盡量 confirm，只有真的不確定才 ask。

輸出 JSON（不要其他文字）：
{{
  "status": "ask 或 confirm",
  "question": "若 status=ask：一個最關鍵的問題，業務員聽得懂；否則空字串",
  "granularity": "帳戶|客戶|營業員|分公司|其他",
  "granularity_detail": "每一列代表什麼，用業務員聽得懂的話說明，50字以內"
}}"""

    resp = _chat(
        model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    raw = (resp.choices[0].message.content or "").strip()
    for fence in ("```json", "```"):
        if raw.startswith(fence):
            raw = raw[len(fence):]
    raw = raw.strip("`").strip()

    tokens = {
        "plan_in": resp.usage.prompt_tokens,
        "plan_out": resp.usage.completion_tokens,
    }
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        d = {}

    return ReportPlan(
        status=d.get("status", "confirm"),
        question=d.get("question", ""),
        granularity=d.get("granularity", "其他"),
        granularity_detail=d.get("granularity_detail", ""),
        tokens=tokens,
    )


def fmt_plan_for_user(plan: ReportPlan) -> str:
    """轉成業務員看得懂的確認文字。"""
    return f"**每一列代表什麼**  \n{plan.granularity_detail}"


def fmt_plan_for_prompt(plan: ReportPlan) -> str:
    """轉成注入 Step A prompt 的說明文字。"""
    return (
        "【報表呈現結構（使用者已確認，請嚴格遵守）】\n"
        f"  每一列粒度：{plan.granularity}（{plan.granularity_detail}）"
    )
