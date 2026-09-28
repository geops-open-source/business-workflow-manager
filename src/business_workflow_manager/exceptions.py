"""Workflow exception hierarchy."""

from dataclasses import dataclass, field

from .dto import NodeInfo


class WorkflowException(Exception):
    pass


class WorkflowNotFound(WorkflowException):
    pass


class WorkflowExists(WorkflowException):
    pass


class InvalidWorkflowStep(WorkflowException):
    pass


class EventHandlerNotRegistered(WorkflowException):
    pass


@dataclass
class CascadeConflict(WorkflowException):
    """Raised when an update would create inconsistencies with child nodes.

    Attributes:
        deadline_conflicts: Child nodes whose deadline exceeds the new deadline.
        status_conflicts: Child nodes that are still started/inactive
            when the parent is being set to FINISHED.
        pending_trigger_conflicts: Child task nodes that are FINISHED but whose
            configured triggers have not yet been fired (``events_triggered`` is
            still ``False``). A parent node cannot be closed while children have
            unfired triggers; use ``cascade=True`` to fire them automatically.
    """

    deadline_conflicts: list[NodeInfo] = field(default_factory=list[NodeInfo])
    status_conflicts: list[NodeInfo] = field(default_factory=list[NodeInfo])
    pending_trigger_conflicts: list[NodeInfo] = field(default_factory=list[NodeInfo])

    def __str__(self) -> str:
        parts: list[str] = []
        if self.deadline_conflicts:
            ids = [n.wf_node_id for n in self.deadline_conflicts]
            parts.append(f"Children with later deadlines: {ids}")
        if self.status_conflicts:
            ids = [n.wf_node_id for n in self.status_conflicts]
            parts.append(f"Children still open/inactive: {ids}")
        if self.pending_trigger_conflicts:
            ids = [n.wf_node_id for n in self.pending_trigger_conflicts]
            parts.append(f"Children with pending triggers: {ids}")
        return "; ".join(parts)
