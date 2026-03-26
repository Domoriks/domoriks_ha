"""Parsing helpers for Domoriks project JSON files.

Public API
----------
parse_json_upload(value)       – decode a raw form value (str / bytes / HA
                                  FileSelector dict) into a parsed JSON object.
modules_from_project_json(obj) – convert a project JSON dict into the list of
                                  module dicts used internally by the integration.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from .const import (
    CONF_MODULE_ID,
    CONF_OUTPUT_NAMES,
    CONF_OUTPUTS,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# parse_json_upload
# ---------------------------------------------------------------------------

def parse_json_upload(value: Any) -> Any:
    """Decode a raw form-field value into a parsed JSON object.

    Handles:
    - str / bytes: plain JSON, base64, or data-URI encoded JSON.
    - dict: HA FileSelector payloads (recurses into content/file/data keys).
    - list[int]: file bytes as integer list.

    Raises ValueError("no_data"), ValueError("invalid_json"),
    or ValueError("invalid_upload").
    """
    if value is None or value == "" or value == {} or value == []:
        raise ValueError("no_data")

    # list[int] → bytes
    if isinstance(value, list) and value and all(isinstance(i, int) for i in value):
        return _decode(bytes(value))

    if isinstance(value, (bytes, bytearray, str)):
        return _decode(value)

    # Dict-like HA FileSelector payloads
    if isinstance(value, dict):
        for key in ("content", "file", "data", "base64"):
            inner = value.get(key)
            if inner:
                try:
                    return parse_json_upload(inner)
                except ValueError:
                    continue
        raise ValueError("invalid_upload")

    raise ValueError("invalid_upload")


def _decode(candidate: bytes | str) -> Any:
    """Try json.loads directly, then strip data-URI prefix, then base64."""
    if isinstance(candidate, (bytes, bytearray)):
        try:
            return json.loads(candidate.decode("utf-8-sig"))
        except Exception:  # noqa: BLE001
            pass
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                return json.loads(decoder(candidate, validate=False).decode("utf-8-sig"))
            except Exception:  # noqa: BLE001
                continue
        raise ValueError("invalid_json")

    # str
    s = candidate.strip().lstrip("\ufeff")
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        pass
    if s.startswith("data:") and "," in s:
        s = s.split(",", 1)[1]
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            return json.loads(decoder(s + "==", validate=False).decode("utf-8-sig"))
        except Exception:  # noqa: BLE001
            continue
    raise ValueError("invalid_json")


# ---------------------------------------------------------------------------
# modules_from_project_json
# ---------------------------------------------------------------------------

def modules_from_project_json(obj: Any) -> list[dict[str, Any]]:
    """Convert a project JSON object into HA module dicts.

    Expects ``{"modules": [...]}``.  Each module must have:
    - ``node`` (int) – Modbus slave address / module ID.
    - ``num_outputs`` (int, optional) – number of outputs; inferred from
      ``outputs`` dict length when absent.
    - ``outputs`` (dict, optional) – ``{name: 1-based-position}`` mapping.

    Modules with zero outputs are skipped.

    Returns list of dicts: CONF_MODULE_ID, CONF_OUTPUTS, CONF_OUTPUT_NAMES.
    """
    if not isinstance(obj, dict):
        return []

    devices = obj.get("modules")
    if not isinstance(devices, list):
        return []

    modules: list[dict[str, Any]] = []

    for device in devices:
        if not isinstance(device, dict):
            continue

        try:
            module_id = int(device["node"])
        except (KeyError, TypeError, ValueError):
            continue

        outputs_map: dict = device.get("outputs") or {}
        num_outputs = device.get("num_outputs")

        if num_outputs is None:
            num_outputs = len(outputs_map) if isinstance(outputs_map, dict) else 0

        try:
            num_outputs = int(num_outputs)
        except (TypeError, ValueError):
            num_outputs = 0

        if num_outputs <= 0:
            continue

        # Convert {name: 1-based-position} → {str(0-based-index): name}
        output_names: dict[str, str] = {}
        if isinstance(outputs_map, dict):
            for name, pos in outputs_map.items():
                try:
                    idx = int(pos) - 1
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < num_outputs:
                    output_names[str(idx)] = str(name)

        modules.append(
            {
                CONF_MODULE_ID: module_id,
                CONF_OUTPUTS: num_outputs,
                CONF_OUTPUT_NAMES: output_names,
            }
        )

    return modules
