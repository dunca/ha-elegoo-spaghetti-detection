"""The Spaghetti Frames sensor and the delete_frames service."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import (
    entity_registry as er,
    service,
    template,
    translation,
)
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.elegoo_spaghetti_detection import frames
from custom_components.elegoo_spaghetti_detection.const import DOMAIN

from .conftest import CAMERA_URL, JPEG, SetupDetector, mock_obico, runtime_of

SENSOR = "sensor.test_detector_spaghetti_frames"


async def test_sensor_lists_the_prints(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """State is the number of prints; attributes say how to view each one."""
    entry = await setup_detector(
        options={
            "job_id_entity": "sensor.test_printer_task_id",
            "job_name_entity": "sensor.test_printer_file_name",
        }
    )
    state = hass.states.get(SENSOR)
    assert state is not None
    assert state.state == "0"
    assert state.attributes["prints"] == []

    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock, score=0.42)
    hass.states.async_set("sensor.test_printer_task_id", "job-1")
    hass.states.async_set("sensor.test_printer_file_name", "cube.gcode")
    await runtime_of(hass, entry).async_run_detection(manual=False)
    await hass.async_block_till_done()

    state = hass.states.get(SENSOR)
    assert state.state == "1"
    (row,) = state.attributes["prints"]
    assert set(row) == {
        "id",
        "job_id",
        "name",
        "start",
        "end",
        "frames",
        "max",
        "viewer_url",
    }
    assert row["id"] == row["job_id"] == "job-1"
    assert row["name"] == "cube.gcode"
    assert row["frames"] == 1
    assert row["max"] == 0.42
    assert row["start"] == row["end"]
    assert row["viewer_url"] == (
        f"/api/elegoo_spaghetti_detection/frames/{entry.entry_id}/viewer"
        f"?token={frames._TOKEN}&print=job-1"
    )
    assert state.attributes["viewer_url"] == (
        f"/api/elegoo_spaghetti_detection/frames/{entry.entry_id}/viewer"
        f"?token={frames._TOKEN}"
    )

    registry = er.async_get(hass)
    assert registry.async_get(SENSOR).unique_id == f"{entry.entry_id}_spaghetti_frames"


async def test_sensor_attributes_are_not_recorded(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """The access token never reaches the recorder database."""
    await setup_detector()
    component = hass.data["entity_components"]["sensor"]
    entity = component.get_entity(SENSOR)
    assert {"prints", "viewer_url"} <= entity._Entity__combined_unrecorded_attributes


async def test_prints_are_newest_first(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A dashboard finds the latest print first."""
    entry = await setup_detector(options={"job_id_entity": "sensor.job"})
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock)
    runtime = runtime_of(hass, entry)
    for job in ("job-a", "job-b", "job-c"):
        hass.states.async_set("sensor.job", job)
        await runtime.async_run_detection(manual=False)
        freezer.tick(60)
    await hass.async_block_till_done()
    state = hass.states.get(SENSOR)
    assert [row["id"] for row in state.attributes["prints"]] == [
        "job-c",
        "job-b",
        "job-a",
    ]


async def test_delete_frames_service(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """The service deletes one print, by detector or by config entry."""
    entry = await setup_detector(options={"job_id_entity": "sensor.job"})
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock)
    runtime = runtime_of(hass, entry)
    for job in ("job-a", "job-b"):
        hass.states.async_set("sensor.job", job)
        await runtime.async_run_detection(manual=False)
    folder = runtime.frames.root

    response = await hass.services.async_call(
        DOMAIN,
        "delete_frames",
        {"detector": "test_detector", "print_id": "job-a"},
        blocking=True,
        return_response=True,
    )
    assert response == {"print_id": "job-a", "deleted": True}
    assert not (folder / "job-a").exists()
    assert (folder / "job-b").is_dir()
    assert hass.states.get(SENSOR).state == "1"

    await hass.services.async_call(
        DOMAIN,
        "delete_frames",
        {"config_entry": entry.entry_id, "print_id": "job-b"},
        blocking=True,
    )
    assert runtime.frames.prints == {}
    assert hass.states.get(SENSOR).state == "0"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "delete_frames",
            {"detector": "test_detector", "print_id": "job-b"},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "delete_frames",
            {"detector": "test_detector", "print_id": "../job-b"},
            blocking=True,
        )
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "delete_frames",
            {"detector": "no_such_detector", "print_id": "job-b"},
            blocking=True,
        )


async def test_the_dashboard_lookup_by_job_id(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """The template in docs/dashboard.md finds a print's frames by job id."""
    entry = await setup_detector(options={"job_id_entity": "sensor.my_printer_task_id"})
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock, score=0.45)
    runtime = runtime_of(hass, entry)
    for job in ("job-a", "job-b"):
        hass.states.async_set("sensor.my_printer_task_id", job)
        await runtime.async_run_detection(manual=False)
    await hass.async_block_till_done()
    hass.states.async_set("sensor.my_printer_task_id", "job-a")

    lookup = template.Template(
        "{% set job = states('sensor.my_printer_task_id') %}"
        "{% set prints = state_attr('sensor.test_detector_spaghetti_frames', 'prints')"
        " or [] %}"
        "{% set match = prints | selectattr('job_id', 'eq', job) | list %}"
        "{% if match %}{{ match[0].viewer_url }} {{ match[0].frames }}"
        " {{ (match[0].max * 100) | round(0) }}{% else %}none{% endif %}",
        hass,
    )
    assert lookup.async_render() == (
        f"{runtime.frames.viewer_url('job-a')} 1 45"
    )
    hass.states.async_set("sensor.my_printer_task_id", "job-z")
    assert lookup.async_render() == "none"


async def test_delete_frames_is_described(
    hass: HomeAssistant, setup_detector: SetupDetector
) -> None:
    """services.yaml and the translations describe the new service."""
    await setup_detector()
    descriptions = await service.async_get_all_descriptions(hass)
    fields = descriptions[DOMAIN]["delete_frames"]["fields"]
    assert set(fields) == {"detector", "config_entry", "print_id"}
    assert fields["print_id"]["required"] is True
    strings = await translation.async_get_translations(
        hass, "en", "services", [DOMAIN]
    )
    assert strings[f"component.{DOMAIN}.services.delete_frames.name"] == "Delete frames"
