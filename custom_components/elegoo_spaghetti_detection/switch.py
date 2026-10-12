"""Switch for Elegoo spaghetti detection."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_INSTANCE_ID, DOMAIN, RUNTIME_DATA
from .entity import SpaghettiDetectorEntity
from .runtime import SpaghettiDetectorRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up the detection switch."""
    runtime = hass.data[DOMAIN][RUNTIME_DATA][entry.entry_id]
    async_add_entities([DetectionSwitch(entry, runtime)])


class DetectionSwitch(SpaghettiDetectorEntity, SwitchEntity, RestoreEntity):
    """Turn scheduled detection on or off; manual checks still run.

    The last state is restored, so detection stays off across restarts and
    option changes until it is switched back on.
    """

    _attr_translation_key = "detection"

    def __init__(
        self,
        entry: ConfigEntry,
        runtime: SpaghettiDetectorRuntime,
    ) -> None:
        super().__init__(entry, runtime, "detection")
        self.entity_id = f"switch.{entry.data[CONF_INSTANCE_ID]}_detection"

    async def async_added_to_hass(self) -> None:
        """Restore the last state, then follow the runtime."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in (STATE_ON, STATE_OFF):
            enabled = last_state.state == STATE_ON
            if enabled != self.runtime.enabled:
                self.runtime.async_set_enabled(enabled)

    @property
    def is_on(self) -> bool:
        """Return true while scheduled detection is on."""
        return self.runtime.enabled

    @property
    def icon(self) -> str:
        """Return an icon that shows whether detection is on."""
        return "mdi:eye-check" if self.runtime.enabled else "mdi:eye-off"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch scheduled detection on."""
        self.runtime.async_set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch scheduled detection off."""
        self.runtime.async_set_enabled(False)
