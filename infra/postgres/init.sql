-- Runs once, when the data volume is first created. Jarvis uses the default database
-- (POSTGRES_DB); LiteLLM and Langfuse each get their own.
CREATE DATABASE litellm;
CREATE DATABASE langfuse;
