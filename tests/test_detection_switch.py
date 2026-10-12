"""The Detection switch turns scheduled checks on and off."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import mock_restore_cache
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.elegoo_spaghetti_detection.const import DOMAIN

from .conftest import (
    OBICO_HOST,
    STATUS,
    SetupDetector,
    calls_to,
    mock_obico,
    runtime_of,
    tick,
)

SWITCH = "switch.test_detector_detection"
STATUS_SENSOR = "sensor.test_detector_status"
NO_FRAMES = {"frames_enabled": False}


async def _switch(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(
        "switch", service, {"entity_id": SWITCH}, blocking=True
    )


def _obico_calls(aioclient_mock: AiohttpClientMocker) -> int:
    return len(calls_to(aioclient_mock, f"{OBICO_HOST}/p/"))


async def test_switch_is_on_by_default(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """A new detector detects; the switch has a stable id and a name."""
    entry = await setup_detector()
    state = hass.states.get(SWITCH)
    assert state.state == "on"
    assert state.attributes["friendly_name"] == "Test detector Detection"
    assert state.attributes["icon"] == "mdi:eye-check"
    entity = er.async_get(hass).async_get(SWITCH)
    assert entity.unique_id == f"{entry.entry_id}_detection"
    assert entity.translation_key == "detection"
    assert runtime_of(hass, entry).enabled is True


async def test_switching_off_stops_scheduled_checks(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Off shows disabled at once and on every tick; no check runs."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, score=0.1)

    await _switch(hass, "turn_off")
    assert runtime.enabled is False
    assert runtime.status == "disabled"
    assert hass.states.get(STATUS_SENSOR).state == "disabled"
    assert hass.states.get(SWITCH).state == "off"
    assert hass.states.get(SWITCH).attributes["icon"] == "mdi:eye-off"

    next_run = runtime.next_run
    await tick(hass, freezer)
    await tick(hass, freezer)
    assert runtime.status == "disabled"
    assert _obico_calls(aioclient_mock) == 0
    assert runtime.next_run > next_run  # the schedule keeps moving


async def test_switching_on_resumes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """On clears disabled at once; the next tick checks again."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, score=0.1)
    await _switch(hass, "turn_off")
    await tick(hass, freezer)

    await _switch(hass, "turn_on")
    assert runtime.enabled is True
    assert runtime.status == "idle"
    assert hass.states.get(SWITCH).state == "on"

    await tick(hass, freezer)
    assert runtime.status == "clear"
    assert _obico_calls(aioclient_mock) == 1


async def test_switching_on_shows_a_gate_right_away(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """Switched on while not printing: the status says so without a tick."""
    entry = await setup_detector()
    await _switch(hass, "turn_off")
    hass.states.async_set(STATUS, "idle")
    await hass.async_block_till_done()
    assert runtime_of(hass, entry).status == "disabled"

    await _switch(hass, "turn_on")
    assert runtime_of(hass, entry).status == "waiting_for_print"


async def test_switching_on_keeps_a_newer_status(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """A manual check while off replaces disabled; switching on keeps it."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, score=0.9)
    await _switch(hass, "turn_off")

    await runtime.async_run_detection(manual=True)
    assert runtime.status == "detected"

    await _switch(hass, "turn_on")
    assert runtime.status == "detected"


async def test_manual_checks_run_while_switched_off(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """The Test button and forced service calls still check; unforced do not."""
    entry = await setup_detector(options=NO_FRAMES)
    mock_obico(aioclient_mock, score=0.1)
    await _switch(hass, "turn_off")

    response = await hass.services.async_call(
        DOMAIN,
        "run_detection",
        {"config_entry": entry.entry_id, "force": False},
        blocking=True,
        return_response=True,
    )
    assert response["status"] == "disabled"
    assert _obico_calls(aioclient_mock) == 0

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.test_detector_test_spaghetti_detection"},
        blocking=True,
    )
    assert runtime_of(hass, entry).status == "clear"
    assert _obico_calls(aioclient_mock) == 1


async def test_reset_while_switched_off_stays_disabled(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """Reset and a print start do not show idle while detection is off."""
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    await _switch(hass, "turn_off")

    runtime.reset()
    assert runtime.status == "disabled"

    hass.states.async_set(STATUS, "idle")
    hass.states.async_set(STATUS, "printing")
    await hass.async_block_till_done()
    assert runtime.status == "disabled"


@pytest.mark.parametrize(
    ("restored", "enabled", "status"),
    [("off", False, "disabled"), ("on", True, "idle"), ("unavailable", True, "idle")],
)
async def test_switch_restores_after_a_restart(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
    restored: str,
    enabled: bool,
    status: str,
) -> None:
    """Off survives a restart; anything else starts switched on."""
    mock_restore_cache(hass, [State(SWITCH, restored)])
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    assert runtime.enabled is enabled
    assert runtime.status == status
    assert hass.states.get(SWITCH).state == ("on" if enabled else "off")
    assert hass.states.get(STATUS_SENSOR).state == status

    mock_obico(aioclient_mock, score=0.1)
    await tick(hass, freezer)
    assert _obico_calls(aioclient_mock) == (1 if enabled else 0)


async def test_switch_stays_off_across_a_reload(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """Saving the options reloads the detector; detection stays off."""
    entry = await setup_detector()
    await _switch(hass, "turn_off")

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert runtime_of(hass, entry).enabled is False
    assert hass.states.get(SWITCH).state == "off"
    assert hass.states.get(STATUS_SENSOR).state == "disabled"
