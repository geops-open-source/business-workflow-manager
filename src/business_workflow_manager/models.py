from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import Enum, ForeignKey, MetaData
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    attribute_keyed_dict,
    mapped_column,
    relationship,
)
from sqlalchemy.sql import func

from .types import ItemType, Language, NodeStatus, NodeType

# Utilities


def get_enum_values(cls: type[StrEnum]) -> list[str]:
    """Helper to store Enum members in sqlalchemy by value instead of by name."""
    return [e.value for e in cls]


Timestamp = Annotated[
    datetime,
    mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
]
Lang = Annotated[
    Language,
    mapped_column(Enum(Language, native_enum=False, values_callable=get_enum_values)),
]
Jsonb = Annotated[Any, mapped_column(JSONB)]


class Base(MappedAsDataclass, DeclarativeBase):
    """Base class for all workflow ORM models, sub-classes are dataclasses."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "chk_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(referred_table_name)s_%(table_name)s_%(column_0_name)s",
            "pk": "pk_%(table_name)s_%(column_0_name)s",
        }
    )


class AuditMixin(MappedAsDataclass):
    """Mixin adding audit timestamp columns."""

    created_at: Mapped[datetime | None] = mapped_column(
        init=False, default_factory=datetime.now
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        init=False, default=None, onupdate=datetime.now
    )
    created_by: Mapped[str | None] = mapped_column(init=False, default=None)
    updated_by: Mapped[str | None] = mapped_column(init=False, default=None)


_ItemType = Annotated[
    ItemType,
    mapped_column(Enum(ItemType, native_enum=False, values_callable=get_enum_values)),
]
_type = type
NodeT = TypeVar("NodeT", bound="Any")


class WorkflowConfig(Base, kw_only=True):
    __tablename__ = "wf_config"
    __mapper_args__ = {
        "polymorphic_on": "type",
        "polymorphic_abstract": True,
    }
    wf_config_id: Mapped[int] = mapped_column(primary_key=True, init=False)
    key: Mapped[UUID] = mapped_column(default_factory=uuid4)
    workflow_id: Mapped[int | None]
    title: Mapped[str]
    type: Mapped[_ItemType] = mapped_column(init=False)
    version: Mapped[int]
    time_period: Mapped[int | None] = mapped_column(default=None)
    triggers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=None)

    def _create_node(self, node_cls: _type["NodeT"], entity_id: str) -> "NodeT":
        now = datetime.now()
        deadline = (
            None if self.time_period is None else now + timedelta(days=self.time_period)
        )
        return node_cls(
            title=self.title,
            version=self.version,
            status=NodeStatus.STARTED,
            started_at=now,
            deadline=deadline,
            config=self,
            entity_id=entity_id,
        )


class Workflow(WorkflowConfig):
    __mapper_args__ = {
        "polymorphic_identity": ItemType.WORKFLOW,
        "polymorphic_load": "inline",
    }
    start_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("wf_config.wf_config_id"),
        init=False,
        nullable=True,
    )
    min_per_entity: Mapped[int] = mapped_column(nullable=True)
    max_per_entity: Mapped[int | None] = mapped_column(nullable=True)
    start_task: Mapped["Task"] = relationship(
        "Task",
        primaryjoin="remote(Task.wf_config_id) == foreign(Workflow.start_task_id)",
        init=False,
        viewonly=True,
        repr=False,
    )
    tasks: Mapped[dict[str, "Task"]] = relationship(
        "Task",
        collection_class=attribute_keyed_dict("name"),
        primaryjoin="remote(foreign(Task.workflow_id)) == Workflow.wf_config_id",
        init=False,
        viewonly=True,
        order_by="Task.wf_config_id",
        repr=False,
    )

    def create_node(self, entity_id: str) -> "WorkflowNode":
        return self._create_node(WorkflowNode, entity_id)


class Task(WorkflowConfig):
    __mapper_args__ = {
        "polymorphic_identity": ItemType.TASK,
        "polymorphic_load": "inline",
    }
    name: Mapped[str] = mapped_column(nullable=True, use_existing_column=True)
    links: Mapped[list["WorkflowLink"]] = relationship(
        "WorkflowLink",
        primaryjoin="remote(foreign(WorkflowLink.task_id)) == Task.wf_config_id",
        init=False,
        viewonly=True,
    )
    steps: Mapped[list["WorkflowStep"]] = relationship(
        "WorkflowStep",
        primaryjoin="remote(foreign(WorkflowStep.task_id)) == Task.wf_config_id",
        init=False,
        viewonly=True,
    )
    is_start_task: Mapped[bool | None] = mapped_column(default=None)

    def create_node(self, entity_id: str) -> "TaskNode":
        return self._create_node(TaskNode, entity_id)


class Form(WorkflowConfig, kw_only=True):
    __mapper_args__ = {
        "polymorphic_identity": ItemType.FORM,
        "polymorphic_load": "inline",
    }
    name: Mapped[str] = mapped_column(nullable=True, use_existing_column=True)
    fields: Mapped[Any] = mapped_column(JSONB, nullable=True, default=None)
    is_optional: Mapped[bool] = mapped_column(nullable=True, use_existing_column=True)

    def create_node(self, entity_id: str) -> "FormNode":
        node = self._create_node(FormNode, entity_id)
        node.form_config = {
            "name": self.name,
            "fields": deepcopy(self.fields),
        }
        return node


class Document(WorkflowConfig):
    __mapper_args__ = {
        "polymorphic_identity": ItemType.DOCUMENT,
        "polymorphic_load": "inline",
    }
    is_optional: Mapped[bool] = mapped_column(nullable=True, use_existing_column=True)

    def create_node(self, entity_id: str) -> "DocumentNode":
        return self._create_node(DocumentNode, entity_id)


# --- link.py ---
class WorkflowLink(Base, kw_only=True):
    __tablename__ = "wf_link"
    task_id: Mapped[int] = mapped_column(
        ForeignKey("wf_config.wf_config_id"), init=False, primary_key=True
    )
    target_task_id: Mapped[int] = mapped_column(
        ForeignKey("wf_config.wf_config_id"), init=False, primary_key=True
    )
    link_no: Mapped[int]
    condition: Mapped[Any] = mapped_column(JSONB, default=None)
    task: Mapped[Task] = relationship(Task, foreign_keys=[task_id], repr=False)
    target: Mapped[Task] = relationship(Task, foreign_keys=[target_task_id], repr=False)


class WorkflowStep(Base, kw_only=True):
    __tablename__ = "wf_step"
    task_id: Mapped[int] = mapped_column(
        ForeignKey("wf_config.wf_config_id"), init=False, primary_key=True
    )
    step_id: Mapped[int] = mapped_column(
        ForeignKey("wf_config.wf_config_id"), init=False, primary_key=True
    )
    step_no: Mapped[int]
    condition: Mapped[Any] = mapped_column(JSONB, default=None)
    task: Mapped[Task] = relationship(Task, foreign_keys=[task_id], repr=False)
    step: Mapped[Form | Document] = relationship(
        WorkflowConfig, foreign_keys=[step_id], repr=False
    )


# --- node.py ---
_NodeType = Annotated[
    NodeType,
    mapped_column(Enum(NodeType, native_enum=False, values_callable=get_enum_values)),
]
_NodeStatus = Annotated[
    NodeStatus,
    mapped_column(Enum(NodeStatus, native_enum=False, values_callable=get_enum_values)),
]


class Node(Base, AuditMixin, kw_only=True):
    __tablename__ = "wf_node"
    __mapper_args__ = {
        "polymorphic_on": "type",
        "polymorphic_abstract": True,
    }
    wf_node_id: Mapped[int] = mapped_column(primary_key=True, init=False)
    title: Mapped[str]
    type: Mapped[_NodeType] = mapped_column(init=False)
    wf_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("wf_config.wf_config_id"), init=False
    )
    version: Mapped[int | None]
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("wf_node.wf_node_id"), init=False
    )
    next_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("wf_node.wf_node_id", ondelete="SET NULL"), init=False
    )
    is_moveable: Mapped[bool] = mapped_column(default=False)
    status: Mapped[_NodeStatus]
    started_at: Mapped[datetime] = mapped_column(default_factory=datetime.now)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    deadline: Mapped[datetime | None] = mapped_column(default=None)
    entity_id: Mapped[str]
    note: Mapped[str | None] = mapped_column(default=None)
    url: Mapped[str | None] = mapped_column(default=None)
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, init=False, default_factory=dict
    )
    is_public: Mapped[bool] = mapped_column(default=False)
    events_triggered: Mapped[bool] = mapped_column(default=False)
    config: Mapped[WorkflowConfig | None] = relationship(WorkflowConfig)
    parent: Mapped["Node | None"] = relationship(
        "Node",
        back_populates="children",
        foreign_keys=[parent_id],
        remote_side="Node.wf_node_id",
        default=None,
        repr=False,
    )
    children: Mapped[list["Node"]] = relationship(
        "Node",
        back_populates="parent",
        foreign_keys=[parent_id],
        cascade="all, delete-orphan",
        default_factory=list,
        repr=False,
    )
    next_node: Mapped["Node | None"] = relationship(
        "Node",
        remote_side="Node.wf_node_id",
        foreign_keys=[next_node_id],
        post_update=True,
        default=None,
        repr=False,
    )

    @property
    def is_readonly(self) -> bool:
        return isinstance(self, FormNode | DocumentNode) and self.next_node is not None


class WorkflowNode(Node):
    __mapper_args__ = {
        "polymorphic_identity": NodeType.WORKFLOW,
        "polymorphic_load": "inline",
    }


class TaskNode(Node):
    __mapper_args__ = {
        "polymorphic_identity": NodeType.TASK,
        "polymorphic_load": "inline",
    }


class FormNode(Node):
    __mapper_args__ = {
        "polymorphic_identity": NodeType.FORM,
        "polymorphic_load": "inline",
    }
    form_config: Mapped[Any] = mapped_column(JSONB, nullable=True, default_factory=dict)
    form_data: Mapped[Any] = mapped_column(JSONB, nullable=True, default_factory=dict)


class DocumentNode(Node):
    __mapper_args__ = {
        "polymorphic_identity": NodeType.DOCUMENT,
        "polymorphic_load": "inline",
    }
    document_ref: Mapped[str | None] = mapped_column(nullable=True, default=None)


class NoteNode(Node):
    __mapper_args__ = {
        "polymorphic_identity": NodeType.NOTE,
        "polymorphic_load": "inline",
    }


class Translation(Base, AuditMixin):
    __tablename__ = "translations"
    key: Mapped[str] = mapped_column("msgid", primary_key=True)
    value: Mapped[str] = mapped_column("msgstr")
    locale: Mapped[Lang] = mapped_column(primary_key=True)


def to_formatted_dict(translations: Sequence[Translation]) -> dict[str, str]:
    """Convert sequence of translations to dict with keys and values."""
    return {t.key: t.value for t in translations}
