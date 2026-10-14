-- Runs once on an empty data volume. Each service gets a login role that owns only its schema,
-- so a write into another service's schema fails in the database, not just by convention.
\set ON_ERROR_STOP on
\getenv gateway_password GATEWAY_DB_PASSWORD
\getenv agent_password AGENT_DB_PASSWORD

CREATE ROLE gateway LOGIN PASSWORD :'gateway_password';
CREATE ROLE agent LOGIN PASSWORD :'agent_password';

REVOKE ALL ON DATABASE :"DBNAME" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"DBNAME" TO gateway, agent;
-- drizzle-kit migrate always runs CREATE SCHEMA IF NOT EXISTS, which needs this even when the
-- schema exists. Ownership still blocks writes into the agent schema.
GRANT CREATE ON DATABASE :"DBNAME" TO gateway;

CREATE SCHEMA gateway AUTHORIZATION gateway;
CREATE SCHEMA agent AUTHORIZATION agent;

-- pgvector for runbook search; only a superuser can install it.
CREATE EXTENSION IF NOT EXISTS vector;
