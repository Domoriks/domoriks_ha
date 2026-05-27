from __future__ import annotations

from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .hub import DomoriksError, DomoriksHub
from .modbus import ModbusCodec

HTTP_VIEWS_REGISTERED = "http_views_registered"
MAX_RTU_FRAME_BYTES = 128


def async_register_http_views(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(HTTP_VIEWS_REGISTERED):
        return

    hass.http.register_view(DomoriksRawView(hass))
    hass.http.register_view(DomoriksDetectView(hass))
    domain_data[HTTP_VIEWS_REGISTERED] = True


class DomoriksBaseView(HomeAssistantView):
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def _get_hub(self, entry_id: str | None) -> DomoriksHub:
        entries = []
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            runtime_data = getattr(entry, "runtime_data", None)
            if runtime_data is None:
                continue
            if entry_id is not None and entry.entry_id != entry_id:
                continue
            entries.append(runtime_data.hub)

        if entry_id is not None and not entries:
            raise web.HTTPBadRequest(reason="Unknown Domoriks entry_id")
        if len(entries) > 1 and entry_id is None:
            raise web.HTTPBadRequest(
                reason="Multiple Domoriks entries configured; provide entry_id"
            )
        if not entries:
            raise web.HTTPServiceUnavailable(reason="No active Domoriks hub")
        return entries[0]

    async def _json(self, request: web.Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except Exception as exc:
            raise web.HTTPBadRequest(reason="Invalid JSON body") from exc
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(reason="JSON body must be an object")
        return data

    @staticmethod
    def _coerce_timeout(value: Any) -> float:
        if value is None:
            return 2.0
        try:
            timeout = float(value)
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(reason="timeout must be a number") from exc
        if timeout <= 0:
            raise web.HTTPBadRequest(reason="timeout must be greater than 0")
        return timeout

    @staticmethod
    def _coerce_slave(value: Any, field_name: str) -> int:
        try:
            slave = int(value)
        except (TypeError, ValueError) as exc:
            raise web.HTTPBadRequest(reason=f"{field_name} must be an integer") from exc
        if slave < 0 or slave > 247:
            raise web.HTTPBadRequest(reason=f"{field_name} must be between 0 and 247")
        return slave


class DomoriksRawView(DomoriksBaseView):
    url = "/api/domoriks/raw"
    name = "api:domoriks:raw"

    async def post(self, request: web.Request) -> web.Response:
        data = await self._json(request)
        hub = self._get_hub(data.get("entry_id"))
        timeout = self._coerce_timeout(data.get("timeout"))
        frame = self._parse_frame(data.get("frame"))

        try:
            response = await hub.async_send_raw_frame(frame, timeout=timeout)
        except DomoriksError as exc:
            raise web.HTTPGatewayTimeout(reason=str(exc)) from exc

        slave, function, payload = ModbusCodec.decode(frame)
        response_body: dict[str, Any] = {
            "tx": {
                "frame": frame.hex(),
                "slave": slave,
                "function": function,
                "payload": payload.hex(),
                "broadcast": slave == 0,
            }
        }
        if response is None:
            response_body["response"] = None
            return web.json_response(response_body)

        response_frame = ModbusCodec.encode(
            response.slave,
            response.function,
            response.payload,
        )
        response_body["response"] = {
            "frame": response_frame.hex(),
            "slave": response.slave,
            "function": response.function,
            "payload": response.payload.hex(),
            "exception": (response.function & 0x80) != 0,
            "exception_code": response.payload[0] if response.function & 0x80 and response.payload else None,
        }
        return web.json_response(response_body)

    @staticmethod
    def _parse_frame(value: Any) -> bytes:
        if not isinstance(value, str):
            raise web.HTTPBadRequest(reason="frame must be a hex string")
        frame_hex = "".join(value.split())
        try:
            frame = bytes.fromhex(frame_hex)
        except ValueError as exc:
            raise web.HTTPBadRequest(reason="frame must contain valid hex bytes") from exc
        if len(frame) < 4:
            raise web.HTTPBadRequest(reason="frame must be at least 4 bytes")
        if len(frame) > MAX_RTU_FRAME_BYTES:
            raise web.HTTPBadRequest(reason="frame exceeds 128-byte RTU limit")
        try:
            ModbusCodec.decode(frame)
        except ValueError as exc:
            raise web.HTTPBadRequest(reason=str(exc)) from exc
        return frame


class DomoriksDetectView(DomoriksBaseView):
    url = "/api/domoriks/detect"
    name = "api:domoriks:detect"

    async def post(self, request: web.Request) -> web.Response:
        data = await self._json(request)
        hub = self._get_hub(data.get("entry_id"))
        timeout = self._coerce_timeout(data.get("timeout"))

        if "slave" in data:
            slave = self._coerce_slave(data.get("slave"), "slave")
            reachable = await hub.async_detect_slave(slave, timeout=timeout)
            return web.json_response({"slave": slave, "reachable": reachable})

        if "start_slave" not in data or "end_slave" not in data:
            raise web.HTTPBadRequest(
                reason="Provide slave, or start_slave and end_slave"
            )

        start_slave = self._coerce_slave(data.get("start_slave"), "start_slave")
        end_slave = self._coerce_slave(data.get("end_slave"), "end_slave")
        if end_slave < start_slave:
            raise web.HTTPBadRequest(reason="end_slave must be greater than or equal to start_slave")

        result = await hub.async_detect_range(
            start_slave=start_slave,
            end_slave=end_slave,
            timeout=timeout,
        )
        result["start_slave"] = start_slave
        result["end_slave"] = end_slave
        return web.json_response(result)