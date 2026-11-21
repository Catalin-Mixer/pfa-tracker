"""Entry point: python3 -m uvicorn app:app --host 127.0.0.1 --port 8765"""
from tracker.web import app  # noqa: F401
