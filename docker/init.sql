-- pgvector 확장
CREATE EXTENSION IF NOT EXISTS vector;

-- 업무 정의 테이블
CREATE TABLE IF NOT EXISTS workflows (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    trigger_type VARCHAR(20) NOT NULL CHECK (trigger_type IN ('manual', 'scheduled', 'event')),
    trigger_config JSONB NOT NULL DEFAULT '{}',
    -- trigger_config 예시:
    -- manual: {"keywords": ["일기", "diary"], "max_input_length": 500}
    -- scheduled: {"cron": "0 21 * * *"}
    -- event: {"url": "https://...", "interval_hours": 6, "condition": "새 프로그램"}
    process_steps JSONB NOT NULL DEFAULT '[]',
    -- process_steps 예시:
    -- [{"type": "store_only"}, {"type": "ai_expand", "prompt": "..."}, {"type": "web_search"}]
    output_format VARCHAR(50) DEFAULT 'markdown',
    output_path VARCHAR(255),
    user_settings JSONB NOT NULL DEFAULT '{}',
    -- user_settings 예시:
    -- {"tone": "concise", "style": "diary", "language": "ko"}
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 임베딩 인덱스 (업무 설명 벡터화 — 업무 매칭용)
ALTER TABLE workflows ADD COLUMN IF NOT EXISTS name_embedding vector(1536);
CREATE INDEX IF NOT EXISTS workflows_name_embedding_idx
    ON workflows USING ivfflat (name_embedding vector_cosine_ops)
    WITH (lists = 100);

-- 업무 실행 이력 테이블
CREATE TABLE IF NOT EXISTS workflow_runs (
    id SERIAL PRIMARY KEY,
    workflow_id INTEGER REFERENCES workflows(id) ON DELETE SET NULL,
    workflow_name VARCHAR(100),  -- workflow 삭제 시에도 이력 보존
    trigger_type VARCHAR(20) NOT NULL,
    input_text TEXT,
    input_source VARCHAR(20) NOT NULL DEFAULT 'text' CHECK (input_source IN ('text', 'voice', 'web', 'scheduled')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    result JSONB,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- 문서 테이블 (업무 실행 결과물)
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    workflow_run_id INTEGER REFERENCES workflow_runs(id) ON DELETE SET NULL,
    workflow_id INTEGER REFERENCES workflows(id) ON DELETE SET NULL,
    title VARCHAR(255),
    content TEXT NOT NULL,
    content_embedding vector(1536),  -- 시맨틱 검색용
    file_path VARCHAR(500),
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 시맨틱 검색 인덱스
CREATE INDEX IF NOT EXISTS documents_content_embedding_idx
    ON documents USING ivfflat (content_embedding vector_cosine_ops)
    WITH (lists = 100);

-- 전문 검색 인덱스 (FTS)
CREATE INDEX IF NOT EXISTS documents_content_fts_idx
    ON documents USING GIN (to_tsvector('simple', content));

-- updated_at 자동 갱신 트리거
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER workflows_updated_at
    BEFORE UPDATE ON workflows
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
