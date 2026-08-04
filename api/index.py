"""Vercel Python function entrypoint.

Vercel's Python runtime auto-detects an ASGI `app` exported from a file under
/api and serves it directly - no WSGI adapter needed for FastAPI. The actual
app and all its routes live in backend/main.py; this file only exists to give
Vercel something to find. See vercel.json for the rewrite that sends every
request path here (FastAPI's own router does the rest).
"""

from backend.main import app  # noqa: F401
