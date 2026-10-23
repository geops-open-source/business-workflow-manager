"""Tests for business_workflow_manager.engine (runtime step progression)."""

from datetime import UTC, datetime
from io import StringIO

import pytest
from sqlalchemy.orm import Session

from business_workflow_manager import WorkflowManager, WorkflowManagerConfig
from business_workflow_manager.dto import (
    DocumentNodeInfo,
    StepInfo,
    StepOption,
    TaskNodeInfo,
    WorkflowNodeInfo,
)
from business_workflow_manager.engine import (
    ConfigStepInfo,
    Engine,
    default_is_applicable,
    get_last_step,
    get_parent_task,
    get_parent_workflow,
)
from business_workflow_manager.exceptions import (
    InvalidWorkflowStep,
    WorkflowException,
    WorkflowNotFound,
)
from business_workflow_manager.models import Node, TaskNode, Workflow
from business_workflow_manager.types import NextStepUnavailableReason, NodeStatus

WORKFLOW_CONFIG = """\
workflow:
  title: Test workflow
  version: 2
  min_per_entity: 0
  max_per_entity: null
  start_task_ref: task1
  tasks:
    task1:
      title: Task 1
      steps:
      - type: document
        title: Document 1
      - type: document
        title: Document 2
      links:
        - task_ref: task2
    task2:
      title: Task 2
      steps: []
      links:
        - task_ref: END
    END:
      title: __END__
      steps: []
      links: []
"""

WORKFLOW_WITH_CONDITIONS = """\
workflow:
  title: Conditional workflow
  version: 1
  min_per_entity: 0
  max_per_entity: null
  start_task_ref: task1
  tasks:
    task1:
      title: Task 1
      steps:
      - type: document
        title: Always
      - type: document
        title: Conditional
        condition: flag == `true`
      - type: document
        title: After Conditional
      links:
        - task_ref: task_yes
          condition: choice == `"yes"`
        - task_ref: task_no
    task_yes:
      title: Yes Task
      steps: []
      links:
        - task_ref: END
    task_no:
      title: No Task
      steps: []
      links:
        - task_ref: END
    END:
      title: __END__
      steps: []
      links: []
"""

WORKFLOW_MAX_ONE = """\
workflow:
  title: Max One
  version: 1
  min_per_entity: 0
  max_per_entity: 1
  start_task_ref: t1
  tasks:
    t1:
      title: Task
      steps: []
      links: []
"""

WORKFLOW_FOR_REASONS = """\
workflow:
  title: Reasons workflow
  version: 1
  min_per_entity: 0
  max_per_entity: null
  start_task_ref: t_doc_links
  tasks:
    t_doc_links:
      title: Document, then conditional link
      start_task: true
      steps:
      - type: document
        title: Doc
      links:
        - task_ref: t_target
          condition: choice == `"yes"`
    t_doc_end:
      title: Document, then END
      start_task: true
      steps:
      - type: document
        title: Last doc
      links:
        - task_ref: END
    t_cond_steps:
      title: Only a conditional step
      start_task: true
      steps:
      - type: document
        title: Conditional doc
        condition: flag == `true`
      links: []
    t_skip_then_step:
      title: Conditional step, then a regular step
      start_task: true
      steps:
      - type: document
        title: Skippable doc
        condition: flag == `true`
      - type: document
        title: Regular doc
      links: []
    t_end_and_cond:
      title: Conditional link and END
      start_task: true
      steps: []
      links:
        - task_ref: t_target
          condition: choice == `"yes"`
        - task_ref: END
    t_target:
      title: Target
      start_task: true
      steps: []
      links:
        - task_ref: END
    END:
      title: __END__
      steps: []
      links: []
"""


@pytest.fixture
def workflow_for_reasons(mgr: WorkflowManager) -> Workflow:
    loaded_wf = mgr.load_workflow_from_file(StringIO(WORKFLOW_FOR_REASONS))
    mgr.session.flush()
    mgr.session.expire_all()
    return loaded_wf.workflow


@pytest.fixture
def workflow(mgr: WorkflowManager) -> Workflow:
    loaded_wf = mgr.load_workflow_from_file(StringIO(WORKFLOW_CONFIG))
    mgr.session.flush()
    mgr.session.expire_all()
    return loaded_wf.workflow


