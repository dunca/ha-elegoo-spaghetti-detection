"""Sensors for Elegoo spaghetti detection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_AT,
    ATTR_CONFIDENCE,
    CONF_INSTANCE_ID,
    DOMAIN,
    LAST_RESULT_OPTIONS,
    RUNTIME_DATA,
    STATUS_OPTIONS,
)
from .entity import SpaghettiDetectorEntity


@dataclass(frozen=True, kw_only=True)
class DetectorSensorDescription(SensorEntityDescription):
    """Detector sensor description."""

    value_fn: Any
    attributes_fn: Callable[[Any], dict[str, Any]] | None = None


def _percent(value: float | None) -> float | None:
    """Return a 0-1 score as a percentage."""
    return None if value is None else round(value * 100, 1)


def _iso(value: datetime | None) -> str | None:
    """Return a timestamp attribute."""
    return value.isoformat() if value is not None else None


def _last_result_attributes(runtime) -> dict[str, Any]:
    """Return when the last completed check finished, and its confidence."""
    return {
        ATTR_AT: _iso(runtime.last_outcome_at),
        ATTR_CONFIDENCE: _percent(runtime.last_outcome_confidence),
    }


SENSORS: tuple[DetectorSensorDescription, ...] = (
    DetectorSensorDescription(
        key="confidence",
        name="Confidence",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda runtime: round(runtime.confidence * 100, 1),
    ),
    DetectorSensorDescription(
        key="raw_score",
        name="Raw Score",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda runtime: round(runtime.raw_score, 4),
    ),
    DetectorSensorDescription(
        key="detections",
        name="Detection Count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda runtime: runtime.detection_count,
    ),
    DetectorSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=STATUS_OPTIONS,
        value_fn=lambda runtime: runtime.status,
    ),
    DetectorSensorDescription(
        key="last_result",
        translation_key="last_result",
        icon="mdi:clipboard-check-outline",
        device_class=SensorDeviceClass.ENUM,
        options=LAST_RESULT_OPTIONS,
        value_fn=lambda runtime: runtime.last_outcome,
        attributes_fn=_last_result_attributes,
    ),
    DetectorSensorDescription(
        key="last_error",
        name="Last Error",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda runtime: runtime.last_error or "none",
    ),
    DetectorSensorDescription(
        key="last_run",
        name="Last Run",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda runtime: runtime.last_run,
    ),
    DetectorSensorDescription(
        key="next_run",
        name="Next Run",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda runtime: runtime.next_run,
    ),
)

FRAMES_SENSOR = DetectorSensorDescription(
    key="spaghetti_frames",
    name="Spaghetti Frames",
    icon="mdi:filmstrip-box-multiple",
    value_fn=lambda runtime: len(runtime.frames.prints),
)

PEAK_SENSOR = DetectorSensorDescription(
    key="peak_confidence",
    translation_key="peak_confidence",
    icon="mdi:chart-bell-curve-cumulative",
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    value_fn=lambda runtime: _percent(runtime.peak_confidence),
    attributes_fn=lambda runtime: {ATTR_AT: _iso(runtime.peak_confidence_at)},
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up sensors."""
    runtime = hass.data[DOMAIN][RUNTIME_DATA][entry.entry_id]
    entities: list[DetectorSensor] = [
        DetectorSensor(entry, runtime, description) for description in SENSORS
    ]
    entities.append(FramesSensor(entry, runtime, FRAMES_SENSOR))
    entities.append(PeakConfidenceSensor(entry, runtime, PEAK_SENSOR))
    async_add_entities(entities)


class DetectorSensor(SpaghettiDetectorEntity, SensorEntity):
    """Detector sensor."""

    entity_description: DetectorSensorDescription

    def __init__(
        self,
        entry: ConfigEntry,
        runtime,
        description: DetectorSensorDescription,
    ) -> None:
        super().__init__(entry, runtime, description.key)
        self.entity_description = description
        self.entity_id = f"sensor.{entry.data[CONF_INSTANCE_ID]}_{description.key}"

    @property
    def native_value(self):
        """Return the current sensor value."""
        return self.entity_description.value_fn(self.runtime)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the attributes of sensors that have them."""
        if self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(self.runtime)


class FramesSensor(DetectorSensor):
    """Number of prints with kept frames, and how to view them."""

    # The attributes carry the viewer's access token: keep them out of the
    # recorder database. They are rebuilt from memory on every state write.
    _unrecorded_attributes = frozenset({"prints", "viewer_url"})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the stored prints, newest first, and the viewer link."""
        return self.runtime.frames.attributes()


class PeakConfidenceSensor(DetectorSensor, RestoreSensor):
    """Highest confidence of a scheduled check during the current print.

    The runtime starts without a peak; the last one is restored so a Home
    Assistant restart in the middle of a print keeps it.
    """

    async def async_added_to_hass(self) -> None:
        """Restore the peak and when it was seen."""
        await super().async_added_to_hass()
        if self.runtime.peak_confidence is not None:
            return  # a print started before this entity was added
        last_data = await self.async_get_last_sensor_data()
        last_state = await self.async_get_last_state()
        if last_data is None or last_state is None:
            return
        try:
            percent = float(last_data.native_value)
        except (TypeError, ValueError):
            return  # unknown: no print seen yet
        self.runtime.peak_confidence = min(1.0, max(0.0, percent / 100))
        at = last_state.attributes.get(ATTR_AT)
        self.runtime.peak_confidence_at = (
            dt_util.parse_datetime(at) if isinstance(at, str) else None
        )
