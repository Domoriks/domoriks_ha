import asyncio
import logging
from time import monotonic
from typing import Awaitable, Callable, Optional

from .modbus import ModbusCodec

_LOGGER = logging.getLogger(__name__)


class SerialDaemon:
    # Modbus RTU marks a frame boundary with >=3.5 character times of silence.
    # At 115200 baud one character is ~87us, so 3.5 chars is ~0.30ms. We use a
    # comfortably larger idle threshold so brief inter-byte gaps while a device
    # is busy (e.g. committing a register write to flash) are NOT mistaken for
    # end-of-frame, which previously caused the in-flight response to be dropped.
    FRAME_TIMEOUT = 0.005  # 5ms idle => frame boundary / flush
    READ_SIZE = 64  # Read multiple bytes at once for efficiency

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

    def _notify_error(self, exc: Exception) -> None:
        """Invoke the error callback, scheduling it if it is a coroutine."""
        result = self._on_error(exc)
        if asyncio.iscoroutine(result):
            asyncio.create_task(result)

    def _try_extract_frame(self) -> bool:
        """
        Try to extract a single complete Modbus frame from the front of the
        buffer. Returns True if a frame was extracted and processed (there may
        be more frames still buffered, so callers should loop).

        Unlike a naive "decode the whole buffer" approach, this locates the end
        of the first frame by function code (or by scanning for a valid CRC),
        so a response that arrives concatenated with an unsolicited event frame
        is no longer lost.
        """
        end = self._frame_end(self.buffer)
        if end is None:
            # No complete/valid frame at the front yet.
            if len(self.buffer) > 256:
                _LOGGER.debug(
                    "domoriks: dropping %d unparseable bytes: %s",
                    len(self.buffer),
                    self.buffer[:64].hex(),
                )
                self.buffer.clear()
            return False

        frame = bytes(self.buffer[:end])
        del self.buffer[:end]

        try:
            slave, function, payload = ModbusCodec.decode(frame)
        except Exception as err:  # noqa: BLE001 - should not happen after _frame_end
            _LOGGER.debug("domoriks: discarding invalid frame %s (%s)", frame.hex(), err)
            return len(self.buffer) >= 4

        _LOGGER.debug(
            "domoriks RX: slave=%d func=0x%02X payload=%s",
            slave,
            function,
            payload.hex(),
        )

        result = self._on_frame(slave, function, payload)
        if asyncio.iscoroutine(result):
            asyncio.create_task(result)

        return len(self.buffer) >= 4

    @staticmethod
    def _frame_end(buffer: bytearray) -> Optional[int]:
        """Return the index just past the first complete, CRC-valid frame in
        *buffer*, or None if no complete frame is present yet.

        The expected length is derived from the Modbus function code when known
        (exceptions, read responses with a byte count, and fixed-length write
        echoes). For unknown function codes it falls back to scanning for the
        shortest prefix with a valid CRC.
        """
        n = len(buffer)
        if n < 4:  # slave + func + at least 2 CRC bytes
            return None

        function = buffer[1]
        expected = SerialDaemon._expected_frame_len(function, buffer)
        if expected is not None:
            if n < expected:
                return None
            if ModbusCodec.crc_ok(bytes(buffer[:expected])):
                return expected
            # Length guess was wrong (e.g. an event frame reusing the code);
            # fall through to CRC scanning.

        for end in range(4, n + 1):
            if ModbusCodec.crc_ok(bytes(buffer[:end])):
                return end
        return None

    @staticmethod
    def _expected_frame_len(function: int, buffer: bytearray) -> Optional[int]:
        """Full frame length (including the 2 CRC bytes) for known Modbus
        response function codes, or None if it cannot be determined."""
        if function & 0x80:  # exception response: slave+func+code+CRC
            return 5
        base = function & 0x7F
        if base in (0x01, 0x02, 0x03, 0x04):  # read: slave+func+bytecount+data+CRC
            if len(buffer) < 3:
                return None
            return 3 + buffer[2] + 2
        if base in (0x05, 0x06, 0x0F, 0x10):  # write echo: slave+func+addr+val/qty+CRC
            return 8
        return None


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
                elif self.reader.at_eof():
                    # An empty read at EOF means the serial device vanished
                    # (e.g. the USB-RS485 adapter was unplugged). Terminate the
                    # loop so the hub's connect loop can restore the bus and
                    # detect the device when it reconnects.
                    _LOGGER.warning(
                        "domoriks: serial EOF - RS485 device disconnected"
                    )
                    self._notify_error(ConnectionError("Serial device disconnected"))
                    break

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
                # A serial fault (e.g. the RS485 adapter was unplugged) is not
                # recoverable in place. Stop the loop so the hub can reopen the
                # port and detect the device once it reconnects.
                self.buffer.clear()
                _LOGGER.warning("domoriks: serial read error, terminating: %s", exc)
                self._notify_error(exc)
                break

        self._running = False

    def send_frame(self, frame: bytes) -> None:
        if not self.writer:
            raise RuntimeError("Serial writer not initialized")

        _LOGGER.debug("domoriks TX: %s", frame.hex(" ").upper())
        self.writer.write(frame)
        asyncio.create_task(self.writer.drain())