@pytest.fixture
def workflow_with_conditions(mgr: WorkflowManager) -> Workflow:
    loaded_wf = mgr.load_workflow_from_file(StringIO(WORKFLOW_WITH_CONDITIONS))
    mgr.session.flush()
    mgr.session.expire_all()
    return loaded_wf.workflow


@pytest.fixture
def workflow_max_one(mgr: WorkflowManager) -> Workflow:
    loaded_wf = mgr.load_workflow_from_file(StringIO(WORKFLOW_MAX_ONE))
    mgr.session.flush()
    mgr.session.expire_all()
    return loaded_wf.workflow


def test_is_applicable_unlimited(mgr: WorkflowManager, workflow: Workflow):
    assert mgr.engine.is_applicable(workflow, entity_id="entity-1")


def test_is_applicable_max_one_before_creating(
    mgr: WorkflowManager, workflow_max_one: Workflow
):
    assert mgr.engine.is_applicable(workflow_max_one, entity_id="entity-42")


def test_is_applicable_max_one_after_creating(
    mgr: WorkflowManager, workflow_max_one: Workflow
):
    mgr.start_workflow(workflow_max_one.wf_config_id, entity_id="entity-42")
    assert not mgr.engine.is_applicable(workflow_max_one, entity_id="entity-42")


def test_is_applicable_custom_callback_receives_arguments(
    session: Session, workflow_max_one: Workflow
):
    received: list[tuple[Session, Workflow, str]] = []

    def callback(cb_session: Session, cb_workflow: Workflow, cb_entity_id: str) -> bool:
        received.append((cb_session, cb_workflow, cb_entity_id))
        return True

    config = WorkflowManagerConfig(is_applicable=callback)
    with WorkflowManager(session, config) as custom_mgr:
        assert custom_mgr.engine.is_applicable(workflow_max_one, entity_id="entity-42")

    assert received == [(session, workflow_max_one, "entity-42")]


def test_is_applicable_custom_callback_denies(session: Session, workflow: Workflow):
    config = WorkflowManagerConfig(is_applicable=lambda s, wf, eid: False)
    with WorkflowManager(session, config) as custom_mgr:
        assert not custom_mgr.engine.is_applicable(workflow, entity_id="entity-1")
        with pytest.raises(WorkflowException, match="not applicable"):
            custom_mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")


def test_is_applicable_custom_callback_overrides_default(
    session: Session, workflow_max_one: Workflow
):
    config = WorkflowManagerConfig(is_applicable=lambda s, wf, eid: True)
    with WorkflowManager(session, config) as custom_mgr:
        custom_mgr.start_workflow(workflow_max_one.wf_config_id, entity_id="entity-42")
        # Default max_per_entity=1 check is replaced by the custom callback.
        assert custom_mgr.engine.is_applicable(workflow_max_one, entity_id="entity-42")
        custom_mgr.start_workflow(workflow_max_one.wf_config_id, entity_id="entity-42")


def test_default_is_applicable_used_without_callback(
    session: Session, workflow_max_one: Workflow
):
    engine = Engine(session)
    assert engine.is_applicable(workflow_max_one, entity_id="entity-42")
    assert default_is_applicable(session, workflow_max_one, "entity-42")


def test_start_workflow_not_found(mgr: WorkflowManager):
    with pytest.raises(WorkflowNotFound):
        mgr.start_workflow(999999, entity_id="entity-1")


def test_start_workflow_not_applicable(
    mgr: WorkflowManager, workflow_max_one: Workflow
):
    mgr.start_workflow(workflow_max_one.wf_config_id, entity_id="entity-1")
    with pytest.raises(WorkflowException, match="not applicable"):
        mgr.start_workflow(workflow_max_one.wf_config_id, entity_id="entity-1")


def test_start_workflow_returns_workflow_node_info(
    mgr: WorkflowManager, workflow: Workflow
):
    node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")
    assert isinstance(node, WorkflowNodeInfo)
    assert node.entity_id == "entity-1"
    assert node.status == NodeStatus.STARTED


def test_evaluate_condition_matching(mgr: WorkflowManager, workflow: Workflow):
    node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    orm_node = mgr.session.get_one(Node, node.wf_node_id)
    orm_node.data = {"some_key": 1}
    mgr.session.flush()

    assert mgr.engine.evaluate_condition(orm_node, "some_key == `1`")


