from __future__ import annotations

from typing import Any, Dict, List

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import DomoriksCoordinator, DomoriksCoordinatorEntity
from .hub import ModuleConfig


def _get_state(data: Dict[int, List[bool]], module_id: int, index: int) -> bool:
    values = data.get(module_id, [])
    if index < len(values):
        return values[index]
    return False


class DomoriksSwitch(DomoriksCoordinatorEntity, SwitchEntity):
    """Switch representing a Domoriks module output."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DomoriksCoordinator,
        module: ModuleConfig,
        index: int,
    ) -> None:
        super().__init__(coordinator)
        self._module = module
        self._index = index

        self._attr_unique_id = f"{module.module_id}_output_{index}"
        self._attr_name = (
            module.output_names.get(str(index)) or f"Output {index + 1}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(module.module_id))},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=f"Module {module.module_id}",
        )

    @property
    def is_on(self) -> bool:
        return _get_state(self.coordinator.data, self._module.module_id, self._index)

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401 - HA signature
        await self.hub.async_write_coil(self._module.module_id, self._index, True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401 - HA signature
        await self.hub.async_write_coil(self._module.module_id, self._index, False)

    @property
    def available(self) -> bool:
        return (
            self.hub.is_connected
            and self._module.module_id not in self.coordinator._unreachable_modules
        )


def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Domoriks switches from config entry."""

    coordinator: DomoriksCoordinator = entry.runtime_data.coordinator
    active_module_ids = {module.module_id for module in coordinator.modules}

    # Remove entities and devices for modules that are no longer in the config.
    registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        uid = entity_entry.unique_id or ""
        # unique_id format: "{module_id}_output_{index}"
        parts = uid.split("_output_")
        if len(parts) == 2:
            try:
                module_id = int(parts[0])
            except ValueError:
                continue
            if module_id not in active_module_ids:
                registry.async_remove(entity_entry.entity_id)

    # Remove device entries for modules no longer in config.
    dev_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(dev_registry, entry.entry_id):
        for domain, identifier in device_entry.identifiers:
            if domain == DOMAIN:
                try:
                    module_id = int(identifier)
                except ValueError:
                    continue
                if module_id not in active_module_ids:
                    dev_registry.async_remove_device(device_entry.id)
                break

    entities: list[DomoriksSwitch] = []
    for module in coordinator.modules:
        for index in range(module.outputs):
            entities.append(DomoriksSwitch(coordinator, module, index))

    async_add_entities(entities)
