"""使用者個人化偏好簡介：載入、更新（Supabase user_profiles 表）。"""

from __future__ import annotations

from .config import CLASSIFICATION_MODEL
from .supabase_logger import get_client

_TABLE = "user_profiles"


def load_profile(employee_id: str) -> str:
    """從 Supabase 載入使用者偏好簡介，找不到時回傳空字串。"""
    client = get_client()
    if not client or not employee_id:
        return ""
    try:
        result = (
            client.table(_TABLE)
            .select("preference_summary")
            .eq("employee_id", employee_id)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0].get("preference_summary", "") or ""
    except Exception as e:
        print(f"[user_profile] 載入失敗：{e}")
    return ""


def update_profile(
    employee_id: str,
    current_profile: str,
    requirement: str,
    qa_history: list[dict],
    understanding: str,
    corrections: list[str] | None = None,
) -> str:
    """根據本次查詢觀察，用 LLM 整合更新偏好簡介，寫回 Supabase。回傳新簡介文字。"""
    from .generator import _chat
    from datetime import datetime, timezone

    observation = _build_observation(requirement, qa_history, understanding, corrections or [])
    if not observation.strip():
        return current_profile

    existing = current_profile.strip() if current_profile.strip() else "（尚無記錄）"

    prompt = f"""\
【現有偏好簡介】
{existing}

【本次查詢觀察】
{observation}

請整合新觀察，輸出更新後的偏好簡介。規則：
- 只記錄可復用的習慣偏好（粒度偏好、常見篩選、輸出格式習慣等），不記錄當次的具體數值、日期、特定名稱
- 新觀察與現有記錄衝突時，以新觀察為準
- 重複的項目合併，不要出現兩條意思相同的記錄
- 每行一條，以「- 」開頭，最多 8 條，每條 30 字以內
- 只輸出簡介內容，不要其他文字"""

    try:
        resp = _chat(
            CLASSIFICATION_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一個用戶偏好分析師，專門從金融業報表查詢紀錄中萃取可復用的使用習慣。"
                        "只輸出簡介條目，不要任何說明文字。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        new_profile = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[user_profile] LLM 更新失敗：{e}")
        return current_profile

    _save_profile(employee_id, new_profile)
    return new_profile


def _build_observation(
    requirement: str,
    qa_history: list[dict],
    understanding: str,
    corrections: list[str],
) -> str:
    parts = [f"需求：{requirement}"]
    if qa_history:
        qa_lines = [f"  系統問：{item['q']}\n  用戶答：{item['a']}" for item in qa_history]
        parts.append("系統提問與用戶回答：\n" + "\n".join(qa_lines))
    if understanding:
        parts.append(f"最終報表理解：{understanding}")
    if corrections:
        parts.append("用戶修正指令：\n" + "\n".join(f"  - {c}" for c in corrections))
    return "\n\n".join(parts)


def _save_profile(employee_id: str, profile: str) -> None:
    from datetime import datetime, timezone
    client = get_client()
    if not client or not employee_id:
        return
    try:
        client.table(_TABLE).upsert({
            "employee_id": employee_id,
            "preference_summary": profile,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        print(f"[user_profile] 寫入失敗：{e}")
