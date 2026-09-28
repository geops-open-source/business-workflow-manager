"""Public API data transfer objects (immutable dataclasses)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Self, TypedDict

from .models import DocumentNode, FormNode, Node, NoteNode, TaskNode, WorkflowNode
from .types import ItemType, NodeStatus, NodeType


class _CommonKwargs(TypedDict):
    wf_node_id: int
    title: str
    type: NodeType
    status: NodeStatus
    version: int | None
    entity_id: str
    started_at: datetime
    finished_at: datetime | None
    deadline: datetime | None
    note: str | None
    url: str | None
    is_public: bool
    is_moveable: bool
    is_readonly: bool
    parent_id: int | None
    next_node_id: int | None
    wf_config_id: int | None
    created_by: str | None
    updated_by: str | None
    created_at: datetime | None
    updated_at: datetime | None


def _common_kwargs(node: Node) -> _CommonKwargs:
    """Extract common field values from an ORM Node instance."""
    return _CommonKwargs(
        wf_node_id=node.wf_node_id,
        title=node.title,
        type=node.type,
        status=node.status,
        version=node.version,
        entity_id=node.entity_id,
        started_at=node.started_at,
        finished_at=node.finished_at,
        deadline=node.deadline,
        note=node.note,
        url=node.url,
        is_public=node.is_public,
        is_moveable=node.is_moveable,
        is_readonly=node.is_readonly,
        parent_id=node.parent_id,
        next_node_id=node.next_node_id,
        wf_config_id=node.wf_config_id,
        created_by=node.created_by,
        updated_by=node.updated_by,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


@dataclass(frozen=True)
class NodeInfo:
    """Read-only representation of a workflow execution node."""

    wf_node_id: int
    title: str
    type: NodeType
    status: NodeStatus
    version: int | None
    entity_id: str
    started_at: datetime
    finished_at: datetime | None
    deadline: datetime | None
    note: str | None
    url: str | None
    is_public: bool
    is_moveable: bool
    is_readonly: bool
    parent_id: int | None
    next_node_id: int | None
    wf_config_id: int | None
    created_by: str | None
    updated_by: str | None
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_model(cls, node: Node) -> "NodeInfo":
        """Convert an ORM Node instance to the appropriate NodeInfo subclass."""
        match node:
            case WorkflowNode():
                return WorkflowNodeInfo.from_model(node)
            case TaskNode():
                return TaskNodeInfo.from_model(node)
            case FormNode():
                return FormNodeInfo.from_model(node)
            case DocumentNode():
                return DocumentNodeInfo.from_model(node)
            case NoteNode():
                return NoteNodeInfo.from_model(node)
            case _:
                return cls(**_common_kwargs(node))


@dataclass(frozen=True)
class WorkflowNodeInfo(NodeInfo):
    """Read-only representation of a workflow node."""

    @classmethod
    def from_model(cls, node: WorkflowNode) -> Self:  # type: ignore[override]
        return cls(**_common_kwargs(node))


@dataclass(frozen=True)
class TaskNodeInfo(NodeInfo):
    """Read-only representation of a task node."""

    @classmethod
    def from_model(cls, node: TaskNode) -> Self:  # type: ignore[override]
        return cls(**_common_kwargs(node))


@dataclass(frozen=True)
class FormNodeInfo(NodeInfo):
    """Read-only representation of a form node."""

    form_config: dict[str, Any] = field(repr=False, default_factory=dict[str, Any])
    form_data: dict[str, Any] = field(repr=False, default_factory=dict[str, Any])

    @classmethod
    def from_model(cls, node: FormNode) -> Self:  # type: ignore[override]
        return cls(
            **_common_kwargs(node),
            form_config=dict(node.form_config) if node.form_config else {},
            form_data=dict(node.form_data) if node.form_data else {},
        )


@dataclass(frozen=True)
class DocumentNodeInfo(NodeInfo):
    """Read-only representation of a document node."""

    document_ref: str | None = None

    @classmethod
    def from_model(cls, node: DocumentNode) -> Self:  # type: ignore[override]
        return cls(**_common_kwargs(node), document_ref=node.document_ref)


@dataclass(frozen=True)
class NoteNodeInfo(NodeInfo):
    """Read-only representation of a note node."""

    @classmethod
    def from_model(cls, node: NoteNode) -> Self:  # type: ignore[override]
        return cls(**_common_kwargs(node))


@dataclass(frozen=True)
class StepOption:
    """A valid next step that can be passed to start_next_step."""

    wf_config_id: int
    title: str
    type: ItemType
    name: str
    is_optional: bool
