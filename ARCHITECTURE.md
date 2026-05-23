# SQL Agent 系統架構說明

## 系統目標

業務員用自然語言描述報表需求，系統自動找出最相關的歷史案例與適合的資料庫表格，輔助工程師快速生成 Oracle SQL。

---

## 整體流程圖

```
業務員輸入需求
      │
      ▼
┌─────────────────────────────────────────────────────┐
│  Phase 1：場景分類                                   │
│  LLM (gpt-5-mini) 將需求分類到 7 個業務場景          │
│  → 主要場景 + 次要場景（0.4 gap 規則）               │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  Phase 3：向量檢索                                   │
│  用 BGE-M3 對需求文字做 cosine 相似度搜尋            │
│  → 從 92 筆歷史案例中找出 Top-5 最相似案例           │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│  Table Selection（評測中）                           │
│  LLM 根據需求文字 + 32 張表格說明                    │
│  → 選出這份報表需要用到的表格清單                    │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
              [輸出：相似案例 + 建議表格]
              （供工程師參考生成 SQL）
```

---

## 各階段詳細說明

### 離線前置作業（一次性）

```
schema.csv (73張表)
      │
      │  schema_summarizer.py
      │  LLM 為每張表產出 150-200 字業務說明
      ▼
table_summaries/*.txt (32張表的說明)

all_cases.json (92筆歷史案例)
      │
      │  summarizer.py
      │  LLM 閱讀 SQL + 需求，產出業務摘要
      ▼
case_summaries/*.txt (92筆摘要)
      │
      │  retriever.py
      │  BGE-M3 向量化，存入 npz cache
      ▼
all_cases_embeddings.npz
```

---

### Phase 1：場景分類

**輸入：** 業務員的需求文字

**處理：**
- LLM 對照 taxonomy.json 中定義的 7 個業務場景，輸出 Pydantic 結構
- 回傳主要場景、次要場景、各場景置信度分數
- **0.4 gap 規則**：若主要場景分數 − 次要場景分數 ≥ 0.4，丟棄次要場景（主場景太明顯時不需要混入次要類別）

**7 個業務場景：**
1. 精準行銷與專案名單篩選
2. 交易動能趨勢與異動偵測
3. 財管商品業績與例行月報
4. 人員異動與客戶移轉管理
5. 庫存與損益明細報表
6. 靜止戶與未實動名單
7. 市佔率與交易排名分析

**範例：**
```
輸入：「查詢南港分公司3月份台股交易量前50大客戶」

輸出：
  主要場景：交易動能趨勢與異動偵測  (分數 0.72)
  次要場景：市佔率與交易排名分析    (分數 0.18)
  → gap = 0.54 ≥ 0.4，丟棄次要場景
```

**相關檔案：** `classifier.py`, `pool_filter.py`, `models.py`, `taxonomy.json`

---

### Phase 3：向量檢索

**輸入：** 需求文字（自然語言）

**處理：**
1. 用 BGE-M3 將需求文字 embed 成 1024 維向量
2. 與 92 筆歷史案例的 embedding（預先計算，cached in npz）做 cosine 相似度
3. 回傳 Top-5 最相似案例

**為什麼用 LLM 摘要而非直接向量化原始需求？**

業務員的需求往往很簡短（「查3月台股前50大」），而歷史案例的 SQL 包含完整業務邏輯。
先用 LLM 將 SQL 轉成業務語言摘要，再向量化，讓兩邊都用相同的「業務語言」做比對。

**摘要原則：**
- 以 SQL 為主要依據（完整邏輯），需求文字為輔（可能簡略）
- 不寫具體年份（避免 2023 vs 2025 造成向量偏移）
- 不寫具體 Top-N 數字（寫「前 N 大客戶」）
- 允許寫分公司名稱（不同分公司報表風格不同，是有效資訊）

**範例：**
```
輸入：「查詢南港分公司3月份台股交易量前50大客戶」

Top-5 檢索結果：
  #1 [案例143] score=0.923  青埔現貨前N大客戶交易量與衰退分析
  #2 [案例116] score=0.871  台股交易量月均比較與動能偵測
  #3 [案例192] score=0.842  分公司市佔率與交易排名報表
  #4 [案例113] score=0.819  分公司客戶交易統計明細
  #5 [案例146] score=0.801  客戶庫存與交易量追蹤
```

**相關檔案：** `retriever.py`, `summarizer.py`, `case_summaries/`

---

### Table Selection（評測中）

**輸入：** 需求文字 + 32 張表格說明