def test_evaluate_condition_non_matching(mgr: WorkflowManager, workflow: Workflow):
    node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    orm_node = mgr.session.get_one(Node, node.wf_node_id)
    orm_node.data = {"some_key": 1}
    mgr.session.flush()

    assert not mgr.engine.evaluate_condition(orm_node, "some_key == `2`")


def test_evaluate_condition_unknown_key(mgr: WorkflowManager, workflow: Workflow):
    node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    orm_node = mgr.session.get_one(Node, node.wf_node_id)
    assert not mgr.engine.evaluate_condition(orm_node, "unknown_key == `3`")


def test_evaluate_condition_has_access_to_entity_data(
    mgr: WorkflowManager, workflow: Workflow
):
    mgr.engine.get_entity_data = lambda session, entity_id: {
        "entity_id": entity_id,
        "value": 42,
    }
    node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    orm_node = mgr.session.get_one(Node, node.wf_node_id)
    mgr.session.flush()

    assert mgr.engine.evaluate_condition(orm_node, "entity.entity_id") == "entity-1"
    assert mgr.engine.evaluate_condition(orm_node, "entity.value") == 42


def test_evaluating_condition_persists_entity_data(
    mgr: WorkflowManager, workflow: Workflow
):
    entity_data = {}
    mgr.engine.get_entity_data = lambda session, entity_id: entity_data

    node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    orm_node = mgr.session.get_one(Node, node.wf_node_id)
    orm_node.data = {"some_key": 0}
    mgr.session.flush()

    entity_data["value"] = 1
    assert mgr.engine.evaluate_condition(orm_node, "entity.value") == 1
    assert orm_node.data == {"some_key": 0, "entity": {"value": 1}}

    entity_data["value"] = 2
    assert mgr.engine.evaluate_condition(orm_node, "entity.value") == 2
    assert orm_node.data == {"some_key": 0, "entity": {"value": 2}}


def test_evaluate_condition_custom_entity_data_key(
    session: Session, workflow: Workflow
):
    config = WorkflowManagerConfig(
        get_entity_data=lambda cb_session, entity_id: {
            "entity_id": entity_id,
            "belastet": True,
        },
        entity_data_key="vflz",
    )
    with WorkflowManager(session, config) as custom_mgr:
        node = custom_mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

        orm_node = custom_mgr.session.get_one(Node, node.wf_node_id)

        assert custom_mgr.engine.evaluate_condition(orm_node, "vflz.belastet")
        assert (
            custom_mgr.engine.evaluate_condition(orm_node, "vflz.entity_id")
            == "entity-1"
        )
        # Entity data is persisted under the custom key, not the default one.
        assert orm_node.data["vflz"] == {"entity_id": "entity-1", "belastet": True}
        assert "entity" not in orm_node.data
        # The default key is not populated for conditions.
        assert not custom_mgr.engine.evaluate_condition(orm_node, "entity.belastet")


def test_entity_data_key_defaults_to_entity(session: Session):
    with WorkflowManager(session) as default_mgr:
        assert default_mgr.engine.entity_data_key == "entity"


def test_entity_data_key_form_raises_value_error():
    with pytest.raises(ValueError, match="entity_data_key 'form' is reserved"):
        WorkflowManagerConfig(entity_data_key="form")


def test_entity_data_key_valid_value_does_not_raise():
    config = WorkflowManagerConfig(entity_data_key="applicant")
    assert config.entity_data_key == "applicant"


def test_get_next_steps_returns_step_options(mgr: WorkflowManager, workflow: Workflow):
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")
    steps = mgr.get_next_steps(wf_node.wf_node_id).available_options
    assert len(steps) >= 1
    assert all(isinstance(s, StepOption) for s in steps)
    assert steps[0].name == "task1"


