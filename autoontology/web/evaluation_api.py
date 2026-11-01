# SPDX-License-Identifier: MIT
"""No-store, bounded frozen-input evaluation; no server-host file selector."""

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from autoontology.evaluation import MAX_BYTES, Comparison
from autoontology.security.policy import AccessError


def install(app, api):
    router = APIRouter(prefix="/api/evaluation")

    @router.get("/sample")
    def sample():
        return JSONResponse(
            Comparison(api.store).sample(), headers={"Cache-Control": "private, no-store"}
        )

    @router.post("/compare")
    async def compare(request: Request):
        service = Comparison(api.store)
        service.gate()
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BYTES:
                raise AccessError(
                    "Comparison input exceeds two MiB.", "comparison_input_invalid", 413
                )
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except (ValueError, RecursionError):
            raise AccessError(
                "Use a valid JSON comparison input.", "comparison_input_invalid", 400
            ) from None
        result = await run_in_threadpool(service.compare, payload)
        return JSONResponse(result, headers={"Cache-Control": "private, no-store"})

    app.include_router(router)
