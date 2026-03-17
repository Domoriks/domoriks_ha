# Domoriks

![Domoriks](custom_components/domoriks/brand/logo.png)

Home Assistant custom integration for controlling Domoriks Modbus modules over serial (RS-485 / USB).

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=domoriks&repository=domoriks_ha&category=integration)
[![Open your Home Assistant instance and start setting up Domoriks.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=domoriks)

---

## Features

- UI-based setup with no YAML required.
- Serial Modbus RTU communication over USB or RS-485.
- Switch entities for every module output.
- Gateway device with live bus activity sensors for last RX, last TX, timestamps, and bus status.
- Gateway command controls: one text entity to stage a raw command and one button entity to send it.
- Per-module output naming and icon configuration during setup.
- Duplicate prevention per serial port.
- Raw command service for automation use.
- Diagnostics download per config entry.

---

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant.
2. Add this repository as a custom repository if not already listed:
   - URL: `https://github.com/domoriks/domoriks_ha`
   - Category: `Integration`
3. Install **Domoriks** from HACS.
4. Restart Home Assistant.

### Manual

1. Copy `custom_components/domoriks` into your HA `custom_components` folder.
2. Restart Home Assistant.

---

## Setup flow

### Step 1 - Connection

Go to **Settings -> Devices & Services -> Add Integration -> Domoriks**.

| Field | Description | Default |
|---|---|---|
| **Port** | Serial path, e.g. `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` | `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` |
| **Baudrate** | Serial baud rate | `115200` |
| **Poll interval (s)** | Seconds between full coil-state reads | `15` |
| **Reconnect interval (s)** | Seconds before retrying after disconnect | `3` |
| **Modules** | Comma-separated `<id>:<outputs>` pairs, e.g. `64:6,65:6,66:6` | `64:6,65:6,66:6,67:6,68:6` |

After submitting, the integration opens a temporary serial connection and moves to Step 2 for each configured module.

### Step 2 - Per-module naming

For each module you will see a form showing:

- **Status**: `Reachable` or `Not responding` (probed via a READ_COILS request).
- **Output name** and **icon** fields for every output on the module.
- If a module is **not responding**, an extra **Remove this module from setup** checkbox allows you to skip it.

Click **Submit** to save names and icons and proceed to the next module.  
After all modules are configured, the integration is created.

### Reconfigure (Options flow)

Open **Settings -> Devices & Services -> Domoriks -> Configure** to:

- Change poll interval and reconnect interval.
- Add, remove, or rename modules (same per-module naming step as setup).

---

## Entities

### Per-module device `Module <id>`

| Entity | Platform | Description |
|---|---|---|
| `Output 1` to `Output N` | `switch` | Toggle a module output. `ON` writes coil `0xFF00`, `OFF` writes coil `0x0000`. Becomes unavailable if the module stops responding. |

### Gateway device `Domoriks Gateway`

| Entity | Platform | Description |
|---|---|---|
| **Last RX** | `sensor` | Human-readable summary of the last received Modbus frame, e.g. `Read Coils from slave 64`. Attributes: `slave`, `function`, `function_name`, `payload` (hex), `timestamp` (UTC). |
| **Last RX Time** | `sensor` | UTC timestamp of the last received frame. |
| **Last TX** | `sensor` | Last transmitted command or frame hex. Attributes: `command`, `frame`, `timestamp`. |
| **Last TX Time** | `sensor` | UTC timestamp of the last transmitted frame. |
| **Bus Status** | `sensor` | `connected`, `disconnected`, or `error`. When `error`, the `error` attribute contains the error message. |
| **Command** | `text` | Stores the raw command that will be sent when the button is pressed. Example: `wc 64 0 1`. |
| **Send Command** | `button` | Sends the current value from the `Command` text entity immediately. |

---

## Services

### `domoriks.command`

Send a raw Modbus command string from an automation or script.

```yaml
service: domoriks.command
data:
  command: "wc 64 0 1"
```

#### Command syntax

```
<cmd> <slave> <address> <data>
```

| Token | Description |
|---|---|
| `cmd` | Command mnemonic; see table below |
| `slave` | Module ID (decimal), e.g. `64` |
| `address` | Coil or register address (decimal) |
| `data` | Value / count |

Common commands:

| Mnemonic | Function code | Description |
|---|---|---|
| `rc` | `0x01` | Read coils |
| `rh` | `0x03` | Read holding registers |
| `wc` | `0x05` | Write single coil. Use `0` = OFF, `1` = ON, `2` = toggle (`0x5555`). |
| `wmc` | `0x0F` | Write multiple coils. Supports `wmc <slave> <start> <hexdata>` and `wmc <slave> <start> <bits> <hexdata>`. |
| `wr` | `0x06` | Write single register. |
| `wmr` | `0x10` | Write multiple registers. |

---

## Diagnostics

A diagnostics download is available per config entry (Settings -> Devices & Services -> Domoriks -> Download diagnostics). It includes:

- Port and baudrate.
- Configured modules with output counts.
- Last RX and TX frames.
- Last error message.
- Connection state.
- Poll interval.

---

## Troubleshooting

| Error | Cause / Fix |
|---|---|
| `cannot_connect` | Check the serial port path and that the USB adapter is connected. |
| `already_configured` | This port is already set up; edit the existing entry instead. |
| Module shows unavailable | Module did not respond to a READ_COILS probe. Check wiring. Remove it via Configure if needed. |
| Unknown error in setup | Restart HA after updating; if persistent, enable debug logging and check the log. |

Enable debug logging in `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.domoriks: debug
```