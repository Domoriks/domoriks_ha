from __future__ import annotations

from typing import Any

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from homeassistant.components.file_upload import process_uploaded_file

from .project_json_parser import modules_from_project_json, parse_json_upload

from .const import (
    CONF_BAUDRATE,
    CONF_MODULE_ID,
    CONF_MODULES,
    CONF_OUTPUT_NAMES,
    CONF_OUTPUTS,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    CONF_RECONNECT_INTERVAL,
    DEFAULT_BAUDRATE,
    DEFAULT_OUTPUTS_PER_MODULE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_RECONNECT_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Setup-mode selector used in the first step of both flows.
_SETUP_MODE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(options=["manual", "upload"])
)

# File selector for JSON import.
_FileSelectorClass = getattr(selector, "FileSelector", None)
_FileSelectorConfigClass = getattr(selector, "FileSelectorConfig", None)
if _FileSelectorClass is not None and _FileSelectorConfigClass is not None:
    _JSON_FILE_SELECTOR = _FileSelectorClass(_FileSelectorConfigClass(accept=".json"))
else:
    _JSON_FILE_SELECTOR = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_modules(value: str | None) -> list[dict[str, Any]]:
    """Parse 'id:outputs, id:outputs, ...' into module dicts."""
    if not value:
        return []
    modules: list[dict[str, Any]] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        tokens = part.split(":")
        if not tokens[0]:
            raise ValueError("Module id required")
        module_id = int(tokens[0])
        outputs = int(tokens[1]) if len(tokens) > 1 else DEFAULT_OUTPUTS_PER_MODULE
        modules.append({CONF_MODULE_ID: module_id, CONF_OUTPUTS: outputs})
    return modules


