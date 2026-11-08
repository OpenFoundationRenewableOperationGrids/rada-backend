"""
RADA bridge: reads live values from OpenEMS Backends through their
Backend-to-Backend (B2B) websocket and posts them to the RADA API.

It runs as its own process (`python -m bridge`), separate from the API,
from the same Docker image.
"""
