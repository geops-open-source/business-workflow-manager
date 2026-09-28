"""WorkflowManager — session-bound public API for the workflow engine."""

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from types import TracebackType
from typing import Any, TextIO, cast
from uuid import UUID

from sqlalchemy.orm import Session

from . import config as cfg
from .dto import (
    DocumentNodeInfo,
    FormNodeInfo,
    NodeInfo,
    NoteNodeInfo,
    StepOption,
    TaskNodeInfo,
    WorkflowNodeInfo,
)
from .engine import (
    Engine,
    get_parent_of_type,
)
from .events import E, Event, EventHandler, EventHandlerMap
from .exceptions import (
    CascadeConflict,
    EventHandlerNotRegistered,
    WorkflowException,
    WorkflowNotFound,
)
from .loader import (
    dump_workflow,
    dump_workflow_to_file,
    load_workflow,
    load_workflow_from_file,
)
from .models import (
    DocumentNode,
    FormNode,
    Node,
    NoteNode,
    TaskNode,
    Workflow,
    WorkflowConfig,
    WorkflowNode,
)
from .types import NodeStatus

VALID_ENTITY_DATA_KEYS: list[str] = ["form"]


class _Unset(Enum):
    UNSET = "UNSET"


UNSET = _Unset.UNSET


def _config_to_step_option(config: Any) -> StepOption:
    """Convert an ORM WorkflowConfig to a StepOption dataclass."""
    return StepOption(
        wf_config_id=config.wf_config_id,
        title=config.title or "",
        type=config.type,
        name=getattr(config, "name", None) or "",
        is_optional=getattr(config, "is_optional", False),
    )


@dataclass(frozen=True)
class WorkflowManagerConfig:
    """Static configuration for :class:`WorkflowManager`.

    Create once at application startup and pass to every
    ``WorkflowManager`` instance (or use :func:`manager_factory`
    to bind it).

    Attributes:
        get_entity_data: Callable that receives (session, entity_id) and returns a JSON-serializable dict.
        is_applicable: Optional callable that receives (session, workflow, entity_id)
            and returns whether a new workflow instance may be created for the
            entity. Defaults to the built-in ``max_per_entity`` check.
        entity_data_key: Key under which the result of ``get_entity_data`` is
            made available to jmespath conditions (default: ``"entity"``).
    """

    get_entity_data: Callable[[Session, str], dict[str, Any]] = field(
        default=lambda session, entity_id: {}
    )
    is_applicable: Callable[[Session, Workflow, str], bool] | None = None
    entity_data_key: str = "entity"
    event_handlers: EventHandlerMap = field(
        default_factory=lambda: cast(EventHandlerMap, dict()),
        init=False,
    )

    def __post_init__(self) -> None:
        if self.entity_data_key in VALID_ENTITY_DATA_KEYS:
            raise ValueError(
                f"entity_data_key {self.entity_data_key!r} is reserved. "
                f"Choose a key not in {VALID_ENTITY_DATA_KEYS!r}."
            )


