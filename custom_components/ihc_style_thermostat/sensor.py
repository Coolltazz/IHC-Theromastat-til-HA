"""Status/telemetry sensors for the IHC-style Room Thermostat.

These mirror the IHC block's "Lokal driftstilstand" and "Aktuelle
setpunkt" outputs. Values are pushed by RoomHeatingCoordinator each time
it evaluates -- entities here never poll.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import RoomHeatingCoordinator

SENSOR_DEFS = {
    "varme_tilstand": {"icon": "mdi:thermostat", "device_class": None, "unit": None},
    "varme_setpunkt_rum": {
        "icon": "mdi:home-thermometer",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": "°C",
    },
    "varme_setpunkt_gulv": {
        "icon": "mdi:heating-coil",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": "°C",
    },
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RoomHeatingCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RoomStatusSensor(coordinator, entry, key) for key in SENSOR_DEFS
    )


class RoomStatusSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: RoomHeatingCoordinator, entry: ConfigEntry, key: str) -> None:
        self._coordinator = coordinator
        self._key = key
        defn = SENSOR_DEFS[key]
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_icon = defn["icon"]
        self._attr_device_class = defn["device_class"]
        self._attr_native_unit_of_measurement = defn["unit"]
        self._attr_native_value = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._coordinator.register_sensor(self._key, self)

    def async_update_state(self, value, **extra_attrs) -> None:
        self._attr_native_value = value
        if extra_attrs:
            self._attr_extra_state_attributes = extra_attrs
        if self.hass is not None:
            self.async_write_ha_state()
