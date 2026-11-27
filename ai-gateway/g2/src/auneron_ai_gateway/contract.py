"""Contrato estrito de entrada, JSON estrito e subconjunto proprio de JSON Schema."""

from __future__ import annotations

import json
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from .profiles import PROFILE_ID

MAX_BODY_BYTES = 32 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_JSON_DEPTH = 32
MAX_SCHEMA_DEPTH = 8
MAX_SCHEMA_NODES = 200
MAX_SCHEMA_BYTES = 8 * 1024
MAX_ENUM_ITEMS = 50

REQUEST_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{7,63}$"

_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
_SCHEMA_KEYWORDS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "description",
    "title",
}


class JsonError(ValueError):
    """JSON invalido. Mensagem fixa, sem trecho da entrada."""


def _reject_constant(_name: str) -> Any:
    raise JsonError("constante nao permitida")


def _reject_float(text: str) -> Any:
    value = float(text)
    if not math.isfinite(value):
        raise JsonError("numero nao finito")
    return value


def _pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise JsonError("chave duplicada")
        out[key] = value
    return out


def _scan_depth(data: bytes, max_depth: int) -> None:
    depth = 0
    in_str = False
    esc = False
    for byte in data:
        if in_str:
            if esc:
                esc = False
            elif byte == 0x5C:  # barra invertida
                esc = True
            elif byte == 0x22:
                in_str = False
            continue
        if byte == 0x22:
            in_str = True
        elif byte in (0x5B, 0x7B):  # [ {
            depth += 1
            if depth > max_depth:
                raise JsonError("aninhamento excessivo")
        elif byte in (0x5D, 0x7D):
            depth -= 1


def _check_strings(value: Any) -> None:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                raise JsonError("unicode invalido") from None
        elif isinstance(item, dict):
            for key, sub in item.items():
                stack.append(key)
                stack.append(sub)
        elif isinstance(item, list):
            stack.extend(item)