class WorkflowManager:
    """Session-bound API for loading, querying, and progressing workflows.

    Use as a context manager to ensure the session reference is released::

        with WorkflowManager(session, config) as mgr:
            mgr.start_workflow(...)

    Or use :func:`manager_factory` to bind the config once::

        new_manager = manager_factory(config)
        with new_manager(session) as mgr:
            mgr.start_workflow(...)

    Args:
        session: An SQLAlchemy session object.
        config: Workflow manager configuration.
        context: Optional runtime context passed to handlers.
    """

    def __init__(
        self,
        session: Session,
        config: WorkflowManagerConfig | None = None,
        *,
        context: Any = None,
    ) -> None:
        config = config or WorkflowManagerConfig()
        # Make a shallow copy of config to prevent modifications of the
        # original object by WorkflowManager.register_event_handler().
        self._config = replace(config)
        self._session: Session | None = session
        self._context: Any = context
        self._engine: Engine | None = Engine(
            session,
            self._config.get_entity_data,
            is_applicable=self._config.is_applicable,
            entity_data_key=self._config.entity_data_key,
        )

    @property
    def session(self) -> Session:
        """Return the active sesssion, raising if used outside context."""
        if self._session is None:
            raise RuntimeError("WorkflowManager used outside of context")
        return self._session

    @property
    def engine(self) -> Engine:
        """Return the engine wrapping the active session, raising if used outside context."""
        if self._engine is None:
            raise RuntimeError("WorkflowManager used outside of context")
        return self._engine

    def register_event_handler(self, event: type[E], handler: EventHandler[E]):
        """Register *handler* to be invoked when an event of type *event* is dispatched.

        Handlers are invoked in registration order by :meth:`handle_event`.
        Multiple handlers may be registered for the same event type.

        Args:
            event: The event type to listen for.
            handler: Callable invoked with ``(session, event, context)`` when
                a matching event is dispatched.
        """
        self._config.event_handlers.setdefault(event, []).append(
            cast(EventHandler[Event], handler)
        )

    def __enter__(self) -> "WorkflowManager":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._session = None

    def handle_event(
        self,
        event: Event,
        node: Node,
        context: Any | _Unset = UNSET,
    ) -> None:
        """Dispatch *event* to matching registered handlers.

        Handlers registered under an event type receive only matching event
        instances.  Handlers are invoked in registration order.

        Args:
            event: The event instance to dispatch.
            node: The affected node.
            context: Optional per-call override for the runtime context passed
                to handlers. When omitted, the manager-level context (set via
                the constructor or the factory) is used. May be ``None``.
        """
        effective_context = self._context if isinstance(context, _Unset) else context
        handled = False
        for event_type, handlers in self._config.event_handlers.items():
            if not isinstance(event, event_type):
                continue
            for handler in handlers:
                handler(self.session, event, node, effective_context)
                handled = True
        if not handled:
            raise EventHandlerNotRegistered(
                f"No handler registered for event {type(event).__name__!r}"
            )

    def _get_node(self, node_id: int) -> Node:
        """Look up an ORM Node by ID, raising if not found."""
        node = self.session.get(Node, node_id)
        if node is None:
            raise WorkflowNotFound(f"No node with id={node_id}")
        return node

    def _get_config(self, config_id: int) -> WorkflowConfig:
        """Look up an ORM WorkflowConfig by ID, raising if not found."""
        config = self.session.get(WorkflowConfig, config_id)
        if config is None:
            raise WorkflowNotFound(f"No workflow config with id={config_id}")
        return config

    def load_workflow_from_file(self, f: TextIO) -> tuple[Workflow, cfg.Workflow]:
        """Parse a YAML file and load the workflow into the database."""
        return load_workflow_from_file(self.session, f, self._config.event_handlers)

    def load_workflow(self, config: cfg.Workflow) -> Workflow:
        """Load a parsed workflow config into the database."""
        return load_workflow(self.session, config, self._config.event_handlers)

    def dump_workflow(self, workflow: Workflow) -> cfg.Workflow:
        """Export an ORM Workflow back to a pydantic config object."""
        return dump_workflow(self.session, workflow)

    def dump_workflow_to_file(self, key: str | UUID, f: TextIO) -> int:
        """Export a workflow (by UUID key) to a YAML file."""
        return dump_workflow_to_file(self.session, key, f)

    def start_workflow(self, workflow_id: int, entity_id: str) -> WorkflowNodeInfo:
        """Create a new workflow instance for the given entity.

        Looks up the workflow config by ID, checks applicability, creates
        the node, adds it to the session, and flushes.

        Raises:
            WorkflowNotFound: If no workflow config with the given ID exists.
            WorkflowException: If the workflow is not applicable for the entity.
        """

        workflow = self.session.get(Workflow, workflow_id)
        if workflow is None:
            raise WorkflowNotFound(f"No workflow with id={workflow_id}")

        if not self.engine.is_applicable(workflow, entity_id):
            raise WorkflowException(
                f"Workflow {workflow_id} is not applicable for entity {entity_id}"
            )

        node = workflow.create_node(entity_id=entity_id)
        self.session.add(node)
        self.session.flush()
        return WorkflowNodeInfo.from_model(node)

    def get_next_steps(self, node_id: int) -> list[StepOption]:
        """Get valid next steps/tasks for the current node state."""
        node = self._get_node(node_id)
        configs = self.engine.get_next_steps(node)
        return [_config_to_step_option(c) for c in configs]

    def start_next_step(self, node_id: int, step_id: int) -> NodeInfo:
        """Create and link the next execution node.

        Args:
            node_id: ID of the current node.
            step_id: wf_config_id of the step to start.

        Raises:
            WorkflowNotFound: If node or config not found.
            InvalidWorkflowStep: If step_id is not a valid next step.
        """
        node = self._get_node(node_id)
        wf_config = self._get_config(step_id)
        result = self.engine.start_next_step(node, wf_config)
        self.session.flush()
        return NodeInfo.from_model(result)

    def update_workflow_node(
        self,
        node_id: int,
        *,
        title: str | None = None,
        status: NodeStatus | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None | _Unset = UNSET,
        deadline: datetime | None | _Unset = UNSET,
        note: str | None | _Unset = UNSET,
        url: str | None | _Unset = UNSET,
        cascade: bool = False,
    ) -> WorkflowNodeInfo:
        """Update a WorkflowNode's fields with business-rule enforcement.

        Only non-None (non-sentinel) arguments are applied. Status transitions
        have side effects:

        - Setting to STARTED clears finished_at.
        - Setting to INACTIVE clears deadline and finished_at.
        - Setting to FINISHED with cascade=False raises CascadeConflict if
          children are still started/inactive.

        When a deadline is set and cascade=False, raises CascadeConflict if
        any child (recursively) has a later deadline and is not finished.

        Args:
            node_id: ID of the WorkflowNode to update.
            title: New title (or None to leave unchanged).
            status: New status (or None to leave unchanged).
            started_at: New started_at datetime (or None to leave unchanged).
            finished_at: New finished_at datetime (pass None to clear, omit to leave unchanged).
            deadline: New deadline datetime (pass None to clear, omit to leave unchanged).
            note: New note (pass None to clear, omit to leave unchanged).
            cascade: If True, propagate deadline/status changes to children.
                If False (default), raise CascadeConflict when conflicts exist.

        Returns:
            Updated NodeInfo dataclass.

        Raises:
            WorkflowNotFound: If node not found.
            CascadeConflict: If cascade=False and the update conflicts with children.
        """
        node = self._get_node(node_id)
        if not isinstance(node, WorkflowNode):
            raise WorkflowNotFound(f"Node {node_id} is not a WorkflowNode")

        # Apply simple field updates
        if title is not None:
            node.title = title
        if started_at is not None:
            node.started_at = started_at
        if finished_at is not UNSET:
            node.finished_at = finished_at
        if deadline is not UNSET:
            node.deadline = deadline
        if note is not UNSET:
            node.note = note
        if url is not UNSET:
            node.url = url

        # Apply status with side effects
        if status is not None:
            _update_node_status(node, status)

        # Collect all descendants
        descendants: list[Node] = list(node.children)
        for child in descendants:
            descendants.extend(child.children)

        # Check for cascade conflicts
        deadline_conflict_nodes: list[Node] = []
        status_conflict_nodes: list[Node] = []
        pending_trigger_conflict_nodes: list[Node] = []

        if status == NodeStatus.FINISHED:
            pending_trigger_conflict_nodes = [
                c
                for c in descendants
                if isinstance(c, TaskNode)
                and c.config is not None
                and c.config.triggers
                and not c.events_triggered
                and c.status in (NodeStatus.STARTED, NodeStatus.INACTIVE)
            ]

            if pending_trigger_conflict_nodes:
                raise CascadeConflict(
                    deadline_conflicts=[],
                    status_conflicts=[],
                    pending_trigger_conflicts=[
                        NodeInfo.from_model(c) for c in pending_trigger_conflict_nodes
                    ],
                )

            status_conflict_nodes = [
                c
                for c in descendants
                if c.status in (NodeStatus.STARTED, NodeStatus.INACTIVE)
            ]

        if deadline is not UNSET and deadline is not None:
            deadline_conflict_nodes = [
                c
                for c in descendants
                if c.status != NodeStatus.FINISHED
                and c.deadline is not None
                and c.deadline > deadline
            ]

        if (deadline_conflict_nodes or status_conflict_nodes) and not cascade:
            raise CascadeConflict(
                deadline_conflicts=[
                    NodeInfo.from_model(c) for c in deadline_conflict_nodes
                ],
                status_conflicts=[
                    NodeInfo.from_model(c) for c in status_conflict_nodes
                ],
                pending_trigger_conflicts=[],
            )

        # Apply cascade
        for c in deadline_conflict_nodes:
            assert not isinstance(deadline, _Unset)
            c.deadline = deadline

        for c in status_conflict_nodes:
            _update_node_status(c, NodeStatus.FINISHED)
            if finished_at is not UNSET:
                c.finished_at = finished_at

        self.trigger_events(node)

        self.session.flush()
        return WorkflowNodeInfo.from_model(node)

    def update_task_node(
        self,
        node_id: int,
        *,
        title: str | None = None,
        status: NodeStatus | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None | _Unset = UNSET,
        deadline: datetime | None | _Unset = UNSET,
        note: str | None | _Unset = UNSET,
        url: str | None | _Unset = UNSET,
        cascade: bool = False,
    ) -> TaskNodeInfo:
        """Update a TaskNode's fields with business-rule enforcement.

        Only non-sentinel arguments are applied. Status transitions have the
        same side effects as update_workflow_node.

        Cascade conflicts (checked against parent and grandparent):
        - If deadline is set later than a parent's deadline (non-finished parents) → conflict.
        - If status ≠ FINISHED and a parent is already FINISHED → conflict.

        With cascade=True:
        - Parents with earlier deadlines are extended to the new deadline.
        - Finished parents are reopened to the given status.

        Args:
            node_id: ID of the TaskNode to update.
            title: New title (or None to leave unchanged).
            status: New status (or None to leave unchanged).
            started_at: New started_at (or None to leave unchanged).
            finished_at: New finished_at (pass None to clear, omit to leave unchanged).
            deadline: New deadline (pass None to clear, omit to leave unchanged).
            note: New note (pass None to clear, omit to leave unchanged).
            cascade: If True, propagate changes to parents. If False, raise on conflict.

        Returns:
            Updated NodeInfo dataclass.

        Raises:
            WorkflowNotFound: If node not found or not a TaskNode.
            CascadeConflict: If cascade=False and the update conflicts with parents.
        """

        node = self._get_node(node_id)
        if not isinstance(node, TaskNode):
            raise WorkflowNotFound(f"Node {node_id} is not a TaskNode")

        # Apply simple field updates
        if title is not None:
            node.title = title
        if started_at is not None:
            node.started_at = started_at
        if finished_at is not UNSET:
            node.finished_at = finished_at
        if deadline is not UNSET:
            node.deadline = deadline
        if note is not UNSET:
            node.note = note
        if url is not UNSET:
            node.url = url

        # Apply status with side effects
        if status is not None:
            _update_node_status(node, status)

        # Collect parent and grandparent
        ancestors: list[Node] = []
        if parent := node.parent:
            ancestors.append(parent)
            if grandparent := parent.parent:
                ancestors.append(grandparent)

        # Check for cascade conflicts
        deadline_conflict_nodes: list[Node] = []
        status_conflict_nodes: list[Node] = []

        if deadline is not UNSET and deadline is not None:
            deadline_conflict_nodes = [
                n
                for n in ancestors
                if n.status != NodeStatus.FINISHED
                and n.deadline is not None
                and n.deadline < deadline
            ]

        effective_status = status if status is not None else node.status
        if effective_status != NodeStatus.FINISHED:
            status_conflict_nodes = [
                n for n in ancestors if n.status == NodeStatus.FINISHED
            ]

        if (deadline_conflict_nodes or status_conflict_nodes) and not cascade:
            raise CascadeConflict(
                deadline_conflicts=[
                    NodeInfo.from_model(n) for n in deadline_conflict_nodes
                ],
                status_conflicts=[
                    NodeInfo.from_model(n) for n in status_conflict_nodes
                ],
            )

        # Apply cascade to parents
        for n in deadline_conflict_nodes:
            assert not isinstance(deadline, _Unset)
            n.deadline = deadline

        for n in status_conflict_nodes:
            _update_node_status(n, effective_status)
            if effective_status == NodeStatus.STARTED and not n.deadline:
                n.deadline = node.deadline

        self.trigger_events(node)

        self.session.flush()
        return TaskNodeInfo.from_model(node)

    def create_task_node(
        self,
        entity_id: str,
        title: str,
        started_at: datetime,
        *,
        deadline: datetime | None = None,
        note: str | None = None,
        url: str | None = None,
        parent_id: int | None = None,
    ) -> TaskNodeInfo:
        """Create an ad-hoc task node (not from a workflow config).

        The task is created with status=STARTED.

        If parent_id is provided, the method walks up from that node to find
        the nearest WorkflowNode as parent. If no suitable parent is found,
        the task is created at the top level.

        Args:
            entity_id: The entity to attach this task to.
            title: Task title.
            started_at: Start timestamp.
            deadline: Optional deadline.
            note: Optional note text.
            parent_id: Optional reference node ID. The method walks up from this
                node to find the nearest WorkflowNode as parent.

        Returns:
            TaskNodeInfo dataclass for the created node.

        Raises:
            WorkflowNotFound: If parent_id is given but the node doesn't exist.
        """
        parent = None
        if parent_id is not None:
            reference_node = self._get_node(parent_id)
            parent = get_parent_of_type(reference_node, WorkflowNode)

        node = TaskNode(
            title=title,
            status=NodeStatus.STARTED,
            started_at=started_at,
            finished_at=None,
            deadline=deadline,
            entity_id=entity_id,
            version=None,
            config=None,
            note=note,
            url=url,
            parent=parent,
        )

        self.session.add(node)
        self.session.flush()
        return TaskNodeInfo.from_model(node)

    def create_document_node(
        self,
        entity_id: str,
        title: str,
        started_at: datetime,
        document_ref: str,
        *,
        note: str | None = None,
        url: str | None = None,
        is_public: bool = False,
        parent_id: int | None = None,
    ) -> DocumentNodeInfo:
        """Create an ad-hoc document node (not from a workflow config).

        The document is created with status=FINISHED and started_at, finished_at,
        and deadline all set to the same value.

        If parent_id is provided, the method walks up the node tree starting at
        that node to find a suitable parent (TaskNode first, then WorkflowNode).
        If no suitable parent is found, the document is created at the top level.
        This means creation never fails due to parent lookup.

        Args:
            entity_id: The entity to attach this document to.
            title: Document title.
            started_at: Timestamp for started_at/finished_at/deadline.
            document_ref: Reference identifier for the document.
            note: Optional note text.
            is_public: Whether the document is publicly visible (default False).
            parent_id: Optional reference node ID. The method walks up from this
                node to find the nearest TaskNode or WorkflowNode as parent.

        Returns:
            DocumentNodeInfo dataclass for the created node.

        Raises:
            WorkflowNotFound: If parent_id is given but the node doesn't exist.
        """
        parent = None
        if parent_id is not None:
            reference_node = self._get_node(parent_id)
            parent = get_parent_of_type(reference_node, TaskNode)
            if parent is None:
                parent = get_parent_of_type(reference_node, WorkflowNode)

        node = DocumentNode(
            title=title,
            status=NodeStatus.FINISHED,
            started_at=started_at,
            finished_at=started_at,
            deadline=started_at,
            entity_id=entity_id,
            document_ref=document_ref,
            version=None,
            config=None,
            note=note,
            url=url,
            is_public=is_public,
            parent=parent,
        )

        self.session.add(node)
        self.session.flush()
        return DocumentNodeInfo.from_model(node)

    def create_note_node(
        self,
        entity_id: str,
        title: str,
        started_at: datetime,
        *,
        note: str | None = None,
        url: str | None = None,
        is_public: bool = False,
        parent_id: int | None = None,
    ) -> NoteNodeInfo:
        """Create an ad-hoc note node (not from a workflow config).

        The note is created with status=FINISHED and started_at, finished_at,
        and deadline all set to the same value.

        If parent_id is provided, the method walks up from that node to find
        the nearest TaskNode or WorkflowNode as parent. If no suitable parent
        is found, the note is created at the top level.

        Args:
            entity_id: The entity to attach this note to.
            title: Note title.
            started_at: Timestamp for started_at/finished_at/deadline.
            note: Optional note text.
            is_public: Whether the note is publicly visible (default False).
            parent_id: Optional reference node ID. The method walks up from this
                node to find the nearest TaskNode or WorkflowNode as parent.

        Returns:
            NoteNodeInfo dataclass for the created node.

        Raises:
            WorkflowNotFound: If parent_id is given but the node doesn't exist.
        """
        parent = None
        if parent_id is not None:
            reference_node = self._get_node(parent_id)
            parent = get_parent_of_type(reference_node, TaskNode)
            if parent is None:
                parent = get_parent_of_type(reference_node, WorkflowNode)

        node = NoteNode(
            title=title,
            status=NodeStatus.FINISHED,
            started_at=started_at,
            finished_at=started_at,
            deadline=started_at,
            entity_id=entity_id,
            version=None,
            config=None,
            note=note,
            url=url,
            is_public=is_public,
            parent=parent,
        )

        self.session.add(node)
        self.session.flush()
        return NoteNodeInfo.from_model(node)

    def update_document_node(
        self,
        node_id: int,
        *,
        title: str | None = None,
        started_at: datetime | None = None,
        note: str | None | _Unset = UNSET,
        url: str | None | _Unset = UNSET,
        document_ref: str | None | _Unset = UNSET,
        is_public: bool | None = None,
    ) -> DocumentNodeInfo:
        """Update a DocumentNode's fields.

        Saving a document always sets its status to FINISHED. Only non-None
        (non-sentinel) arguments are applied.

        Args:
            node_id: ID of the DocumentNode to update.
            title: New title (or None to leave unchanged).
            started_at: New started_at datetime (or None to leave unchanged).
            note: New note (pass None to clear, omit to leave unchanged).
            document_ref: New document reference (pass None to clear, omit to leave unchanged).
            is_public: New visibility (or None to leave unchanged).

        Returns:
            Updated DocumentNodeInfo dataclass.

        Raises:
            WorkflowNotFound: If node not found or not a DocumentNode.
        """
        node = self._get_node(node_id)
        if not isinstance(node, DocumentNode):
            raise WorkflowNotFound(f"Node {node_id} is not a DocumentNode")

        if title is not None:
            node.title = title
        if started_at is not None:
            node.started_at = started_at
        if note is not UNSET:
            node.note = note
        if url is not UNSET:
            node.url = url
        if document_ref is not UNSET:
            node.document_ref = document_ref
        if is_public is not None:
            node.is_public = is_public

        _update_node_status(node, NodeStatus.FINISHED)
        self.trigger_events(node)

        self.session.flush()
        return DocumentNodeInfo.from_model(node)

    def update_form_node(
        self,
        node_id: int,
        *,
        title: str | None = None,
        started_at: datetime | None = None,
        form_data: dict[str, Any] | None = None,
        note: str | None | _Unset = UNSET,
        url: str | None | _Unset = UNSET,
    ) -> FormNodeInfo:
        """Update a FormNode's fields.

        Saving a form always sets its status to FINISHED. When form_data is
        provided, it is stored both in ``node.form_data`` and propagated to
        ``node.data["form"][<form_name>]`` so that jmespath conditions on
        subsequent workflow links can access the answers.

        Args:
            node_id: ID of the FormNode to update.
            title: New title (or None to leave unchanged).
            started_at: New started_at datetime (or None to leave unchanged).
            form_data: Form answers dict (or None to leave unchanged).
            note: New note text (pass None to clear, omit to leave unchanged).

        Returns:
            Updated FormNodeInfo dataclass.

        Raises:
            WorkflowNotFound: If node not found or not a FormNode.
        """
        node = self._get_node(node_id)
        if not isinstance(node, FormNode):
            raise WorkflowNotFound(f"Node {node_id} is not a FormNode")

        if title is not None:
            node.title = title
        if started_at is not None:
            node.started_at = started_at
        if note is not UNSET:
            node.note = note
        if url is not UNSET:
            node.url = url

        if form_data is not None:
            node.form_data = form_data
            # SQLAlchemy only detects JSONB mutations when a new object is assigned,
            # so we deepcopy to ensure a fresh dict instance.
            node_data = deepcopy(node.data or {})
            form_name = dict(node.form_config or {}).get("name", "")
            if form_name:
                node_data.setdefault("form", {})[form_name] = form_data
            node.data = node_data

        _update_node_status(node, NodeStatus.FINISHED)
        self.trigger_events(node)

        self.session.flush()
        return FormNodeInfo.from_model(node)

    def update_note_node(
        self,
        node_id: int,
        *,
        title: str | None = None,
        started_at: datetime | None = None,
        note: str | None | _Unset = UNSET,
        url: str | None | _Unset = UNSET,
        is_public: bool | None = None,
    ) -> NoteNodeInfo:
        """Update a NoteNode's fields.

        Saving a note always sets its status to FINISHED. When started_at is
        provided, finished_at and deadline are also set to the same value.

        Args:
            node_id: ID of the NoteNode to update.
            title: New title (or None to leave unchanged).
            started_at: New timestamp (also updates finished_at and deadline).
            note: New note text (pass None to clear, omit to leave unchanged).
            is_public: New visibility (or None to leave unchanged).

        Returns:
            Updated NoteNodeInfo dataclass.

        Raises:
            WorkflowNotFound: If node not found or not a NoteNode.
        """
        node = self._get_node(node_id)
        if not isinstance(node, NoteNode):
            raise WorkflowNotFound(f"Node {node_id} is not a NoteNode")

        if title is not None:
            node.title = title
        if started_at is not None:
            node.started_at = started_at
            node.finished_at = started_at
            node.deadline = started_at
        if note is not UNSET:
            node.note = note
        if url is not UNSET:
            node.url = url
        if is_public is not None:
            node.is_public = is_public

        _update_node_status(node, NodeStatus.FINISHED)
        self.trigger_events(node)

        self.session.flush()
        return NoteNodeInfo.from_model(node)

    def delete_node(self, node_id: int) -> None:
        """Delete a node.

        A node can only be deleted if it has no children and is not readonly.

        Args:
            node_id: ID of the node to delete.

        Raises:
            WorkflowNotFound: If the node doesn't exist.
            WorkflowException: If the node has children or is readonly.
        """

        node = self._get_node(node_id)

        if node.children:
            raise WorkflowException("Cannot delete node: it has children assigned.")
        if node.is_readonly:
            raise WorkflowException("Cannot delete node: it is read-only.")

        self.session.delete(node)
        self.session.flush()

    def trigger_events(self, node: Node):
        if not node.config:
            return

        if (node.status is not NodeStatus.FINISHED) or node.events_triggered:
            return

        events_by_name: dict[str, Callable[..., Event]] = {
            cls.__name__: cls for cls in self._config.event_handlers
        }

        for trigger in node.config.triggers or {}:
            type_, value = trigger["type"], trigger["value"]
            try:
                event_cls = events_by_name[type_]
            except KeyError:
                raise EventHandlerNotRegistered(
                    f"No handler registered for event {type_!r}"
                )

            event = event_cls(**value) if isinstance(value, dict) else event_cls(value)
            self.handle_event(event, node)

        node.events_triggered = True


def _update_node_status(node: Node, status: NodeStatus) -> None:
    """Apply a status transition with side effects."""
    if node.status == status:
        return
    node.status = status
    match status:
        case NodeStatus.STARTED:
            node.finished_at = None
        case NodeStatus.INACTIVE:
            node.deadline = None
            node.finished_at = None
        case _:
            return


def manager_factory(
    config: WorkflowManagerConfig,
) -> "Callable[..., WorkflowManager]":
    """Return a factory that creates configured :class:`WorkflowManager` instances.

    The returned factory accepts a session and an optional opaque
    ``context`` value that is forwarded to event handlers.

    Example::

        new_manager = manager_factory(WorkflowManagerConfig())

        with new_manager(session, context=request_ctx) as mgr:
            mgr.start_workflow(...)
    """

    def factory(
        session: Session,
        *,
        context: Any = None,
    ) -> WorkflowManager:
        return WorkflowManager(session, config, context=context)

    return factory
