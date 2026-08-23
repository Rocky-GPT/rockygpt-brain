-- Brain-owned schema. Applied idempotently at startup by persistence/db.py.
-- This credential must never be granted access to the data-service schema
-- (spec/system-boundaries.md, spec/acceptance.md).

CREATE TABLE IF NOT EXISTS chat_logs (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL,
    visitor_id           TEXT,
    user_message         TEXT NOT NULL DEFAULT '',
    assistant_message    TEXT NOT NULL DEFAULT '',
    route                TEXT NOT NULL,
    question_origin      TEXT CHECK (question_origin IN ('client', 'dev', 'bot')),
    tools_invoked        TEXT[] NOT NULL DEFAULT '{}',
    tool_arguments       JSONB NOT NULL DEFAULT '{}'::jsonb,
    citations            JSONB NOT NULL DEFAULT '[]'::jsonb,
    facts_extracted       JSONB NOT NULL DEFAULT '[]'::jsonb,
    debug_info           JSONB,
    latency_ms           INTEGER NOT NULL DEFAULT 0,
    feedback             TEXT CHECK (feedback IN ('positive', 'negative')),
    feedback_rating       INTEGER CHECK (feedback_rating IN (-1, 1)),
    feedback_category     TEXT,
    feedback_comment      TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    text_expires_at       TIMESTAMPTZ NOT NULL,
    metadata_expires_at   TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS chat_logs_created_at_idx ON chat_logs (created_at);
CREATE INDEX IF NOT EXISTS chat_logs_updated_at_idx ON chat_logs (updated_at DESC);
CREATE INDEX IF NOT EXISTS chat_logs_text_expires_at_idx ON chat_logs (text_expires_at);
CREATE INDEX IF NOT EXISTS chat_logs_metadata_expires_at_idx ON chat_logs (metadata_expires_at);
CREATE INDEX IF NOT EXISTS chat_logs_route_idx ON chat_logs (route);