def test_full_progression(mgr: WorkflowManager, workflow: Workflow):
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")
    assert isinstance(wf_node, WorkflowNodeInfo)

    # Get available steps from workflow node
    steps = mgr.get_next_steps(wf_node.wf_node_id).available_options
    task1_step = next(s for s in steps if s.name == "task1")

    # Start task1
    task_node = mgr.start_next_step(wf_node.wf_node_id, task1_step.wf_config_id)
    assert isinstance(task_node, TaskNodeInfo)
    assert task_node.status == NodeStatus.STARTED

    # Get steps for the task
    task_steps = mgr.get_next_steps(task_node.wf_node_id).available_options
    assert len(task_steps) == 1
    doc1_config = workflow.tasks["task1"].steps[0].step
    assert task_steps[0].wf_config_id == doc1_config.wf_config_id

    # Start doc 1
    doc1 = mgr.start_next_step(task_node.wf_node_id, task_steps[0].wf_config_id)
    assert isinstance(doc1, DocumentNodeInfo)

    # Finish doc1 (direct ORM manipulation for now)

    orm_doc1 = mgr.session.get_one(Node, doc1.wf_node_id)
    orm_doc1.status = NodeStatus.FINISHED
    mgr.session.flush()

    # Get next step after doc1
    next_steps = mgr.get_next_steps(doc1.wf_node_id).available_options
    assert len(next_steps) == 1
    doc2_config = workflow.tasks["task1"].steps[1].step
    assert next_steps[0].wf_config_id == doc2_config.wf_config_id

    # Start doc2
    doc2 = mgr.start_next_step(doc1.wf_node_id, next_steps[0].wf_config_id)
    assert isinstance(doc2, DocumentNodeInfo)

    # Finish doc2, proceed to task2
    orm_doc2 = mgr.session.get_one(Node, doc2.wf_node_id)
    orm_doc2.status = NodeStatus.FINISHED
    mgr.session.flush()

    next_steps = mgr.get_next_steps(doc2.wf_node_id).available_options
    assert len(next_steps) == 1
    assert next_steps[0].name == "task2"

    # Start task2
    task2_node = mgr.start_next_step(doc2.wf_node_id, next_steps[0].wf_config_id)
    assert isinstance(task2_node, TaskNodeInfo)

    # Finish task2, END should not be returned
    orm_task2 = mgr.session.get_one(Node, task2_node.wf_node_id)
    orm_task2.status = NodeStatus.FINISHED
    mgr.session.flush()

    assert mgr.get_next_steps(task2_node.wf_node_id) == StepInfo(
        [], [NextStepUnavailableReason.END_OF_WORKFLOW]
    )


def test_invalid_step_raises(mgr: WorkflowManager, workflow: Workflow):
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    # Get task2's config id (not a valid start step)
    task2_config_id = workflow.tasks["task2"].wf_config_id

    with pytest.raises(InvalidWorkflowStep):
        mgr.start_next_step(wf_node.wf_node_id, task2_config_id)


def test_workflow_node_returns_start_tasks(mgr: WorkflowManager, workflow: Workflow):
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    steps = mgr.get_next_steps(wf_node.wf_node_id).available_options
    assert any(s.name == "task1" for s in steps)


def test_finished_workflow_returns_nothing(mgr: WorkflowManager, workflow: Workflow):
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    orm_node = mgr.session.get_one(Node, wf_node.wf_node_id)
    orm_node.status = NodeStatus.FINISHED
    mgr.session.flush()

    assert mgr.get_next_steps(wf_node.wf_node_id) == StepInfo(
        [], [NextStepUnavailableReason.TASK_CLOSED]
    )


@pytest.mark.parametrize("status", [NodeStatus.SKIPPED, NodeStatus.INACTIVE])
def test_skipped_or_inactive_workflow_has_no_reasons(
    mgr: WorkflowManager, workflow: Workflow, status: NodeStatus
):
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    orm_node = mgr.session.get_one(Node, wf_node.wf_node_id)
    orm_node.status = status
    mgr.session.flush()

    assert mgr.get_next_steps(wf_node.wf_node_id) == StepInfo([], [])


def test_task_node_returns_steps(mgr: WorkflowManager, workflow: Workflow):
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    steps = mgr.get_next_steps(wf_node.wf_node_id).available_options
    task_node = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    task_steps = mgr.get_next_steps(task_node.wf_node_id).available_options
    assert len(task_steps) == 1
    assert (
        task_steps[0].wf_config_id == workflow.tasks["task1"].steps[0].step.wf_config_id
    )


