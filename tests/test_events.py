# pyright: strict
"""Tests for event handling and WorkflowManager context manager."""

from dataclasses import dataclass
from io import StringIO

import pytest
from sqlalchemy.orm import Session

from business_workflow_manager import (
    Event,
    EventHandlerNotRegistered,
    WorkflowManager,
    WorkflowManagerConfig,
    manager_factory,
)
from business_workflow_manager.models import Node, Task, WorkflowConfig, WorkflowNode
from business_workflow_manager.types import NodeStatus

WORKFLOW_YAML = """\
workflow:
  title: Test
  version: 1
  start_task_ref: t1
  min_per_entity: 0
  max_per_entity: null
  tasks:
    t1:
      title: Task One
      start_task: true
      steps: []
      links:
        - task_ref: END
      triggers:
         - type: MyEvent
           value:
             name: name-from-config
    END:
      title: __END__
      steps: []
      links: []
"""

WORKFLOW_YAML_SCALAR_TRIGGER = """\
workflow:
  title: Test Scalar Trigger
  version: 1
  start_task_ref: t1
  min_per_entity: 0
  max_per_entity: null
  tasks:
    t1:
      title: Task One
      start_task: true
      steps: []
      links:
        - task_ref: END
      triggers:
         - type: MyEvent
           value: name-from-scalar
    END:
      title: __END__
      steps: []
      links: []
"""


@dataclass
class MyEvent(Event):
    name: str


def _make_node() -> Node:
    """Build a standalone, unpersisted Node for tests that don't need a real one."""
    return WorkflowNode(
        title="test-node",
        version=1,
        status=NodeStatus.STARTED,
        entity_id="e1",
        config=None,
    )


@dataclass
class MyOtherEvent(Event):
    value: int


def test_handle_event_calls_handlers(session: Session) -> None:
    received: list[tuple[Session, Event, Node, object]] = []

    def handler(s: Session, e: Event, n: Node, ctx: object) -> None:
        received.append((s, e, n, ctx))

    config = WorkflowManagerConfig()
    mgr = WorkflowManager(session, config)
    mgr.register_event_handler(MyEvent, handler)

    event = MyEvent(name="some-name")
    node = _make_node()
    mgr.handle_event(event, node)

    assert len(received) == 1
    assert received[0][0] is session
    assert isinstance(received[0][1], MyEvent)
    assert received[0][1].name == "some-name"
    assert received[0][2] is node
    assert received[0][3] is None


def test_handle_event_calls_multiple_handlers(session: Session) -> None:
    call_order: list[str] = []

    def handler_a(s: Session, e: Event, n: Node, ctx: object) -> None:
        call_order.append("a")

    def handler_b(s: Session, e: Event, n: Node, ctx: object) -> None:
        call_order.append("b")

    config = WorkflowManagerConfig()
    mgr = WorkflowManager(session, config)
    mgr.register_event_handler(MyEvent, handler_a)
    mgr.register_event_handler(MyEvent, handler_b)
    mgr.handle_event(MyEvent("test"), _make_node())

    assert call_order == ["a", "b"]


def test_handle_event_no_handlers(session: Session) -> None:
    mgr = WorkflowManager(session)
    with pytest.raises(EventHandlerNotRegistered, match="MyEvent"):
        mgr.handle_event(MyEvent("test"), _make_node())


def test_context_manager_clears_session(session: Session) -> None:
    mgr = WorkflowManager(session)
    with mgr:
        assert mgr.session is session
    with pytest.raises(RuntimeError, match="outside of context"):
        mgr.session


def test_context_manager_clears_on_exception(session: Session) -> None:
    mgr = WorkflowManager(session)
    with pytest.raises(ValueError, match="boom"), mgr:
        raise ValueError("boom")
    with pytest.raises(RuntimeError, match="outside of context"):
        mgr.session


def test_manager_factory_factory(session: Session) -> None:
    config = WorkflowManagerConfig()
    factory = manager_factory(config)
    mgr = factory(session)

    assert isinstance(mgr, WorkflowManager)
    assert mgr.session is session


