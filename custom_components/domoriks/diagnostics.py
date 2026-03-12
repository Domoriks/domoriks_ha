from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT: set[str] = set()


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    runtime = entry.runtime_data
    hub = runtime.hub if runtime else None
    diagnostics = hub.diagnostics() if hub else {}
    return async_redact_data(diagnostics, TO_REDACT)
