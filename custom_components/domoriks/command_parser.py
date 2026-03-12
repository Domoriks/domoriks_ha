import struct
from .const import *

def parse_command(command: str):
    tokens = command.strip().split()
    if not tokens:
        raise ValueError("Empty command")

    cmd = tokens[0].lower()
    
    if cmd == "wmc":
        if len(tokens) == 5:
            # New format: wmc <slave> <start> <bits> <hexdata>
            slave_id = int(tokens[1])
            start = int(tokens[2])
            count = int(tokens[3])  # This is the precise bit count
            hex_data = tokens[4]

            byte_count = (count + 7) // 8
            
            if len(hex_data) < byte_count * 2:
                hex_data = hex_data.zfill(byte_count * 2)

            data = bytes.fromhex(hex_data)

            payload = struct.pack(">HHB", start, count, byte_count) + data[:byte_count]
            return WRITE_MULTI_COILS, slave_id, payload

        elif len(tokens) == 4:
            # Old format: wmc <slave> <start> <hexdata>
            slave_id = int(tokens[1])
            start = int(tokens[2])
            hex_data = tokens[3]
            
            # This is the logic from two steps ago, to pad odd-length strings
            if len(hex_data) % 2 != 0:
                hex_data = '0' + hex_data
            
            data = bytes.fromhex(hex_data)
            count = len(data) * 8 # Derive bit count from data length
            byte_count = len(data)

            payload = struct.pack(">HHB", start, count, byte_count) + data
            return WRITE_MULTI_COILS, slave_id, payload
        else:
            raise ValueError("wmc format: <slave> <start> [<bits>] <hexdata>")

    args = [int(t, 16) if t.startswith("0x") else int(t) for t in tokens[1:]]

    if cmd == "rc":
        return READ_COILS, args[0], struct.pack(">HH", args[1], args[2])

    if cmd == "ri":
        return READ_DISC_INPUTS, args[0], struct.pack(">HH", args[1], args[2])

    if cmd == "rh":
        return READ_HOLD_REGS, args[0], struct.pack(">HH", args[1], args[2])

    if cmd == "rr":
        return READ_INPUT_REGS, args[0], struct.pack(">HH", args[1], args[2])

    if cmd == "wc":
        val = {0: 0x0000, 1: 0xFF00, 2: 0x5555}[args[2]]
        return WRITE_SINGLE_COIL, args[0], struct.pack(">HH", args[1], val)

    if cmd == "wr":
        return WRITE_SINGLE_REG, args[0], struct.pack(">HH", args[1], args[2])

    if cmd == "wmr":
        start = args[1]
        regs = args[2:]
        payload = struct.pack(">HHB", start, len(regs), len(regs) * 2)
        for r in regs:
            payload += struct.pack(">H", r)
        return WRITE_MULTI_REGS, args[0], payload

    raise ValueError(f"Unknown command: {cmd}")
