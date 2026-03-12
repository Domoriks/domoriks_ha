from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_BAUDRATE,
    CONF_MODULE_ID,
    CONF_MODULES,
    CONF_MODULE_IDS,
    CONF_OUTPUT_ICONS,
    CONF_OUTPUT_NAMES,
    CONF_OUTPUTS,
    CONF_OUTPUTS_PER_MODULE,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    CONF_RECONNECT_INTERVAL,
    DEFAULT_BAUDRATE,
    DEFAULT_MODULES,
    DEFAULT_OUTPUTS_PER_MODULE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_RECONNECT_INTERVAL,
    DOMAIN,
)
from .hub import DomoriksHub


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_modules(value: str | None) -> List[Dict[str, Any]]:
    if not value:
        return []
    modules: List[Dict[str, Any]] = []
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


def _modules_to_text(modules: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for module in modules:
        outputs = module.get(CONF_OUTPUTS, DEFAULT_OUTPUTS_PER_MODULE)
        module_id = module.get(CONF_MODULE_ID)
        parts.append(f"{module_id}:{outputs}")
    return ",".join(parts)


# Default icon pre-filled in the setup form.
DEFAULT_OUTPUT_ICON = "mdi:toggle-switch"


def _build_module_schema(module: dict, prefill: dict, is_reachable: bool) -> vol.Schema:
    """Build per-output individual name / icon / test-toggle schema."""
    outputs: int = module[CONF_OUTPUTS]
    existing_names: dict = module.get(CONF_OUTPUT_NAMES, {})
    existing_icons: dict = module.get(CONF_OUTPUT_ICONS, {})

    schema_dict: dict = {}

    if not is_reachable:
        schema_dict[vol.Optional("remove_module", default=False)] = bool

    for i in range(outputs):
        default_name = (
            prefill.get(f"name_{i}")
            or existing_names.get(str(i))
            or f"Output {i + 1}"
        )
        default_icon = (
            prefill.get(f"icon_{i}")
            or existing_icons.get(str(i))
            or DEFAULT_OUTPUT_ICON
        )
        schema_dict[vol.Optional(f"name_{i}", default=default_name)] = str
        schema_dict[vol.Optional(f"icon_{i}", default=default_icon)] = str
        schema_dict[vol.Optional(f"test_{i}", default=False)] = bool

    return vol.Schema(schema_dict)


class _ModuleNamingMixin:
    """Shared per-module naming step for ConfigFlow and OptionsFlow."""

    _modules_pending: list
    _modules_done: list
    _current_input: dict

    def _hub_for_test(self) -> Optional[DomoriksHub]:
        """Return a connected hub for test pulses. Override in subclass."""
        return None

    async def _check_reachable(self, module_id: int, outputs: int) -> bool:
        hub = self._hub_for_test()
        if not hub or not hub.is_connected:
            return False
        try:
            await hub.async_read_coils(module_id, 0, max(outputs, 1))
            return True
        except Exception:  # noqa: BLE001
            return False

    async def async_step_module(
        self, user_input: dict[str, Any] | None = None
    ) -> "FlowResult":
        if not self._modules_pending:
            return self._finalize()

        module = self._modules_pending[0]
        module_id: int = module[CONF_MODULE_ID]
        outputs: int = module[CONF_OUTPUTS]
        is_reachable = await self._check_reachable(module_id, outputs)

        if user_input is not None:
            # User chose to remove an unreachable module.
            if user_input.get("remove_module", False):
                self._modules_pending.pop(0)
                self._current_input = {}
                return await self.async_step_module()

            # Strip test toggles from saved state so they reset on re-show.
            self._current_input = {
                k: v for k, v in user_input.items() if not k.startswith("test_")
            }

            # Pulse any output whose test toggle was flipped on.
            triggered = [
                int(k.split("_")[1])
                for k, v in user_input.items()
                if k.startswith("test_") and v
            ]
            if triggered:
                hub = self._hub_for_test()
                if hub and hub.is_connected:
                    for idx in triggered:
                        try:
                            await hub.async_write_coil(module_id, idx, True)
                            await asyncio.sleep(0.5)
                            await hub.async_write_coil(module_id, idx, False)
                        except Exception:  # noqa: BLE001
                            pass
                return self._show_module_form(module, is_reachable)

            # Save names + icons and advance to next module.
            output_names = {
                str(i): (self._current_input.get(f"name_{i}") or f"Output {i + 1}").strip()
                for i in range(outputs)
            }
            output_icons = {
                str(i): (self._current_input.get(f"icon_{i}") or DEFAULT_OUTPUT_ICON).strip()
                for i in range(outputs)
            }
            self._modules_done.append(
                {
                    **module,
                    CONF_OUTPUT_NAMES: output_names,
                    CONF_OUTPUT_ICONS: output_icons,
                }
            )
            self._modules_pending.pop(0)
            self._current_input = {}
            return await self.async_step_module()

        return self._show_module_form(module, is_reachable)

    def _show_module_form(
        self,
        module: dict,
        is_reachable: bool,
    ) -> "FlowResult":
        module_id: int = module[CONF_MODULE_ID]
        outputs: int = module[CONF_OUTPUTS]
        status = "✓ Reachable" if is_reachable else "✗ Not responding"

        return self.async_show_form(
            step_id="module",
            data_schema=_build_module_schema(module, self._current_input, is_reachable),
            errors={"base": "module_unreachable"} if not is_reachable else {},
            description_placeholders={
                "module_id": str(module_id),
                "outputs": str(outputs),
                "status": status,
            },
        )


# ---------------------------------------------------------------------------
# Config flow (initial setup)
# ---------------------------------------------------------------------------

class DomoriksConfigFlow(_ModuleNamingMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Multi-step setup: connection → per-module naming / toggle."""

    VERSION = 1

    def __init__(self) -> None:
        self._import_data: dict[str, Any] | None = None
        self._connection_data: dict[str, Any] = {}
        self._modules_pending: list[dict] = []
        self._modules_done: list[dict] = []
        self._hub: Optional[DomoriksHub] = None
        self._current_input: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Step 1 – connection + modules list
    # ------------------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                modules = _parse_modules(user_input.get(CONF_MODULES))
            except ValueError:
                errors[CONF_MODULES] = "invalid_modules"
                modules = []

            if not errors:
                if not modules:
                    modules = DEFAULT_MODULES

                await self.async_set_unique_id(user_input[CONF_PORT])
                self._abort_if_unique_id_configured()

                self._connection_data = {
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_BAUDRATE: user_input[CONF_BAUDRATE],
                    CONF_POLL_INTERVAL: user_input[CONF_POLL_INTERVAL],
                    CONF_RECONNECT_INTERVAL: user_input[CONF_RECONNECT_INTERVAL],
                }

                # Start a temporary hub to probe reachability and enable test toggles.
                if self._hub:
                    await self._hub.async_stop()
                self._hub = DomoriksHub(
                    self.hass,
                    {**self._connection_data, CONF_MODULES: modules},
                )
                await self._hub.async_start()
                await self._hub.async_wait_connected(timeout=5.0)

                self._modules_pending = list(modules)
                self._modules_done = []
                self._current_input = {}
                return await self.async_step_module()

        defaults = {
            CONF_PORT: DEFAULT_PORT,
            CONF_BAUDRATE: DEFAULT_BAUDRATE,
            CONF_POLL_INTERVAL: int(DEFAULT_POLL_INTERVAL.total_seconds()),
            CONF_RECONNECT_INTERVAL: DEFAULT_RECONNECT_INTERVAL,
            CONF_MODULES: _modules_to_text(DEFAULT_MODULES),
        }

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PORT, default=defaults[CONF_PORT]): str,
                    vol.Required(CONF_BAUDRATE, default=defaults[CONF_BAUDRATE]): int,
                    vol.Required(
                        CONF_POLL_INTERVAL, default=defaults[CONF_POLL_INTERVAL]
                    ): vol.Coerce(int),
                    vol.Required(
                        CONF_RECONNECT_INTERVAL,
                        default=defaults[CONF_RECONNECT_INTERVAL],
                    ): vol.Coerce(float),
                    vol.Optional(
                        CONF_MODULES,
                        default=defaults[CONF_MODULES],
                    ): str,
                }
            ),
            errors=errors,
        )

    def _hub_for_test(self) -> Optional[DomoriksHub]:
        return self._hub

    def _finalize(self) -> FlowResult:
        if self._hub:
            self.hass.async_create_task(self._hub.async_stop())
            self._hub = None
        return self.async_create_entry(
            title="Domoriks",
            data={**self._connection_data, CONF_MODULES: self._modules_done},
        )

    async def async_remove(self) -> None:
        """Clean up temporary hub when flow is aborted."""
        if self._hub:
            await self._hub.async_stop()
            self._hub = None

    async def async_step_import(self, import_config: Dict[str, Any]) -> FlowResult:
        self._import_data = import_config

        modules: List[Dict[str, Any]] = []
        raw_modules = import_config.get(CONF_MODULES)
        if isinstance(raw_modules, list):
            modules = [
                {
                    CONF_MODULE_ID: int(module.get(CONF_MODULE_ID)),
                    CONF_OUTPUTS: int(module.get(CONF_OUTPUTS, DEFAULT_OUTPUTS_PER_MODULE)),
                }
                for module in raw_modules
                if module.get(CONF_MODULE_ID) is not None
            ]
        elif isinstance(raw_modules, str):
            try:
                modules = _parse_modules(raw_modules)
            except ValueError:
                modules = []
        elif import_config.get("module_ids"):
            outputs = int(import_config.get(CONF_OUTPUTS_PER_MODULE, DEFAULT_OUTPUTS_PER_MODULE))
            modules = [
                {
                    CONF_MODULE_ID: int(module_id),
                    CONF_OUTPUTS: outputs,
                }
                for module_id in import_config.get("module_ids", [])
            ]

        if not modules:
            modules = DEFAULT_MODULES

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

class DomoriksOptionsFlow(_ModuleNamingMixin, config_entries.OptionsFlow):
    """Options: poll/reconnect settings + per-module naming (same module step)."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry
        self._modules_pending: list[dict] = []
        self._modules_done: list[dict] = []
        self._current_input: dict[str, Any] = {}
        self._poll_interval: int = int(DEFAULT_POLL_INTERVAL.total_seconds())
        self._reconnect_interval: float = float(DEFAULT_RECONNECT_INTERVAL)

    @property
    def _hub(self) -> Optional[DomoriksHub]:
        runtime = getattr(self.entry, "runtime_data", None)
        return runtime.hub if runtime else None

    # ------------------------------------------------------------------
    # Step 1 – connection settings + modules text
    # ------------------------------------------------------------------

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        current = {**self.entry.data, **self.entry.options}
        old_module_map: dict[int, dict] = {
            m[CONF_MODULE_ID]: m for m in current.get(CONF_MODULES, [])
        }

        if user_input is not None:
            try:
                modules = _parse_modules(user_input.get(CONF_MODULES))
            except ValueError:
                errors[CONF_MODULES] = "invalid_modules"
                modules = []

            if not errors:
                new_modules = modules or current.get(CONF_MODULES, DEFAULT_MODULES)

                # Preserve existing names/icons; per-module step handles reachability.
                merged_modules = []
                for m in new_modules:
                    old = old_module_map.get(m[CONF_MODULE_ID], {})
                    merged_modules.append(
                        {
                            **m,
                            CONF_OUTPUT_NAMES: old.get(CONF_OUTPUT_NAMES, {}),
                            CONF_OUTPUT_ICONS: old.get(CONF_OUTPUT_ICONS, {}),
                        }
                    )

                self._modules_pending = merged_modules
                self._modules_done = []
                self._current_input = {}
                self._poll_interval = user_input[CONF_POLL_INTERVAL]
                self._reconnect_interval = user_input[CONF_RECONNECT_INTERVAL]
                return await self.async_step_module()

        defaults = {
            CONF_POLL_INTERVAL: current.get(
                CONF_POLL_INTERVAL, int(DEFAULT_POLL_INTERVAL.total_seconds())
            ),
            CONF_RECONNECT_INTERVAL: current.get(
                CONF_RECONNECT_INTERVAL, DEFAULT_RECONNECT_INTERVAL
            ),
            CONF_MODULES: _modules_to_text(current.get(CONF_MODULES, DEFAULT_MODULES)),
        }

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=int(defaults[CONF_POLL_INTERVAL]),
                    ): vol.Coerce(int),
                    vol.Required(
                        CONF_RECONNECT_INTERVAL,
                        default=float(defaults[CONF_RECONNECT_INTERVAL]),
                    ): vol.Coerce(float),
                    vol.Optional(CONF_MODULES, default=defaults[CONF_MODULES]): str,
                }
            ),
            errors=errors,
        )

    def _hub_for_test(self) -> Optional[DomoriksHub]:
        runtime = getattr(self.entry, "runtime_data", None)
        return runtime.hub if runtime else None

    def _finalize(self) -> FlowResult:
        return self.async_create_entry(
            title="Domoriks options",
            data={
                CONF_POLL_INTERVAL: self._poll_interval,
                CONF_RECONNECT_INTERVAL: self._reconnect_interval,
                CONF_MODULES: self._modules_done,
            },
        )
