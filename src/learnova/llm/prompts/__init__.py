"""Prompt templates as Markdown files, so they can be tuned without touching code."""

from importlib.resources import files


def load(name: str) -> str:
    return files(__name__).joinpath(f"{name}.md").read_text(encoding="utf-8")
