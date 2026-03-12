import asyncio
import logging
from time import monotonic
from typing import Awaitable, Callable, Optional

from .modbus import ModbusCodec

_LOGGER = logging.getLogger(__name__)


class SerialDaemon:
    # For 115200 baud: 3.5 chars = 0.334ms, half = 0.167ms
    FRAME_TIMEOUT = 0.0001  # 0.1ms - 0.17ms is half of 3.5 character time at 115200 baud
    READ_SIZE = 8  # Read multiple bytes at once for efficiency

    def __init__(
        self,
        on_frame: Callable[[int, int, bytes], Awaitable[None] | None],
        on_error: Callable[[Exception], Awaitable[None] | None],
    ) -> None:
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.buffer = bytearray()
        self._running = False
        self._last_byte_time = 0.0
        self._on_frame = on_frame
        self._on_error = on_error

    async def start(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self._running = True
        _LOGGER.info("domoriks: SerialDaemon start()")
        await self._read_loop()

    async def stop(self) -> None:
        self._running = False
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        _LOGGER.warning("domoriks: SerialDaemon stopped")

    def _try_extract_frame(self) -> bool:
        """
        Try to extract a complete Modbus frame from the buffer.
        Returns True if a frame was extracted and processed.
        """
        if len(self.buffer) < 5:  # Minimum Modbus frame: slave(1) + func(1) + data(1+) + crc(2)
            return False

        try:
            slave, function, payload = ModbusCodec.decode(bytes(self.buffer))

            _LOGGER.debug(
                "domoriks RX: slave=%d func=0x%02X payload=%s",
                slave,
                function,
                payload.hex(),
            )

            result = self._on_frame(slave, function, payload)
            if asyncio.iscoroutine(result):
                asyncio.create_task(result)

            self.buffer.clear()
            return True

        except Exception as err:  # noqa: BLE001 - keep broad to avoid dropping frames
            if len(self.buffer) > 256:
                _LOGGER.debug(
                    "domoriks: buffer too large, clearing: %s (%s)",
                    self.buffer[:64].hex(),
                    err,
                )
                self.buffer.clear()
            return False

    async def _read_loop(self) -> None:
        self._last_byte_time = monotonic()

        while self._running:
            try:
                data = await asyncio.wait_for(
                    self.reader.read(self.READ_SIZE),
                    timeout=self.FRAME_TIMEOUT,
                )

                if data:
                    now = monotonic()
                    self.buffer.extend(data)
                    self._last_byte_time = now

                    while self._try_extract_frame():
                        pass

            except asyncio.TimeoutError:
                if self.buffer:
                    now = monotonic()
                    if now - self._last_byte_time > self.FRAME_TIMEOUT:
                        if not self._try_extract_frame():
                            if len(self.buffer) > 0:
                                _LOGGER.debug(
                                    "domoriks: timeout with incomplete frame: %s",
                                    self.buffer.hex(),
                                )
                                self.buffer.clear()

            except Exception as exc:  # noqa: BLE001 - keep broad to surface serial faults
                self.buffer.clear()
                await asyncio.sleep(1)
                result = self._on_error(exc)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)

    def send_frame(self, frame: bytes) -> None:
        if not self.writer:
            raise RuntimeError("Serial writer not initialized")

        _LOGGER.debug("domoriks TX: %s", frame.hex(" ").upper())
        self.writer.write(frame)
        asyncio.create_task(self.writer.drain())
