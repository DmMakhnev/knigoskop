from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    application = FastAPI(title=settings.app_name, version="0.1.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["Content-Type"],
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        """Liveness only: does not assert database or broker readiness."""
        return {"status": "ok"}

    @application.get("/api/v1/status")
    def status() -> dict[str, object]:
        return {
            "name": settings.app_name,
            "stage": "scaffold",
            "capabilities": {"ai": False, "tts": False},
        }

    return application


app = create_app()
