"""Pydantic models for reading workflow configuration in YAML format."""

import uuid
from typing import Annotated, Any, Literal, Self, TextIO, TypeAlias

import pydantic
import yaml


class ValidationError(Exception):
    pass


class BaseModel(pydantic.BaseModel):
    model_config = {
        "extra": "forbid",
    }


class TranslatedString(BaseModel):
    de: str
    fr: str
    it: str


class HasKey(BaseModel):
    key: uuid.UUID | None = pydantic.Field(default=None)

    @pydantic.field_serializer("key")
    def serialize_key(self, key: str | None, _info: object) -> str | None:
        if key is None:
            return None
        return str(key)


class Choice(BaseModel):
    label: TranslatedString | str
    value: str


class FormField(BaseModel):
    name: str
    type: str
    label: TranslatedString | str = ""
    choices: list[Choice] | None = None


class BaseStep(HasKey, BaseModel):
    title: TranslatedString | str
    condition: str | None = None
    optional: bool = False
    time_period: int | None = None


class Document(BaseStep):
    type: Literal["document"]


class Form(BaseStep):
    type: Literal["form"]
    name: str
    fields: list[FormField]


TaskStep: TypeAlias = Annotated[Document | Form, pydantic.Field(discriminator="type")]


class TaskLink(BaseModel):
    task_ref: str
    condition: str | None = None


class EventTrigger(BaseModel):
    """A trigger that fires an event when a task node reaches FINISHED status.

    ``type`` must match the ``__name__`` of a registered event class.

    ``value`` controls how the event is instantiated at runtime:

    * **Mapping** (YAML object) — unpacked as keyword arguments:
      ``event_cls(**value)``
    * **Scalar** (string, int, …) — passed as a single positional argument:
      ``event_cls(value)``
    * Omitted — equivalent to an empty mapping, so ``event_cls()`` is called.
    """

    type: str
    value: Any = pydantic.Field(default_factory=dict)


class Task(HasKey, BaseModel):
    title: TranslatedString | str
    steps: list[TaskStep] = pydantic.Field(default_factory=list[TaskStep])
    links: list[TaskLink]
    triggers: list[EventTrigger] = pydantic.Field(default_factory=list[EventTrigger])
    start_task: bool = False
    time_period: int | None = None

    def validate_keys(self) -> None:
        for step in self.steps:
            if not self.key and step.key:
                raise ValidationError(
                    f"Step {step.title!r} ({step.type}) of task {self.title!r} must not have a key."
                )


class Workflow(HasKey, BaseModel):
    title: TranslatedString | str
    version: int
    start_task_ref: str
    min_per_entity: int
    max_per_entity: int | None
    tasks: dict[str, Task]
    time_period: int | None = None

    @classmethod
    def read(cls, f: TextIO) -> Self:
        document = yaml.safe_load(f)
        try:
            workflow_data = document["workflow"]
        except KeyError:
            raise ValueError("Top-level key 'workflow' not found in input data")
        config = cls.model_validate(workflow_data)
        config.validate_keys()
        return config

    def write(self, f: TextIO) -> int:
        self.validate_keys()
        model_data = self.model_dump(exclude_defaults=True, round_trip=True)
        yaml_data = yaml.safe_dump(
            {"workflow": model_data}, allow_unicode=True, sort_keys=False
        )
        return f.write(yaml_data)

    def validate_keys(self) -> None:
        for task_ref, task in self.tasks.items():
            if not self.key and task.key:
                raise ValidationError(
                    f"Task {task_ref} of process {self.title!r} must not have a key."
                )
            task.validate_keys()
