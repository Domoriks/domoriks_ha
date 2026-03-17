from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from homeassistant.components.persistent_notification import (
    async_create as pn_create,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_BAUDRATE,
    CONF_MODULE_ID,
    CONF_MODULE_IDS,
    CONF_MODULES,
    CONF_PORT,
    DOMAIN,
    PLATFORMS,
    SERVICE_COMMAND,
)
from .coordinator import DomoriksCoordinator
from .hub import DomoriksHub

type DomoriksConfigEntry = ConfigEntry["DomoriksRuntimeData"]

_LOGGER = logging.getLogger(__name__)


@dataclass
class DomoriksRuntimeData:
    hub: DomoriksHub
    coordinator: DomoriksCoordinator
    manual_command: str = "rc 64 0 6"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import YAML and defer to config entries."""
    if DOMAIN not in config:
        return True

    hass.data.setdefault(DOMAIN, {})

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "import"},
            data=config[DOMAIN],
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DomoriksConfigEntry) -> bool:
    """Set up Domoriks from a config entry."""

    # Merge options over data so that changes made via the options flow
    # (e.g. modules, poll_interval) are applied on every (re)load.
    effective_data = {**entry.data, **entry.options}

    hub = DomoriksHub(hass, effective_data)
    await hub.async_start()

    # Wait up to 10 s for the serial port to open before the first poll.
    # If the port is not ready in time we continue anyway; the coordinator
    # will keep retrying on its normal update interval.
    await hub.async_wait_connected(timeout=10.0)

    coordinator = DomoriksCoordinator(hass, hub, entry)
    coordinator.async_subscribe_events()
    # Seed coordinator with safe default data so the entry loads immediately.
    # Real state is fetched in the background; entities update via RX events.
    coordinator.async_set_updated_data(
        {m.module_id: [False] * m.outputs for m in hub.modules}
    )

    async def _initial_refresh_and_notify() -> None:
        """Run the first poll and notify about any unreachable modules."""
        await coordinator.async_request_refresh()
        unreachable = sorted(coordinator._unreachable_modules)
        if unreachable:
            pn_create(
                hass,
                (
                    f"Module(s) {unreachable} did not respond during startup and are "
                    "marked unavailable. Verify wiring, or remove them via "
                    "Settings \u2192 Integrations \u2192 Domoriks \u2192 Configure."
                ),
                title="Domoriks \u2014 unreachable modules",
                notification_id="domoriks_unreachable",
            )

    hass.async_create_task(_initial_refresh_and_notify())

    entry.runtime_data = DomoriksRuntimeData(hub=hub, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass, hub)

    # Reload the entry whenever the user saves new options.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: DomoriksConfigEntry) -> None:
    """Reload entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: DomoriksConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removal of a module device from the device page.

    Removes the module from options so it is not re-created on next reload.
    """
    module_id: int | None = None
    for domain, identifier in device_entry.identifiers:
        if domain == DOMAIN:
            try:
                module_id = int(identifier)
            except ValueError:
                pass
            break

    if module_id is None:
        return False

    # Remove module from the stored options/data.
    current_modules = {
        **entry.data,
        **entry.options,
    }.get(CONF_MODULES, [])
    new_modules = [m for m in current_modules if m.get(CONF_MODULE_ID) != module_id]
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_MODULES: new_modules}
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DomoriksConfigEntry) -> bool:
    """Unload a config entry."""

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    runtime = entry.runtime_data
    if runtime:
        await runtime.hub.async_stop()

    return unload_ok


def _register_services(hass: HomeAssistant, hub: DomoriksHub) -> None:
    """Register integration level services."""

    async def handle_command(call):
        command = call.data[SERVICE_COMMAND]
        await hub.async_send_command_string(command)

    if not hass.services.has_service(DOMAIN, SERVICE_COMMAND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_COMMAND,
            handle_command,
            schema=cv.make_entity_service_schema({SERVICE_COMMAND: cv.string}),
        )
