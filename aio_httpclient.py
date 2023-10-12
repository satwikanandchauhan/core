import asyncio
import aiohttp
from aiohttp import web
from aiohttp.hdrs import CONTENT_TYPE, USER_AGENT
from aiohttp.web_exceptions import HTTPBadGateway, HTTPGatewayTimeout
from homeassistant import config_entries
from homeassistant.const import APPLICATION_NAME, EVENT_HOMEASSISTANT_CLOSE, __version__
from homeassistant.core import HomeAssistant, Event
from homeassistant.loader import bind_hass
from homeassistant.util import ssl as ssl_util
from homeassistant.util.json import json_loads
from aiohttp.typedefs import JSONDecoder
from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import Any, cast
from contextlib import suppress
import sys

DATA_CONNECTOR = "aiohttp_connector"
DATA_CONNECTOR_NOTVERIFY = "aiohttp_connector_notverify"
DATA_CLIENTSESSION = "aiohttp_clientsession"
DATA_CLIENTSESSION_NOTVERIFY = "aiohttp_clientsession_notverify"
SERVER_SOFTWARE = f"{APPLICATION_NAME}/{__version__} aiohttp/{aiohttp.__version__} Python/{sys.version_info[0]}.{sys.version_info[1]}"

ENABLE_CLEANUP_CLOSED = not (3, 11, 1) <= sys.version_info < (3, 11, 4)
MAXIMUM_CONNECTIONS = 4096
MAXIMUM_CONNECTIONS_PER_HOST = 100
WARN_CLOSE_MSG = "closes the Home Assistant aiohttp session"

async def _noop_wait(*args: Any, **kwargs: Any) -> None:
    return

web.BaseSite._wait = _noop_wait

class HassClientResponse(aiohttp.ClientResponse):
    async def json(self, *args: Any, loads: JSONDecoder = json_loads, **kwargs: Any) -> Any:
        return await super().json(*args, loads=loads, **kwargs)

@bind_hass
def async_get_clientsession(hass: HomeAssistant, verify_ssl: bool = True) -> aiohttp.ClientSession:
    key = DATA_CLIENTSESSION if verify_ssl else DATA_CLIENTSESSION_NOTVERIFY

    if key not in hass.data:
        hass.data[key] = _async_create_clientsession(hass, verify_ssl, auto_cleanup_method=_async_register_default_clientsession_shutdown)

    return cast(aiohttp.ClientSession, hass.data[key])

@bind_hass
def async_create_clientsession(hass: HomeAssistant, verify_ssl: bool = True, auto_cleanup: bool = True, **kwargs: Any) -> aiohttp.ClientSession:
    auto_cleanup_method = None
    if auto_cleanup:
        auto_cleanup_method = _async_register_clientsession_shutdown

    clientsession = _async_create_clientsession(hass, verify_ssl, auto_cleanup_method=auto_cleanup_method, **kwargs)
    return clientsession

def _async_create_clientsession(hass: HomeAssistant, verify_ssl: bool = True, auto_cleanup_method: Callable[[HomeAssistant, aiohttp.ClientSession], None] | None = None, **kwargs: Any) -> aiohttp.ClientSession:
    clientsession = aiohttp.ClientSession(
        connector=_async_get_connector(hass, verify_ssl),
        json_serialize=json_dumps,
        response_class=HassClientResponse,
        **kwargs
    )
    clientsession._default_headers = MappingProxyType({USER_AGENT: SERVER_SOFTWARE})
    clientsession.close = warn_use(clientsession.close, WARN_CLOSE_MSG)

    if auto_cleanup_method:
        auto_cleanup_method(hass, clientsession)

    return clientsession

async def async_aiohttp_proxy_web(hass: HomeAssistant, request: web.BaseRequest, web_coro: Awaitable[aiohttp.ClientResponse], buffer_size: int = 102400, timeout: int = 10) -> web.StreamResponse | None:
    try:
        async with asyncio.timeout(timeout):
            req = await web_coro
    except asyncio.CancelledError:
        return None
    except asyncio.TimeoutError as err:
        raise HTTPGatewayTimeout() from err
    except aiohttp.ClientError as err:
        raise HTTPBadGateway() from err

    try:
        return await async_aiohttp_proxy_stream(hass, request, req.content, req.headers.get(CONTENT_TYPE))
    finally:
        req.close()

async def async_aiohttp_proxy_stream(hass: HomeAssistant, request: web.BaseRequest, stream: aiohttp.StreamReader, content_type: str | None, buffer_size: int = 102400, timeout: int = 10) -> web.StreamResponse:
    response = web.StreamResponse()
    if content_type is not None:
        response.content_type = content_type
    await response.prepare(request)

    with suppress(asyncio.TimeoutError, aiohttp.ClientError):
        while hass.is_running:
            async with asyncio.timeout(timeout):
                data = await stream.read(buffer_size)
            if not data:
                break
            await response.write(data)

    return response

@callback
def _async_register_clientsession_shutdown(hass: HomeAssistant, clientsession: aiohttp.ClientSession) -> None:
    @callback
    def _async_close_websession(*_: Any) -> None:
        clientsession.detach()

    unsub = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_CLOSE, _async_close_websession)

    if not (config_entry := config_entries.current_entry.get()):
        return

    config_entry.async_on_unload(unsub)
    config_entry.async_on_unload(_async_close_websession)

@callback
def _async_register_default_clientsession_shutdown(hass: HomeAssistant, clientsession: aiohttp.ClientSession) -> None:
    @callback
    def _async_close_websession(event: Event) -> None:
        clientsession.detach()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_CLOSE, _async_close_websession)

@callback
def _async_get_connector(hass: HomeAssistant, verify_ssl: bool = True) -> aiohttp.BaseConnector:
    key = DATA_CONNECTOR if verify_ssl else DATA_CONNECTOR_NOTVERIFY

    if key in hass.data:
        return cast(aiohttp.BaseConnector, hass.data[key])

    if verify_ssl:
        ssl_context = ssl_util.get_default_context()
    else:
        ssl_context = ssl_util.get_default_no_verify_context()

    connector = aiohttp.TCPConnector(
        enable_cleanup_closed=ENABLE_CLEANUP_CLOSED,
        ssl=ssl_context,
        limit=MAXIMUM_CONNECTIONS,
        limit_per_host=MAXIMUM_CONNECTIONS_PER_HOST
    )
    hass.data[key] = connector

    async def _async_close_connector(event: Event) -> None:
        await connector.close()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_CLOSE, _async_close_connector)

    return connector