def test_condition_met_includes_step(
    mgr: WorkflowManager, workflow_with_conditions: Workflow
):
    wf_node = mgr.start_workflow(
        workflow_with_conditions.wf_config_id, entity_id="entity-1"
    )

    steps = mgr.get_next_steps(wf_node.wf_node_id).available_options
    task_node = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    # Set condition data on task node

    orm_task = mgr.session.get_one(Node, task_node.wf_node_id)
    orm_task.data = {"flag": True, "choice": "yes"}
    mgr.session.flush()

    # Start first step (always present)
    task_steps = mgr.get_next_steps(task_node.wf_node_id).available_options
    doc1 = mgr.start_next_step(task_node.wf_node_id, task_steps[0].wf_config_id)

    # Finish doc1
    orm_doc1 = mgr.session.get_one(Node, doc1.wf_node_id)
    orm_doc1.status = NodeStatus.FINISHED
    mgr.session.flush()

    # Next step should include the conditional one (flag == true)
    conditional_config_id = (
        workflow_with_conditions.tasks["task1"].steps[1].step.wf_config_id
    )
    next_steps = mgr.get_next_steps(doc1.wf_node_id).available_options
    assert any(s.wf_config_id == conditional_config_id for s in next_steps)


def test_condition_not_met_skips_step(
    mgr: WorkflowManager, workflow_with_conditions: Workflow
):
    wf_node = mgr.start_workflow(
        workflow_with_conditions.wf_config_id, entity_id="entity-1"
    )

    steps = mgr.get_next_steps(wf_node.wf_node_id).available_options
    task_node = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    # Set condition data: flag is False
    orm_task = mgr.session.get_one(Node, task_node.wf_node_id)
    orm_task.data = {"flag": False, "choice": "no"}
    mgr.session.flush()

    # Start first step
    task_steps = mgr.get_next_steps(task_node.wf_node_id).available_options
    doc1 = mgr.start_next_step(task_node.wf_node_id, task_steps[0].wf_config_id)

    # Finish doc1
    orm_doc1 = mgr.session.get_one(Node, doc1.wf_node_id)
    orm_doc1.status = NodeStatus.FINISHED
    mgr.session.flush()

    # Conditional step should be skipped
    conditional_config_id = (
        workflow_with_conditions.tasks["task1"].steps[1].step.wf_config_id
    )
    next_steps = mgr.get_next_steps(doc1.wf_node_id).available_options
    assert all(s.wf_config_id != conditional_config_id for s in next_steps)


def test_conditional_links(mgr: WorkflowManager, workflow_with_conditions: Workflow):
    wf_node = mgr.start_workflow(
        workflow_with_conditions.wf_config_id, entity_id="entity-1"
    )

    steps = mgr.get_next_steps(wf_node.wf_node_id).available_options
    task_node = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    # Set data and finish task
    orm_task = mgr.session.get_one(Node, task_node.wf_node_id)
    orm_task.data = {"choice": "yes"}
    orm_task.status = NodeStatus.FINISHED
    mgr.session.flush()

    next_tasks = mgr.get_next_steps(task_node.wf_node_id).available_options
    task_names = [t.name for t in next_tasks]
    assert "task_yes" in task_names


def test_get_last_step(mgr: WorkflowManager, workflow: Workflow):
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    # No next_node — returns itself
    orm_wf = mgr.session.get_one(Node, wf_node.wf_node_id)
    assert get_last_step(orm_wf) is orm_wf

    steps = mgr.get_next_steps(wf_node.wf_node_id).available_options
    task_node = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    mgr.session.expire(orm_wf)
    orm_task = mgr.session.get_one(Node, task_node.wf_node_id)
    assert get_last_step(orm_wf) is orm_task


def test_get_parent_task(mgr: WorkflowManager, workflow: Workflow):
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    steps = mgr.get_next_steps(wf_node.wf_node_id).available_options
    task_node = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    task_steps = mgr.get_next_steps(task_node.wf_node_id).available_options
    doc_node = mgr.start_next_step(task_node.wf_node_id, task_steps[0].wf_config_id)

    orm_doc = mgr.session.get_one(Node, doc_node.wf_node_id)
    orm_task = mgr.session.get_one(Node, task_node.wf_node_id)
    assert get_parent_task(orm_doc) is orm_task


