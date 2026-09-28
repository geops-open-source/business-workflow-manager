"""Event system for business-workflow-manager.

Users subclass :class:`Event` to define domain-specific events, then
dispatch them via :meth:`WorkflowManager.handle_event`.  Each registered
handler receives the current SQLAlchemy session, the event instance, and
the affected node.

Example::

    from business_workflow_manager.events import Event

    class InvoiceApproved(Event):
        invoice_id: int

        def __init__(self, invoice_id: int) -> None:
            self.invoice_id = invoice_id

    def on_invoice_approved(session: Session, event: Event, node: Node, context) -> None:
        ...  # use session to query/update the database; ``context`` is opaque
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias, TypeVar

from sqlalchemy.orm import Session

from business_workflow_manager.models import Node


@dataclass
class Event:
    """Base class for user-defined events.

    Subclass this to create concrete event types.  The library never
    instantiates ``Event`` directly — it only passes subclass instances
    to registered handlers.
    """


E = TypeVar("E", bound=Event)

EventHandler: TypeAlias = Callable[[Session, E, Node, Any], None]
"""Signature for event handler callables: ``(session, event, node, context) -> None``."""

EventHandlerMap: TypeAlias = dict[type[Event], list[EventHandler[Event]]]
"""Event handlers keyed by the event type they handle."""
