# Copyright (c) 2026 Memai. Licensed under AGPL-3.0.
"""TOML reader for persona bundles (see docs/BRIEF_phase11_bundle_format.md).

A bundle is a plain directory — bundle.toml manifest + lessons/*.toml — read via
stdlib tomllib. memai only ever READS bundles; authoring tools live outside this
repo and meet memai at this versioned file format.
"""
import tomllib
from datetime import date, datetime
from pathlib import Path

from ..domain.model import DEFAULT_VOICE_ROLE, Language, MemoryType
from ..services.ports import (
    BUNDLE_FORMAT_VERSION,
    BundleFormatError,
    BundleItemSpec,
    BundleLesson,
    BundlePersonaDefinition,
    PersonaBundle,
)

_ITEM_TYPES = {"concept": MemoryType.CONCEPT, "procedure": MemoryType.PROCEDURE}
# Allowlist per the format spec. engagement_level / persona_state / embedding are
# structurally impossible in a bundle — reject loudly rather than silently ignore.
_ITEM_KEYS = {"type", "name", "category", "language", "description", "steps"}
_PERSONA_KEYS = {"name", "system_prompt", "languages", "response_language", "voices", "settings", "strategy"}


class TomlPersonaBundleSource:
    """Implements PersonaBundleSource. Parse-and-reject: any malformation raises
    BundleFormatError with the offending file/entry named."""

    def load(self, path: Path) -> PersonaBundle:
        path = Path(path)
        if not path.is_dir():
            raise BundleFormatError(f"bundle path is not a directory: {path}")
        manifest = _load_toml(path / "bundle.toml")

        format_version = manifest.get("format_version")
        if format_version != BUNDLE_FORMAT_VERSION:
            raise BundleFormatError(
                f"unsupported format_version {format_version!r} — this memai reads version {BUNDLE_FORMAT_VERSION}"
            )
        persona_key = manifest.get("persona_key")
        if not isinstance(persona_key, str) or not persona_key.strip():
            raise BundleFormatError("persona_key is required and must be a non-empty string")

        bundle_table = manifest.get("bundle")
        if not isinstance(bundle_table, dict):
            raise BundleFormatError("[bundle] table is required")
        name = _require_str(bundle_table, "name", "[bundle]")
        version = _require_str(bundle_table, "version", "[bundle]")
        author = _require_str(bundle_table, "author", "[bundle]")
        description = bundle_table.get("description", "")
        if not isinstance(description, str):
            raise BundleFormatError("[bundle] description must be a string")

        provenance = manifest.get("provenance", {})
        if not isinstance(provenance, dict):
            raise BundleFormatError("[provenance] must be a table")

        lessons_dir = path / "lessons"
        lesson_files = sorted(lessons_dir.glob("*.toml"), key=lambda p: p.name) if lessons_dir.is_dir() else []
        if not lesson_files:
            raise BundleFormatError(f"no lesson files found under {lessons_dir}")

        return PersonaBundle(
            persona_key=persona_key,
            name=name,
            version=version,
            author=author,
            description=description,
            manifest=_json_safe({"bundle": bundle_table, "provenance": provenance}),
            persona=_parse_persona(manifest.get("persona")),
            lessons=tuple(_parse_lesson(f) for f in lesson_files),
        )


def _load_toml(file: Path) -> dict:
    if not file.is_file():
        raise BundleFormatError(f"{file} not found")
    try:
        with file.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise BundleFormatError(f"{file.name}: invalid TOML — {exc}") from exc


def _require_str(table: dict, key: str, where: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BundleFormatError(f"{where}: '{key}' is required and must be a non-empty string")
    return value


def _parse_persona(table: object) -> BundlePersonaDefinition | None:
    if table is None:
        return None
    if not isinstance(table, dict):
        raise BundleFormatError("[persona] must be a table")
    unknown = set(table) - _PERSONA_KEYS
    if unknown:
        raise BundleFormatError(f"[persona]: unknown keys {sorted(unknown)}")

    languages_raw = table.get("languages")
    if (
        not isinstance(languages_raw, list)
        or not languages_raw
        or not all(isinstance(c, str) and c.strip() for c in languages_raw)
    ):
        raise BundleFormatError("[persona]: 'languages' must be a non-empty list of language codes")

    voices = table.get("voices", {})
    if not isinstance(voices, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in voices.items()
    ):
        raise BundleFormatError("[persona.voices] must map speaker roles to voice names")
    if "|" in voices.get(DEFAULT_VOICE_ROLE, ""):
        raise BundleFormatError(
            f'[persona.voices]: the "{DEFAULT_VOICE_ROLE}" voice must be a single voice '
            '(the fixed anchor) — "|" rotation pools are only for additional roles'
        )

    settings = table.get("settings")
    if settings is not None and not isinstance(settings, dict):
        raise BundleFormatError("[persona.settings] must be a table")

    strategy = table.get("strategy")
    if strategy is not None and (not isinstance(strategy, str) or not strategy.strip()):
        raise BundleFormatError("[persona]: 'strategy' must be a non-empty string when present")

    return BundlePersonaDefinition(
        name=_require_str(table, "name", "[persona]"),
        system_prompt=_require_str(table, "system_prompt", "[persona]"),
        languages=tuple(Language(c) for c in languages_raw),
        response_language=Language(_require_str(table, "response_language", "[persona]")),
        voices=dict(voices),
        settings=_json_safe(settings) if settings is not None else None,
        strategy=strategy,
    )


def _parse_lesson(file: Path) -> BundleLesson:
    data = _load_toml(file)
    items_raw = data.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        raise BundleFormatError(f"{file.name}: lesson file must contain at least one [[items]] entry")
    # 'title' is authoring metadata only — never persisted, deliberately not parsed.
    return BundleLesson(
        filename=file.name,
        items=tuple(_parse_item(entry, file.name, i) for i, entry in enumerate(items_raw, start=1)),
    )


def _parse_item(entry: object, filename: str, position: int) -> BundleItemSpec:
    where = f"{filename} [[items]] #{position}"
    if not isinstance(entry, dict):
        raise BundleFormatError(f"{where}: item must be a table")
    unknown = set(entry) - _ITEM_KEYS
    if unknown:
        raise BundleFormatError(
            f"{where}: unknown keys {sorted(unknown)} — items carry no engagement_level, "
            "persona_state, or embedding (installer-owned)"
        )

    type_raw = _require_str(entry, "type", where)
    memory_type = _ITEM_TYPES.get(type_raw)
    if memory_type is None:
        raise BundleFormatError(f"{where}: type must be 'concept' or 'procedure', got {type_raw!r}")

    steps_raw = entry.get("steps", [])
    if memory_type is MemoryType.CONCEPT and steps_raw:
        raise BundleFormatError(f"{where}: 'steps' is only valid on procedures")
    if not isinstance(steps_raw, list) or not all(isinstance(s, str) for s in steps_raw):
        raise BundleFormatError(f"{where}: 'steps' must be a list of strings")

    category = entry.get("category")
    if category is not None and not isinstance(category, str):
        raise BundleFormatError(f"{where}: 'category' must be a string")

    return BundleItemSpec(
        memory_type=memory_type,
        name=_require_str(entry, "name", where),
        description=_require_str(entry, "description", where),
        language=Language(_require_str(entry, "language", where)),
        category=category,
        steps=tuple(steps_raw),
    )


def _json_safe(value: object) -> object:
    """TOML natively parses dates/datetimes (e.g. provenance's generated_at); the manifest
    is persisted to a JSONB column, so coerce them to ISO strings on the way in."""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value
