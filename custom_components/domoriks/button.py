from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER
from .hub import DomoriksHub

_LOGGER = logging.getLogger(__name__)


def _gateway_device(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"gateway_{entry.entry_id}")},
        name="Domoriks Gateway",
        manufacturer=MANUFACTURER,
        model="Domoriks Bus Interface",
    )


class DomoriksSendCommandButton(ButtonEntity):
    """Button entity that sends the staged raw Domoriks command."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:send"

    def __init__(self, hub: DomoriksHub, entry: ConfigEntry) -> None:
        self._hub = hub
        self._runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_send_command"
        self._attr_name = "Send Command"
        self._attr_device_info = _gateway_device(entry)

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        command = self._runtime.manual_command.strip()
        if not command:
            raise HomeAssistantError("Command is empty")

        _LOGGER.debug("domoriks: sending staged manual command: %s", command)
        try:
            await self._hub.async_send_command_string(command)
        except Exception as exc:  # noqa: BLE001
            raise HomeAssistantError(f"Failed to send command: {exc}") from exc


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub: DomoriksHub = entry.runtime_data.hub
    async_add_entities([DomoriksSendCommandButton(hub, entry)])