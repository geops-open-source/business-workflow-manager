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
