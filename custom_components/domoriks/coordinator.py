from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Dict, List, Optional

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    DOMAIN,
    EVENT_RX,
    READ_COILS,
    WRITE_FUNCTIONS,
    WRITE_MULTI_COILS,
    WRITE_MULTI_REGS,
    WRITE_SINGLE_COIL,
)
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


def _module_is_disabled_in_ha(
    hass: HomeAssistant,
    entry_id: str,
    module_id: int,
) -> bool:
    """Return True when HA registry marks a module device/entities as disabled."""
    entity_registry = er.async_get(hass)
    unique_id_prefix = f"{module_id}_output_"
    module_entities = [
        entity_entry
        for entity_entry in er.async_entries_for_config_entry(entity_registry, entry_id)
        if (entity_entry.unique_id or "").startswith(unique_id_prefix)
    ]
    if module_entities:
        return all(entity_entry.disabled_by is not None for entity_entry in module_entities)

    device_registry = dr.async_get(hass)
    for device_entry in dr.async_entries_for_config_entry(device_registry, entry_id):
        if any(domain == DOMAIN and identifier == str(module_id) for domain, identifier in device_entry.identifiers):
            return getattr(device_entry, "disabled_by", None) is not None

    return False


class DomoriksCoordinator(DataUpdateCoordinator[Dict[int, List[bool]]]):
    """Event-driven coordinator: state updated from RX bus events, not a timer.

    Observed write frames are decoded and applied optimistically, then verified with a
    targeted poll of the addressed module (the source of truth). Polls also happen at
    startup and after a delayed action's delay elapses.
    """

    # A write request and the module's byte-identical echo both appear on the shared
    # bus; collapse that pair into a single optimistic update within this window (s).
    DEDUPE_WINDOW = 0.1
    # Per-module debounce before the verify-poll fires, so a burst of writes to one
    # module collapses into a single read (s).
    VERIFY_POLL_DEBOUNCE = 0.15
    # Extra margin added after a delayed action's delay before the reconciling poll (s).
    DELAY_POLL_MARGIN = 0.5

    def __init__(self, hass: HomeAssistant, hub: DomoriksHub, entry) -> None:
        self.hub = hub
        self.modules: List[ModuleConfig] = [
            module
            for module in hub.modules
            if not _module_is_disabled_in_ha(hass, entry.entry_id, module.module_id)
        ]
        self._module_map: Dict[int, ModuleConfig] = {m.module_id: m for m in self.modules}
        self._unreachable_modules: set[int] = set()
        self._pending_verify_polls: Dict[int, asyncio.Task] = {}
        self._recent_write_frames: Dict[tuple[int, int, str], float] = {}

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
            # A write was observed on the bus. Decode it, update the affected module's
            # state optimistically, and verify against the module with a targeted poll.
            self._handle_write_frame(slave, function, payload_hex)
        # else: read-only responses (0x02, 0x03, 0x04) — ignore, state hasn't changed.

    @callback
    def _handle_write_frame(self, slave: int, function: int, payload_hex: str) -> None:
        """Decode an observed write frame, apply it optimistically, and schedule a
        targeted verify-poll of the addressed module (the source of truth)."""
        module = self._module_map.get(slave)
        if module is None:
            return

        # The module is transmitting/echoing, so it is reachable.
        self._unreachable_modules.discard(slave)

        delayed = False
        delay_s = 0
        if not self._is_duplicate_write(slave, function, payload_hex):
            module_known = bool(self.data) and module.module_id in self.data
            current = (
                list(self.data[module.module_id])
                if module_known
                else [False] * module.outputs
            )
            effects, delayed, delay_s = self._decode_coil_effects(
                function, payload_hex, current, module_known
            )
            if effects:
                new_states = current[:]
                changed = False
                for index, value in effects.items():
                    if 0 <= index < len(new_states) and new_states[index] != value:
                        new_states[index] = value
                        changed = True
                if changed:
                    self.async_set_updated_data(
                        {**(self.data or {}), module.module_id: new_states}
                    )
            # Toggle-with-unknown-state and delayed actions apply no optimistic change;
            # the verify-poll below establishes the truth.

        # Verify against the module regardless (debounced per module).
        self._schedule_verify_poll(module.module_id)
        if delayed and delay_s > 0:
            # The output changes only after the delay elapses; poll again then.
            self.hass.async_create_task(
                self._async_delayed_verify_poll(module.module_id, delay_s)
            )

    def _is_duplicate_write(self, slave: int, function: int, payload_hex: str) -> bool:
        """Return True if this exact write frame was seen within DEDUPE_WINDOW.

        On the shared RS485 bus a write request and the module's identical echo both
        arrive; deduping prevents an optimistic toggle from flipping twice.
        """
        now = monotonic()
        key = (slave, function, payload_hex)
        for stale_key in [
            k for k, ts in self._recent_write_frames.items() if now - ts > self.DEDUPE_WINDOW
        ]:
            del self._recent_write_frames[stale_key]
        duplicate = key in self._recent_write_frames
        self._recent_write_frames[key] = now
        return duplicate

    @staticmethod
    def _coil_value_to_state(value: int, address: int, current: List[bool], known: bool) -> Optional[bool]:
        """Map a single-coil write value to a resulting state, or None if unknown."""
        if value == 0xFF00:
            return True
        if value == 0x0000:
            return False
        if value == 0x5555:  # toggle — flip the known bit, or poll if unknown
            if known and 0 <= address < len(current):
                return not current[address]
            return None
        return None

    def _decode_coil_effects(
        self,
        function: int,
        payload_hex: str,
        current: List[bool],
        known: bool,
    ) -> tuple[Dict[int, bool], bool, int]:
        """Decode a write frame into coil effects.

        Returns (effects, delayed, delay_s) where *effects* maps output index to its
        resulting state for immediate changes, *delayed* marks a delayed action whose
        output changes after *delay_s* seconds.
        """
        effects: Dict[int, bool] = {}
        try:
            raw = bytes.fromhex(payload_hex)
        except ValueError:
            return effects, False, 0

        if function == WRITE_SINGLE_COIL and len(raw) >= 4:
            address = int.from_bytes(raw[0:2], "big")
            value = int.from_bytes(raw[2:4], "big")
            result = self._coil_value_to_state(value, address, current, known)
            if result is not None:
                effects[address] = result
            return effects, False, 0

        if function == WRITE_MULTI_COILS and len(raw) >= 5:
            start = int.from_bytes(raw[0:2], "big")
            count = int.from_bytes(raw[2:4], "big")
            byte_count = raw[4]
            data = raw[5 : 5 + byte_count]
            bits = int.from_bytes(data, "little")
            for i in range(count):
                effects[start + i] = (bits >> i) & 0x01 == 1
            return effects, False, 0

        if function == WRITE_MULTI_REGS and len(raw) >= 5:
            start = int.from_bytes(raw[0:2], "big")
            byte_count = raw[4]
            reg_data = raw[5 : 5 + byte_count]
            regs = [
                int.from_bytes(reg_data[i : i + 2], "big")
                for i in range(0, len(reg_data) - 1, 2)
            ]
            # Delayed action: [output index, coil_data, delay (seconds), pwm].
            if start == 0 and len(regs) >= 3:
                return effects, True, regs[2]
            return effects, False, 0

        return effects, False, 0

    async def _async_update_data(self) -> Dict[int, List[bool]]:
        """Called once on startup to fetch initial coil state for all modules."""
        if not self.hub.is_connected:
            if self.data:
                return self.data
            return {m.module_id: [False] * m.outputs for m in self.modules}

        data: Dict[int, List[bool]] = {}
        for module in self.modules:
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

    def _schedule_verify_poll(self, module_id: int) -> None:
        """Schedule (or reschedule) a debounced verify-poll of a single module."""
        task = self._pending_verify_polls.get(module_id)
        if task and not task.done():
            task.cancel()
        self._pending_verify_polls[module_id] = self.hass.async_create_task(
            self._async_verify_poll(module_id)
        )

    async def _async_verify_poll(self, module_id: int) -> None:
        """After a short debounce, read the addressed module to confirm its state."""
        try:
            await asyncio.sleep(self.VERIFY_POLL_DEBOUNCE)
        except asyncio.CancelledError:
            return
        await self._async_read_module(module_id)

    async def _async_delayed_verify_poll(self, module_id: int, delay_s: int) -> None:
        """Poll a module after a delayed action's delay elapses, so the eventual
        output change is reflected in HA."""
        try:
            await asyncio.sleep(delay_s + self.DELAY_POLL_MARGIN)
        except asyncio.CancelledError:
            return
        await self._async_read_module(module_id)

    async def _async_read_module(self, module_id: int) -> None:
        """Read coils for a single module. The RX reply updates coordinator state."""
        module = self._module_map.get(module_id)
        if module is None or not self.hub.is_connected:
            return
        try:
            await self.hub.async_read_coils(module.module_id, 0, max(module.coil_count, 1))
            self._unreachable_modules.discard(module_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("domoriks: verify-poll failed for module %d: %s", module_id, err)
            self._unreachable_modules.add(module_id)
            # Notify entities so available goes False immediately.
            self.async_set_updated_data(self.data or {})


class DomoriksCoordinatorEntity(CoordinatorEntity[DomoriksCoordinator]):
    """Base entity bound to Domoriks coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DomoriksCoordinator) -> None:
        super().__init__(coordinator)
        self.hub = coordinator.hub
