import logging
from typing import Any


logger = logging.getLogger(__name__)


async def handle_example_event(msg: dict[str, Any]) -> None:
    logger.info("Processing EXAMPLE_EVENT", extra={"event_message": msg})