def _modules_to_text(modules: list[dict[str, Any]]) -> str:
    """Convert module dicts back to 'id:outputs, ...' text."""
    parts: list[str] = []
    for module in modules:
        outputs = module.get(CONF_OUTPUTS, DEFAULT_OUTPUTS_PER_MODULE)
        module_id = module.get(CONF_MODULE_ID)
        parts.append(f"{module_id}:{outputs}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Config flow (initial setup)
# ---------------------------------------------------------------------------

class DomoriksConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Setup: connection settings → manual or file upload."""

    VERSION = 1

    def __init__(self) -> None:
        self._connection_data: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Step 1 – connection settings + mode selection
    # ------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_PORT])
            self._abort_if_unique_id_configured()

            self._connection_data = {
                CONF_PORT: user_input[CONF_PORT],
                CONF_BAUDRATE: user_input[CONF_BAUDRATE],
                CONF_POLL_INTERVAL: user_input[CONF_POLL_INTERVAL],
                CONF_RECONNECT_INTERVAL: user_input[CONF_RECONNECT_INTERVAL],
            }

            if user_input.get("setup_mode") == "upload":
                return await self.async_step_upload()
            return await self.async_step_manual()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): str,
                    vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): int,
                    vol.Required(
                        CONF_POLL_INTERVAL, default=int(DEFAULT_POLL_INTERVAL.total_seconds())
                    ): vol.Coerce(int),
                    vol.Required(
                        CONF_RECONNECT_INTERVAL, default=DEFAULT_RECONNECT_INTERVAL
                    ): vol.Coerce(float),
                    vol.Required("setup_mode", default="upload"): _SETUP_MODE_SELECTOR,
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2a – manual module list
    # ------------------------------------------------------------------

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                modules = _parse_modules(user_input.get(CONF_MODULES))
            except ValueError:
                errors[CONF_MODULES] = "invalid_modules"
                modules = []

            if not errors:
                if not modules:
                    errors[CONF_MODULES] = "invalid_modules"
                else:
                    return self.async_create_entry(
                        title="Domoriks",
                        data={**self._connection_data, CONF_MODULES: modules},
                    )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {vol.Required(CONF_MODULES, default="64:6, 65:6"): str}
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2b – file upload
    # ------------------------------------------------------------------

    async def async_step_upload(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if _JSON_FILE_SELECTOR is None:
            errors["base"] = "file_selector_not_supported"
            return self.async_show_form(
                step_id="upload",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        if user_input is not None:
            config_file = user_input.get("config_file")
            if config_file:
                try:
                    with process_uploaded_file(self.hass, config_file) as file_path:
                        content = await self.hass.async_add_executor_job(
                            file_path.read_text, "utf-8"
                        )
                    parsed = parse_json_upload(content)
                    modules = modules_from_project_json(parsed)
                except Exception as err:
                    _LOGGER.warning("JSON parse failed (%s): %.200r", err, config_file)
                    errors["config_file"] = "invalid_json"
                    modules = []

                if not errors:
                    if not modules:
                        errors["config_file"] = "no_modules_found"
                    else:
                        return self.async_create_entry(
                            title="Domoriks",
                            data={**self._connection_data, CONF_MODULES: modules},
                        )
            else:
                errors["config_file"] = "invalid_json"

        return self.async_show_form(
            step_id="upload",
            data_schema=vol.Schema(
                {vol.Required("config_file"): _JSON_FILE_SELECTOR}
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # YAML import (backward compatibility)
    # ------------------------------------------------------------------

    async def async_step_import(self, import_config: dict[str, Any]) -> FlowResult:
        modules: list[dict[str, Any]] = []

        if import_config.get("modules"):
            try:
                modules = modules_from_project_json(import_config)
            except Exception:
                modules = []

        if not modules:
            raw_modules = import_config.get(CONF_MODULES)
            if isinstance(raw_modules, list):
                modules = [
                    {
                        CONF_MODULE_ID: int(m.get(CONF_MODULE_ID)),
                        CONF_OUTPUTS: int(m.get(CONF_OUTPUTS, DEFAULT_OUTPUTS_PER_MODULE)),
                    }
                    for m in raw_modules
                    if m.get(CONF_MODULE_ID) is not None
                ]

        if not modules:
            return self.async_abort(reason="no_modules_found")

        data = {
            CONF_PORT: import_config.get(CONF_PORT, DEFAULT_PORT),
            CONF_BAUDRATE: import_config.get(CONF_BAUDRATE, DEFAULT_BAUDRATE),
            CONF_POLL_INTERVAL: int(
                import_config.get(
                    CONF_POLL_INTERVAL, int(DEFAULT_POLL_INTERVAL.total_seconds())
                )
            ),
            CONF_RECONNECT_INTERVAL: float(
                import_config.get(CONF_RECONNECT_INTERVAL, DEFAULT_RECONNECT_INTERVAL)
            ),
            CONF_MODULES: modules,
        }

        await self.async_set_unique_id(data[CONF_PORT])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Domoriks", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return DomoriksOptionsFlow(config_entry)


# ---------------------------------------------------------------------------
# Options flow (reconfigure after setup)
# ---------------------------------------------------------------------------

class DomoriksOptionsFlow(config_entries.OptionsFlow):
    """Options: poll/reconnect settings + manual or file re-import."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry
        self._poll_interval: int = int(DEFAULT_POLL_INTERVAL.total_seconds())
        self._reconnect_interval: float = float(DEFAULT_RECONNECT_INTERVAL)

    # ------------------------------------------------------------------
    # Step 1 – timing settings + mode selection
    # ------------------------------------------------------------------

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        current = {**self.entry.data, **self.entry.options}

        if user_input is not None:
            self._poll_interval = user_input[CONF_POLL_INTERVAL]
            self._reconnect_interval = user_input[CONF_RECONNECT_INTERVAL]

            if user_input.get("setup_mode") == "upload":
                return await self.async_step_upload()
            return await self.async_step_manual()

        defaults_poll = int(current.get(CONF_POLL_INTERVAL, int(DEFAULT_POLL_INTERVAL.total_seconds())))
        defaults_reconnect = float(current.get(CONF_RECONNECT_INTERVAL, DEFAULT_RECONNECT_INTERVAL))

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_POLL_INTERVAL, default=defaults_poll): vol.Coerce(int),
                    vol.Required(CONF_RECONNECT_INTERVAL, default=defaults_reconnect): vol.Coerce(float),
                    vol.Required("setup_mode", default="upload"): _SETUP_MODE_SELECTOR,
                }
            ),
        )

    # ------------------------------------------------------------------
    # Step 2a – manual module list
    # ------------------------------------------------------------------

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        current = {**self.entry.data, **self.entry.options}

        if user_input is not None:
            try:
                modules = _parse_modules(user_input.get(CONF_MODULES))
            except ValueError:
                errors[CONF_MODULES] = "invalid_modules"
                modules = []

            if not errors:
                if not modules:
                    errors[CONF_MODULES] = "invalid_modules"
                else:
                    return self.async_create_entry(
                        title="Domoriks options",
                        data={
                            CONF_POLL_INTERVAL: self._poll_interval,
                            CONF_RECONNECT_INTERVAL: self._reconnect_interval,
                            CONF_MODULES: modules,
                        },
                    )

        defaults_modules = _modules_to_text(current.get(CONF_MODULES, []))

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {vol.Required(CONF_MODULES, default=defaults_modules): str}
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2b – file upload
    # ------------------------------------------------------------------

    async def async_step_upload(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        current = {**self.entry.data, **self.entry.options}

        if _JSON_FILE_SELECTOR is None:
            errors["base"] = "file_selector_not_supported"
            return self.async_show_form(
                step_id="upload",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        if user_input is not None:
            import_file = user_input.get("import_file")
            if import_file:
                try:
                    with process_uploaded_file(self.hass, import_file) as file_path:
                        content = await self.hass.async_add_executor_job(
                            file_path.read_text, "utf-8"
                        )
                    parsed = parse_json_upload(content)
                    modules = modules_from_project_json(parsed)
                except Exception as err:
                    _LOGGER.warning("JSON parse failed (%s): %.200r", err, import_file)
                    errors["import_file"] = "invalid_json"
                    modules = []

                if not errors:
                    if not modules:
                        errors["import_file"] = "no_modules_found"
                    else:
                        # Merge: existing user-set names preserved.
                        old_module_map: dict[int, dict] = {
                            m[CONF_MODULE_ID]: m for m in current.get(CONF_MODULES, [])
                        }
                        merged: list[dict[str, Any]] = []
                        for m in modules:
                            old = old_module_map.get(m[CONF_MODULE_ID], {})
                            merged.append(
                                {
                                    **m,
                                    CONF_OUTPUT_NAMES: {
                                        **m.get(CONF_OUTPUT_NAMES, {}),
                                        **old.get(CONF_OUTPUT_NAMES, {}),
                                    },
                                }
                            )
                        return self.async_create_entry(
                            title="Domoriks options",
                            data={
                                CONF_POLL_INTERVAL: self._poll_interval,
                                CONF_RECONNECT_INTERVAL: self._reconnect_interval,
                                CONF_MODULES: merged,
                            },
                        )
            else:
                errors["import_file"] = "invalid_json"

        return self.async_show_form(
            step_id="upload",
            data_schema=vol.Schema(
                {vol.Required("import_file"): _JSON_FILE_SELECTOR}
            ),
            errors=errors,
        )
