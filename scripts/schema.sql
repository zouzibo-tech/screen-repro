-- screen-repro v3.0 SQLite Schema
-- ================================
-- 数据库: screening.db
-- 方案B: SQLite（权威数据源） + MD文件（人类可读）

-- 文献库
CREATE TABLE IF NOT EXISTS papers (
    key TEXT PRIMARY KEY,           -- Author_Year_TitleHash6
    author TEXT,
    year INTEGER,
    title TEXT,
    doi TEXT,
    pdf_path TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 筛选记录（核心表）
CREATE TABLE IF NOT EXISTS screening (
    key TEXT PRIMARY KEY REFERENCES papers(key),
    decision TEXT NOT NULL,         -- INCLUDE/EXCLUDE/MAYBE/SKIPPED/ERROR
    exclusion_code TEXT,            -- E1-E9
    -- PICOS判定详情
    p_result TEXT,                  -- ✅/❌/⚠️
    p_evidence TEXT,                -- JSON array
    p_analysis TEXT,
    i_result TEXT,
    i_device_type TEXT,
    i_evidence TEXT,
    i_analysis TEXT,
    c_result TEXT,
    c_evidence TEXT,
    c_analysis TEXT,
    o_result TEXT,
    o_outcome_type TEXT,
    o_retention_weeks INTEGER,
    o_evidence TEXT,
    o_analysis TEXT,
    s_result TEXT,
    s_design_type TEXT,
    s_evidence TEXT,
    s_analysis TEXT,
    reason TEXT,
    text_quality TEXT,
    -- 文件路径
    pdf_path TEXT,
    mining_path TEXT,
    md_path TEXT,
    -- 元数据
    screened_at TEXT,
    model TEXT,
    text_hash TEXT,
    -- 可复现性字段
    fingerprint TEXT,               -- 五维指纹
    model_version TEXT,             -- 精确模型版本
    prompt_hash TEXT,               -- prompt模板hash
    extraction_method TEXT,         -- MinerU/PyMuPDF
    temperature REAL DEFAULT 0,     -- 记录使用的温度
    seed INTEGER DEFAULT 42,        -- 记录使用的seed
    llm_response_raw TEXT           -- 原始LLM响应（审计用）
);

-- 进度追踪（单行表）
CREATE TABLE IF NOT EXISTS progress (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    total INTEGER DEFAULT 0,
    processed INTEGER DEFAULT 0,
    remaining INTEGER DEFAULT 0,
    current_key TEXT,
    status TEXT DEFAULT 'idle',     -- idle/running/done
    include_count INTEGER DEFAULT 0,
    exclude_count INTEGER DEFAULT 0,
    maybe_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    started_at TEXT,
    last_updated TEXT
);

-- QA复核记录
CREATE TABLE IF NOT EXISTS qa_reviews (
    key TEXT REFERENCES screening(key),
    action TEXT NOT NULL,           -- resolve/confirm
    old_decision TEXT,
    new_decision TEXT,
    reason TEXT,
    reviewed_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (key, action)
);

-- 迁移日志
CREATE TABLE IF NOT EXISTS migration_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    old_key TEXT,
    new_key TEXT,
    migrated_at TEXT DEFAULT (datetime('now'))
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_screening_decision ON screening(decision);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);
CREATE INDEX IF NOT EXISTS idx_screening_fingerprint ON screening(fingerprint);
CREATE INDEX IF NOT EXISTS idx_screening_model ON screening(model_version);

-- 初始化progress行（如果不存在）
INSERT OR IGNORE INTO progress (id, total, processed, remaining, status)
VALUES (1, 0, 0, 0, 'idle');
