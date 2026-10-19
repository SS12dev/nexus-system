"""ASGI entry point: `uvicorn jarvis_api.app:app`."""

from jarvis_api.main import create_app

app = create_app()
