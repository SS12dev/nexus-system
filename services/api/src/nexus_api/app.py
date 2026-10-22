"""ASGI entry point: `uvicorn nexus_api.app:app`."""

from nexus_api.main import create_app

app = create_app()
