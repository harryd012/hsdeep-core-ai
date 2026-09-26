"""
Custom-property definition validation (Phase 6.3E §7/§8).

Pure functions, no database and no evaluation. A definition is untrusted tenant
configuration, so:
  * keys are restricted to a safe identifier shape;
  * `data_type` must be one of the controlled types;
  * `validation` accepts only whitelisted DECLARATIVE bounds - an expression can
    never be supplied because nothing here is ever eval'd or compiled from
    tenant input beyond a bounded regex that is itself shape-checked;
  * enum definitions must declare allowed values and their default must be one
    of them;
  * a required property may not declare an empty default (that combination is
    contradictory).
"""
from __future__ import annotations

import re

from app.models.tenant_custom_property import (
    ALLOWED_VALIDATION_KEYS,
    DATA_TYPES,
    MAX_ALLOWED_VALUES,
    MAX_DISPLAY_NAME_LENGTH,
    MAX_KEY_LENGTH,
)

#: Lowercase identifier: starts with a letter, then letters/digits/underscore.
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_INT_VALIDATION_KEYS = frozenset({"min_length", "max_length", "min", "max"})
#: A regex bound is stored as data, never executed by this service. The cap on
#: length plus the ban on catastrophic constructs keeps it declarative.
_MAX_REGEX_LENGTH = 200
_UNSAFE_REGEX = ("(?<=", "(?<!", "(?>", "\\1", "\\2")


class PropertyDefinitionInvalid(ValueError):
    """Raised when a property definition is not acceptable."""


def validate_key(key: str) -> str:
    value = (key or "").strip().lower()
    if not value:
        raise PropertyDefinitionInvalid("key is required")
    if len(value) > MAX_KEY_LENGTH or not _KEY_RE.match(value):
        raise PropertyDefinitionInvalid(
            "key must start with a lowercase letter and contain only "
            "lowercase letters, digits and underscores (max "
            f"{MAX_KEY_LENGTH} characters)"
        )
    return value


def validate_display_name(name: str | None) -> str:
    value = (name or "").strip()
    if not value:
        raise PropertyDefinitionInvalid("display_name is required")
    if len(value) > MAX_DISPLAY_NAME_LENGTH:
        raise PropertyDefinitionInvalid(
            f"display_name must be at most {MAX_DISPLAY_NAME_LENGTH} characters"
        )
    return value


def validate_data_type(data_type: str) -> str:
    value = (data_type or "").strip().lower()
    if value not in DATA_TYPES:
        raise PropertyDefinitionInvalid(
            f"unsupported data_type {data_type!r}; expected one of {list(DATA_TYPES)}"
        )
    return value


def validate_allowed_values(data_type: str, allowed_values: list | None) -> list[str]:
    values = [str(v) for v in (allowed_values or [])]
    if data_type == "enum":
        if not values:
            raise PropertyDefinitionInvalid(
                "an 'enum' property must declare at least one allowed value"
            )
    elif values:
        # allowed_values is only meaningful for enum; silently ignoring it would
        # hide a configuration mistake, so reject it explicitly.
        raise PropertyDefinitionInvalid(
            f"allowed_values is only supported for data_type 'enum', not {data_type!r}"
        )
    if len(values) > MAX_ALLOWED_VALUES:
        raise PropertyDefinitionInvalid(
            f"at most {MAX_ALLOWED_VALUES} allowed values are supported"
        )
    if len(set(values)) != len(values):
        raise PropertyDefinitionInvalid("allowed values must be unique")
    if any(not v.strip() for v in values):
        raise PropertyDefinitionInvalid("allowed values must not be empty")
    return values


def validate_validation_rules(data_type: str, rules: dict | None) -> dict:
    """Whitelist and type-check declarative bounds."""
    supplied = dict(rules or {})
    unknown = sorted(set(supplied) - ALLOWED_VALIDATION_KEYS)
    if unknown:
        raise PropertyDefinitionInvalid(
            f"unsupported validation keys {unknown}; "
            f"allowed: {sorted(ALLOWED_VALIDATION_KEYS)}"
        )

    cleaned: dict = {}
    for name, raw in supplied.items():
        if name in _INT_VALIDATION_KEYS:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise PropertyDefinitionInvalid(f"validation.{name} must be an integer")
            if raw < 0:
                raise PropertyDefinitionInvalid(f"validation.{name} must not be negative")
            cleaned[name] = raw
        else:  # regex / pattern
            if not isinstance(raw, str) or not raw.strip():
                raise PropertyDefinitionInvalid(
                    f"validation.{name} must be a non-empty string"
                )
            if len(raw) > _MAX_REGEX_LENGTH:
                raise PropertyDefinitionInvalid(
                    f"validation.{name} is too long (max {_MAX_REGEX_LENGTH})"
                )
            if any(token in raw for token in _UNSAFE_REGEX):
                raise PropertyDefinitionInvalid(
                    f"validation.{name} uses an unsupported construct"
                )
            try:
                re.compile(raw)
            except re.error as exc:
                raise PropertyDefinitionInvalid(
                    f"validation.{name} is not a valid regular expression: {exc}"
                ) from exc
            cleaned[name] = raw

    low, high = cleaned.get("min_length"), cleaned.get("max_length")
    if low is not None and high is not None and low > high:
        raise PropertyDefinitionInvalid("min_length must not exceed max_length")
    low, high = cleaned.get("min"), cleaned.get("max")
    if low is not None and high is not None and low > high:
        raise PropertyDefinitionInvalid("min must not exceed max")

    if data_type not in ("integer", "string") and ("min" in cleaned or "max" in cleaned):
        raise PropertyDefinitionInvalid(
            f"numeric bounds are not supported for data_type {data_type!r}"
        )
    return cleaned
