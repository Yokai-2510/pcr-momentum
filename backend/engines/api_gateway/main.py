"""FastAPI app construction for Phase 9 API Gateway."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from engines.api_gateway.errors import register_exception_handlers
from engines.api_gateway.middleware.rate_limit_middleware import RateLimitMiddleware
from engines.api_gateway.rest import (
    auth,
    capital,
    commands,
    configs,
    credentials,
    delta_pcr,
    health,
    pnl,
    positions,
    strategy,
)
from engines.api_gateway.ws_endpoints import router as ws_router
from log_setup import configure
from state import keys as K
from state import postgres_client, redis_client
from state.config_loader import get_settings


def create_app(*, init_resources: bool = True) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure(engine_name="api_gateway")
        if init_resources:
            redis_client.init_pools()
            app.state.redis = redis_client.get_redis()
            app.state.pg_pool = await postgres_client.init_pool(settings.database_url)
            await app.state.redis.set(K.system_flag_engine_up("api_gateway"), "true")
        try:
            yield
        finally:
            if init_resources:
                await app.state.redis.set(K.system_flag_engine_up("api_gateway"), "false")
                await app.state.redis.set(K.system_flag_engine_exited("api_gateway"), "true")
                await postgres_client.close_pool()
                await redis_client.close_pools()

    app = FastAPI(
        title="Premium-Diff Bot API",
        version="0.1.0",
        default_response_class=JSONResponse,
        lifespan=lifespan,
    )
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "https://upstoxapipcrmomentum.com"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    for router in (
        auth.router,
        credentials.router,
        configs.router,
        strategy.router,
        positions.router,
        pnl.router,
        delta_pcr.router,
        health.router,
        commands.router,
        capital.router,
        ws_router,
    ):
        app.include_router(router)

    return app


app = create_app()
