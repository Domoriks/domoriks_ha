from __future__ import annotations

import logging
from datetime import datetime
from typing import Any


def _format_timestamp() -> str:
    """Format current UTC time as YYYY-MM-DD HHh MMm SS.mmmms"""
    now = datetime.utcnow()
    ms = now.microsecond // 1000
    return now.strftime('%Y-%m-%d, %H:%M:%S') + f".{ms:03d}s"

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    EVENT_DISCONNECTED,
    EVENT_ERROR,
    EVENT_RX,
    EVENT_STARTED,
    EVENT_TX,
    MANUFACTURER,
)
from .hub import DomoriksHub
from .modbus import ModbusCodec

_LOGGER = logging.getLogger(__name__)

_FUNC_NAMES: dict[int, str] = {
    0x01: "Read Coils",
    0x02: "Read Disc Inputs",
    0x03: "Read Hold Regs",
    0x04: "Read Input Regs",
    0x05: "Write Coil",
    0x06: "Write Register",
    0x0F: "Write Multi Coils",
    0x10: "Write Multi Regs",
}


def _event_parts(event: Event) -> tuple[int, int, str] | None:
    """Return event data as (slave, function, payload_hex)."""
    slave = event.data.get("slave")
    function = event.data.get("function")
    payload = event.data.get("payload", "")

    if isinstance(slave, int) and isinstance(function, int):
        return slave, function, payload if isinstance(payload, str) else ""

    frame = event.data.get("frame")
    if not isinstance(frame, str) or not frame:
        return None

    try:
        raw = bytes.fromhex(frame)
    except ValueError:
        return None

    try:
        parsed_slave, parsed_function, parsed_payload = ModbusCodec.decode(raw)
        return parsed_slave, parsed_function, parsed_payload.hex()
    except ValueError:
        if len(raw) < 4:
            return None
        return raw[0], raw[1], raw[2:-2].hex()


def _command_details(
    slave: int,
    function: int,
    payload_hex: str,
    direction: str,
) -> tuple[str, dict[str, Any]]:
    fn = _FUNC_NAMES.get(function, f"0x{function:02X}")
    value = f"{fn} {direction} slave {slave}"
    extra = {
        "slave": slave,
        "function": f"0x{function:02X}",
        "function_name": fn,
        "payload": payload_hex,
    }
    return value, extra


def _frame_hex(slave: int, function: int, payload_hex: str) -> str:
    try:
        payload = bytes.fromhex(payload_hex)
    except ValueError:
        return payload_hex
    return ModbusCodec.encode(slave, function, payload).hex()


def _gateway_device(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"gateway_{entry.entry_id}")},
        name="Domoriks Gateway",
        manufacturer=MANUFACTURER,
        model="Domoriks Bus Interface",
    )


class _GatewayBase(SensorEntity):
    _attr_has_entity_name = True
    _unsub: Any = None

    def __init__(self, hub: DomoriksHub, entry: ConfigEntry) -> None:
        self._hub = hub
        self._entry = entry
        self._attr_device_info = _gateway_device(entry)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None


class DomoriksRxSensor(_GatewayBase):
    """Last received Modbus frame."""

    _attr_icon = "mdi:download-network"

    def __init__(self, hub: DomoriksHub, entry: ConfigEntry) -> None:
        super().__init__(hub, entry)
        self._attr_unique_id = f"{entry.entry_id}_bus_rx"
        self._attr_name = "Last RX Command"
        self._extra: dict[str, Any] = {}
        if hub.last_rx:
            f = hub.last_rx
            value, extra = _command_details(
                f.slave,
                f.function,
                f.payload.hex(),
                "from",
            )
            self._attr_native_value = value
            self._extra = {**extra, "timestamp": "-"}

    async def async_added_to_hass(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_RX, self._handle)

    @callback
    def _handle(self, event: Event) -> None:
        parts = _event_parts(event)
        if parts:
            slave, function, payload_hex = parts
            value, extra = _command_details(slave, function, payload_hex, "from")
            self._attr_native_value = value
            self._extra = {**extra, "timestamp": _format_timestamp()}
        else:
            command: str = event.data.get("command", "")
            frame: str = event.data.get("frame", "")
            self._attr_native_value = command or frame
            self._extra = {
                "command": command,
                "frame": frame,
                "timestamp": _format_timestamp(),
            }
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._extra


class DomoriksRxTimeSensor(_GatewayBase):
    """Timestamp of the last received frame."""

    _attr_icon = "mdi:clock-in"
    _attr_native_value: str | None = None

    def __init__(self, hub: DomoriksHub, entry: ConfigEntry) -> None:
        super().__init__(hub, entry)
        self._attr_unique_id = f"{entry.entry_id}_bus_rx_time"
        self._attr_name = "Last RX Time"

    async def async_added_to_hass(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_RX, self._handle)

    @callback
    def _handle(self, event: Event) -> None:
        self._attr_native_value = _format_timestamp()
        self.async_write_ha_state()