def test_manager_factory_factory_with_context(session: Session) -> None:
    received: list[Event] = []

    def handler(s: Session, e: Event, n: Node, ctx: object) -> None:
        received.append(e)

    factory = manager_factory(WorkflowManagerConfig())

    with factory(session) as mgr:
        mgr.register_event_handler(MyEvent, handler)
        mgr.handle_event(MyEvent("test"), _make_node())

    assert len(received) == 1
    with pytest.raises(RuntimeError):
        mgr.session


def test_handle_event_within_workflow(session: Session) -> None:
    """Event handlers can use the session to query workflow data."""
    triggered_workflows: list[str] = []

    def handler(s: Session, e: Event, n: Node, ctx: object) -> None:
        assert isinstance(e, MyEvent)
        triggered_workflows.append(e.name)

    config = WorkflowManagerConfig()
    mgr = WorkflowManager(session, config)
    mgr.register_event_handler(MyEvent, handler)

    workflow, _ = mgr.load_workflow_from_file(StringIO(WORKFLOW_YAML))
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    mgr.handle_event(MyEvent(str(wf_node.wf_node_id)), _make_node())
    assert triggered_workflows == [str(wf_node.wf_node_id)]


def test_event_handler_type_filtering(session: Session) -> None:
    """Event handlers are only called for their registered event type."""

    @dataclass
    class EventA(Event):
        value: int

    @dataclass
    class EventB(Event):
        value: int

    called: list[tuple[str, int]] = []

    def handler_a(_session: Session, event: Event, _node: Node, _ctx: object) -> None:
        assert isinstance(event, EventA)
        called.append(("A", event.value))

    def handler_b(_session: Session, event: Event, _node: Node, _ctx: object) -> None:
        assert isinstance(event, EventB)
        called.append(("B", event.value))

    config = WorkflowManagerConfig()
    mgr = WorkflowManager(session, config)
    mgr.register_event_handler(EventA, handler_a)
    mgr.register_event_handler(EventB, handler_b)

    mgr.handle_event(EventA(1), _make_node())
    mgr.handle_event(EventB(2), _make_node())

    assert called == [("A", 1), ("B", 2)]


def test_can_fetch_related_events_via_node_config(session: Session) -> None:
    """It is possible to get the events that will be created for a node."""
    config = WorkflowManagerConfig()
    mgr = WorkflowManager(session, config)
    mgr.register_event_handler(MyEvent, lambda s, e, n, c: None)
    workflow, _ = mgr.load_workflow_from_file(StringIO(WORKFLOW_YAML))

    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")
    [step_info] = mgr.get_next_steps(wf_node.wf_node_id)
    task_node = mgr.start_next_step(wf_node.wf_node_id, step_info.wf_config_id)

    config = session.get(WorkflowConfig, task_node.wf_config_id)
    assert isinstance(config, Task)
    assert config.triggers == [
        {"type": "MyEvent", "value": {"name": "name-from-config"}}
    ]


def test_context_from_constructor_passed_to_handler(session: Session) -> None:
    """Context provided via the constructor is forwarded to handlers."""
    received: list[object] = []

    def handler(_s: Session, _e: Event, _n: Node, ctx: object) -> None:
        received.append(ctx)

    config = WorkflowManagerConfig()
    ctx = {"user_id": 42}
    mgr = WorkflowManager(session, config, context=ctx)
    mgr.register_event_handler(MyEvent, handler)
    mgr.handle_event(MyEvent("x"), _make_node())

    assert received == [ctx]


def test_context_default_is_none(session: Session) -> None:
    received: list[object] = []

    def handler(_s: Session, _e: Event, _n: Node, ctx: object) -> None:
        received.append(ctx)

    mgr = WorkflowManager(session, WorkflowManagerConfig())
    mgr.register_event_handler(MyEvent, handler)
    mgr.handle_event(MyEvent("x"), _make_node())

    assert received == [None]


