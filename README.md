# Domoriks

![Domoriks](custom_components/domoriks/brand/logo.png)

Home Assistant custom integration for controlling Domoriks Modbus modules over serial.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=domoriks&repository=domoriks_ha&category=integration)
[![Open your Home Assistant instance and start setting up Domoriks.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=domoriks)

## Features

- UI-based setup (no YAML required).
- Serial Modbus communication with Domoriks modules.
- Switch entities per module output.
- Binary sensor entities per module input.
- Built-in duplicate prevention per serial port.
- Raw command service for advanced use.
- Diagnostics download per config entry.

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant.
2. Add this repository as a custom repository if needed:
   - URL: `https://github.com/domoriks/domoriks_ha`
   - Category: `Integration`
3. Install **Domoriks** from HACS.
4. Restart Home Assistant.

### Manual installation

1. Copy `custom_components/domoriks` into your Home Assistant `custom_components` folder.
2. Restart Home Assistant.

## Configuration

1. Go to **Settings -> Devices & Services**.
2. Click **Add Integration**.
3. Search for **Domoriks**.
4. Enter the following:
   - **Port**: Serial path (e.g. `/dev/serial/by-id/...`).
   - **Baudrate**: Typically `115200`.
   - **Poll interval**: Seconds between coil state refreshes.
   - **Reconnect interval**: Seconds before retrying a serial reconnect.
   - **Modules**: Comma-separated list `<id>:<outputs>[:inputs]` (e.g. `64:6,65:6:2`). Inputs are optional.

Options can be edited after setup to change modules.

## Entities

Platform | Description
-- | --
`switch` | One per module output
`binary_sensor` | One per module input

## Services

- `domoriks.command`: Send a raw Modbus command string (e.g. `rc 64 0 16`).

## Diagnostics

A diagnostics download is available per config entry with connection state and last RX/TX frames.

## Troubleshooting

- `cannot_connect`: Verify the serial port path and that the device is connected.
- `already_configured`: This port is already set up in Home Assistant.

Enable debug logging in `configuration.yaml`:

``````yaml
logger:
  default: info
  logs:
    custom_components.domoriks: debug
``````