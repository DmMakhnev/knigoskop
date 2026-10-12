import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.api.v1.ai import router as ai_router
from app.api.v1.auth import router
from app.api.v1.books import router as books_router
from app.api.v1.library import router as library_router
from app.api.v1.reader import router as reader_router
from app.api.v1.summaries import router as summaries_router
from app.core.errors import AppError
from app.core.upload_limit import UploadLimitMiddleware

app = FastAPI(title="Knigoskop", version="0.1.0")
app.include_router(router)
app.include_router(library_router)
app.include_router(books_router)
app.include_router(reader_router)
app.include_router(ai_router)
app.include_router(summaries_router)


app.add_middleware(UploadLimitMiddleware)


@app.middleware("http")
async def request_id(request: Request, call_next):
    request.state.request_id = str(uuid4())
    try:
        response = await call_next(request)
    except Exception as exc:
        logging.getLogger("knigoskop").error(
            "Request failed request_id=%s error_type=%s",
            request.state.request_id,
            type(exc).__name__,
        )
        response = JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "Внутренняя ошибка сервера",
                "request_id": request.state.request_id,
            },
        )
    response.headers["X-Request-ID"] = request.state.request_id
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": "file_too_large"
            if exc.status_code == 413
            else f"http_{exc.status_code}",
            "message": "Файл превышает допустимый размер"
            if exc.status_code == 413
            else "Запрошенный ресурс недоступен",
            "request_id": request.state.request_id,
        },
    )


class HealthResponse(BaseModel):
    status: str
    service: str


@app.get("/health", response_model=HealthResponse, tags=["operations"])
def health():
    # Liveness only; dependency readiness is checked by Compose.
    return HealthResponse(status="ok", service="knigoskop-api")


@app.exception_handler(AppError)
async def app_error(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status,
        content={
            "code": exc.code,
            "message": exc.message,
            "request_id": request.state.request_id,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Never echo passwords or email tokens from validation input.
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "Проверьте заполнение полей",
            "request_id": request.state.request_id,
        },
    )
