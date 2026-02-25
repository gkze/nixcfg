"""Public update UI API.

This module is a compatibility facade over focused UI modules:
- ``ui_state`` for state models and status mapping
- ``ui_render`` for terminal/non-terminal rendering
- ``ui_consumer`` for queued event processing
"""

from lib.update.ui_consumer import ConsumeEventsOptions, EventConsumer, consume_events
from lib.update.ui_render import Renderer
from lib.update.ui_state import (
    ItemMeta,
    ItemState,
    OperationKind,
    OperationState,
    SummaryStatus,
)

__all__ = [
    "ConsumeEventsOptions",
    "EventConsumer",
    "ItemMeta",
    "ItemState",
    "OperationKind",
    "OperationState",
    "Renderer",
    "SummaryStatus",
    "consume_events",
]
