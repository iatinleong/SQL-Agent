"""SQL 語法驗證：sqlglot（解析層）+ sqlfluff（規則層），失敗時用 LLM 自動修正。
Step C-2：決定性幻覺檢查（AST + schema.csv 比對），有錯同樣送 LLM 修正。
"""

from __future__ import annotations

import csv
from pathlib import Path

from .config import CLASSIFICATION_MODEL

_SCHEMA_PATH = Path(__file__).parent.parent / "schema.csv"
_DM_S_VIEW = "DM_S_VIEW."


# ── 前處理：清理 LLM 輸出的雜訊 ───────────────────────────────────

def _clean(sql: str) -> str:
    """移除 LLM 輸出中可能夾帶的 markdown fence 與多餘空白。"""
    s = sql.strip()
    for fence in ("```sql", "```"):
        if s.startswith(fence):
            s = s[len(fence):]
            break
    if s.endswith("```"):
        s = s[:-3]
    return s.strip()


# ── 驗證層 ─────────────────────────────────────────────────────────

def _run_sqlglot(sql: str) -> list[str]:
    """用 sqlglot 做 parse 層語法檢查。"""
    try:
        import sqlglot
        sqlglot.transpile(sql, read="oracle", write="oracle")
        return []
    except Exception as e:
        return [f"[sqlglot] {e}"]


_SQLFLUFF_STYLE_PREFIXES = ("LT", "AL08", "CP", "RF", "CV10", "CV11")


def _run_sqlfluff(sql: str) -> list[str]:
    """用 sqlfluff oracle dialect 做規則層語法檢查，過濾純樣式規則。"""
    try:
        import sqlfluff
        result = sqlfluff.lint(sql, dialect="oracle")
        issues = []
        for v in result:
            code = v.get("code", "")
            if any(code.startswith(p) for p in _SQLFLUFF_STYLE_PREFIXES):
                continue
            desc = v.get("description", "")
            line = v.get("line_no", "?")
            issues.append(f"[sqlfluff {code}] L{line}: {desc}")
        return issues
    except Exception as e:
        return [f"[sqlfluff] {e}"]


def validate_sql(sql: str) -> list[str]:
    """sqlglot 先跑；有 parse 錯誤就不跑 sqlfluff。"""
    sql = _clean(sql)
    glot_errors = _run_sqlglot(sql)
    if glot_errors:
        return glot_errors
    return _run_sqlfluff(sql)


# ── LLM 修正 ───────────────────────────────────────────────────────

def _fix_with_llm(sql: str, errors: list[str], model: str) -> tuple[str, dict]:
    from .generator import _chat

    error_text = "\n".join(errors)
    resp = _chat(
        model,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是 Oracle SQL 專家。"
                    "根據錯誤訊息修正 SQL，只輸出修正後的完整 SQL，"
                    "不要任何說明、不要 markdown fence。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"【錯誤訊息】\n{error_text}\n\n"
                    f"【原始 SQL】\n{sql}"
                ),
            },
        ],
        temperature=0,
    )
    fixed = (resp.choices[0].message.content or "").strip()
    for fence in ("```sql", "```"):
        if fixed.startswith(fence):
            fixed = fixed[len(fence):]
    fixed = fixed.strip("`").strip()
    tokens = {
        "fix_in": resp.usage.prompt_tokens,
        "fix_out": resp.usage.completion_tokens,
    }
    return fixed, tokens


# ── 決定性幻覺檢查（AST + schema.csv）─────────────────────────────

