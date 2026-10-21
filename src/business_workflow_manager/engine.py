"""Workflow runtime engine: condition evaluation and step progression."""

import logging
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, TypeVar

import jmespath
from sqlalchemy import select
from sqlalchemy.orm import Session

from .exceptions import InvalidWorkflowStep
from .models import (
    Document,
    DocumentNode,
    Form,
    FormNode,
    Node,
    Task,
    TaskNode,
    Workflow,
    WorkflowConfig,
    WorkflowNode,
)
from .types import NextStepUnavailableReason, NodeStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfigStepInfo:
    """Valid next step configs of a node, plus all reasons that restricted them."""

    available_configs: Sequence[WorkflowConfig]
    unavailable_reasons: set[NextStepUnavailableReason] = field(
        default_factory=set[NextStepUnavailableReason]
    )


def default_is_applicable(session: Session, workflow: Workflow, entity_id: str) -> bool:
    """Default applicability check based on ``workflow.max_per_entity``.

    Returns true if the number of existing workflow instances for the entity
    is below ``max_per_entity`` (or unconditionally if it is ``None``).
    """
    workflow_nodes = session.scalars(
        select(WorkflowNode).where(
            WorkflowNode.entity_id == entity_id,
            WorkflowNode.wf_config_id == workflow.wf_config_id,
        )
    ).all()

    if workflow.max_per_entity is not None:
        return len(workflow_nodes) < workflow.max_per_entity

    return True


