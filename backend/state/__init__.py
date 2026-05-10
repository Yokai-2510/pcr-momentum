"""Shared state-layer primitives — the only modules engines may share.

Public surface:
    - keys: canonical Redis key namespace (per Schema.md §1)
    - streams: stream + pub/sub channel constants (per Schema.md §1)
    - schemas: pydantic models for every cross-engine payload (per Schema.md §5)
    - redis_client: async + sync Redis pool factories
    - postgres_client: asyncpg pool factory + transaction helpers
    - config_loader: .env + config_settings table loader
"""