def test_get_parent_workflow(mgr: WorkflowManager, workflow: Workflow):
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    steps = mgr.get_next_steps(wf_node.wf_node_id).available_options
    task_node = mgr.start_next_step(wf_node.wf_node_id, steps[0].wf_config_id)

    orm_task = mgr.session.get_one(Node, task_node.wf_node_id)
    orm_wf = mgr.session.get_one(Node, wf_node.wf_node_id)
    assert get_parent_workflow(orm_task) is orm_wf


R = NextStepUnavailableReason


def _set_node(
    mgr: WorkflowManager,
    node_id: int,
    *,
    status: NodeStatus | None = None,
    data: dict[str, object] | None = None,
) -> None:
    orm_node = mgr.session.get_one(Node, node_id)
    if status is not None:
        orm_node.status = status
    if data is not None:
        orm_node.data = data
    mgr.session.flush()


def _start_task(
    mgr: WorkflowManager,
    workflow: Workflow,
    name: str,
    data: dict[str, object] | None = None,
) -> TaskNodeInfo:
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")
    task_node = mgr.start_next_step(
        wf_node.wf_node_id, workflow.tasks[name].wf_config_id
    )
    assert isinstance(task_node, TaskNodeInfo)
    if data is not None:
        _set_node(mgr, task_node.wf_node_id, data=data)
    return task_node


def _finish_first_step(mgr: WorkflowManager, task_node: TaskNodeInfo) -> int:
    """Start the first step of the task, mark it finished, return its node id."""
    [step] = mgr.get_next_steps(task_node.wf_node_id).available_options
    doc = mgr.start_next_step(task_node.wf_node_id, step.wf_config_id)
    _set_node(mgr, doc.wf_node_id, status=NodeStatus.FINISHED)
    return doc.wf_node_id


def _step_config_id(workflow: Workflow, task: str, index: int) -> int:
    return workflow.tasks[task].steps[index].step.wf_config_id


def test_get_next_steps_returns_step_info(mgr: WorkflowManager, workflow: Workflow):
    wf_node = mgr.start_workflow(workflow.wf_config_id, entity_id="entity-1")

    info = mgr.get_next_steps(wf_node.wf_node_id)

    assert isinstance(info, StepInfo)
    assert [o.name for o in info.available_options] == ["task1"]
    assert info.unavailable_reasons == []


@pytest.mark.parametrize("status", [NodeStatus.SKIPPED, NodeStatus.INACTIVE])
def test_skipped_or_inactive_task_has_no_reasons(
    mgr: WorkflowManager, workflow: Workflow, status: NodeStatus
):
    task_node = _start_task(mgr, workflow, "task1")
    _set_node(mgr, task_node.wf_node_id, status=status)

    assert mgr.get_next_steps(task_node.wf_node_id) == StepInfo([], [])


@pytest.mark.parametrize("status", [NodeStatus.SKIPPED, NodeStatus.INACTIVE])
def test_step_of_skipped_or_inactive_task_has_no_reasons(
    mgr: WorkflowManager, workflow: Workflow, status: NodeStatus
):
    task_node = _start_task(mgr, workflow, "task1")
    doc_id = _finish_first_step(mgr, task_node)
    _set_node(mgr, task_node.wf_node_id, status=status)

    assert mgr.get_next_steps(doc_id) == StepInfo([], [])
    # The task itself resolves to its last step.
    assert mgr.get_next_steps(task_node.wf_node_id) == StepInfo([], [])


def test_reason_task_closed_for_step_of_finished_task(
    mgr: WorkflowManager, workflow: Workflow
):
    task_node = _start_task(mgr, workflow, "task1")
    doc_id = _finish_first_step(mgr, task_node)
    _set_node(mgr, task_node.wf_node_id, status=NodeStatus.FINISHED)

    assert mgr.get_next_steps(doc_id) == StepInfo([], [R.TASK_CLOSED])
    # The task itself resolves to its last step.
    assert mgr.get_next_steps(task_node.wf_node_id) == StepInfo([], [R.TASK_CLOSED])


