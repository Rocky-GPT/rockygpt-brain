-- Frozen test schema from the current Data workspace, 2026-09-16.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS rockygpt_v2.schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.dataset_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  version TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('staging', 'validating', 'active', 'failed', 'retired')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  activated_at TIMESTAMPTZ,
  source_commit_sha TEXT,
  quality_summary JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_dataset_version
  ON rockygpt_v2.dataset_versions ((status))
  WHERE status = 'active';

CREATE TABLE IF NOT EXISTS rockygpt_v2.sources (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_key TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  canonical_url TEXT NOT NULL,
  trust_tier TEXT NOT NULL CHECK (trust_tier IN ('official_primary', 'official_secondary', 'community')),
  freshness_sla_hours INTEGER NOT NULL CHECK (freshness_sla_hours > 0),
  domain TEXT NOT NULL
);

-- Immutable ingestion metadata. Raw payloads live in private object storage;
-- PostgreSQL keeps the content-addressed audit trail and release graph.
CREATE TABLE IF NOT EXISTS rockygpt_v2.ingestion_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed', 'static')),
  started_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ,
  raw_uri TEXT,
  raw_hash TEXT,
  source_etag TEXT,
  parser_version TEXT NOT NULL DEFAULT '1',
  source_commit_sha TEXT,
  record_count INTEGER,
  output_hash TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ingestion_runs_source_started_idx
  ON rockygpt_v2.ingestion_runs (source_id, started_at DESC);

