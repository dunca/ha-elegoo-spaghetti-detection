"""The frame options in the options flow."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.elegoo_spaghetti_detection import frames

from .conftest import SetupDetector, runtime_of


async def test_options_flow_sets_the_frames_options(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """The options flow stores the six new options; cleared entities stay off."""
    entry = await setup_detector(job_id_entity="sensor.from_setup")
    aioclient_mock.get("http://obico.example.test:3333/hc/", text="ok")
    aioclient_mock.get("http://obico.example.test:3333/debug/image", text="ok")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    keys = {str(key): key for key in schema}
    for key in (
        "frames_enabled",
        "job_id_entity",
        "job_name_entity",
        "frames_keep_prints",
        "frames_max_per_print",
        "frames_max_mb",
    ):
        assert key in keys
    assert keys["frames_enabled"].default() is True
    assert keys["frames_keep_prints"].default() == 10
    assert keys["frames_max_per_print"].default() == 1500
    assert keys["frames_max_mb"].default() == 1500
    assert keys["job_id_entity"].description == {"suggested_value": "sensor.from_setup"}

    values = {
        str(key): key.default()
        for key in schema
        if callable(getattr(key, "default", None)) and key.default() is not None
    }
    # job_id_entity is left out: cleared in the form
    values.update(
        {
            "frames_enabled": False,
            "frames_keep_prints": 5,
            "frames_max_per_print": 300,
            "frames_max_mb": 100,
            "job_name_entity": "sensor.file",
        }
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input=values
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["frames_enabled"] is False
    assert entry.options["job_id_entity"] is None
    assert entry.options["job_name_entity"] == "sensor.file"

    runtime = runtime_of(hass, entry)  # reloaded with the new options
    assert runtime.frames.enabled is False
    assert runtime.frames.limits == frames.Limits(
        keep_prints=5, max_frames=300, max_bytes=100 * 1024 * 1024
    )
    assert runtime._current_job()[0] == ""
