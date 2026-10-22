-- Runs once, when the data volume is first created. Nexus uses the default database
-- (POSTGRES_DB); LiteLLM and Langfuse each get their own.
CREATE DATABASE litellm;
CREATE DATABASE langfuse;