CREATE TABLE IF NOT EXISTS rockygpt_v2.source_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  ingestion_run_id UUID NOT NULL REFERENCES rockygpt_v2.ingestion_runs(id),
  schema_version TEXT NOT NULL DEFAULT '1',
  content_hash TEXT NOT NULL,
  collected_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('staging', 'valid', 'failed', 'retired')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_id, content_hash, schema_version)
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.releases (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  version TEXT UNIQUE NOT NULL,
  dataset_version_id UUID UNIQUE REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  previous_release_id UUID REFERENCES rockygpt_v2.releases(id) ON DELETE SET NULL,
  status TEXT NOT NULL CHECK (status IN ('staging', 'validating', 'active', 'failed', 'retired')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  activated_at TIMESTAMPTZ,
  manifest_hash TEXT,
  quality_summary JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_release
  ON rockygpt_v2.releases ((status))
  WHERE status = 'active';

CREATE TABLE IF NOT EXISTS rockygpt_v2.release_sources (
  release_id UUID NOT NULL REFERENCES rockygpt_v2.releases(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  snapshot_id UUID NOT NULL REFERENCES rockygpt_v2.source_snapshots(id),
  PRIMARY KEY (release_id, source_id)
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.source_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_key TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  source_url TEXT NOT NULL,
  record_count INTEGER,
  content_hash TEXT,
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.critical_facts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  source_record_key TEXT NOT NULL,
  fact_key TEXT NOT NULL,
  fact_value TEXT NOT NULL,
  verified_at TIMESTAMPTZ NOT NULL,
  collected_at TIMESTAMPTZ NOT NULL,
  valid_from DATE,
  valid_until DATE,
  content_hash TEXT NOT NULL,
  UNIQUE (dataset_version_id, fact_key)
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.campus_contacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  source_record_key TEXT NOT NULL,
  name TEXT NOT NULL,
  department TEXT,
  phone TEXT,
  email TEXT,
  office TEXT,
  aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
  collected_at TIMESTAMPTZ NOT NULL,
  valid_from DATE,
  valid_until DATE,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.campus_hours (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  source_record_key TEXT NOT NULL,
  name TEXT NOT NULL,
  day TEXT NOT NULL,
  schedule TEXT NOT NULL,
  collected_at TIMESTAMPTZ NOT NULL,
  valid_from DATE,
  valid_until DATE,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.dining_hours (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  source_record_key TEXT NOT NULL,
  name TEXT NOT NULL,
  day TEXT NOT NULL,
  schedule TEXT NOT NULL,
  collected_at TIMESTAMPTZ NOT NULL,
  valid_from DATE,
  valid_until DATE,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.menu_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  source_record_key TEXT NOT NULL,
  meal TEXT NOT NULL,
  station TEXT NOT NULL,
  name TEXT NOT NULL,
  calories TEXT,
  vegan BOOLEAN,
  vegetarian BOOLEAN,
  label_coverage JSONB NOT NULL DEFAULT '{}'::jsonb,
  allergens JSONB NOT NULL DEFAULT '[]'::jsonb,
  collected_at TIMESTAMPTZ NOT NULL,
  valid_from DATE,
  valid_until DATE,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.shuttle_routes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  source_record_key TEXT NOT NULL,
  name TEXT NOT NULL,
  -- 'weekday' | 'saturday' | 'sunday'. Nullable additive compatibility stage:
  -- rows from datasets published before this column stay eligible every day.
  service_day TEXT,
  collected_at TIMESTAMPTZ NOT NULL,
  valid_from DATE,
  valid_until DATE,
  content_hash TEXT NOT NULL
);

ALTER TABLE rockygpt_v2.shuttle_routes
  ADD COLUMN IF NOT EXISTS service_day TEXT;

CREATE TABLE IF NOT EXISTS rockygpt_v2.shuttle_trips (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  route_id UUID NOT NULL REFERENCES rockygpt_v2.shuttle_routes(id) ON DELETE CASCADE,
  source_record_key TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  departure TEXT NOT NULL,
  arrival TEXT NOT NULL,
  stops JSONB NOT NULL DEFAULT '[]'::jsonb,
  collected_at TIMESTAMPTZ NOT NULL,
  valid_from DATE,
  valid_until DATE,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.academic_dates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  source_record_key TEXT NOT NULL,
  family TEXT NOT NULL,
  kind TEXT NOT NULL,
  term TEXT NOT NULL,
  term_id TEXT NOT NULL,
  session TEXT,
  session_id TEXT,
  date_label TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  starts_at TIMESTAMPTZ,
  collected_at TIMESTAMPTZ NOT NULL,
  valid_from DATE,
  valid_until DATE,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.campus_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  source_record_key TEXT NOT NULL,
  title TEXT NOT NULL,
  date_label TEXT NOT NULL,
  starts_at TIMESTAMPTZ,
  start_time TEXT,
  end_time TEXT,
  organizer TEXT,
  description TEXT,
  event_url TEXT,
  collected_at TIMESTAMPTZ NOT NULL,
  valid_from DATE,
  valid_until DATE,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.clubs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  source_record_key TEXT NOT NULL,
  name TEXT NOT NULL,
  category TEXT,
  website_url TEXT,
  collected_at TIMESTAMPTZ NOT NULL,
  valid_from DATE,
  valid_until DATE,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.programs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  source_record_key TEXT NOT NULL,
  name TEXT NOT NULL,
  degree TEXT,
  program_kind TEXT,
  school TEXT,
  description TEXT,
  program_url TEXT,
  collected_at TIMESTAMPTZ NOT NULL,
  valid_from DATE,
  valid_until DATE,
  content_hash TEXT NOT NULL
);

ALTER TABLE rockygpt_v2.programs
  ADD COLUMN IF NOT EXISTS program_kind TEXT;

CREATE TABLE IF NOT EXISTS rockygpt_v2.documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  source_id UUID NOT NULL REFERENCES rockygpt_v2.sources(id),
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  collected_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS rockygpt_v2.document_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES rockygpt_v2.documents(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  lexical_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS document_chunks_lexical_idx
  ON rockygpt_v2.document_chunks USING GIN (lexical_vector);

-- Versioned UI projections let browser surfaces and chat pin the same release
-- during the gradual migration away from checked-in public/data files.
CREATE TABLE IF NOT EXISTS rockygpt_v2.release_artifacts (
  dataset_version_id UUID NOT NULL REFERENCES rockygpt_v2.dataset_versions(id) ON DELETE CASCADE,
  artifact_key TEXT NOT NULL,
  payload JSONB NOT NULL,
  content_hash TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (dataset_version_id, artifact_key)
);

-- Legacy compatibility only. Feedback is now owned by rockygpt-brain in the
-- rockygpt_brain schema. Keep this definition until every deployed database
-- has passed the historic migrations that reference it.
CREATE TABLE IF NOT EXISTS rockygpt_v2.feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id UUID NOT NULL,
  question TEXT NOT NULL DEFAULT '',
  answer TEXT NOT NULL DEFAULT '',
  rating SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
  category TEXT,
  comments TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '90 days'),
  CONSTRAINT feedback_request_id_unique UNIQUE (request_id)
);

ALTER TABLE rockygpt_v2.feedback
  ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '90 days');

CREATE INDEX IF NOT EXISTS feedback_expires_at_idx
  ON rockygpt_v2.feedback (expires_at);
