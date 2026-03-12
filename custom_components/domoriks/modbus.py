import struct

def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return ((crc & 0xFF) << 8) | (crc >> 8)


class ModbusCodec:
    @staticmethod
    def encode(slave: int, function: int, payload: bytes) -> bytes:
        frame = bytes([slave, function]) + payload
        crc = crc16(frame)
        return frame + struct.pack(">H", crc)

    @staticmethod
    def decode(frame: bytes):
        if len(frame) < 4:
            raise ValueError("Frame too short")

        body = frame[:-2]
        crc_rx = struct.unpack(">H", frame[-2:])[0]
        if crc16(body) != crc_rx:
            raise ValueError("CRC mismatch")

        return body[0], body[1], body[2:]
