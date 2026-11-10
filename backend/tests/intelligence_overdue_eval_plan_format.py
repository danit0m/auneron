"""Area 3: the JSON Schema sent as ``format`` in the PLAN stage of the overdue evaluation.

PURE and deterministic: no network, no clock, no randomness. Defined and exercised ONLY under
``tests/``; nothing in ``app/`` is changed.

The source of truth is ``app.intelligence.planning.schema.build_plan_schema`` (the plan.v1
schema generated FROM the capability registry, ``llm_view`` on). Nothing is copied by hand
here. This module only reshapes that document so that the runtime can turn it into a grammar:

* ``$ref`` to ``#/$defs/...`` is inlined (the grammar converter is only trusted with the
  keywords listed in ``FORMAT_KEYWORDS``);
* ``$schema`` and ``$defs`` are dropped (they are annotations/containers, not constraints).

No constraint is added or removed by this reshaping: ``tests`` prove that the flattened
document and the original one give the same verdict on a synthetic corpus, and that both
agree with the structural stages of the real validator (declared gaps in ``DECLARED_GAPS``).

The keywords are limited to the subset that the llama.cpp JSON-Schema-to-grammar converter
(used by the Ollama ``format`` field) documents as supported. This is an ASSUMPTION about the
runtime that only the second short proof can confirm; it is not verified offline.
"""

from __future__ import annotations

import copy
from typing import Any, Final

from app.intelligence.planning.schema import JSON_SCHEMA_DIALECT, build_plan_schema

# Keywords the final schema may use (and ONLY these). Each is part of the subset the
# llama.cpp converter supports: types and object/array structure, closed objects, fixed
# values (const/enum), alternatives (anyOf), bounded strings/arrays/integers and ``pattern``.
FORMAT_KEYWORDS: Final[frozenset[str]] = frozenset({
    "type", "properties", "required", "additionalProperties", "items",
    "anyOf", "const", "enum", "pattern",
    "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems",
})
# Keywords deliberately NOT used because the converter does not support them well (or at all).
AVOIDED_KEYWORDS: Final[frozenset[str]] = frozenset({
    "$schema", "$defs", "$ref", "definitions", "oneOf", "allOf", "not", "if", "then", "else",
    "format", "uniqueItems", "patternProperties", "propertyNames", "dependentRequired",
    "dependentSchemas", "contains", "prefixItems", "minProperties", "maxProperties",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "unevaluatedProperties",
})
# What the schema cannot say (the grammar only constrains the SHAPE of the text). Each of
# these is still refused by the real validator after the answer is parsed.
DECLARED_GAPS: Final[tuple[str, ...]] = (
    "calendar_date",           # a well-formed date that does not exist (2026-02-30)
    "string_content",          # NFC normalisation and control characters inside strings
    "integer_magnitude",       # integers above the validator's max_integer
    "float_integer",           # 1.0 where an integer is wanted (JSON Schema accepts it)
    "duplicate_keys",          # repeated object keys in the raw text
    "input_size_and_depth",    # bytes and nesting limits of the raw text
)

_REF_PREFIX: Final[str] = "#/$defs/"


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_inline(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if ref is not None:
        if not (isinstance(ref, str) and ref.startswith(_REF_PREFIX) and ref[len(_REF_PREFIX):] in defs):
            raise ValueError("unresolvable_ref")
        if set(node) != {"$ref"}:
            raise ValueError("ref_with_siblings")
        return _inline(copy.deepcopy(defs[ref[len(_REF_PREFIX):]]), defs)
    return {key: _inline(value, defs) for key, value in node.items()}


def plan_format_schema(registry: Any) -> dict:
    """The ``format`` of the plan stage: the plan.v1 schema of ``registry``, flattened.

    ``registry`` must already be the verified snapshot (the caller checks the pinned digest).
    """
    source = build_plan_schema(registry, llm_view=True)
    if source.get("$schema") != JSON_SCHEMA_DIALECT:
        raise ValueError("unexpected_dialect")
    defs = source.get("$defs", {})
    body = {key: value for key, value in source.items() if key not in ("$schema", "$defs")}
    flat = _inline(copy.deepcopy(body), defs)
    if "$ref" in repr(flat) or "$defs" in repr(flat):
        raise ValueError("ref_left")
    return flat


def keywords_used(schema: Any) -> set[str]:
    """Every JSON Schema keyword the document uses (property NAMES are not keywords)."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            found.add(key)
            if key == "properties" and isinstance(value, dict):
                for sub in value.values():
                    walk(sub)
            elif key in ("items", "additionalProperties", "anyOf"):
                walk(value)

    walk(schema)
    return found
