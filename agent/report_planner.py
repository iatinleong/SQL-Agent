"""報表結構規劃：在生成 SQL 前，透過多輪對話確認報表呈現方式。"""

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
    pivot: bool = False
    pivot_detail: str = ""
    subtotal: bool = False
    subtotal_detail: str = ""
    tokens: dict = field(default_factory=dict)


_SYSTEM = """\
你是一位熟悉台灣金融業報表的顧問，擅長解讀業務員需求。
根據使用者的需求、歷史案例 SQL 與雙方的對話記錄，判斷這份報表應該長什麼樣子。
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
        lines = []
        for item in qa_history:
            lines.append(f"系統問：{item['q']}\n使用者答：{item['a']}")
        qa_block = "\n\n【雙方對話記錄（已確認的資訊，請以此為依據）】\n" + "\n\n".join(lines)

    prompt = f"""\
【使用者需求】
{requirement}

【相似歷史案例 SQL（了解這類需求通常怎麼寫）】
{sqls_text}{qa_block}

請判斷這份報表的結構。判斷原則：
- 結合需求與歷史案例，若能清楚判斷所有項目 → status="confirm"，直接輸出完整結構。
- 若有真正無法判斷的關鍵資訊 → status="ask"，提一個最重要的問題（業務員聽得懂的話）。
- 顯而易見的事情（例如排名報表就是要排名）不需要問。盡量 confirm，只有真的不確定才 ask。

輸出 JSON（不要其他文字）：
{{
  "status": "ask 或 confirm",
  "question": "若 status=ask：一個最關鍵的問題，用業務員聽得懂的話問；否則空字串",
  "granularity": "帳戶|客戶|營業員|分公司|其他",
  "granularity_detail": "每一列代表什麼，用業務員聽得懂的話說明，50字以內",
  "pivot": true 或 false,
  "pivot_detail": "若需要把期間轉成欄位，說明轉哪個維度（例如：把月份轉成欄位，1月～12月各一欄）；否則空字串",
  "subtotal": true 或 false,
  "subtotal_detail": "若需要小計合計列，說明在哪個維度加；否則空字串"
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
        pivot=bool(d.get("pivot", False)),
        pivot_detail=d.get("pivot_detail", ""),
        subtotal=bool(d.get("subtotal", False)),
        subtotal_detail=d.get("subtotal_detail", ""),
        tokens=tokens,
    )


def fmt_plan_for_user(plan: ReportPlan) -> str:
    """轉成業務員看得懂的確認文字。"""
    pivot_text = (
        f"需要 — {plan.pivot_detail}" if plan.pivot
        else "不需要（每筆資料各佔一列）"
    )
    subtotal_text = (
        f"需要 — {plan.subtotal_detail}" if plan.subtotal
        else "不需要"
    )
    return (
        f"**每一列代表什麼**  \n{plan.granularity_detail}\n\n"
        f"**是否把月份 / 期間轉成欄位**  \n{pivot_text}\n\n"
        f"**是否需要小計 / 合計列**  \n{subtotal_text}"
    )


def fmt_plan_for_prompt(plan: ReportPlan) -> str:
    """轉成注入 Step A prompt 的說明文字。"""
    pivot_line = f"需要，{plan.pivot_detail}" if plan.pivot else "不需要"
    subtotal_line = f"需要，{plan.subtotal_detail}" if plan.subtotal else "不需要"
    return (
        "【報表呈現結構（使用者已確認，請嚴格遵守）】\n"
        f"  每一列粒度：{plan.granularity}（{plan.granularity_detail}）\n"
        f"  月份/期間轉欄位（PIVOT）：{pivot_line}\n"
        f"  小計/合計列：{subtotal_line}"
    )
