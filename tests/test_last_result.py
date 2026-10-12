"""The Last result sensor keeps the outcome of the last completed check."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import ATTR_OPTIONS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from .conftest import CAMERA, LIGHT, STATUS, SetupDetector, mock_obico, runtime_of, tick

LAST_RESULT = "sensor.test_detector_last_result"
NO_FRAMES = {"frames_enabled": False}


def _state(hass: HomeAssistant) -> tuple[str, dict[str, Any]]:
    state = hass.states.get(LAST_RESULT)
    return state.state, {key: state.attributes.get(key) for key in ("at", "confidence")}


async def test_none_before_the_first_check(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """An enum sensor with every outcome as an option, none at first."""
    entry = await setup_detector()
    state = hass.states.get(LAST_RESULT)
    assert state.state == "none"
    assert state.attributes["at"] is None
    assert state.attributes["confidence"] is None
    assert state.attributes["device_class"] == "enum"
    assert state.attributes[ATTR_OPTIONS] == [
        "none",
        "clear",
        "warning",
        "detected",
        "error",
    ]
    assert state.attributes["friendly_name"] == "Test detector Last result"
    entity = er.async_get(hass).async_get(LAST_RESULT)
    assert entity.unique_id == f"{entry.entry_id}_last_result"
    assert entity.translation_key == "last_result"


@pytest.mark.parametrize(
    ("score", "outcome"),
    [(0.1, "clear"), (0.35, "warning"), (0.8, "detected")],
)
@pytest.mark.parametrize("manual", [False, True])
async def test_a_scored_check_sets_the_outcome(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
    score: float,
    outcome: str,
    manual: bool,
) -> None:
    """Scheduled and manual checks both count, with time and confidence."""
    entry = await setup_detector(options=NO_FRAMES)
    mock_obico(aioclient_mock, score=score)
    await runtime_of(hass, entry).async_run_detection(manual=manual)
    await hass.async_block_till_done()

    assert _state(hass) == (
        outcome,
        {"at": dt_util.utcnow().isoformat(), "confidence": round(score * 100, 1)},
    )


async def test_a_failed_check_is_an_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An ML failure replaces the previous result with error, no confidence."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, score=0.4)
    await runtime.async_run_detection(manual=False)

    freezer.tick(30)
    aioclient_mock.clear_requests()
    mock_obico(aioclient_mock, exc=aiohttp.ClientConnectionError("down"))
    await runtime.async_run_detection(manual=False)
    await hass.async_block_till_done()

    assert _state(hass) == (
        "error",
        {"at": dt_util.utcnow().isoformat(), "confidence": None},
    )


async def test_no_camera_image_is_an_error(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """A check that cannot get an image also completes, as an error."""
    entry = await setup_detector(options=NO_FRAMES)
    hass.states.async_set(CAMERA, "idle", {})
    await runtime_of(hass, entry).async_run_detection(manual=True)
    await hass.async_block_till_done()
    assert _state(hass)[0] == "error"


async def test_unchanged_while_checking_or_busy(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A check in progress shows on Status only; Last result keeps the last one."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, score=0.35)
    await runtime.async_run_detection(manual=False)
    await hass.async_block_till_done()
    before = _state(hass)
    assert before[0] == "warning"

    release = asyncio.Event()

    async def slow_predict(image_url: str) -> dict:
        await release.wait()
        return {"detections": [["failure", 0.1, [0, 0, 1, 1]]]}

    with patch.object(runtime, "_async_predict", slow_predict):
        task = hass.async_create_task(runtime.async_run_detection(manual=False))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert hass.states.get("sensor.test_detector_status").state == "checking"
        assert _state(hass) == before

        await runtime.async_run_detection(manual=True)
        assert hass.states.get("sensor.test_detector_status").state == "busy"
        assert _state(hass) == before

        release.set()
        await task
    await hass.async_block_till_done()
    assert _state(hass) == (
        "clear",
        {"at": dt_util.utcnow().isoformat(), "confidence": 10.0},
    )


async def test_unchanged_by_skipped_ticks(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Not printing, printer unavailable, light off and switched off keep it."""
    entry = await setup_detector(
        options={**NO_FRAMES, "chamber_light": LIGHT, "require_light": True}
    )
    runtime = runtime_of(hass, entry)
    hass.states.async_set(LIGHT, "on")
    mock_obico(aioclient_mock, score=0.8)
    await tick(hass, freezer)
    before = _state(hass)
    assert before[0] == "detected"

    hass.states.async_set(LIGHT, "off")
    await tick(hass, freezer)
    assert runtime.status == "light_off"
    assert _state(hass) == before

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.test_detector_detection"},
        blocking=True,
    )
    await tick(hass, freezer)
    assert runtime.status == "disabled"
    assert _state(hass) == before

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.test_detector_detection"},
        blocking=True,
    )
    hass.states.async_set(STATUS, "idle")
    await tick(hass, freezer)
    assert runtime.status == "waiting_for_print"
    assert _state(hass) == before

    hass.states.async_set(STATUS, "unavailable")
    await tick(hass, freezer)
    assert runtime.status == "status_unavailable"
    assert _state(hass) == before


async def test_reset_and_a_new_print_clear_it(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """Reset (button, service) and a print start go back to none."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, score=0.8)

    await runtime.async_run_detection(manual=False)
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.test_detector_reset_detection_state"},
        blocking=True,
    )
    assert _state(hass) == ("none", {"at": None, "confidence": None})

    await runtime.async_run_detection(manual=False)
    await hass.services.async_call(
        "elegoo_spaghetti_detection",
        "reset_state",
        {"detector": "test_detector"},
        blocking=True,
    )
    assert _state(hass)[0] == "none"

    await runtime.async_run_detection(manual=False)
    hass.states.async_set(STATUS, "idle")
    hass.states.async_set(STATUS, "printing")
    await hass.async_block_till_done()
    assert _state(hass) == ("none", {"at": None, "confidence": None})
