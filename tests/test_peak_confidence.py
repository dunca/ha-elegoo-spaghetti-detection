"""The Peak confidence sensor: the highest score of the current print."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from .conftest import STATUS, SetupDetector, mock_obico, runtime_of

PEAK = "sensor.test_detector_peak_confidence"
NO_FRAMES = {"frames_enabled": False}


def _peak(hass: HomeAssistant) -> tuple[str, str | None]:
    state = hass.states.get(PEAK)
    return state.state, state.attributes.get("at")


async def _start_print(hass: HomeAssistant) -> None:
    hass.states.async_set(STATUS, "idle")
    hass.states.async_set(STATUS, "printing")
    await hass.async_block_till_done()


async def _check(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    runtime,
    score: float,
    *,
    manual: bool = False,
) -> None:
    aioclient_mock.clear_requests()
    mock_obico(aioclient_mock, score=score)
    await runtime.async_run_detection(manual=manual)
    await hass.async_block_till_done()


async def test_unknown_until_the_first_print(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """A new install has no peak, and checks without a print start keep it so."""
    entry = await setup_detector(options=NO_FRAMES)
    state = hass.states.get(PEAK)
    assert state.state == "unknown"
    assert state.attributes["at"] is None
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["state_class"] == "measurement"
    assert state.attributes["friendly_name"] == "Test detector Peak confidence"
    entity = er.async_get(hass).async_get(PEAK)
    assert entity.unique_id == f"{entry.entry_id}_peak_confidence"

    await _check(hass, aioclient_mock, runtime_of(hass, entry), 0.6)
    assert _peak(hass) == ("unknown", None)


async def test_scheduled_checks_raise_it(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A print start sets 0; higher scores raise it, lower ones do not."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    await _start_print(hass)
    assert _peak(hass) == ("0.0", None)

    await _check(hass, aioclient_mock, runtime, 0.0)
    first = dt_util.utcnow().isoformat()
    assert _peak(hass) == ("0.0", first)  # a seen 0 has a time

    freezer.tick(60)
    await _check(hass, aioclient_mock, runtime, 0.25)
    seen = dt_util.utcnow().isoformat()
    assert _peak(hass) == ("25.0", seen)

    freezer.tick(60)
    await _check(hass, aioclient_mock, runtime, 0.1)
    assert _peak(hass) == ("25.0", seen)

    freezer.tick(60)
    await _check(hass, aioclient_mock, runtime, 0.25)  # equal: first sighting stays
    assert _peak(hass) == ("25.0", seen)

    freezer.tick(60)
    await _check(hass, aioclient_mock, runtime, 0.62)
    assert _peak(hass) == ("62.0", dt_util.utcnow().isoformat())


async def test_manual_and_failed_checks_do_not_count(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """The Test button and errors leave the peak alone."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    await _start_print(hass)
    await _check(hass, aioclient_mock, runtime, 0.2)
    before = _peak(hass)

    await _check(hass, aioclient_mock, runtime, 0.9, manual=True)
    assert runtime.status == "detected"
    assert _peak(hass) == before

    aioclient_mock.clear_requests()
    mock_obico(aioclient_mock, exc=aiohttp.ClientConnectionError("down"))
    await runtime.async_run_detection(manual=False)
    await hass.async_block_till_done()
    assert runtime.status == "error"
    assert _peak(hass) == before