**處理：**
- 將所有表格說明一次性送給 LLM
- LLM 根據需求判斷需要哪些表格，回傳 JSON 陣列
- 過濾幻覺（只保留 available 範圍內的表格名稱）

**32 張表格來源：**
- 30 張：schema.csv 中被歷史 SQL 實際使用過的表
- 2 張：自訂客群貼標表（經紀客群、財管客群）

**範例：**
```
輸入：「查詢ABC經紀客群的客戶明細，包含歷年交易量」

LLM 回傳：
  ["M_AC_ACCOUNT",
   "M_AT_STOCK_TXN",
   "S_ARIELSHAO.CUSTOMER_GROUP_2026Q1"]

Ground truth（SQL實際用到）：
  M_AC_ACCOUNT, M_AC_ACCOUNT_ACTU_BENEFIT,
  M_AT_BOND_TXN, M_AT_FUND_TXN, M_AT_INSURANCE_TXN,
  M_AT_SN_TXN, M_AT_STOCK_TXN,
  S_ARIELSHAO.CUSTOMER_GROUP_2026Q1,
  S_MELODYJJJIAN.CUSTOMER_GROUP_2026
```

**相關檔案：** `eval_table_selection.py`, `schema_summarizer.py`, `table_summaries/`

---

## 資料流與檔案結構

```
SQLagentnew/
│
├── all_cases.json              # 92 筆歷史案例（需求 + SQL）
├── all_cases_embeddings.npz    # 92 筆案例的 BGE-M3 向量 cache
├── schema.csv                  # 73 張表格定義（欄位名、中文名、說明）
├── used_tables.txt             # 30 張被 SQL 實際使用的表名清單
│
├── case_summaries/             # 92 筆 LLM 業務摘要（Phase 3 索引源）
│   ├── 113.txt
│   ├── 116.txt
│   └── ...
│
├── table_summaries/            # 32 張表格的業務說明（Table Selection 用）
│   ├── M_AC_ACCOUNT.txt
│   ├── M_AT_STOCK_TXN.txt
│   ├── S_ARIELSHAO.CUSTOMER_GROUP_2026Q1.txt
│   └── ...
│
├── experiment/                 # 每次實驗的 stdout log + JSON 結果
│   ├── 20250523_120000_eval_retrieval.txt
│   └── 20250523_120000_eval_table_selection.json
│
└── agent/
    ├── config.py               # 模型、路徑、費率設定
    ├── classifier.py           # Phase 1 場景分類
    ├── pool_filter.py          # 0.4 gap 規則 + 候選池建立
    ├── summarizer.py           # Case 業務摘要（LLM）
    ├── retriever.py            # BGE-M3 向量檢索
    ├── schema_summarizer.py    # Table 業務說明（LLM）+ raw schema 載入
    ├── eval_table_selection.py # Table selection 準確度評測
    ├── eval_retrieval.py       # 向量檢索準確度評測（無 LLM）
    ├── batch_test.py           # 10 案例批次評測（P1 + P3）
    └── main.py                 # CLI 入口
```

---

## CLI 指令速查

```bash
# 單筆查詢（Phase 1 + Phase 3）
python -m agent "幫我拉南港分公司台股交易量排名"

# 批次評測（10 筆固定案例，P1 + P3）
python -m agent --test

# 全庫向量檢索評測（92 筆，無 LLM 花費）
python -m agent --eval-retrieval

# Table selection 評測（LLM summary 模式）
python -m agent --eval-table-selection

# Table selection 評測（raw schema 模式，費用約 15x）
python -m agent --eval-table-selection --raw-schema

# 產出案例業務摘要（需先跑一次）
python -m agent --summarize
python -m agent --summarize 143          # 單筆
python -m agent --summarize --force      # 強制重跑

# 產出表格業務說明
python -m agent --schema-summarize
python -m agent --schema-summarize M_AC_ACCOUNT --force
```

---

## 評測指標說明

### 向量檢索（eval_retrieval）
- **命中率**：用自身需求查詢，自身出現在 Top-5 的比例
- **平均排名**：命中案例的平均 rank
- 目前成績：**92/92 (100%)，平均排名 1.0**

### Table Selection（eval_table_selection）
- **Precision**：LLM 選出的表中，有幾張真的用到
- **Recall**：SQL 實際用到的表中，LLM 選到了幾張
- **F1**：P 與 R 的調和平均
- **Exact match**：LLM 選出的集合與 ground truth 完全一致
- Ground truth 來源：解析每個 case 的 SQL，找出實際引用且在 table_summaries/ 中的表格名稱
