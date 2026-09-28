"""GraphViz DOT export for workflow configurations."""

from typing import TextIO

from . import config as cfg
from .types import Language


def _format_condition(condition: str) -> str:
    return "\\n.".join(condition.split("."))


def _translate(value: cfg.TranslatedString | str, lang: Language) -> str:
    if isinstance(value, str):
        return value

    match lang:
        case Language.DE:
            return value.de
        case Language.FR:
            return value.fr
        case Language.IT:
            return value.it


def write_graph(config: cfg.Workflow, f: TextIO, lang: Language) -> None:
    """Write a GraphViz DOT representation of a workflow config."""
    f.write("digraph {\n")
    f.write(
        f"  graph [label=< <B>{_translate(config.title, lang)}</B><BR/><BR/> > labelloc=t]\n"
    )
    f.write("  node [shape=record]\n")
    f.write("\n")
    f.write("  __START__ [label=< <B>START</B> >]\n")
    f.write(f"  __START__ -> {config.start_task_ref}\n")
    for task_ref, task_config in config.tasks.items():
        if task_config.start_task and task_ref != config.start_task_ref:
            f.write(f'  __START__{task_ref} [label="START"]\n')
            f.write(f"  __START__{task_ref} -> {task_ref}\n")
        steps = "|".join(
            [
                f"[{s.type[0].upper()}] {_translate(s.title, lang)}{' *' if s.condition else ''}"
                for s in task_config.steps
            ]
        )
        sep = "|" if steps else ""
        f.write(
            f"  {task_ref} [label=< {{<B>{_translate(task_config.title, lang)}</B>{sep}{steps}}} >]\n"
        )
    f.write("\n")
    for task_ref, task_config in config.tasks.items():
        for link in task_config.links:
            condition = _format_condition(link.condition) if link.condition else ""
            f.write(
                f'  {task_ref} -> {link.task_ref} [label="{condition}" fontsize=10]\n'
            )
    f.write("}\n")
