"""Binary sensors for Elegoo spaghetti detection."""

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_INSTANCE_ID, DOMAIN, RUNTIME_DATA
from .entity import SpaghettiDetectorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up binary sensors."""
    runtime = hass.data[DOMAIN][RUNTIME_DATA][entry.entry_id]
    async_add_entities(
        [
            SpaghettiDetectedBinarySensor(entry, runtime),
            DetectionStalledBinarySensor(entry, runtime),
        ]
    )


class SpaghettiDetectedBinarySensor(SpaghettiDetectorEntity, BinarySensorEntity):
    """Spaghetti detected state."""

    _attr_name = "Spaghetti Detected"
    _attr_icon = "mdi:alert-octagram"

    def __init__(self, entry: ConfigEntry, runtime) -> None:
        super().__init__(entry, runtime, "spaghetti_detected")
        self.entity_id = (
            f"binary_sensor.{entry.data[CONF_INSTANCE_ID]}_spaghetti_detected"
        )

    @property
    def is_on(self) -> bool:
        """Return true if spaghetti was detected."""
        return self.runtime.detected

    @property
    def extra_state_attributes(self) -> dict:
        """Return debug attributes."""
        return {
            "confidence": self.runtime.confidence,
            "raw_score": self.runtime.raw_score,
            "warning": self.runtime.warning,
            "detections": self.runtime.detection_count,
            "last_error": self.runtime.last_error,
        }


class DetectionStalledBinarySensor(SpaghettiDetectorEntity, BinarySensorEntity):
    """On when detection should be running but no check has completed.

    The runtime re-evaluates this on every interval tick and status change;
    see SpaghettiDetectorRuntime._update_stalled.
    """

    _attr_translation_key = "detection_stalled"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: ConfigEntry, runtime) -> None:
        super().__init__(entry, runtime, "detection_stalled")
        self.entity_id = (
            f"binary_sensor.{entry.data[CONF_INSTANCE_ID]}_detection_stalled"
        )

    @property
    def is_on(self) -> bool:
        """Return true if detection is due but checks stopped completing."""
        return self.runtime.stalled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return when a check last completed and the allowed gap."""
        last = self.runtime.last_completed
        return {
            "last_completed": last.isoformat() if last is not None else None,
            "stalled_after": int(self.runtime.stall_after.total_seconds()),
        }
