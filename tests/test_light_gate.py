"""The Only check while the chamber light is on option."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.elegoo_spaghetti_detection.config_flow import _schema

from .conftest import (
    LIGHT,
    OBICO_HOST,
    STATUS,
    SetupDetector,
    calls_to,
    mock_obico,
    runtime_of,
    tick,
)

STATUS_SENSOR = "sensor.test_detector_status"
NO_FRAMES = {"frames_enabled": False}


def _gated(**options) -> dict:
    """Return detector options with the light gate on."""
    return {**NO_FRAMES, "chamber_light": LIGHT, "require_light": True, **options}


def _mock_light(hass: HomeAssistant) -> tuple[list[ServiceCall], list[ServiceCall]]:
    """Register light services that switch the test light and record calls."""
    calls: dict[str, list[ServiceCall]] = {"turn_on": [], "turn_off": []}

    for service, state in (("turn_on", "on"), ("turn_off", "off")):

        async def handle(call: ServiceCall, service=service, state=state) -> None:
            calls[service].append(call)
            hass.states.async_set(call.data["entity_id"], state)

        hass.services.async_register("light", service, handle)
    return calls["turn_on"], calls["turn_off"]


def _obico_calls(aioclient_mock: AiohttpClientMocker) -> int:
    return len(calls_to(aioclient_mock, f"{OBICO_HOST}/p/"))


@pytest.mark.parametrize("light_mode", ["off", "leave_on", "restore"])
async def test_light_off_skips_the_check_and_leaves_the_light_off(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
    light_mode: str,
) -> None:
    """The gate comes before light control: a gated-off tick never turns it on."""
    turn_on, turn_off = _mock_light(hass)
    hass.states.async_set(LIGHT, "off")
    entry = await setup_detector(options=_gated(light_control_mode=light_mode))
    mock_obico(aioclient_mock)

    await tick(hass, freezer)

    runtime = runtime_of(hass, entry)
    assert runtime.status == "light_off"
    assert hass.states.get(STATUS_SENSOR).state == "light_off"
    assert _obico_calls(aioclient_mock) == 0
    assert turn_on == []
    assert turn_off == []
    assert runtime.last_run is None


async def test_light_on_runs_the_check(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """With the light on, scheduled checks run as usual."""
    hass.states.async_set(LIGHT, "on")
    entry = await setup_detector(options=_gated())
    mock_obico(aioclient_mock, score=0.1)

    await tick(hass, freezer)

    assert runtime_of(hass, entry).status == "clear"
    assert _obico_calls(aioclient_mock) == 1


async def test_the_gate_follows_the_light(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Off, on, off again: checks stop and resume with the light."""
    hass.states.async_set(LIGHT, "off")
    entry = await setup_detector(options=_gated())
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, score=0.1)

    await tick(hass, freezer)
    assert runtime.status == "light_off"
    hass.states.async_set(LIGHT, "on")
    await tick(hass, freezer)
    assert runtime.status == "clear"
    hass.states.async_set(LIGHT, "off")
    await tick(hass, freezer)
    assert runtime.status == "light_off"
    assert _obico_calls(aioclient_mock) == 1


@pytest.mark.parametrize("light_state", [None, "unavailable", "unknown"])
async def test_a_missing_or_unknown_light_counts_as_off(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
    light_state: str | None,
) -> None:
    """Only a light that is really on lets a scheduled check through."""
    if light_state is not None:
        hass.states.async_set(LIGHT, light_state)
    entry = await setup_detector(options=_gated())
    mock_obico(aioclient_mock)

    await tick(hass, freezer)

    assert runtime_of(hass, entry).status == "light_off"
    assert _obico_calls(aioclient_mock) == 0


