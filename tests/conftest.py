"""Shared fixtures for the Elegoo Spaghetti Detection tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.elegoo_spaghetti_detection.const import (
    CONF_ACTIVE_PRINT_STATES,
    CONF_CAMERA,
    CONF_HOME_ASSISTANT_HOST,
    CONF_INSTANCE_ID,
    CONF_LIGHT_CONTROL_MODE,
    CONF_OBICO_AUTH_TOKEN,
    CONF_OBICO_HOST,
    CONF_PRINT_STATUS_SENSOR,
    DOMAIN,
    LIGHT_CONTROL_OFF,
    RUNTIME_DATA,
)

# Documentation-only hosts (RFC 2606 / RFC 5737); nothing is contacted.
HA_HOST = "http://ha.example.test:8123"
OBICO_HOST = "http://obico.example.test:3333"
OBICO_TOKEN = "test-obico-token"
CAMERA = "camera.test_printer"
CAMERA_PICTURE = "/api/camera_proxy/camera.test_printer?token=camera-token"
CAMERA_URL = f"{HA_HOST}{CAMERA_PICTURE}"
STATUS = "sensor.test_printer_print_status"
JOB_ID = "sensor.test_printer_task_id"
JOB_NAME = "sensor.test_printer_file_name"
PREFIX = "test_detector"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 96  # 100 bytes, JPEG magic


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    """Give every test its own config folder for the frames."""
    return hass_tmp_config_dir


def entry_data(**overrides: Any) -> dict[str, Any]:
    """Return the data of a detector config entry."""
    data = {
        CONF_INSTANCE_ID: PREFIX,
        CONF_HOME_ASSISTANT_HOST: HA_HOST,
        CONF_OBICO_HOST: OBICO_HOST,
        CONF_OBICO_AUTH_TOKEN: OBICO_TOKEN,
        CONF_CAMERA: CAMERA,
        CONF_PRINT_STATUS_SENSOR: STATUS,
        CONF_ACTIVE_PRINT_STATES: "printing",
        CONF_LIGHT_CONTROL_MODE: LIGHT_CONTROL_OFF,
    }
    data.update(overrides)
    return data


SetupDetector = Callable[..., Awaitable[MockConfigEntry]]


@pytest.fixture
def setup_detector(hass: HomeAssistant) -> SetupDetector:
    """Return a coroutine that sets up one detector and returns its entry."""

    async def setup(options: dict[str, Any] | None = None, **data: Any) -> MockConfigEntry:
        hass.states.async_set(CAMERA, "idle", {"entity_picture": CAMERA_PICTURE})
        hass.states.async_set(STATUS, "printing")
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Test detector",
            data=entry_data(**data),
            options=options or {},
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return setup


def runtime_of(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """Return a detector's runtime."""
    return hass.data[DOMAIN][RUNTIME_DATA][entry.entry_id]


def mock_obico(
    aioclient_mock: AiohttpClientMocker,
    score: float = 0.2,
    **kwargs: Any,
) -> None:
    """Answer Obico's prediction endpoint with one detection of ``score``."""
    if "json" not in kwargs and "exc" not in kwargs and "side_effect" not in kwargs:
        kwargs["json"] = {"detections": [["failure", score, [0, 0, 1, 1]]]}
    aioclient_mock.get(f"{OBICO_HOST}/p/", **kwargs)


def calls_to(aioclient_mock: AiohttpClientMocker, prefix: str) -> list[Any]:
    """Return the mocked requests whose URL starts with ``prefix``."""
    return [call for call in aioclient_mock.mock_calls if str(call[1]).startswith(prefix)]


def obico_img(aioclient_mock: AiohttpClientMocker) -> list[str]:
    """Return the ``img`` parameter of every request made to Obico."""
    return [
        parse_qs(urlsplit(str(call[1])).query)["img"][0]
        for call in calls_to(aioclient_mock, f"{OBICO_HOST}/p/")
    ]