def strict_json_loads(data: bytes, *, max_depth: int = MAX_JSON_DEPTH) -> Any:
    """Decodifica JSON recusando: UTF-8 invalido, surrogates soltos, chaves duplicadas,
    NaN/Infinity, aninhamento acima do limite."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise JsonError("utf-8 invalido") from None
    if text.startswith("﻿"):
        raise JsonError("bom nao permitido")
    _scan_depth(data, max_depth)
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_hook,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
        )
    except JsonError:
        raise
    except (ValueError, RecursionError):
        raise JsonError("json invalido") from None
    _check_strings(value)
    return value


# ---------------------------------------------------------------- esquema JSON


class SchemaDefinitionError(ValueError):
    pass


def _check_schema_node(node: Any, depth: int, counter: list[int]) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        raise SchemaDefinitionError("esquema profundo demais")
    counter[0] += 1
    if counter[0] > MAX_SCHEMA_NODES:
        raise SchemaDefinitionError("esquema grande demais")
    if not isinstance(node, dict):
        raise SchemaDefinitionError("no de esquema invalido")
    for key in node:
        if key not in _SCHEMA_KEYWORDS:
            # inclui $ref, $defs, $schema, allOf, anyOf, patternProperties, pattern...
            raise SchemaDefinitionError("palavra-chave nao suportada")
    t = node.get("type")
    if t is not None:
        names = [t] if isinstance(t, str) else t
        if (
            not isinstance(names, list)
            or not names
            or not all(isinstance(n, str) and n in _TYPES for n in names)
        ):
            raise SchemaDefinitionError("type invalido")
    props = node.get("properties")
    if props is not None:
        if not isinstance(props, dict) or len(props) > 100:
            raise SchemaDefinitionError("properties invalido")
        for sub in props.values():
            _check_schema_node(sub, depth + 1, counter)
    req = node.get("required")
    if req is not None and (not isinstance(req, list) or not all(isinstance(r, str) for r in req)):
        raise SchemaDefinitionError("required invalido")
    ap = node.get("additionalProperties")
    if ap is not None and not isinstance(ap, bool):
        raise SchemaDefinitionError("additionalProperties deve ser booleano")
    items = node.get("items")
    if items is not None:
        _check_schema_node(items, depth + 1, counter)
    enum = node.get("enum")
    if enum is not None:
        if (
            not isinstance(enum, list)
            or not enum
            or len(enum) > MAX_ENUM_ITEMS
            or not all(e is None or isinstance(e, (str, int, float, bool)) for e in enum)
        ):
            raise SchemaDefinitionError("enum invalido")
    if "const" in node:
        c = node["const"]
        if not (c is None or isinstance(c, (str, int, float, bool))):
            raise SchemaDefinitionError("const invalido")
    for kw in ("minLength", "maxLength", "minItems", "maxItems"):
        v = node.get(kw)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0 or v > 1_000_000):
            raise SchemaDefinitionError("limite invalido")
    for kw in ("minimum", "maximum"):
        v = node.get(kw)
        if v is not None and (
            not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
        ):
            raise SchemaDefinitionError("limite invalido")
    for kw in ("description", "title"):
        v = node.get(kw)
        if v is not None and (not isinstance(v, str) or len(v) > 500):
            raise SchemaDefinitionError("texto invalido")


def validate_schema_definition(schema: Any) -> None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise SchemaDefinitionError("raiz deve ser type=object")
    try:
        size = len(json.dumps(schema, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError):
        raise SchemaDefinitionError("esquema nao serializavel") from None
    if size > MAX_SCHEMA_BYTES:
        raise SchemaDefinitionError("esquema grande demais")
    _check_schema_node(schema, 1, [0])


class SchemaMismatch(ValueError):
    pass


def _type_ok(name: str, value: Any) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "null":
        return value is None
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool) or (
            isinstance(value, float) and value.is_integer()
        )
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def validate_instance(schema: dict[str, Any], value: Any, depth: int = 0) -> None:
    """Valida `value` contra o subconjunto. Levanta SchemaMismatch."""
    if depth > MAX_JSON_DEPTH + 2:
        raise SchemaMismatch("profundo demais")
    t = schema.get("type")
    if t is not None:
        names = [t] if isinstance(t, str) else t
        if not any(_type_ok(n, value) for n in names):
            raise SchemaMismatch("tipo")
    if "enum" in schema and not any(_same(value, e) for e in schema["enum"]):
        raise SchemaMismatch("enum")
    if "const" in schema and not _same(value, schema["const"]):
        raise SchemaMismatch("const")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaMismatch("minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaMismatch("maxLength")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaMismatch("minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaMismatch("maximum")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaMismatch("minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaMismatch("maxItems")
        items = schema.get("items")
        if items is not None:
            for element in value:
                validate_instance(items, element, depth + 1)
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                raise SchemaMismatch("required")
        props = schema.get("properties", {})
        for key, sub in value.items():
            if key in props:
                validate_instance(props[key], sub, depth + 1)
            elif schema.get("additionalProperties", True) is False:
                raise SchemaMismatch("additionalProperties")


# ---------------------------------------------------------------- requisicao


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    profile: Literal["qwen3-4b-instruct-2507", "qwen3-4b-think"]
    instructions: StrictStr = Field(min_length=1, max_length=MAX_BODY_BYTES)
    input_text: StrictStr = Field(min_length=1, max_length=MAX_BODY_BYTES)
    output_schema: dict[str, Any]
    data_classification: Literal["synthetic"]
    request_id: StrictStr = Field(pattern=REQUEST_ID_PATTERN)

    @field_validator("output_schema")
    @classmethod
    def _schema_ok(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            validate_schema_definition(value)
        except SchemaDefinitionError:
            raise ValueError("esquema invalido") from None
        return value


assert PROFILE_ID == "qwen3-4b-instruct-2507"
