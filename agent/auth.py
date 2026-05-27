"""使用者認證：員工編號 + 密碼，bcrypt hash 存 Supabase users 表。"""
from __future__ import annotations

from .supabase_logger import get_client


def _hash(password: str) -> str:
    import bcrypt
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify(password: str, hashed: str) -> bool:
    import bcrypt
    return bcrypt.checkpw(password.encode(), hashed.encode())


def register_user(
    employee_id: str,
    password: str,
    display_name: str = "",
) -> tuple[bool, str]:
    """註冊新使用者。回傳 (success, message)。"""
    client = get_client()
    if client is None:
        return False, "資料庫連線失敗"
    existing = (
        client.table("users")
        .select("employee_id")
        .eq("employee_id", employee_id)
        .execute()
    )
    if existing.data:
        return False, "此員工編號已被註冊"
    client.table("users").insert({
        "employee_id": employee_id,
        "password_hash": _hash(password),
        "display_name": display_name.strip() or employee_id,
    }).execute()
    return True, "註冊成功"


def login_user(
    employee_id: str,
    password: str,
) -> tuple[bool, dict | str]:
    """驗證登入。回傳 (success, user_dict) 或 (False, error_message)。"""
    client = get_client()
    if client is None:
        return False, "資料庫連線失敗"
    result = (
        client.table("users")
        .select("employee_id, password_hash, display_name")
        .eq("employee_id", employee_id)
        .execute()
    )
    if not result.data:
        return False, "員工編號不存在"
    user = result.data[0]
    if not _verify(password, user["password_hash"]):
        return False, "密碼錯誤"
    return True, {
        "employee_id": user["employee_id"],
        "display_name": user.get("display_name") or employee_id,
    }
