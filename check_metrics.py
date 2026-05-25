"""掃描 92 個 case 的 SQL，提取所有聚合與視窗函數使用模式。"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ALL_CASES_PATH = Path(__file__).parent / "all_cases.json"

# 聚合函數
AGG_PATTERN = re.compile(
    r'\b(SUM|COUNT|AVG|MAX|MIN|MEDIAN|STDDEV|VARIANCE|LISTAGG|WM_CONCAT)\s*\(',
    re.IGNORECASE
)

# 視窗函數
WINDOW_PATTERN = re.compile(
    r'\b(ROW_NUMBER|RANK|DENSE_RANK|NTILE|LAG|LEAD|FIRST_VALUE|LAST_VALUE|'
    r'SUM|COUNT|AVG|MAX|MIN)\s*\(.*?\)\s*OVER\s*\(',
    re.IGNORECASE | re.DOTALL
)

# SUM(CASE WHEN ...) 模式
SUM_CASE_PATTERN = re.compile(r'\bSUM\s*\(\s*CASE\b', re.IGNORECASE)
MAX_CASE_PATTERN = re.compile(r'\bMAX\s*\(\s*CASE\b', re.IGNORECASE)
COUNT_DISTINCT_PATTERN = re.compile(r'\bCOUNT\s*\(\s*DISTINCT\b', re.IGNORECASE)

# NVL(SUM(...))
NVL_AGG_PATTERN = re.compile(r'\bNVL\s*\(\s*(SUM|COUNT|AVG|MAX|MIN)\s*\(', re.IGNORECASE)

# PIVOT
PIVOT_PATTERN = re.compile(r'\bPIVOT\s*\(', re.IGNORECASE)

# 抓 SUM/COUNT/MAX/MIN 的括號內容（簡化：只取第一層引數）
COL_INSIDE_AGG = re.compile(
    r'\b(SUM|COUNT|AVG|MAX|MIN)\s*\(\s*(DISTINCT\s+)?([A-Z_][A-Z0-9_.]*)',
    re.IGNORECASE
)

def get_all_sql_texts(cases: list[dict]) -> list[tuple[str, str]]:
    """回傳 [(case_id, sql_text), ...]"""
    result = []
    for c in cases:
        cid = str(c.get("資料夾", ""))
        for s in (c.get("SQL") or []):
            text = s.get("內容", "")
            if text.strip():
                result.append((cid, text))
    return result


def main():
    with open(ALL_CASES_PATH, encoding="utf-8") as f:
        cases = json.load(f)

    sql_texts = get_all_sql_texts(cases)
    print(f"共 {len(cases)} 個 case，{len(sql_texts)} 個 SQL 檔\n")

    # ── 1. 各聚合函數出現次數與 case 清單 ──────────────────────────
    agg_cases: dict[str, set] = defaultdict(set)
    for cid, text in sql_texts:
        for m in AGG_PATTERN.finditer(text):
            agg_cases[m.group(1).upper()].add(cid)

    # ── 2. 視窗函數（OVER）出現 case 清單 ─────────────────────────
    window_cases: dict[str, set] = defaultdict(set)
    window_simple = re.compile(
        r'\b(ROW_NUMBER|RANK|DENSE_RANK|NTILE|LAG|LEAD|FIRST_VALUE|LAST_VALUE)\s*\(',
        re.IGNORECASE
    )
    for cid, text in sql_texts:
        for m in window_simple.finditer(text):
            window_cases[m.group(1).upper()].add(cid)
        # SUM/COUNT OVER
        for m in re.finditer(r'\b(SUM|COUNT|AVG|MAX|MIN)\s*\([^)]*\)\s*OVER\s*\(', text, re.IGNORECASE):
            window_cases[m.group(1).upper() + "_OVER"].add(cid)

    # ── 3. 特殊模式 ────────────────────────────────────────────────
    pattern_cases: dict[str, set] = defaultdict(set)
    for cid, text in sql_texts:
        if SUM_CASE_PATTERN.search(text):
            pattern_cases["SUM(CASE WHEN ...)"].add(cid)
        if MAX_CASE_PATTERN.search(text):
            pattern_cases["MAX(CASE WHEN ...)"].add(cid)
        if COUNT_DISTINCT_PATTERN.search(text):
            pattern_cases["COUNT(DISTINCT ...)"].add(cid)
        if NVL_AGG_PATTERN.search(text):
            pattern_cases["NVL(agg(...))"].add(cid)
        if PIVOT_PATTERN.search(text):
            pattern_cases["PIVOT(MAX(...))"].add(cid)

    # ── 4. SUM/COUNT/MAX/MIN 作用的欄位名 ─────────────────────────
    col_usage: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for cid, text in sql_texts:
        for m in COL_INSIDE_AGG.finditer(text):
            func = m.group(1).upper()
            col = m.group(3).upper().split(".")[-1]  # 去掉 alias 前綴
            if len(col) >= 3:  # 排掉太短的（如 *、1）
                col_usage[func][col].add(cid)

    # ── 輸出 ────────────────────────────────────────────────────────
    out = {
        "aggregation_functions": {
            k: {"case_count": len(v), "cases": sorted(v)}
            for k, v in sorted(agg_cases.items(), key=lambda x: -len(x[1]))
        },
        "window_functions": {
            k: {"case_count": len(v), "cases": sorted(v)}
            for k, v in sorted(window_cases.items(), key=lambda x: -len(x[1]))
        },
        "special_patterns": {
            k: {"case_count": len(v), "cases": sorted(v)}
            for k, v in sorted(pattern_cases.items(), key=lambda x: -len(x[1]))
        },
        "columns_by_function": {
            func: {
                col: {"case_count": len(cases_), "cases": sorted(cases_)}
                for col, cases_ in sorted(cols.items(), key=lambda x: -len(x[1]))
                if len(cases_) >= 2  # 只顯示出現 2 次以上的欄位
            }
            for func, cols in col_usage.items()
        }
    }

    out_path = Path("/tmp/metrics_audit.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"結果已寫入 {out_path}")

    # 終端摘要
    print("\n=== 聚合函數 ===")
    for fn, info in out["aggregation_functions"].items():
        print(f"  {fn:<12} {info['case_count']:3d} cases")

    print("\n=== 視窗函數 ===")
    for fn, info in out["window_functions"].items():
        print(f"  {fn:<18} {info['case_count']:3d} cases")

    print("\n=== 特殊模式 ===")
    for pat, info in out["special_patterns"].items():
        print(f"  {pat:<25} {info['case_count']:3d} cases")

    print("\n=== SUM 作用的欄位（≥2 cases）===")
    for col, info in out["columns_by_function"].get("SUM", {}).items():
        print(f"  {col:<30} {info['case_count']:3d} cases")

    print("\n=== COUNT 作用的欄位（≥2 cases）===")
    for col, info in out["columns_by_function"].get("COUNT", {}).items():
        print(f"  {col:<30} {info['case_count']:3d} cases")

    print("\n=== MAX 作用的欄位（≥2 cases）===")
    for col, info in out["columns_by_function"].get("MAX", {}).items():
        print(f"  {col:<30} {info['case_count']:3d} cases")


if __name__ == "__main__":
    main()