class Engine:
    def __init__(
        self,
        session: Session,
        get_entity_data: Callable[
            [Session, str], dict[str, Any]
        ] = lambda session, entity_id: {},
        is_applicable: Callable[[Session, Workflow, str], bool] | None = None,
        entity_data_key: str = "entity",
    ):
        self.session = session
        self.get_entity_data = get_entity_data
        self._is_applicable = is_applicable or default_is_applicable
        self.entity_data_key = entity_data_key

    def is_applicable(self, workflow: Workflow, entity_id: str) -> bool:
        """Returns true if an instance of the workflow can be created for the entity.

        Uses the custom callback passed to the constructor if provided,
        otherwise falls back to :func:`default_is_applicable`.
        """
        return self._is_applicable(self.session, workflow, entity_id)

    def evaluate_condition(self, node: Node, condition: str) -> Any:
        """Evaluate a jmespath condition against node data.

        The entity data returned by ``get_entity_data`` is injected under
        the key configured via ``entity_data_key`` (default: ``"entity"``).
        """
        data = (
            node.data.copy()
        )  # Make sure to create a new object so changes are persisted
        data[self.entity_data_key] = self.get_entity_data(self.session, node.entity_id)
        node.data = data
        result = jmespath.search(condition, data)
        logger.debug(f"jmespath.search({condition!r}, {data!r}) == {result!r}")  # noqa G004
        return result

    def get_valid_links(
        self, node: TaskNode, ref_node: Node | None = None
    ) -> ConfigStepInfo:
        """Evaluate the links of a task node.

        ``END`` is never returned as a config. Reasons:

        - ``STEP_NOT_CLOSED`` if a link condition was not met (e.g. a form
          selection is missing),
        - ``END_OF_WORKFLOW`` if a valid link points to ``END`` or the task
          has no links at all.
        """
        result: list[Task] = []
        reasons: set[NextStepUnavailableReason] = set()
        assert isinstance(node.config, Task)
        if not node.config.links:
            reasons.add(NextStepUnavailableReason.END_OF_WORKFLOW)
        for link in node.config.links:
            if link.condition and not self.evaluate_condition(
                ref_node or node, link.condition
            ):
                reason = NextStepUnavailableReason.STEP_NOT_CLOSED
            elif link.target.name == "END":
                reason = NextStepUnavailableReason.END_OF_WORKFLOW
            else:
                result.append(link.target)
                continue
            reasons.add(reason)
        return ConfigStepInfo(result, reasons)

    def get_next_task_steps(
        self, node: TaskNode, after_step: Form | Document | None = None
    ) -> ConfigStepInfo:
        """Determine the next steps within a task.

        Adds ``STEP_NOT_CLOSED`` if a step was skipped because its condition
        was not met.
        """
        next_steps: list[Form | Document] = []
        reasons: set[NextStepUnavailableReason] = set()

        assert isinstance(node.config, Task)
        available_steps = node.config.steps

        if after_step:
            all_steps = [step.step for step in node.config.steps]
            curr_step = all_steps.index(after_step)
            available_steps = available_steps[curr_step + 1 :]

        for step in available_steps:
            assert isinstance(step.step, Form | Document)
            if step.step.is_optional and not step.condition:
                next_steps.append(step.step)
            else:
                if step.condition:
                    if self.evaluate_condition(node, step.condition):
                        next_steps.append(step.step)
                    else:
                        reasons.add(NextStepUnavailableReason.STEP_NOT_CLOSED)
                        continue
                else:
                    next_steps.append(step.step)
                break
        return ConfigStepInfo(next_steps, reasons)

    def get_next_steps(self, node: Node) -> ConfigStepInfo:
        """Determine the valid next steps of a node and why others are unavailable.

        Nodes that are skipped or inactive (or whose parent task is) yield
        neither options nor reasons.
        """
        if not node.config:
            if not node.parent:
                return ConfigStepInfo(
                    [], {NextStepUnavailableReason.TASK_WITHOUT_WORKFLOW}
                )
            return self.get_next_steps(node.parent)
        if node.next_node:
            node = get_last_step(node)

        match node:
            case WorkflowNode():
                if node.status != NodeStatus.STARTED:
                    logger.warning(
                        "Workflow Node has no valid next steps because it is not started: %s",
                        node,
                    )
                    if node.status == NodeStatus.FINISHED:
                        return ConfigStepInfo(
                            [], {NextStepUnavailableReason.TASK_CLOSED}
                        )
                    return ConfigStepInfo([])
                assert isinstance(node.config, Workflow)
                return ConfigStepInfo(
                    [node.config.start_task]
                    + [
                        t
                        for t in node.config.tasks.values()
                        if t.is_start_task and t is not node.config.start_task
                    ]
                )

            case TaskNode():
                assert isinstance(node.config, Task)
                if node.status == NodeStatus.FINISHED:
                    return self.get_valid_links(node)
                if node.status != NodeStatus.STARTED:
                    logger.warning(
                        "Task Node has no valid next steps because it is not started: %s",
                        node,
                    )
                    return ConfigStepInfo([])
                steps = self.get_next_task_steps(node)
                if steps.available_configs:
                    return steps
                logger.warning(
                    "Task Node has no valid next steps because none are left: %s",
                    node,
                )
                return ConfigStepInfo(
                    [],
                    steps.unavailable_reasons
                    | {NextStepUnavailableReason.STEP_NOT_CLOSED},
                )

            case FormNode() | DocumentNode():
                if node.status != NodeStatus.FINISHED:
                    logger.warning(
                        "Form or Document Node has no valid next steps because it is not finished: %s",
                        node,
                    )
                    return ConfigStepInfo(
                        [], {NextStepUnavailableReason.STEP_NOT_CLOSED}
                    )
                assert isinstance(node.config, Form | Document)

                task_node = node.parent
                assert isinstance(task_node, TaskNode)
                assert isinstance(task_node.config, Task)

                if task_node.status != NodeStatus.STARTED:
                    logger.warning(
                        "Form or Document Node has no valid next steps as parent task is not marked as STARTED: %s",
                        node,
                    )
                    if task_node.status == NodeStatus.FINISHED:
                        return ConfigStepInfo(
                            [], {NextStepUnavailableReason.TASK_CLOSED}
                        )
                    return ConfigStepInfo([])

                steps = self.get_next_task_steps(task_node, after_step=node.config)
                if steps.available_configs:
                    return steps

                logger.warning(
                    "Parent task of node has no more steps left: %s",
                    node,
                )
                links = self.get_valid_links(task_node, ref_node=node)
                return ConfigStepInfo(
                    links.available_configs,
                    steps.unavailable_reasons | links.unavailable_reasons,
                )

            case _:
                logger.warning("Can not get valid next step for node: %s", node)
                return ConfigStepInfo([])

    def start_next_step(self, node: Node, wf_config: WorkflowConfig) -> Node:
        if not isinstance(wf_config, Task | Document | Form):
            raise InvalidWorkflowStep(f"Not a valid next step: {wf_config}")

        if not node.config:
            assert node.parent
            return self.start_next_step(node.parent, wf_config)

        if node.next_node:
            node = get_last_step(node)

        valid_steps = self.get_next_steps(node).available_configs
        if wf_config not in valid_steps:
            valid_steps_str = "\n".join(str(s) for s in valid_steps)
            raise InvalidWorkflowStep(
                f"Not a valid next step for {node}:\n{wf_config}\nValid steps:\n{valid_steps_str}"
            )

        next_node = wf_config.create_node(entity_id=node.entity_id)

        node.next_node = next_node
        next_node.data = deepcopy(node.data)

        match next_node:
            case TaskNode():
                next_node.parent = get_parent_workflow(node)
            case DocumentNode() | FormNode():
                next_node.parent = get_parent_task(node)

        return next_node


NodeT = TypeVar("NodeT", bound=Node)


def get_parent_of_type(node: Node, node_type: type[NodeT]) -> NodeT | None:
    current_node: Node = node
    while not isinstance(current_node, node_type):
        if current_node.parent is not None:
            current_node = current_node.parent
        else:
            return None
    return current_node


def get_parent_task(node: Node) -> TaskNode:
    if task_node := get_parent_of_type(node, TaskNode):
        return task_node
    raise ValueError(f"Can not get parent task for node: {node}")


def get_parent_workflow(node: Node) -> WorkflowNode:
    if workflow_node := get_parent_of_type(node, WorkflowNode):
        return workflow_node
    raise ValueError(f"Can not get parent workflow for node: {node}")


def get_last_step(node: Node) -> Node:
    """Follow node.next_node links and return the last node."""
    if not node.next_node:
        return node
    while node.next_node:
        node = node.next_node
    return node