def _load_schema_lookup() -> dict[str, set[str]]:
    """從 schema.csv 建立 {正規化表格名稱: {欄位名稱大寫, ...}}。"""
    lookup: dict[str, set[str]] = {}
    with open(_SCHEMA_PATH, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            tname = row.get("表格名稱", "").strip().upper()
            col = row.get("欄位名稱", "").strip().upper()
            if tname and col:
                lookup.setdefault(tname, set()).add(col)
    return lookup


def _normalize_table(db: str, name: str) -> str:
    """將 SQL 中的表格引用正規化為 schema.csv 的 key 格式。
    DM_S_VIEW.M_AC_ACCOUNT → M_AC_ACCOUNT
    S_ARIELSHAO.CUSTOMER_GROUP_2026Q1 → S_ARIELSHAO.CUSTOMER_GROUP_2026Q1（保留）
    """
    if db:
        full = f"{db.upper()}.{name.upper()}"
    else:
        full = name.upper()
    if full.startswith(_DM_S_VIEW):
        return full[len(_DM_S_VIEW):]
    return full


def check_hallucination(sql: str) -> list[str]:
    """
    決定性幻覺檢查：用 sqlglot AST 提取表格與有明確表格限定詞的欄位，
    與 schema.csv 做字串比對。
    回傳錯誤訊息 list（空 list = 通過）。

    跳過的情況（避免誤報）：
    - CTE 名稱（非實際表格）
    - 沒有表格限定詞的欄位（無法歸屬）
    - 無法解析 alias 的欄位引用
    """
    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        return []

    schema_lookup = _load_schema_lookup()
    errors: list[str] = []

    try:
        tree = sqlglot.parse_one(sql, dialect="oracle")
    except Exception:
        return []  # parse 錯誤已由 C-1 處理

    # 1. 收集 CTE 名稱（虛擬表格，不驗證）
    cte_names: set[str] = {
        cte.alias_or_name.upper() for cte in tree.find_all(exp.CTE)
    }

    # 2. 建立 alias → 正規化表格名 的對照表
    alias_map: dict[str, str] = {}
    for tnode in tree.find_all(exp.Table):
        raw_name = tnode.name or ""
        if not raw_name:
            continue
        normalized = _normalize_table(tnode.db or "", raw_name)
        if normalized in cte_names or raw_name.upper() in cte_names:
            continue
        alias = (tnode.alias or "").upper()
        if alias:
            alias_map[alias] = normalized
        alias_map[raw_name.upper()] = normalized
        alias_map[normalized] = normalized

    # 3. 驗證表格存在
    seen_table_errors: set[str] = set()
    for tnode in tree.find_all(exp.Table):
        raw_name = tnode.name or ""
        if not raw_name:
            continue
        normalized = _normalize_table(tnode.db or "", raw_name)
        if normalized in cte_names or raw_name.upper() in cte_names:
            continue
        if normalized not in schema_lookup and normalized not in seen_table_errors:
            seen_table_errors.add(normalized)
            errors.append(f"[幻覺] 表格不存在於 schema：{normalized}")

    # 4. 驗證有限定詞的欄位存在
    seen_col_errors: set[str] = set()
    for cnode in tree.find_all(exp.Column):
        col_name = (cnode.name or "").upper()
        qualifier = (cnode.table or "").upper()

        if not col_name or col_name == "*" or not qualifier:
            continue
        if qualifier in cte_names:
            continue

        actual_table = alias_map.get(qualifier)
        if not actual_table or actual_table not in schema_lookup:
            continue  # 表格錯誤已報告，或無法解析

        if col_name not in schema_lookup[actual_table]:
            key = f"{actual_table}.{col_name}"
            if key not in seen_col_errors:
                seen_col_errors.add(key)
                errors.append(
                    f"[幻覺] 欄位不存在於 schema：{actual_table}.{col_name}"
                )

    return errors


def check_and_fix_hallucination(
    sql: str,
    model: str = CLASSIFICATION_MODEL,
) -> tuple[str, list[str], bool, dict]:
    """
    決定性幻覺檢查 + LLM 修正（若有錯）。
    回傳 (final_sql, errors, passed, tokens)。
    """
    errors = check_hallucination(sql)
    if not errors:
        return sql, [], True, {}
    fixed_sql, tokens = _fix_with_llm(sql, errors, model)
    return fixed_sql, errors, False, tokens


# ── 主入口（C-1）──────────────────────────────────────────────────

def validate_and_fix(
    sql: str,
    model: str = CLASSIFICATION_MODEL,
    max_iter: int = 3,
) -> tuple[str, list[dict], dict]:
    """
    語法驗證並自動修正，最多 max_iter 輪。
    回傳 (final_sql, log, total_tokens)。
    log 每筆：{"round": int, "errors": list[str], "passed": bool}
    """
    sql = _clean(sql)
    total_tokens: dict[str, int] = {}
    log: list[dict] = []

    for i in range(max_iter):
        errors = validate_sql(sql)
        passed = len(errors) == 0
        log.append({"round": i + 1, "errors": errors, "passed": passed})

        if passed:
            break

        sql, tokens = _fix_with_llm(sql, errors, model)
        for k, v in tokens.items():
            total_tokens[k] = total_tokens.get(k, 0) + v

    return sql, log, total_tokens
