"""Enforce the request budget before multipart spooling, including chunked uploads."""

from uuid import uuid4

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app.core.config import settings


class UploadLimitExceeded(HTTPException):
    def __init__(self):
        super().__init__(413, "Upload budget exceeded")


class UploadLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/v1/books/upload":
            return await self.app(scope, receive, send)
        budget = settings.upload_max_bytes + 65536
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > budget:
                    raise UploadLimitExceeded()
            return message

        async def too_large():
            response = JSONResponse(
                status_code=413,
                content={
                    "code": "file_too_large",
                    "message": "Файл превышает допустимый размер",
                    "request_id": scope.get("state", {}).get(
                        "request_id", str(uuid4())
                    ),
                },
            )
            await response(scope, receive, send)

        headers = dict(scope.get("headers", []))
        size = headers.get(b"content-length", b"0")
        if size.isdigit() and int(size) > budget:
            return await too_large()
        try:
            return await self.app(scope, bounded_receive, send)
        except UploadLimitExceeded:
            return await too_large()