async def test_no_effect_without_a_chamber_light(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The option needs a chamber light; without one checks run."""
    entry = await setup_detector(options={**NO_FRAMES, "require_light": True})
    runtime = runtime_of(hass, entry)
    assert runtime.light_gate_entity is None
    mock_obico(aioclient_mock, score=0.1)

    await tick(hass, freezer)

    assert runtime.status == "clear"
    assert _obico_calls(aioclient_mock) == 1


async def test_option_off_keeps_the_light_control(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Without the option an off light is still switched on for the check."""
    turn_on, turn_off = _mock_light(hass)
    hass.states.async_set(LIGHT, "off")
    entry = await setup_detector(
        options={
            **NO_FRAMES,
            "chamber_light": LIGHT,
            "light_control_mode": "restore",
            "light_settle_seconds": 0,
        }
    )
    runtime = runtime_of(hass, entry)
    assert runtime.light_gate_entity is None
    mock_obico(aioclient_mock, score=0.1)

    await tick(hass, freezer)

    assert runtime.status == "clear"
    assert [call.data["entity_id"] for call in turn_on] == [LIGHT]
    assert [call.data["entity_id"] for call in turn_off] == [LIGHT]


async def test_manual_checks_ignore_the_gate(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """The Test button runs with the light off, with the usual light control."""
    turn_on, turn_off = _mock_light(hass)
    hass.states.async_set(LIGHT, "off")
    entry = await setup_detector(
        options=_gated(light_control_mode="restore", light_settle_seconds=0)
    )
    mock_obico(aioclient_mock, score=0.1)

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.test_detector_test_spaghetti_detection"},
        blocking=True,
    )

    assert runtime_of(hass, entry).status == "clear"
    assert _obico_calls(aioclient_mock) == 1
    assert len(turn_on) == 1
    assert len(turn_off) == 1


async def test_unforced_service_call_respects_the_gate(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """run_detection with force: false is a scheduled check; force: true is not."""
    hass.states.async_set(LIGHT, "off")
    entry = await setup_detector(options=_gated())
    mock_obico(aioclient_mock, score=0.1)

    response = await hass.services.async_call(
        "elegoo_spaghetti_detection",
        "run_detection",
        {"detector": "test_detector", "force": False},
        blocking=True,
        return_response=True,
    )
    assert response["status"] == "light_off"
    assert _obico_calls(aioclient_mock) == 0

    response = await hass.services.async_call(
        "elegoo_spaghetti_detection",
        "run_detection",
        {"config_entry": entry.entry_id, "force": True},
        blocking=True,
        return_response=True,
    )
    assert response["status"] == "clear"
    assert _obico_calls(aioclient_mock) == 1


@pytest.mark.parametrize(
    ("printer", "status"),
    [("idle", "waiting_for_print"), ("unavailable", "status_unavailable")],
)
async def test_the_print_status_is_checked_first(
    hass: HomeAssistant,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
    printer: str,
    status: str,
) -> None:
    """Not printing and light off reads as not printing."""
    hass.states.async_set(LIGHT, "off")
    entry = await setup_detector(options=_gated())
    hass.states.async_set(STATUS, printer)

    await tick(hass, freezer)

    assert runtime_of(hass, entry).status == status


async def test_gate_without_a_print_status_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A camera-only detector that runs without printing still needs the light."""
    hass.states.async_set(LIGHT, "off")
    entry = await setup_detector(
        print_status_sensor=None, options=_gated(run_without_printing=True)
    )
    runtime = runtime_of(hass, entry)
    mock_obico(aioclient_mock, score=0.1)

    await tick(hass, freezer)
    assert runtime.status == "light_off"
    assert runtime.printer_state is None

    hass.states.async_set(LIGHT, "on")
    await tick(hass, freezer)
    assert runtime.status == "clear"


async def test_a_gated_check_never_switches_the_light_on(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """Light preparation itself refuses for scheduled checks under the gate."""
    turn_on, _ = _mock_light(hass)
    hass.states.async_set(LIGHT, "off")
    entry = await setup_detector(
        options=_gated(light_control_mode="leave_on", light_settle_seconds=0)
    )
    runtime = runtime_of(hass, entry)

    assert await runtime._async_prepare_light(manual=False) is None
    assert turn_on == []

    assert await runtime._async_prepare_light(manual=True) is None  # leave_on
    assert len(turn_on) == 1


async def test_options_flow_offers_the_option_after_the_light(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """Off by default, right after the chamber light; saving it turns the gate on."""
    entry = await setup_detector(options={"chamber_light": LIGHT})
    aioclient_mock.get(f"{OBICO_HOST}/hc/", text="ok")
    aioclient_mock.get(f"{OBICO_HOST}/debug/image", text="ok")

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    keys = [str(key) for key in result["data_schema"].schema]
    assert keys[keys.index("chamber_light") + 1] == "require_light"
    markers = {str(key): key for key in result["data_schema"].schema}
    assert markers["require_light"].default() is False

    values = {
        str(key): key.default()
        for key in result["data_schema"].schema
        if callable(getattr(key, "default", None)) and key.default() is not None
    }
    values["require_light"] = True
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=values
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["require_light"] is True
    assert runtime_of(hass, entry).light_gate_entity == LIGHT

    result = await hass.config_entries.options.async_init(entry.entry_id)
    markers = {str(key): key for key in result["data_schema"].schema}
    assert markers["require_light"].default() is True


def test_setup_form_has_the_option_too() -> None:
    """The setup form shares the schema: the option sits after the light."""
    keys = [str(key) for key in _schema({}, include_identity=True).schema]
    assert keys[keys.index("chamber_light") + 1] == "require_light"
