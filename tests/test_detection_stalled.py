"""The Detection stalled binary sensor."""

from __future__ import annotations

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from .conftest import LIGHT, STATUS, SetupDetector, mock_obico, runtime_of, tick

STALLED = "binary_sensor.test_detector_detection_stalled"
NO_FRAMES = {"frames_enabled": False}


def _stalled(hass: HomeAssistant) -> bool:
    return hass.states.get(STALLED).state == "on"


async def _start_print(hass: HomeAssistant) -> None:
    hass.states.async_set(STATUS, "idle")
    hass.states.async_set(STATUS, "printing")
    await hass.async_block_till_done()


async def _switch(hass: HomeAssistant, service: str) -> None:
    await hass.services.async_call(
        "switch",
        service,
        {"entity_id": "switch.test_detector_detection"},
        blocking=True,
    )


async def _ticks(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, count: int
) -> None:
    for _ in range(count):
        await tick(hass, freezer)


async def test_the_entity(hass: HomeAssistant, setup_detector: SetupDetector) -> None:
    """A problem sensor, off at first, that says how long a gap may be."""
    entry = await setup_detector()
    state = hass.states.get(STALLED)
    assert state.state == "off"
    assert state.attributes["device_class"] == "problem"
    assert state.attributes["friendly_name"] == "Test detector Detection stalled"
    assert state.attributes["stalled_after"] == 90
    assert state.attributes["last_completed"] is None
    entity = er.async_get(hass).async_get(STALLED)
    assert entity.unique_id == f"{entry.entry_id}_detection_stalled"
    assert entity.translation_key == "detection_stalled"