def test_unfinished_step_of_finished_task_is_step_not_closed(
    mgr: WorkflowManager, workflow: Workflow
):
    task_node = _start_task(mgr, workflow, "task1")
    [step] = mgr.get_next_steps(task_node.wf_node_id).available_options
    doc = mgr.start_next_step(task_node.wf_node_id, step.wf_config_id)
    _set_node(mgr, task_node.wf_node_id, status=NodeStatus.FINISHED)

    assert mgr.get_next_steps(doc.wf_node_id) == StepInfo([], [R.STEP_NOT_CLOSED])


def test_reason_document_not_finished(mgr: WorkflowManager, workflow: Workflow):
    task_node = _start_task(mgr, workflow, "task1")
    [step] = mgr.get_next_steps(task_node.wf_node_id).available_options
    doc = mgr.start_next_step(task_node.wf_node_id, step.wf_config_id)

    assert mgr.get_next_steps(doc.wf_node_id) == StepInfo([], [R.STEP_NOT_CLOSED])


def test_reason_started_task_without_steps(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(mgr, workflow_for_reasons, "t_target")

    assert mgr.get_next_steps(task_node.wf_node_id) == StepInfo([], [R.STEP_NOT_CLOSED])


def test_reason_end_of_workflow_after_task(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(mgr, workflow_for_reasons, "t_target")
    _set_node(mgr, task_node.wf_node_id, status=NodeStatus.FINISHED)

    assert mgr.get_next_steps(task_node.wf_node_id) == StepInfo([], [R.END_OF_WORKFLOW])


def test_reason_end_of_workflow_after_last_document(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(mgr, workflow_for_reasons, "t_doc_end")
    doc_id = _finish_first_step(mgr, task_node)

    assert mgr.get_next_steps(doc_id) == StepInfo([], [R.END_OF_WORKFLOW])


def test_reason_end_of_workflow_for_task_without_links(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(mgr, workflow_for_reasons, "t_cond_steps")
    _set_node(mgr, task_node.wf_node_id, status=NodeStatus.FINISHED)

    assert mgr.get_next_steps(task_node.wf_node_id) == StepInfo([], [R.END_OF_WORKFLOW])


def test_reason_unmet_link_after_task(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(
        mgr, workflow_for_reasons, "t_doc_links", data={"choice": "no"}
    )
    _set_node(mgr, task_node.wf_node_id, status=NodeStatus.FINISHED)

    assert mgr.get_next_steps(task_node.wf_node_id) == StepInfo([], [R.STEP_NOT_CLOSED])


def test_reason_unmet_link_after_last_document(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(mgr, workflow_for_reasons, "t_doc_links")
    doc_id = _finish_first_step(mgr, task_node)

    assert mgr.get_next_steps(doc_id) == StepInfo([], [R.STEP_NOT_CLOSED])


def test_selection_made_returns_link_target(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(mgr, workflow_for_reasons, "t_doc_links")
    doc_id = _finish_first_step(mgr, task_node)
    _set_node(mgr, doc_id, data={"choice": "yes"})

    info = mgr.get_next_steps(doc_id)

    assert [o.name for o in info.available_options] == ["t_target"]
    assert info.unavailable_reasons == []


def test_reason_unmet_condition_for_only_step(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(
        mgr, workflow_for_reasons, "t_cond_steps", data={"flag": False}
    )

    assert mgr.get_next_steps(task_node.wf_node_id) == StepInfo([], [R.STEP_NOT_CLOSED])


def test_conditional_step_met_has_no_reasons(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(
        mgr, workflow_for_reasons, "t_cond_steps", data={"flag": True}
    )

    info = mgr.get_next_steps(task_node.wf_node_id)

    assert [o.wf_config_id for o in info.available_options] == [
        _step_config_id(workflow_for_reasons, "t_cond_steps", 0)
    ]
    assert info.unavailable_reasons == []


def test_reasons_alongside_options(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(
        mgr, workflow_for_reasons, "t_skip_then_step", data={"flag": False}
    )

    info = mgr.get_next_steps(task_node.wf_node_id)

    assert [o.wf_config_id for o in info.available_options] == [
        _step_config_id(workflow_for_reasons, "t_skip_then_step", 1)
    ]
    assert info.unavailable_reasons == [R.STEP_NOT_CLOSED]


def test_multiple_reasons_end_and_unmet_link(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(
        mgr, workflow_for_reasons, "t_end_and_cond", data={"choice": "no"}
    )
    _set_node(mgr, task_node.wf_node_id, status=NodeStatus.FINISHED)

    assert mgr.get_next_steps(task_node.wf_node_id) == StepInfo(
        [], [R.END_OF_WORKFLOW, R.STEP_NOT_CLOSED]
    )


def test_end_link_reported_alongside_link_target(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(
        mgr, workflow_for_reasons, "t_end_and_cond", data={"choice": "yes"}
    )
    _set_node(mgr, task_node.wf_node_id, status=NodeStatus.FINISHED)

    info = mgr.get_next_steps(task_node.wf_node_id)

    # END is never offered as an option, but reported as a reason.
    assert [o.name for o in info.available_options] == ["t_target"]
    assert info.unavailable_reasons == [R.END_OF_WORKFLOW]


def test_reason_task_without_workflow_for_adhoc_note(mgr: WorkflowManager):
    note = mgr.create_note_node(
        entity_id="entity-1", title="Note", started_at=datetime.now(UTC)
    )

    assert mgr.get_next_steps(note.wf_node_id) == StepInfo(
        [], [R.TASK_WITHOUT_WORKFLOW]
    )


def test_reason_task_without_workflow_for_adhoc_task(mgr: WorkflowManager):
    task = mgr.create_task_node(
        entity_id="entity-1", title="Task", started_at=datetime.now(UTC)
    )

    assert mgr.get_next_steps(task.wf_node_id) == StepInfo(
        [], [R.TASK_WITHOUT_WORKFLOW]
    )


def test_reason_task_without_workflow_for_note_in_adhoc_task(mgr: WorkflowManager):
    task = mgr.create_task_node(
        entity_id="entity-1", title="Task", started_at=datetime.now(UTC)
    )
    note = mgr.create_note_node(
        entity_id="entity-1",
        title="Note",
        started_at=datetime.now(UTC),
        parent_id=task.wf_node_id,
    )

    assert mgr.get_next_steps(note.wf_node_id) == StepInfo(
        [], [R.TASK_WITHOUT_WORKFLOW]
    )


def test_adhoc_note_uses_parent_task(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(
        mgr, workflow_for_reasons, "t_cond_steps", data={"flag": False}
    )
    note = mgr.create_note_node(
        entity_id="entity-1",
        title="Note",
        started_at=datetime.now(UTC),
        parent_id=task_node.wf_node_id,
    )

    assert mgr.get_next_steps(note.wf_node_id) == StepInfo([], [R.STEP_NOT_CLOSED])


def test_engine_get_valid_links_returns_config_step_info(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(
        mgr, workflow_for_reasons, "t_end_and_cond", data={"choice": "no"}
    )
    orm_task = mgr.session.get_one(Node, task_node.wf_node_id)
    assert isinstance(orm_task, TaskNode)

    info = mgr.engine.get_valid_links(orm_task)

    assert isinstance(info, ConfigStepInfo)
    assert list(info.available_configs) == []
    assert info.unavailable_reasons == {R.STEP_NOT_CLOSED, R.END_OF_WORKFLOW}


def test_engine_get_next_task_steps_returns_config_step_info(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(
        mgr, workflow_for_reasons, "t_skip_then_step", data={"flag": False}
    )
    orm_task = mgr.session.get_one(Node, task_node.wf_node_id)
    assert isinstance(orm_task, TaskNode)

    info = mgr.engine.get_next_task_steps(orm_task)

    assert isinstance(info, ConfigStepInfo)
    assert [c.wf_config_id for c in info.available_configs] == [
        _step_config_id(workflow_for_reasons, "t_skip_then_step", 1)
    ]
    assert info.unavailable_reasons == {R.STEP_NOT_CLOSED}


def test_start_next_step_rejects_step_skipped_by_condition(
    mgr: WorkflowManager, workflow_for_reasons: Workflow
):
    task_node = _start_task(
        mgr, workflow_for_reasons, "t_skip_then_step", data={"flag": False}
    )

    with pytest.raises(InvalidWorkflowStep):
        mgr.start_next_step(
            task_node.wf_node_id,
            _step_config_id(workflow_for_reasons, "t_skip_then_step", 0),
        )
