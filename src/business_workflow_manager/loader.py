"""Load and dump workflow configurations between YAML and database."""

from typing import Any, TextIO
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import config as cfg
from .events import EventHandlerMap
from .exceptions import EventHandlerNotRegistered, WorkflowNotFound
from .models import Document as MDocument
from .models import Form as MForm
from .models import Task as MTask
from .models import Translation, WorkflowConfig, WorkflowLink, WorkflowStep
from .models import Workflow as MWorkflow
from .types import Language


def load_workflow_from_file(
    session: Session, f: TextIO, event_handlers: EventHandlerMap | None = None
) -> tuple[MWorkflow, cfg.Workflow]:
    config = cfg.Workflow.read(f)
    return load_workflow(session, config, event_handlers), config


def create_translation(
    session: Session, key: str, translated_string: cfg.TranslatedString
) -> None:
    session.add_all(
        [
            Translation(key=key, value=translated_string.de, locale=Language.DE),
            Translation(key=key, value=translated_string.fr, locale=Language.FR),
            Translation(key=key, value=translated_string.it, locale=Language.IT),
        ]
    )


def load_workflow(
    session: Session,
    config: cfg.Workflow,
    event_handlers: EventHandlerMap | None = None,
) -> MWorkflow:
    """Load a workflow config into the database. Does NOT commit — caller must commit."""
    config.validate_keys()

    workflow_key = config.key or uuid4()
    workflow_translation_prefix = f"wf:{workflow_key}"

    # Clear existing translations for this workflow
    session.execute(
        delete(Translation).where(
            Translation.key.startswith(workflow_translation_prefix)
        )
    )

    def _translate(key: str, value: cfg.TranslatedString | str) -> str:
        key = f"{workflow_translation_prefix}:{key}"
        if isinstance(value, str):
            value = cfg.TranslatedString(de=value, fr=value, it=value)
        create_translation(session, key, value)
        return key

    def _dump_fields(prefix: str, fields: list[cfg.FormField]) -> list[Any]:
        result: list[Any] = []
        for field in fields:
            data = field.model_dump()
            if field.label:
                data["label"] = _translate(
                    f"{prefix}:field:{field.name}:label", field.label
                )
            if field.choices:
                for i, choice in enumerate(field.choices):
                    data["choices"][i]["label"] = _translate(
                        f"{prefix}:field:{field.name}:choice:{i + 1}:label",
                        choice.label,
                    )
            result.append(data)
        return result

    if config.key:
        if workflow := session.scalars(
            select(WorkflowConfig).where(WorkflowConfig.key == config.key)
        ).one_or_none():
            assert isinstance(workflow, MWorkflow)
            workflow.title = _translate("title", config.title)
            workflow.version = config.version
            workflow.min_per_entity = config.min_per_entity
            workflow.max_per_entity = config.max_per_entity
            workflow.time_period = config.time_period
        else:
            workflow = MWorkflow(
                key=config.key,
                title=_translate("title", config.title),
                workflow_id=None,
                version=config.version,
                min_per_entity=config.min_per_entity,
                max_per_entity=config.max_per_entity,
                time_period=config.time_period,
            )
    else:
        workflow = MWorkflow(
            key=workflow_key,
            title=_translate("title", config.title),
            workflow_id=None,
            version=config.version,
            min_per_entity=config.min_per_entity,
            max_per_entity=config.max_per_entity,
            time_period=config.time_period,
        )

    session.add(workflow)
    session.flush()
    workflow_id = workflow.wf_config_id
    assert workflow_id
    workflow.workflow_id = workflow_id

    tasks_by_ref: dict[str, MTask] = {}

    # Clear links (they will be re-created later)
    for task in workflow.tasks.values():
        session.execute(
            delete(WorkflowLink).where(WorkflowLink.task_id == task.wf_config_id)
        )

    # Delete tasks that are no longer in config
    tasks_to_delete_by_keys = set(t.key for t in workflow.tasks.values()) - set(
        t.key for t in config.tasks.values() if t.key is not None
    )
    for task in workflow.tasks.values():
        if task.key in tasks_to_delete_by_keys:
            for step in task.steps:
                session.delete(step.step)
            session.delete(task)

    for task_ref, task_config in config.tasks.items():
        if task_config.key:
            if task := session.scalars(
                select(WorkflowConfig).where(WorkflowConfig.key == task_config.key)
            ).one_or_none():
                assert isinstance(task, MTask)
                assert task.workflow_id == workflow_id
                task.title = _translate(f"task:{task_ref}:title", task_config.title)
                task.is_start_task = task_config.start_task
                task.triggers = validate_event_triggers(
                    task_config.triggers, event_handlers
                )
                task.time_period = task_config.time_period
                task.version = config.version
                task.name = task_ref
            else:
                task = MTask(
                    key=task_config.key,
                    workflow_id=workflow_id,
                    title=_translate(f"task:{task_ref}:title", task_config.title),
                    is_start_task=task_config.start_task,
                    time_period=task_config.time_period,
                    triggers=validate_event_triggers(
                        task_config.triggers, event_handlers
                    ),
                    version=config.version,
                    name=task_ref,
                )
        else:
            task = MTask(
                key=uuid4(),
                workflow_id=workflow_id,
                title=_translate(f"task:{task_ref}:title", task_config.title),
                is_start_task=task_config.start_task,
                time_period=task_config.time_period,
                triggers=validate_event_triggers(task_config.triggers, event_handlers),
                version=config.version,
                name=task_ref,
            )
        session.add(task)
        session.flush()
        tasks_by_ref[task_ref] = task

        steps_to_delete_by_keys = set(s.step.key for s in task.steps) - set(
            s.key for s in task_config.steps if s.key is not None
        )
        session.execute(
            delete(WorkflowConfig).where(
                WorkflowConfig.key.in_(steps_to_delete_by_keys),
                WorkflowConfig.workflow_id == workflow_id,
            )
        )

        # Clear and re-create step mappings
        session.execute(
            delete(WorkflowStep).where(WorkflowStep.task_id == task.wf_config_id)
        )

        for i, step_config in enumerate(task_config.steps, start=1):
            match step_config:
                case cfg.Document():
                    if step_config.key:
                        if step := session.scalars(
                            select(WorkflowConfig).where(
                                WorkflowConfig.key == step_config.key
                            )
                        ).one_or_none():
                            assert isinstance(step, MDocument)
                            assert step.workflow_id == workflow_id
                            step.title = _translate(
                                f"task:{task_ref}:step:{i}:title", step_config.title
                            )
                            step.is_optional = step_config.optional
                            step.time_period = step_config.time_period
                            step.version = config.version
                        else:
                            step = MDocument(
                                key=step_config.key,
                                workflow_id=workflow_id,
                                title=_translate(
                                    f"task:{task_ref}:step:{i}:title", step_config.title
                                ),
                                is_optional=step_config.optional,
                                time_period=step_config.time_period,
                                version=config.version,
                            )
                    else:
                        step = MDocument(
                            key=uuid4(),
                            workflow_id=workflow_id,
                            title=_translate(
                                f"task:{task_ref}:step:{i}:title", step_config.title
                            ),
                            is_optional=step_config.optional,
                            time_period=step_config.time_period,
                            version=config.version,
                        )
                case cfg.Form():
                    if step_config.key:
                        if step := session.scalars(
                            select(WorkflowConfig).where(
                                WorkflowConfig.key == step_config.key
                            )
                        ).one_or_none():
                            assert isinstance(step, MForm)
                            assert step.workflow_id == workflow_id
                            step.title = _translate(
                                f"task:{task_ref}:step:{i}:title", step_config.title
                            )
                            step.is_optional = step_config.optional
                            step.version = config.version
                            step.fields = _dump_fields(
                                f"task:{task_ref}:step:{i}", step_config.fields
                            )
                            step.name = step_config.name
                            step.time_period = step_config.time_period
                        else:
                            step = MForm(
                                key=step_config.key,
                                workflow_id=workflow_id,
                                title=_translate(
                                    f"task:{task_ref}:step:{i}:title", step_config.title
                                ),
                                is_optional=step_config.optional,
                                version=config.version,
                                fields=_dump_fields(
                                    f"task:{task_ref}:step:{i}", step_config.fields
                                ),
                                name=step_config.name,
                                time_period=step_config.time_period,
                            )
                    else:
                        step = MForm(
                            key=uuid4(),
                            workflow_id=workflow_id,
                            title=_translate(
                                f"task:{task_ref}:step:{i}:title", step_config.title
                            ),
                            is_optional=step_config.optional,
                            version=config.version,
                            fields=_dump_fields(
                                f"task:{task_ref}:step:{i}", step_config.fields
                            ),
                            name=step_config.name,
                            time_period=step_config.time_period,
                        )
                case _:  # type: ignore
                    raise ValueError(f"Unknown step type: {step_config}")

            wf_step = WorkflowStep(
                task=task,
                step=step,
                step_no=i,
                condition=step_config.condition,
            )
            session.add(step)
            session.add(wf_step)

    # After creating all tasks, create the links
    for task_ref, task_config in config.tasks.items():
        task = tasks_by_ref[task_ref]
        for i, link_config in enumerate(task_config.links, start=1):
            target_task = tasks_by_ref[link_config.task_ref]
            wf_link = WorkflowLink(
                task=task,
                link_no=i,
                target=target_task,
                condition=link_config.condition,
            )
            session.add(wf_link)

    workflow.start_task_id = tasks_by_ref[config.start_task_ref].wf_config_id
    assert workflow.start_task_id

    session.flush()
    return workflow


