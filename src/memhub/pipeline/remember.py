"""`/remember ...`: a user's explicit order to keep something, saved without the strictness of background extraction.

The order either says what to keep ("/remember prefiro ser chamada de Bia") or points at the conversation
("/remember save this case"): then the messages above it are read, with their context, and what they are about is kept."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from memhub.config import Settings
from memhub.pipeline.extract import ExtractResult, _labels, _relocate, _resolve_id, _sample
from memhub.pipeline.segment import Segment
from memhub.types import TypeRegistry

REMEMBER_INSTRUCTIONS = (
    "The user typed an explicit command asking you to remember something: the last message starts with the command. Two "
    "cases. (1) The command says what to keep: keep exactly that, one memory for each distinct thing, and quote the words "
    "after the command. (2) The command only points at the conversation (\"save this\", \"guarda isso\"): read the messages "
    "ABOVE it, with their context, work out what is being referred to (usually what was just discussed) and keep that, "
    "in the type that fits. Never invent a part: use \"not stated\" for a field the conversation does not give. Write in the "
    "language of the conversation, without \"the user\", never in the first person; `content` is your own rewrite and each "
    "`quote` is copied verbatim from ONE message (cite its id). Use absolute dates. Utility 5, assertion `stated`. If "
    "nothing can be worked out, return an empty list. A project usually adds its own rules here "
    "(`extraction.remember_instructions`)."
)


def is_command(content: str, command: str | None) -> bool:
    return bool(command) and content.lstrip().lower().startswith(command.lower())


def command_messages(segment: Segment, command: str | None) -> list:
    return [m for m in segment.messages if m.role == "user" and is_command(m.content, command)]


def without_commands(segment: Segment, command: str | None) -> Segment:
    """The segment as the background extractor sees it: the command messages are handled separately."""
    skip = {m.message_id for m in command_messages(segment, command)}
    return replace(segment, messages=[m for m in segment.messages if m.message_id not in skip]) if skip else segment


def remember_types(settings: Settings) -> list[str]:
    """What an order may create: the semantic kinds and any type marked `on_remember` (a `case`), never episodes or terms."""
    skip = {"area", "term", "episode", "skill"}
    return [n for n, c in settings.types.items() if n not in skip and (c.extract or c.on_remember)]


def extract_remembered(
    extractor: Any, segment: Segment, *, settings: Settings, registry: TypeRegistry, areas=None, history=None,
) -> ExtractResult:
    """Candidates for what each command message asks to keep, read with the messages above it; their evidence points at
    real message ids."""
    names = remember_types(settings)
    out, seen = ExtractResult(), set()
    if not names:
        return out
    thread = history or segment.messages  # the whole thread: the case may have been discussed in an earlier slice
    for cmd in command_messages(segment, settings.ingestion.remember_command):
        at = next(i for i, m in enumerate(thread) if m.message_id == cmd.message_id)
        window = thread[max(0, at - settings.ingestion.remember_context): at + 1]
        only = replace(segment, messages=window, is_final_pass=False)
        result, found = _sample(extractor, only, names, settings.extraction.remember_instructions or REMEMBER_INSTRUCTIONS, 5, 1, settings=settings, registry=registry, areas=areas)
        out.tokens_in += result.tokens_in
        out.tokens_out += result.tokens_out
        labels, real = _labels(only), {m.message_id for m in window}
        for c in found:
            for ev in c.evidence:
                ev.message_id = _resolve_id(ev.message_id, labels, real)
                _relocate(ev, only)
            key = (c.type, str(getattr(c.fields, "content", c.fields)))
            if key not in seen:
                seen.add(key)
                out.candidates.append(c)
    return out
