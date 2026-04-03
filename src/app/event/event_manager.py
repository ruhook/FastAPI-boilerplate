import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any


logger = logging.getLogger(__name__)


class AsyncEventManager:
    def __init__(self, concurrency: int = 3, stats_interval: int = 30) -> None:
        self._handlers: dict[str, list[Callable[[dict[str, Any]], Awaitable[None] | None]]] = {}
        self._stats: dict[str, dict[str, Any]] = {}
        self._last_func: str | None = None
        self._semaphore = asyncio.Semaphore(concurrency)
        self._stats_interval = stats_interval
        self._stop_event = asyncio.Event()
        self._stats_task: asyncio.Task[None] | None = None
        self._mq_client: Any = None

    def set_mq_client(self, mq_client: Any) -> None:
        self._mq_client = mq_client

    def register_handler(self, ev_type: Enum | str, handler: Callable[[dict[str, Any]], Awaitable[None] | None]) -> None:
        type_name = ev_type.value if isinstance(ev_type, Enum) else str(ev_type)
        self._handlers.setdefault(type_name, []).append(handler)
        logger.info("Registered event handler", extra={"event_type": type_name, "handler": handler.__name__})

    async def receive(self, msg: dict[str, Any]) -> None:
        async with self._semaphore:
            await self._receive(msg)

    async def _receive(self, msg: dict[str, Any]) -> None:
        ev_type = msg.get("type")
        if not ev_type:
            logger.warning("Event message missing type", extra={"message": msg})
            return

        handlers = self._handlers.get(ev_type)
        if not handlers:
            logger.warning("No handler registered for event", extra={"event_type": ev_type})
            return

        for handler in handlers:
            func_name = handler.__name__
            self._last_func = func_name
            start_time = time.time() * 1000

            try:
                result = handler(msg)
                if asyncio.iscoroutine(result):
                    await result
                duration = int(time.time() * 1000 - start_time)
                self._update_stats(func_name, duration)
            except Exception:
                logger.exception("Process event error", extra={"event_type": ev_type, "handler": func_name})

    def _update_stats(self, func_name: str, duration: int) -> None:
        stats = self._stats.setdefault(func_name, {"count": 0, "time": 0, "max_time": 0})
        stats["count"] += 1
        stats["time"] += duration
        if duration > stats["max_time"]:
            stats["max_time"] = duration

    async def _log_stats_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._stats_interval)
            except asyncio.TimeoutError:
                pass

            if self._stop_event.is_set():
                break

            stats = self._stats
            self._stats = {}

            if not stats:
                logger.info("No event processed", extra={"last_callback": self._last_func})
                continue

            for func_name, data in stats.items():
                count = data["count"]
                average = 0 if count == 0 else data["time"] / count
                logger.info(
                    "Event handler stats",
                    extra={
                        "handler": func_name,
                        "count": count,
                        "average_ms": round(average, 2),
                        "max_ms": data["max_time"],
                    },
                )

    def _start_stats_logging(self) -> None:
        if self._stats_task is None or self._stats_task.done():
            self._stop_event.clear()
            self._stats_task = asyncio.create_task(self._log_stats_loop())

    def _stop_stats_logging(self) -> None:
        self._stop_event.set()
        if self._stats_task:
            self._stats_task.cancel()
            self._stats_task = None

    async def run(self) -> None:
        if self._mq_client is None:
            raise ValueError("MQ client not set, call set_mq_client() first.")

        loop = asyncio.get_running_loop()

        def signal_handler() -> None:
            logger.info("Received shutdown signal")
            asyncio.create_task(self._mq_client.stop())

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, signal_handler)

        logger.info("Starting event consumer", extra={"registered_event_types": self.get_registered_types()})
        self._start_stats_logging()

        try:
            await self._mq_client.start()
        except asyncio.CancelledError:
            logger.info("Event consumer cancelled")
        finally:
            self._stop_stats_logging()
            logger.info("Event consumer stopped")

    def get_registered_types(self) -> list[str]:
        return list(self._handlers.keys())
