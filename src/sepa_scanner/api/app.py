from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sepa_scanner.api.routes import ohlcv, scanner, analysis, backtest


def create_app() -> FastAPI:
    app = FastAPI(title="Multibagger API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(ohlcv.router)
    app.include_router(scanner.router)
    app.include_router(analysis.router)
    app.include_router(backtest.router, prefix="/api")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
