"""Shared sampler and a dependency-free browser interface."""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager, suppress
import json
import logging
from pathlib import Path
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .metrics import MetricsCollector, history_entry


LOGGER = logging.getLogger(__name__)
STATIC_DIRECTORY = Path(__file__).resolve().parent.parent / "static"
INTERVAL_SECONDS = 1.0


class MonitorState:
    def __init__(self) -> None:
        self.collector = MetricsCollector()
        self.history: deque[dict[str, Any]] = deque(maxlen=60)
        self.payload: dict[str, Any] | None = None
        self.version = 0
        self.condition = asyncio.Condition()
        self.task: asyncio.Task[None] | None = None
        self.last_collection_monotonic: float | None = None
        self.last_error: str | None = None

    async def collect(self) -> None:
        try:
            snapshot = await asyncio.to_thread(self.collector.collect)
        except Exception as error:
            self.last_error = type(error).__name__
            LOGGER.exception("Unable to collect a server monitoring sample")
            return
        self.history.append(history_entry(snapshot))
        while self.history and self.history[0]["timestamp"] < snapshot["timestamp"] - 60:
            self.history.popleft()
        async with self.condition:
            self.payload = {"snapshot": snapshot, "history": list(self.history), "interval_seconds": INTERVAL_SECONDS}
            self.version += 1
            self.last_collection_monotonic = time.monotonic()
            self.last_error = None
            self.condition.notify_all()

    async def run(self) -> None:
        while True:
            started = time.monotonic()
            await self.collect()
            await asyncio.sleep(max(0.01, INTERVAL_SECONDS - (time.monotonic() - started)))


@asynccontextmanager
async def lifespan(application: FastAPI):
    state = MonitorState()
    application.state.monitor = state
    await state.collect()
    # Wait one interval after the startup sample before collecting again.
    async def sample_loop() -> None:
        await asyncio.sleep(INTERVAL_SECONDS)
        await state.run()

    state.task = asyncio.create_task(sample_loop(), name="server-metrics-sampler")
    try:
        yield
    finally:
        state.task.cancel()
        with suppress(asyncio.CancelledError):
            await state.task
        close = getattr(state.collector, "close", None)
        if callable(close):
            await asyncio.to_thread(close)


app = FastAPI(title="Server Monitor", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(STATIC_DIRECTORY), check_dir=False), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIRECTORY / "index.html")


@app.get("/api/snapshot")
async def snapshot(request: Request) -> JSONResponse:
    state: MonitorState = request.app.state.monitor
    if state.payload is None:
        return JSONResponse({"detail": "The first monitoring sample is not ready."}, status_code=503)
    return JSONResponse(state.payload, headers={"Cache-Control": "no-store"})


@app.get("/api/health")
async def health(request: Request) -> JSONResponse:
    state: MonitorState = request.app.state.monitor
    age = time.monotonic() - state.last_collection_monotonic if state.last_collection_monotonic is not None else None
    ready = state.payload is not None and state.last_error is None and age is not None and age < 10
    return JSONResponse(
        {"status": "ok" if ready else "unavailable", "ready": ready, "sample_age_seconds": round(age, 2) if age is not None else None},
        status_code=200 if ready else 503,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    state: MonitorState = request.app.state.monitor

    async def stream():
        version = -1
        while not await request.is_disconnected():
            heartbeat = False
            async with state.condition:
                if state.version == version or state.payload is None:
                    try:
                        await asyncio.wait_for(state.condition.wait_for(lambda: state.version != version and state.payload is not None), timeout=15)
                    except asyncio.TimeoutError:
                        heartbeat = True
                payload = state.payload
                current_version = state.version
            if heartbeat:
                yield ": heartbeat\n\n"
            elif payload is not None:
                version = current_version
                yield f"id: {version}\ndata: {json.dumps(payload, separators=(',', ':'), allow_nan=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