async def test_a_new_print_starts_from_zero(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """The peak belongs to one print; the reset button does not clear it."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    await _start_print(hass)
    await _check(hass, aioclient_mock, runtime, 0.7)
    assert _peak(hass)[0] == "70.0"

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.test_detector_reset_detection_state"},
        blocking=True,
    )
    assert _peak(hass)[0] == "70.0"

    hass.states.async_set(STATUS, "complete")
    await hass.async_block_till_done()
    assert _peak(hass)[0] == "70.0"  # kept after the print ends

    hass.states.async_set(STATUS, "printing")
    await hass.async_block_till_done()
    assert _peak(hass) == ("0.0", None)


@pytest.mark.parametrize("before", [None, "unknown", "unavailable"])
async def test_a_reconnect_is_not_a_new_print(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    before: str | None,
) -> None:
    """Printing again after no state or unavailable keeps the peak."""
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    await _start_print(hass)
    await _check(hass, aioclient_mock, runtime, 0.4)

    if before is None:
        hass.states.async_remove(STATUS)
    else:
        hass.states.async_set(STATUS, before)
    hass.states.async_set(STATUS, "printing")
    await hass.async_block_till_done()
    assert _peak(hass)[0] == "40.0"


async def test_the_guard_sensor_also_starts_a_print(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """The inferred current_status sensor going active resets it, like reset()."""
    guard = "sensor.test_printer_current_status"
    hass.states.async_set(guard, "printing")
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    await _start_print(hass)
    await _check(hass, aioclient_mock, runtime, 0.4)

    hass.states.async_set(guard, "idle")
    hass.states.async_set(guard, "printing")
    await hass.async_block_till_done()
    assert _peak(hass) == ("0.0", None)


async def test_restored_after_a_restart(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The peak and its time survive a restart in the middle of a print."""
    seen = dt_util.utcnow() - timedelta(minutes=5)
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(PEAK, "42.5", {"at": seen.isoformat()}),
                {"native_value": 42.5, "native_unit_of_measurement": "%"},
            )
        ],
    )
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    assert runtime.peak_confidence == pytest.approx(0.425)
    assert runtime.peak_confidence_at == seen
    assert _peak(hass) == ("42.5", seen.isoformat())

    # the printer integration coming up after the restart is not a new print
    hass.states.async_remove(STATUS)
    hass.states.async_set(STATUS, "printing")
    await hass.async_block_till_done()
    assert _peak(hass) == ("42.5", seen.isoformat())

    await _check(hass, aioclient_mock, runtime, 0.3)
    assert _peak(hass) == ("42.5", seen.isoformat())
    await _check(hass, aioclient_mock, runtime, 0.5)
    assert _peak(hass) == ("50.0", dt_util.utcnow().isoformat())


@pytest.mark.parametrize(
    ("state", "extra"),
    [
        ("unknown", {"native_value": None, "native_unit_of_measurement": "%"}),
        ("unavailable", {"native_value": None, "native_unit_of_measurement": "%"}),
        ("0.0", {"native_value": "garbage", "native_unit_of_measurement": "%"}),
    ],
)
async def test_restored_unknown_stays_unknown(
    hass: HomeAssistant,
    setup_detector: SetupDetector,
    state: str,
    extra: dict,
) -> None:
    """Without a stored number there is no peak to restore."""
    mock_restore_cache_with_extra_data(hass, [(State(PEAK, state), extra)])
    entry = await setup_detector(options=NO_FRAMES)
    assert runtime_of(hass, entry).peak_confidence is None
    assert _peak(hass) == ("unknown", None)


async def test_restored_without_a_time(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """A stored 0 from a print start, before any check, has no time."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(PEAK, "0.0", {"at": None}),
                {"native_value": 0.0, "native_unit_of_measurement": "%"},
            )
        ],
    )
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    assert runtime.peak_confidence == 0.0
    assert runtime.peak_confidence_at is None
    assert _peak(hass) == ("0.0", None)


async def test_kept_across_a_reload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """Saving the options reloads the detector without losing the peak."""
    entry = await setup_detector(options=NO_FRAMES)
    await _start_print(hass)
    await _check(hass, aioclient_mock, runtime_of(hass, entry), 0.33)
    before = _peak(hass)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert runtime_of(hass, entry).peak_confidence == pytest.approx(0.33)
    assert _peak(hass) == before


async def test_a_print_start_before_the_entity_wins(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """If a print started before the sensor was added, the old peak is not restored."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(PEAK, "42.5"),
                {"native_value": 42.5, "native_unit_of_measurement": "%"},
            )
        ],
    )
    entry = await setup_detector(options=NO_FRAMES)
    runtime = runtime_of(hass, entry)
    sensor = hass.data["entity_components"]["sensor"].get_entity(PEAK)
    runtime.peak_confidence = 0.0
    await sensor.async_added_to_hass()
    assert runtime.peak_confidence == 0.0
