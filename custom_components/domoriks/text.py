from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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


class DomoriksCommandText(TextEntity):
    """Text entity to store a raw Domoriks command string for the UI."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:console"
    _attr_native_min = 1
    _attr_native_max = 200

    def __init__(self, hub: DomoriksHub, entry: ConfigEntry) -> None:
        self._hub = hub
        self._runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_command"
        self._attr_name = "Command"
        self._attr_device_info = _gateway_device(entry)
        self._attr_native_value = self._runtime.manual_command

    @property
    def available(self) -> bool:
        return True

    async def async_set_value(self, value: str) -> None:
        _LOGGER.debug("domoriks: staged manual command: %s", value)
        self._runtime.manual_command = value
        self._attr_native_value = value
        self.async_write_ha_state()


def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub: DomoriksHub = entry.runtime_data.hub
    async_add_entities([DomoriksCommandText(hub, entry)])