async def test_off_while_checks_complete(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A print with a check every interval never stalls."""
    entry = await setup_detector(options=NO_FRAMES)
    mock_obico(aioclient_mock, score=0.1)
    await _start_print(hass)
    for _ in range(20):
        await tick(hass, freezer)
        assert not _stalled(hass)
    assert hass.states.get(STALLED).attributes["last_completed"] == (
        dt_util.utcnow().isoformat()
    )
    assert runtime_of(hass, entry).status == "clear"


async def test_on_when_checks_stop_completing(
    hass: HomeAssistant,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A hung check: on once 90 s pass after the print started, status untouched."""
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    await _start_print(hass)
    runtime.running = True  # a check that never returns

    await _ticks(hass, freezer, 9)  # 90 s: not yet
    assert not _stalled(hass)
    assert runtime.status == "busy"
    await tick(hass, freezer)  # 100 s
    assert _stalled(hass)
    assert runtime.status == "busy"

    # evaluating it never changes the status
    runtime.status = "clear"
    runtime._notify_listeners()
    assert runtime.status == "clear"
    assert _stalled(hass)


async def test_the_limit_is_three_intervals(
    hass: HomeAssistant,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """With a 60 s interval the limit is 180 s."""
    entry = await setup_detector(options={"detection_interval": 60})
    runtime = runtime_of(hass, entry)
    assert hass.states.get(STALLED).attributes["stalled_after"] == 180
    await _start_print(hass)
    runtime.running = True

    for _ in range(3):  # 60, 120, 180 s
        await tick(hass, freezer, 60)
        assert not _stalled(hass)
    await tick(hass, freezer, 60)
    assert _stalled(hass)


async def test_measured_from_the_last_completed_check(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A check that completes restarts the clock, also when it fails."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, exc=aiohttp.ClientConnectionError("down"))
    await _start_print(hass)

    await _ticks(hass, freezer, 15)  # errors every time: they complete
    assert runtime.status == "error"
    assert not _stalled(hass)

    runtime.running = True  # completed at 150 s, hung after
    await _ticks(hass, freezer, 9)  # 240 s
    assert not _stalled(hass)
    await tick(hass, freezer)
    assert _stalled(hass)

    runtime.running = False  # it recovers
    await tick(hass, freezer)
    assert runtime.status == "error"
    assert not _stalled(hass)


async def test_manual_checks_count_as_completed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Any completed check proves the pipeline works, the Test button too."""
    entry = await setup_detector(options={**NO_FRAMES, "detection_interval": 60})
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, score=0.1)
    await _start_print(hass)
    freezer.tick(150)
    await runtime.async_run_detection(manual=True)
    runtime.running = True  # scheduled checks hang from now on
    await tick(hass, freezer, 60)  # 210 s after the print started, 60 after the check
    assert not _stalled(hass)
    await tick(hass, freezer, 130)  # 190 s after the check
    assert _stalled(hass)


async def test_off_while_switched_off_and_no_flap_when_back_on(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Switching off clears it at once; switching on starts a new wait."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    await _start_print(hass)
    runtime.running = True
    await _ticks(hass, freezer, 10)
    assert _stalled(hass)

    await _switch(hass, "turn_off")
    assert not _stalled(hass)
    await _ticks(hass, freezer, 30)
    assert not _stalled(hass)

    await _switch(hass, "turn_on")
    assert not _stalled(hass)
    await _ticks(hass, freezer, 9)
    assert not _stalled(hass)
    await tick(hass, freezer)
    assert _stalled(hass)


async def test_off_when_the_print_stops(
    hass: HomeAssistant,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The print status change clears it without waiting for a tick."""
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    await _start_print(hass)
    runtime.running = True
    await _ticks(hass, freezer, 10)
    assert _stalled(hass)

    hass.states.async_set(STATUS, "complete")
    await hass.async_block_till_done()
    assert not _stalled(hass)
    assert runtime.status == "busy"  # the status waits for the next tick

    await _ticks(hass, freezer, 30)
    assert not _stalled(hass)
    assert runtime.status == "waiting_for_print"


async def test_no_flap_when_a_print_starts(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The last check was hours ago; a new print still starts a new wait."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, score=0.1)
    await tick(hass, freezer)
    assert runtime.last_completed is not None

    hass.states.async_set(STATUS, "complete")
    await tick(hass, freezer, 3 * 3600)
    hass.states.async_set(STATUS, "printing")
    await hass.async_block_till_done()
    assert not _stalled(hass)
    for _ in range(12):
        await tick(hass, freezer)
        assert not _stalled(hass)


async def test_unavailable_printer_means_not_due(
    hass: HomeAssistant,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """While the print status is unavailable there is nothing to stall."""
    entry = await setup_detector()
    runtime_of(hass, entry).running = True
    hass.states.async_set(STATUS, "unavailable")
    await _ticks(hass, freezer, 20)
    assert not _stalled(hass)


async def test_off_while_the_light_gate_holds_checks(
    hass: HomeAssistant,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Light off clears it at once; light on starts a new wait."""
    hass.states.async_set(LIGHT, "on")
    entry = await setup_detector(
        options={"chamber_light": LIGHT, "require_light": True}
    )
    runtime = runtime_of(hass, entry)
    await _start_print(hass)
    runtime.running = True
    await _ticks(hass, freezer, 10)
    assert _stalled(hass)

    hass.states.async_set(LIGHT, "off")
    await hass.async_block_till_done()
    assert not _stalled(hass)
    await _ticks(hass, freezer, 30)
    assert not _stalled(hass)
    assert runtime.status == "light_off"

    hass.states.async_set(LIGHT, "on")
    await hass.async_block_till_done()
    assert not _stalled(hass)
    await _ticks(hass, freezer, 9)
    assert not _stalled(hass)
    await tick(hass, freezer)
    assert _stalled(hass)


async def test_reset_does_not_look_like_a_stall(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Reset clears the result, not when a check last completed."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, score=0.1)
    await _start_print(hass)
    await _ticks(hass, freezer, 20)
    runtime.reset()
    assert runtime.last_completed is not None
    assert not _stalled(hass)


async def test_no_flap_after_a_restart_mid_print(
    hass: HomeAssistant,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Started while already printing: the wait starts at the first update."""
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    runtime.running = True
    await _ticks(hass, freezer, 10)  # due since the first tick, 10 s in
    assert not _stalled(hass)
    await tick(hass, freezer)
    assert _stalled(hass)


async def test_not_due_without_a_print_status_or_run_without_printing(
    hass: HomeAssistant,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A camera-only detector that does not run on a schedule never stalls."""
    entry = await setup_detector(print_status_sensor=None)
    runtime_of(hass, entry).running = True
    await _ticks(hass, freezer, 20)
    assert not _stalled(hass)
