"""The Status sensor is an enum with translated labels."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.sensor import ATTR_OPTIONS
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, translation
import pytest

from custom_components.elegoo_spaghetti_detection.const import (
    DOMAIN,
    LAST_RESULT_OPTIONS,
    STATUS_OPTIONS,
)

from .conftest import STATUS, SetupDetector, runtime_of, tick

STATUS_SENSOR = "sensor.test_detector_status"
GUARD = "sensor.test_printer_current_status"

ENGLISH_STATUS = {
    "idle": "Idle",
    "waiting_for_print": "Standby – not printing",
    "status_unavailable": "Waiting for printer",
    "checking": "Checking frame…",
    "clear": "Watching – all clear",
    "warning": "Watching – possible issue",
    "detected": "Spaghetti detected",
    "error": "Error",
    "busy": "Busy",
    "light_off": "Off – chamber light is off",
    "disabled": "Off – detection switched off",
}


async def test_status_is_an_enum_with_the_same_entity_id(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """Same entity id and unique id as before, now with options."""
    entry = await setup_detector()
    state = hass.states.get(STATUS_SENSOR)
    assert state.state == "idle"
    assert state.attributes["device_class"] == "enum"
    assert state.attributes[ATTR_OPTIONS] == STATUS_OPTIONS
    assert set(STATUS_OPTIONS) == set(ENGLISH_STATUS)
    assert state.attributes["friendly_name"] == "Test detector Status"
    entity = er.async_get(hass).async_get(STATUS_SENSOR)
    assert entity.unique_id == f"{entry.entry_id}_status"
    assert entity.translation_key == "status"


async def test_english_labels(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """Home Assistant finds a label for every status and last result."""
    await setup_detector()
    strings = await translation.async_get_translations(
        hass, "en", "entity", [DOMAIN]
    )
    prefix = f"component.{DOMAIN}.entity.sensor"
    for value, label in ENGLISH_STATUS.items():
        assert strings[f"{prefix}.status.state.{value}"] == label
    for value in LAST_RESULT_OPTIONS:
        assert f"{prefix}.last_result.state.{value}" in strings
    assert strings[f"{prefix}.last_result.state.none"] == "No check yet"


@pytest.mark.parametrize("language", ["ar", "es", "hi", "tr", "zh-Hans"])
async def test_every_locale_labels_every_state(
    hass: HomeAssistant, setup_detector: SetupDetector, language: str
) -> None:
    """Each locale translates the states, not just copies the English."""
    await setup_detector()
    strings = await translation.async_get_translations(
        hass, language, "entity", [DOMAIN]
    )
    prefix = f"component.{DOMAIN}.entity.sensor"
    for value in STATUS_OPTIONS:
        assert strings[f"{prefix}.status.state.{value}"]
    for value in LAST_RESULT_OPTIONS:
        assert strings[f"{prefix}.last_result.state.{value}"]
    assert strings[f"{prefix}.status.state.disabled"] != ENGLISH_STATUS["disabled"]


@pytest.mark.parametrize(
    ("printer", "guard", "status", "printer_state"),
    [
        ("printing", None, None, "printing"),
        ("printing", "printing", None, f"printing; {GUARD}=printing"),
        ("printing", "idle", "waiting_for_print", f"printing; {GUARD}=idle"),
        ("printing", "unknown", "status_unavailable", f"printing; {GUARD}=unknown"),
        ("idle", "printing", "waiting_for_print", "idle"),
        ("unavailable", None, "status_unavailable", "unavailable"),
        ("Printing", None, None, "Printing"),
    ],
)
async def test_print_status_gate(
    hass: HomeAssistant,
    setup_detector: SetupDetector,
    printer: str,
    guard: str | None,
    status: str | None,
    printer_state: str,
) -> None:
    """The print status and the inferred current_status guard, as before."""
    if guard is not None:
        hass.states.async_set(GUARD, guard)
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    hass.states.async_set(STATUS, printer)
    await hass.async_block_till_done()
    runtime.status = "clear"

    assert runtime._should_run_scheduled() is (status is None)
    assert runtime.status == (status or "clear")
    assert runtime.printer_state == printer_state


async def test_an_inactive_guard_keeps_status_unavailable(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """Once unavailable, an idle guard does not switch to waiting_for_print."""
    hass.states.async_set(GUARD, "idle")
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    runtime.status = "status_unavailable"
    assert runtime._should_run_scheduled() is False
    assert runtime.status == "status_unavailable"


async def test_print_status_entity_missing(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """A configured print status entity that does not exist is unavailable."""
    entry = await setup_detector()
    runtime = runtime_of(hass, entry)
    hass.states.async_remove(STATUS)
    assert runtime._should_run_scheduled() is False
    assert runtime.status == "status_unavailable"
    assert runtime.printer_state is None


@pytest.mark.parametrize(("run_anyway", "expected"), [(False, False), (True, True)])
async def test_no_print_status_sensor(
    hass: HomeAssistant,
    setup_detector: SetupDetector,
    run_anyway: bool,
    expected: bool,
) -> None:
    """Without a print status sensor, run_without_printing decides."""
    entry = await setup_detector(
        print_status_sensor=None, options={"run_without_printing": run_anyway}
    )
    runtime = runtime_of(hass, entry)
    assert runtime._should_run_scheduled() is expected
    assert runtime.status == ("idle" if expected else "waiting_for_print")
    assert runtime.printer_state is None


async def test_status_sensor_follows_the_runtime(
    hass: HomeAssistant,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Every status written is a valid option of the enum."""
    entry = await setup_detector()
    hass.states.async_set(STATUS, "idle")
    await tick(hass, freezer)
    assert hass.states.get(STATUS_SENSOR).state == "waiting_for_print"
    hass.states.async_set(STATUS, "unavailable")
    await tick(hass, freezer)
    assert hass.states.get(STATUS_SENSOR).state == "status_unavailable"
    runtime_of(hass, entry).running = True
    hass.states.async_set(STATUS, "printing")
    await tick(hass, freezer)
    assert hass.states.get(STATUS_SENSOR).state == "busy"