def test_context_override_on_handle_event(session: Session) -> None:
    """Explicit context passed to handle_event overrides the manager-level one."""
    received: list[object] = []

    def handler(_s: Session, _e: Event, _n: Node, ctx: object) -> None:
        received.append(ctx)

    mgr = WorkflowManager(
        session,
        WorkflowManagerConfig(),
        context="from-ctor",
    )
    mgr.register_event_handler(MyEvent, handler)
    mgr.handle_event(MyEvent("x"), _make_node(), context="override")
    mgr.handle_event(MyEvent("y"), _make_node())
    mgr.handle_event(MyEvent("z"), _make_node(), context=None)

    assert received == ["override", "from-ctor", None]


def test_context_propagates_through_trigger_events(session: Session) -> None:
    """Manager-level context flows through YAML-configured triggers."""
    received: list[object] = []

    def handler(_s: Session, _e: Event, _n: Node, ctx: object) -> None:
        received.append(ctx)

    ctx = {"request_id": "abc"}
    config = WorkflowManagerConfig()
    mgr = WorkflowManager(session, config, context=ctx)
    mgr.register_event_handler(MyEvent, handler)

    workflow, _ = mgr.load_workflow_from_file(StringIO(WORKFLOW_YAML))
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-ctx")
    [step_info] = mgr.get_next_steps(wf_node.wf_node_id)
    task_node = mgr.start_next_step(wf_node.wf_node_id, step_info.wf_config_id)

    from business_workflow_manager.types import NodeStatus

    mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.FINISHED)

    assert received == [ctx]


def test_factory_forwards_context(session: Session) -> None:
    received: list[object] = []

    def handler(_s: Session, _e: Event, _n: Node, ctx: object) -> None:
        received.append(ctx)

    factory = manager_factory(WorkflowManagerConfig())
    ctx = object()
    with factory(session, context=ctx) as mgr:
        mgr.register_event_handler(MyEvent, handler)
        mgr.handle_event(MyEvent("x"), _make_node())

    assert received == [ctx]


def test_trigger_events_with_mapping_value(session: Session) -> None:
    """A dict trigger value is unpacked as kwargs into the event constructor."""
    received: list[MyEvent] = []

    def handler(_s: Session, e: Event, _n: Node, _ctx: object) -> None:
        assert isinstance(e, MyEvent)
        received.append(e)

    config = WorkflowManagerConfig()
    mgr = WorkflowManager(session, config)
    mgr.register_event_handler(MyEvent, handler)

    workflow, _ = mgr.load_workflow_from_file(StringIO(WORKFLOW_YAML))
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-mapping")
    [step_info] = mgr.get_next_steps(wf_node.wf_node_id)
    task_node = mgr.start_next_step(wf_node.wf_node_id, step_info.wf_config_id)

    from business_workflow_manager.types import NodeStatus

    mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.FINISHED)

    assert len(received) == 1
    assert received[0].name == "name-from-config"


def test_trigger_events_with_scalar_value(session: Session) -> None:
    """A scalar trigger value is passed as a single positional arg to the event constructor."""
    received: list[MyEvent] = []

    def handler(_s: Session, e: Event, _n: Node, _ctx: object) -> None:
        assert isinstance(e, MyEvent)
        received.append(e)

    config = WorkflowManagerConfig()
    mgr = WorkflowManager(session, config)
    mgr.register_event_handler(MyEvent, handler)

    workflow, _ = mgr.load_workflow_from_file(StringIO(WORKFLOW_YAML_SCALAR_TRIGGER))
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-scalar")
    [step_info] = mgr.get_next_steps(wf_node.wf_node_id)
    task_node = mgr.start_next_step(wf_node.wf_node_id, step_info.wf_config_id)

    from business_workflow_manager.types import NodeStatus

    mgr.update_task_node(task_node.wf_node_id, status=NodeStatus.FINISHED)

    assert len(received) == 1
    assert received[0].name == "name-from-scalar"
