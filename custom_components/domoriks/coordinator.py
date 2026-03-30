from __future__ import annotations

import asyncio
import logging
from typing import Dict, List

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, EVENT_RX, READ_COILS, WRITE_FUNCTIONS
from .hub import DomoriksHub, ModuleConfig

_LOGGER = logging.getLogger(__name__)


def _parse_read_coils_payload(payload_hex: str, count: int) -> List[bool]:
    """Parse a READ_COILS response payload hex string into a list of bool states."""
    try:
        raw = bytes.fromhex(payload_hex)
        # payload: [byte_count, data...]
        byte_count = raw[0]
        data = raw[1 : 1 + byte_count]
        value = int.from_bytes(data, byteorder="little")
        return [(value >> bit) & 0x01 == 1 for bit in range(count)]
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("domoriks: failed to parse coil payload '%s': %s", payload_hex, exc)
        return [False] * count


class DomoriksCoordinator(DataUpdateCoordinator[Dict[int, List[bool]]]):
    """Event-driven coordinator: state updated from RX bus events, not a timer."""

    def __init__(self, hass: HomeAssistant, hub: DomoriksHub, entry) -> None:
        self.hub = hub
        self.modules: List[ModuleConfig] = hub.modules
        self._module_map: Dict[int, ModuleConfig] = {m.module_id: m for m in hub.modules}
        self._unreachable_modules: set[int] = set()
        self._read_after_activity_task: asyncio.Task | None = None

        # update_interval=None disables the built-in scheduler entirely.
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} coordinator",
            update_interval=None,
        )

    @callback
    def async_subscribe_events(self) -> None:
        """Subscribe to RX bus events. Call once after coordinator is created."""
        self.hass.bus.async_listen(EVENT_RX, self._handle_rx_event)

    @callback
    def _handle_rx_event(self, event: Event) -> None:
        """Handle an incoming domoriks_rx bus event and update coordinator data."""
        slave: int = event.data.get("slave", -1)
        function: int = event.data.get("function", -1)
        payload_hex: str = event.data.get("payload", "")

        if function == READ_COILS:
            # Parse and apply the coil state update for the responding module.
            module = self._module_map.get(slave)
            if module is None:
                return
            # Module replied — mark it as reachable.
            self._unreachable_modules.discard(slave)
            states = _parse_read_coils_payload(payload_hex, module.outputs)
            new_data = {**(self.data or {}), module.module_id: states}
            self.async_set_updated_data(new_data)
        elif function in WRITE_FUNCTIONS:
            # Write response — trigger a debounced coil re-read so the new state is
            # reflected in HA. Cancel any already-pending read so that a burst of writes
            # (rapid button presses) only results in ONE follow-up read.
            if self._read_after_activity_task and not self._read_after_activity_task.done():
                self._read_after_activity_task.cancel()
            self._read_after_activity_task = self.hass.async_create_task(
                self._async_read_all_after_activity()
            )
        # else: read-only responses (0x02, 0x03, 0x04) — ignore, state hasn't changed.

    async def _async_update_data(self) -> Dict[int, List[bool]]:
        """Called once on startup to fetch initial coil state for all modules."""
        if not self.hub.is_connected:
            if self.data:
                return self.data
            return {m.module_id: [False] * m.outputs for m in self.modules if m.enabled}

        data: Dict[int, List[bool]] = {}
        for module in self.modules:
            if not module.enabled:
                # Skip disabled modules when polling
                continue
            coil_count = max(module.coil_count, 1)
            try:
                states = await self.hub.async_read_coils(module.module_id, 0, coil_count)
                self._unreachable_modules.discard(module.module_id)
                data[module.module_id] = states
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("domoriks: module %d not reachable: %s", module.module_id, err)
                self._unreachable_modules.add(module.module_id)
                data[module.module_id] = [False] * module.outputs
        return data

    async def _async_read_all_after_activity(self) -> None:
        """After non-read bus activity, read all enabled modules with inter-module delay.

        Mirrors the original domoriks_ha automation:
          delay 150 ms → rc module_1 → delay 50 ms → rc module_2 → …
        The resulting RX events are handled by _handle_rx_event to update state.
        """
        await asyncio.sleep(0.150)
        for module in self.modules:
            if not module.enabled:
                continue
            if not self.hub.is_connected:
                break
            try:
                await self.hub.async_read_coils(module.module_id, 0, max(module.coil_count, 1))
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("domoriks: post-activity read failed for module %d: %s", module.module_id, err)
                self._unreachable_modules.add(module.module_id)
                # Notify entities so available goes False immediately.
                self.async_set_updated_data(self.data or {})
            await asyncio.sleep(0.050)


class DomoriksCoordinatorEntity(CoordinatorEntity[DomoriksCoordinator]):
    """Base entity bound to Domoriks coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DomoriksCoordinator) -> None:
        super().__init__(coordinator)
        self.hub = coordinator.hub
