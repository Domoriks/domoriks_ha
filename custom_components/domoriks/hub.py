from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from serial_asyncio_fast import open_serial_connection

from .command_parser import parse_command
from .const import (
    CONF_BAUDRATE,
    CONF_MODULE_ID,
    CONF_OUTPUT_ICONS,
    CONF_OUTPUT_NAMES,
    CONF_MODULES,
    CONF_MODULE_IDS,
    CONF_OUTPUTS,
    CONF_OUTPUTS_PER_MODULE,
    CONF_POLL_INTERVAL,
    CONF_PORT,
    CONF_RECONNECT_INTERVAL,
    DEFAULT_MODULE_IDS,
    DEFAULT_OUTPUTS_PER_MODULE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_RECONNECT_INTERVAL,
    DOMAIN,
    EVENT_DISCONNECTED,
    EVENT_ERROR,
    EVENT_RX,
    EVENT_STARTED,
    EVENT_TX,
    READ_COILS,
    WRITE_SINGLE_COIL,
)
from .modbus import ModbusCodec
from .serial_daemon import SerialDaemon

_LOGGER = logging.getLogger(__name__)


@dataclass
class Frame:
    slave: int
    function: int
    payload: bytes


@dataclass
class ModuleConfig:
    module_id: int
    outputs: int
    output_names: dict = field(default_factory=dict)
    output_icons: dict = field(default_factory=dict)

    @property
    def coil_count(self) -> int:
        return self.outputs


class DomoriksError(HomeAssistantError):
    """Raised when Domoriks operations fail."""


