import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .config import settings
from .api import router, public_router
from .core import daily_sync_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    sync_task = asyncio.create_task(daily_sync_loop()) if settings.enable_daily_sync else None
    yield
    if sync_task is not None:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    if settings.interface_mode != "api":
        raise RuntimeError("INTERFACE_MODE must be 'api' to run FastAPI.")

    app = FastAPI(
        title="Biwenger Agent API",
        version="0.1.1",
        description="Multitenant fixed-price squad optimizer for Biwenger World Cup 2026.",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    app.include_router(router)
    app.include_router(public_router)

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "biwenger-agent-api"}

    return app


app = create_app()
