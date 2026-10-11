"""Browsing the frames in the Media panel."""

from __future__ import annotations

from homeassistant.components.media_player import BrowseError
from homeassistant.components.media_source import MediaSourceItem, Unresolvable
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.elegoo_spaghetti_detection import frames
from custom_components.elegoo_spaghetti_detection.const import DOMAIN
from custom_components.elegoo_spaghetti_detection.media_source import (
    SpaghettiFramesSource,
    model_name,
)

from .conftest import CAMERA_URL, JPEG, SetupDetector, mock_obico, runtime_of


async def test_media_source_lists_detectors_prints_and_frames(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    setup_detector: SetupDetector,
) -> None:
    """Media panel: detectors, their prints, then frames."""
    entry = await setup_detector(
        options={
            "job_id_entity": "sensor.job",
            "job_name_entity": "sensor.file",
        }
    )
    aioclient_mock.get(CAMERA_URL, content=JPEG)
    mock_obico(aioclient_mock, score=0.5)
    hass.states.async_set("sensor.job", "job-1")
    hass.states.async_set("sensor.file", "ECC2_0.4_Benchy+Boat_PLA_0.2_1h2m.gcode")
    runtime = runtime_of(hass, entry)
    await runtime.async_run_detection(manual=False)
    frame = runtime.last_frame.frame
    source = SpaghettiFramesSource(hass)

    def item(identifier: str) -> MediaSourceItem:
        return MediaSourceItem(hass, DOMAIN, identifier, None)

    root = await source.async_browse_media(item(""))
    assert [c.identifier for c in root.children] == [entry.entry_id]
    assert root.children[0].title == "Test detector"

    prints = await source.async_browse_media(item(entry.entry_id))
    assert [c.identifier for c in prints.children] == [f"{entry.entry_id}/job-1"]
    assert prints.children[0].title.startswith("Benchy Boat · ")
    assert prints.children[0].title.endswith("· 1 frames · max 50 %")

    listing = await source.async_browse_media(item(f"{entry.entry_id}/job-1"))
    (child,) = listing.children
    assert child.identifier == f"{entry.entry_id}/job-1/{frame}"
    assert child.media_content_type == "image/jpeg"
    assert child.can_play
    assert f"token={frames._TOKEN}" in child.thumbnail

    played = await source.async_resolve_media(item(child.identifier))
    # no token: Home Assistant signs a resolved URL itself
    assert played.url == (
        f"/api/elegoo_spaghetti_detection/frames/{entry.entry_id}/job-1/{frame}"
    )
    assert played.mime_type == "image/jpeg"

    with pytest.raises(BrowseError):
        await source.async_browse_media(item("no-such-entry"))
    with pytest.raises(BrowseError):
        await source.async_browse_media(item(f"{entry.entry_id}/job-9"))
    with pytest.raises(BrowseError):
        await source.async_browse_media(item(child.identifier))
    for bad in (
        f"{entry.entry_id}/job-1/123456_00000.jpg",
        f"{entry.entry_id}/job-1/../../secrets.yaml",
        f"{entry.entry_id}/job-1",
    ):
        with pytest.raises(Unresolvable):
            await source.async_resolve_media(item(bad))


def test_model_names() -> None:
    """Slicer file names are shortened to the model."""
    assert model_name("ECC2_0.4_Cube+Two_PLA_0.2_1h2m.gcode") == "Cube Two"
    assert model_name("plain.gcode") == "plain"
