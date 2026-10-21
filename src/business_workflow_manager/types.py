"""Shared enums and type definitions."""

from enum import StrEnum, unique


@unique
class Language(StrEnum):
    DE = "de"
    FR = "fr"
    IT = "it"


@unique
class ItemType(StrEnum):
    WORKFLOW = "workflow"
    TASK = "task"
    FORM = "form"
    DOCUMENT = "document"


@unique
class NodeType(StrEnum):
    WORKFLOW = "workflow"
    TASK = "task"
    FORM = "form"
    DOCUMENT = "document"
    NOTE = "note"


@unique
class NodeStatus(StrEnum):
    STARTED = "started"
    FINISHED = "finished"
    SKIPPED = "skipped"
    INACTIVE = "inactive"


@unique
class NextStepUnavailableReason(StrEnum):
    """Why next steps are (partly) unavailable for a node.

    The definition order is the display priority: if several reasons apply,
    the first one is the most relevant.
    """

    TASK_CLOSED = "TASK_CLOSED"
    """The parent task of a step or the workflow is finished."""
    END_OF_WORKFLOW = "END_OF_WORKFLOW"
    """A valid link points to ``END`` or the task has no links."""
    STEP_NOT_CLOSED = "STEP_NOT_CLOSED"
    """The current step or task is not finished yet, or a step/link condition
    was not met (e.g. a form selection is missing)."""
    TASK_WITHOUT_WORKFLOW = "TASK_WITHOUT_WORKFLOW"
    """The node is not part of a configured workflow."""