class DomoriksRxRawDataSensor(_GatewayBase):
    """Raw payload data of the last received frame."""

    _attr_icon = "mdi:hexadecimal"
    _attr_native_value: str | None = None

    def __init__(self, hub: DomoriksHub, entry: ConfigEntry) -> None:
        super().__init__(hub, entry)
        self._attr_unique_id = f"{entry.entry_id}_bus_rx_raw"
        self._attr_name = "Last RX Raw Data"
        if hub.last_rx:
            self._attr_native_value = ModbusCodec.encode(
                hub.last_rx.slave,
                hub.last_rx.function,
                hub.last_rx.payload,
            ).hex()

    async def async_added_to_hass(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_RX, self._handle)

    @callback
    def _handle(self, event: Event) -> None:
        parts = _event_parts(event)
        if parts:
            self._attr_native_value = _frame_hex(*parts)
        else:
            frame: str = event.data.get("frame", "") or event.data.get("payload", "")
            self._attr_native_value = frame
        self.async_write_ha_state()


class DomoriksLastTxSensor(_GatewayBase):
    """Last transmitted Modbus frame."""

    _attr_icon = "mdi:upload-network"

    def __init__(self, hub: DomoriksHub, entry: ConfigEntry) -> None:
        super().__init__(hub, entry)
        self._attr_unique_id = f"{entry.entry_id}_bus_tx"
        self._attr_name = "Last TX Command"
        self._extra: dict[str, Any] = {}
        if hub.last_tx:
            f = hub.last_tx
            value, extra = _command_details(
                f.slave,
                f.function,
                f.payload.hex(),
                "to",
            )
            self._attr_native_value = value
            self._extra = {**extra, "timestamp": "-"}

    async def async_added_to_hass(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_TX, self._handle)

    @callback
    def _handle(self, event: Event) -> None:
        parts = _event_parts(event)
        if parts:
            slave, function, payload_hex = parts
            value, extra = _command_details(slave, function, payload_hex, "to")
            self._attr_native_value = value
            self._extra = {**extra, "timestamp": _format_timestamp()}
        else:
            command: str = event.data.get("command", "")
            frame: str = event.data.get("frame", "")
            self._attr_native_value = command or frame
            self._extra = {
                "command": command,
                "frame": frame,
                "timestamp": _format_timestamp(),
            }
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._extra


class DomoriksLastTxTimeSensor(_GatewayBase):
    """Timestamp of the last transmitted frame."""

    _attr_icon = "mdi:clock-out"
    _attr_native_value: str | None = None

    def __init__(self, hub: DomoriksHub, entry: ConfigEntry) -> None:
        super().__init__(hub, entry)
        self._attr_unique_id = f"{entry.entry_id}_bus_tx_time"
        self._attr_name = "Last TX Time"

    async def async_added_to_hass(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_TX, self._handle)

    @callback
    def _handle(self, event: Event) -> None:
        self._attr_native_value = _format_timestamp()
        self.async_write_ha_state()


class DomoriksLastTxRawDataSensor(_GatewayBase):
    """Raw frame data of the last transmitted command."""

    _attr_icon = "mdi:hexadecimal"
    _attr_native_value: str | None = None

    def __init__(self, hub: DomoriksHub, entry: ConfigEntry) -> None:
        super().__init__(hub, entry)
        self._attr_unique_id = f"{entry.entry_id}_bus_tx_raw"
        self._attr_name = "Last TX Raw Data"
        if hub.last_tx:
            self._attr_native_value = ModbusCodec.encode(
                hub.last_tx.slave,
                hub.last_tx.function,
                hub.last_tx.payload,
            ).hex()

    async def async_added_to_hass(self) -> None:
        self._unsub = self.hass.bus.async_listen(EVENT_TX, self._handle)

    @callback
    def _handle(self, event: Event) -> None:
        parts = _event_parts(event)
        if parts:
            self._attr_native_value = _frame_hex(*parts)
        else:
            frame: str = event.data.get("frame", "") or event.data.get("payload", "")
            self._attr_native_value = frame
        self.async_write_ha_state()


class DomoriksConnectionStatusSensor(_GatewayBase):
    """Connection status of the serial bus."""

    _attr_icon = "mdi:serial-port"

    def __init__(self, hub: DomoriksHub, entry: ConfigEntry) -> None:
        super().__init__(hub, entry)
        self._attr_unique_id = f"{entry.entry_id}_bus_status"
        self._attr_name = "Bus Status"
        self._unsubs: list = []
        self._attr_native_value = "connected" if hub.is_connected else "disconnected"

    async def async_added_to_hass(self) -> None:
        self._unsubs = [
            self.hass.bus.async_listen(EVENT_STARTED, self._on_connected),
            self.hass.bus.async_listen(EVENT_DISCONNECTED, self._on_disconnected),
            self.hass.bus.async_listen(EVENT_ERROR, self._on_error),
        ]

    async def async_will_remove_from_hass(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    @callback
    def _on_connected(self, event: Event) -> None:
        self._attr_native_value = "connected"
        self.async_write_ha_state()

    @callback
    def _on_disconnected(self, event: Event) -> None:
        self._attr_native_value = "disconnected"
        self.async_write_ha_state()

    @callback
    def _on_error(self, event: Event) -> None:
        error: str = event.data.get("error", "unknown")
        self._attr_native_value = "error"
        self._attr_extra_state_attributes = {"error": error, "timestamp": _format_timestamp()}
        self.async_write_ha_state()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub: DomoriksHub = entry.runtime_data.hub
    async_add_entities([
        DomoriksRxSensor(hub, entry),
        DomoriksRxTimeSensor(hub, entry),
        DomoriksRxRawDataSensor(hub, entry),
        DomoriksLastTxSensor(hub, entry),
        DomoriksLastTxTimeSensor(hub, entry),
        DomoriksLastTxRawDataSensor(hub, entry),
        DomoriksConnectionStatusSensor(hub, entry),
    ])