def dump_workflow_to_file(session: Session, key: str | UUID, f: TextIO) -> int:
    if isinstance(key, str):
        key = UUID(key)
    workflow = session.scalars(
        select(MWorkflow).where(MWorkflow.key == key)
    ).one_or_none()
    if workflow is None:
        raise WorkflowNotFound(str(key))

    config = dump_workflow(session, workflow)
    return config.write(f)


def get_translations(session: Session, workflow_key: str) -> dict[str, dict[str, str]]:
    result = dict[str, dict[str, str]]()
    query = select(Translation).where(Translation.key.startswith(f"wf:{workflow_key}"))
    for translation in session.scalars(query):
        result.setdefault(translation.key, {})[translation.locale] = translation.value
    return result


def validate_event_triggers(
    triggers: list[cfg.EventTrigger], event_handlers: EventHandlerMap | None = None
) -> list[dict[str, Any]]:
    if event_handlers is not None:
        registered_event_names = {
            event_type.__name__
            for event_type, handlers in event_handlers.items()
            if handlers
        }
        for trigger in triggers:
            if trigger.type not in registered_event_names:
                raise EventHandlerNotRegistered(
                    f"No handler registered for event {trigger.type!r}"
                )
    return [t.model_dump() for t in triggers]


def dump_workflow(session: Session, workflow: MWorkflow) -> cfg.Workflow:
    assert workflow.start_task

    translations = get_translations(session, str(workflow.key))

    def _translated(key: str) -> cfg.TranslatedString | str:
        if not key:
            return ""
        translation = translations[key]
        if translation["de"] == translation["fr"] == translation["it"]:
            return translation["de"]
        return cfg.TranslatedString(**translation)

    config = cfg.Workflow(
        key=workflow.key,
        title=_translated(workflow.title),
        version=workflow.version,
        start_task_ref=workflow.start_task.name,
        min_per_entity=workflow.min_per_entity,
        max_per_entity=workflow.max_per_entity,
        tasks={},
        time_period=workflow.time_period,
    )
    task_ref_by_id: dict[int, str] = {}

    for task_ref, task in workflow.tasks.items():
        task_config = cfg.Task(
            key=task.key,
            title=_translated(task.title),
            start_task=task.is_start_task or False,
            steps=[],
            links=[],
            triggers=[cfg.EventTrigger(**t) for t in task.triggers],
            time_period=task.time_period,
        )
        task_ref_by_id[task.wf_config_id] = task_ref
        config.tasks[task_ref] = task_config
        for task_step in task.steps:
            match task_step.step:
                case MDocument() as doc:
                    doc_config = cfg.Document(
                        type="document",
                        key=doc.key,
                        title=_translated(doc.title),
                        optional=doc.is_optional,
                        time_period=doc.time_period,
                        condition=task_step.condition,
                    )
                    task_config.steps.append(doc_config)
                case MForm() as form:
                    fields: list[cfg.FormField] = []
                    for field in form.fields:
                        field_cfg = cfg.FormField(
                            name=field["name"],
                            type=field["type"],
                            label=_translated(field["label"]),
                        )
                        if field.get("choices") is not None:
                            field_cfg.choices = []
                            for choice in field["choices"]:
                                field_cfg.choices.append(
                                    cfg.Choice(
                                        label=_translated(choice["label"]),
                                        value=choice["value"],
                                    )
                                )
                        fields.append(field_cfg)
                    form_config = cfg.Form(
                        type="form",
                        key=form.key,
                        title=_translated(form.title),
                        optional=form.is_optional,
                        time_period=form.time_period,
                        name=form.name,
                        condition=task_step.condition,
                        fields=fields,
                    )
                    task_config.steps.append(form_config)
                case _:  # pyright: ignore[reportUnnecessaryComparison]
                    raise ValueError(f"Unknown task step: {task_step.step}")

    for task_ref, task in workflow.tasks.items():
        task_config = config.tasks[task_ref]
        for link in task.links:
            target_ref = task_ref_by_id[link.target_task_id]
            task_config.links.append(
                cfg.TaskLink(task_ref=target_ref, condition=link.condition)
            )

    return config
