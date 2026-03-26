from __future__ import annotations

from datetime import timedelta
from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "domoriks"

CONF_PORT: Final = "port"
CONF_BAUDRATE: Final = "baudrate"
CONF_MODULES: Final = "modules"  # preferred structure: list of module dicts
CONF_MODULE_ID: Final = "id"
CONF_OUTPUTS: Final = "outputs"
CONF_OUTPUT_NAMES: Final = "output_names"   # dict[str(index), friendly_name]
CONF_POLL_INTERVAL: Final = "poll_interval"
CONF_RECONNECT_INTERVAL: Final = "reconnect_interval"

DEFAULT_PORT: Final = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
DEFAULT_BAUDRATE: Final = 115200
DEFAULT_OUTPUTS_PER_MODULE: Final = 6
DEFAULT_POLL_INTERVAL: Final = timedelta(seconds=15)
DEFAULT_RECONNECT_INTERVAL: Final = 3

READ_COILS: Final = 0x01
READ_DISC_INPUTS: Final = 0x02
READ_HOLD_REGS: Final = 0x03
READ_INPUT_REGS: Final = 0x04
WRITE_SINGLE_COIL: Final = 0x05
WRITE_SINGLE_REG: Final = 0x06
WRITE_MULTI_COILS: Final = 0x0F
WRITE_MULTI_REGS: Final = 0x10

WRITE_FUNCTIONS: Final = frozenset({WRITE_SINGLE_COIL, WRITE_SINGLE_REG, WRITE_MULTI_COILS, WRITE_MULTI_REGS})

EVENT_RX: Final = f"{DOMAIN}_rx"
EVENT_TX: Final = f"{DOMAIN}_tx"
EVENT_ERROR: Final = f"{DOMAIN}_error"
EVENT_STARTED: Final = f"{DOMAIN}_started"
EVENT_DISCONNECTED: Final = f"{DOMAIN}_disconnected"

MANUFACTURER: Final = "Domoriks"
MODEL: Final = "Domoriks Modbus Module"

PLATFORMS: Final[list[Platform]] = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.TEXT,
    Platform.BUTTON,
]

SERVICE_COMMAND: Final = "command"
