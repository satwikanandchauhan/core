"""Sensors flow for Withings."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import WithingsDataUpdateCoordinator
from .entity import WithingsEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor config entry."""
    coordinator: WithingsDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [WithingsBinarySensor(coordinator)]

    async_add_entities(entities)


class WithingsBinarySensor(WithingsEntity, BinarySensorEntity):
    """Implementation of a Withings sensor."""

    _attr_icon = "mdi:bed"
    _attr_translation_key = "in_bed"
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, coordinator: WithingsDataUpdateCoordinator) -> None:
        """Initialize binary sensor."""
        super().__init__(coordinator, "in_bed")

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        return self.coordinator.in_bed
