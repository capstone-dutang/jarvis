-- JARVIS PostgreSQL initialization — extensions only
-- SQL functions are created after tables exist (via alembic migration or manual script)

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pgroonga";