class DomoriksHub:
    """Manage the Domoriks serial connection and Modbus frames."""

    def __init__(self, hass: HomeAssistant, entry_data: dict) -> None:
        self.hass = hass
        self._port: str = entry_data.get(CONF_PORT)
        self._baudrate: int = int(entry_data.get(CONF_BAUDRATE))

        raw_modules = entry_data.get(CONF_MODULES)
        if raw_modules:
            self.modules: List[ModuleConfig] = [
                ModuleConfig(
                    module_id=int(module.get(CONF_MODULE_ID)),
                    outputs=int(module.get(CONF_OUTPUTS, DEFAULT_OUTPUTS_PER_MODULE)),
                    output_names=dict(module.get(CONF_OUTPUT_NAMES, {})),
                    output_icons=dict(module.get(CONF_OUTPUT_ICONS, {})),
                )
                for module in raw_modules
                if module.get(CONF_MODULE_ID) is not None
            ]
        else:
            module_ids = entry_data.get(CONF_MODULE_IDS, DEFAULT_MODULE_IDS)
            outputs_per_module = int(
                entry_data.get(CONF_OUTPUTS_PER_MODULE, DEFAULT_OUTPUTS_PER_MODULE)
            )
            self.modules = [
                ModuleConfig(
                    module_id=int(module_id),
                    outputs=outputs_per_module,
                )
                for module_id in module_ids
            ]
        self.module_ids: List[int] = [module.module_id for module in self.modules]

        raw_poll = entry_data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self.poll_interval = (
            raw_poll
            if isinstance(raw_poll, timedelta)
            else timedelta(seconds=float(raw_poll))
        )

        self.reconnect_interval = float(
            entry_data.get(CONF_RECONNECT_INTERVAL, DEFAULT_RECONNECT_INTERVAL)
        )

        self._daemon: Optional[SerialDaemon] = None
        self._connect_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._frame_queue: asyncio.Queue[Frame] = asyncio.Queue()
        self._connected = asyncio.Event()

        self.last_rx: Optional[Frame] = None
        self.last_tx: Optional[Frame] = None
        self.last_error: Optional[str] = None

    async def async_wait_connected(self, timeout: float = 10.0) -> bool:
        """Wait until connected, up to *timeout* seconds. Returns True if connected."""
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def async_start(self) -> None:
        if self._connect_task:
            return

        self._connect_task = self.hass.loop.create_task(self._connect_loop())

    async def async_stop(self) -> None:
        if self._connect_task:
            self._connect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._connect_task
            self._connect_task = None

        if self._daemon:
            await self._daemon.stop()
            self._daemon = None

    async def _connect_loop(self) -> None:
        while True:
            try:
                _LOGGER.info(
                    "domoriks: opening serial port %s @ %s",
                    self._port,
                    self._baudrate,
                )

                reader, writer = await open_serial_connection(
                    url=self._port,
                    baudrate=self._baudrate,
                )

                self._daemon = SerialDaemon(self._handle_frame, self._handle_error)
                self._connected.set()
                self.hass.bus.async_fire(
                    EVENT_STARTED,
                    {"port": self._port, "baudrate": self._baudrate},
                )
                _LOGGER.info("domoriks: serial connected")

                await self._daemon.start(reader, writer)

                _LOGGER.warning("domoriks: daemon terminated, reconnecting")
                self._connected.clear()
                self.hass.bus.async_fire(EVENT_DISCONNECTED)

            except asyncio.CancelledError:
                _LOGGER.info("domoriks: connect loop cancelled")
                raise

            except Exception as exc:  # noqa: BLE001 - reconnect on any error
                self._connected.clear()
                self.last_error = str(exc)
                self.hass.bus.async_fire(EVENT_ERROR, {"error": str(exc)})
                _LOGGER.exception("domoriks: connection error")

            await asyncio.sleep(self.reconnect_interval)

    async def _handle_frame(self, slave: int, function: int, payload: bytes) -> None:
        frame = Frame(slave, function, payload)
        self.last_rx = frame
        await self._frame_queue.put(frame)
        self.hass.bus.async_fire(
            EVENT_RX, {"slave": slave, "function": function, "payload": payload.hex()}
        )

    async def _handle_error(self, exc: Exception) -> None:
        self.last_error = str(exc)
        self._connected.clear()
        self.hass.bus.async_fire(EVENT_ERROR, {"error": str(exc)})

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    async def _send_raw(self, frame: bytes) -> None:
        """Write frame to serial. Caller must hold self._lock."""
        if not self._daemon or not self._connected.is_set():
            raise DomoriksError("Serial connection not ready")
        self._daemon.send_frame(frame)

    async def _wait_for_response(
        self, slave: int, function: int, timeout: float = 2.0
    ) -> Frame:
        try:
            while True:
                frame = await asyncio.wait_for(
                    self._frame_queue.get(),
                    timeout=timeout,
                )
                if frame.slave == slave and frame.function == function:
                    return frame
        except asyncio.TimeoutError as exc:
            raise DomoriksError("Timed out waiting for response") from exc

    async def async_read_coils(self, slave: int, start: int, count: int) -> List[bool]:
        payload = struct.pack(">HH", start, count)
        frame = ModbusCodec.encode(slave, READ_COILS, payload)
        async with self._lock:
            await self._send_raw(frame)
            self.last_tx = Frame(slave, READ_COILS, payload)
            response = await self._wait_for_response(slave, READ_COILS)
        if not response.payload:
            raise DomoriksError("Empty Modbus response")

        byte_count = response.payload[0]
        data = response.payload[1 : 1 + byte_count]
        value = int.from_bytes(data, byteorder="little")
        return [(value >> bit) & 0x01 == 1 for bit in range(count)]

    async def async_write_coil(self, slave: int, address: int, state: bool) -> None:
        _LOGGER.info(
            "Pulse test: Writing coil - slave=%s, address=%s, state=%s",
            slave, address, state
        )
        payload = struct.pack(">HH", address, 0xFF00 if state else 0x0000)
        frame = ModbusCodec.encode(slave, WRITE_SINGLE_COIL, payload)
        async with self._lock:
            try:
                await self._send_raw(frame)
                self.last_tx = Frame(slave, WRITE_SINGLE_COIL, payload)
                await self._wait_for_response(slave, WRITE_SINGLE_COIL)
                _LOGGER.info(
                    "Pulse test: Coil write successful - slave=%s, address=%s, state=%s",
                    slave, address, state
                )
            except Exception as exc:
                _LOGGER.error(
                    "Pulse test: Coil write failed - slave=%s, address=%s, state=%s, error=%s",
                    slave, address, state, exc
                )
                raise

    async def async_send_command_string(self, command: str) -> None:
        function, slave, payload = parse_command(command)
        frame = ModbusCodec.encode(slave, function, payload)
        async with self._lock:
            await self._send_raw(frame)
        self.last_tx = Frame(slave, function, payload)
        self.hass.bus.async_fire(EVENT_TX, {"command": command, "frame": frame.hex()})

    def diagnostics(self) -> Dict[str, object]:
        return {
            "port": self._port,
            "baudrate": self._baudrate,
            "modules": [
                {"id": module.module_id, "outputs": module.outputs}
                for module in self.modules
            ],
            "last_rx": self._frame_to_dict(self.last_rx),
            "last_tx": self._frame_to_dict(self.last_tx),
            "last_error": self.last_error,
            "connected": self.is_connected,
            "poll_interval_seconds": self.poll_interval.total_seconds()
            if hasattr(self.poll_interval, "total_seconds")
            else self.poll_interval,
        }

    @staticmethod
    def _frame_to_dict(frame: Optional[Frame]) -> Optional[Dict[str, object]]:
        if not frame:
            return None
        return {
            "slave": frame.slave,
            "function": frame.function,
            "payload": frame.payload.hex(),
            "received": datetime.utcnow().isoformat() + "Z",
        }
