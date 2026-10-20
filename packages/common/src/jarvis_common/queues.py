"""Names shared by the services that put jobs on, and take jobs off, the arq Redis queues."""

# Jarvis turns and memory jobs run on the orchestrator, which listens on arq's default queue.
ORCHESTRATOR_QUEUE = "arq:queue"
# Parsing and embedding uploads run on the doc-worker.
DOCS_QUEUE = "arq:docs"
# arq refreshes this key on each worker's queue while the worker is alive.
HEALTH_SUFFIX = ":health-check"
