"""API Gateway engine - FastAPI HTTP + WebSocket surface.

The gateway is intentionally thin: it authenticates the operator, validates
requests, then reads/writes Redis/Postgres or calls the broker facade. Trading
decisions remain inside the cyclic engines.
"""
