"""Thermostat-dial (climate) entity for the IHC-style Room Thermostat.

Presents the same coordinator-driven state as the number/select/sensor
entities, but as a real HA climate entity for the round dial card. Only
the "occupied" setpoint and forced-off mode are editable from the dial --
everything else (frost/night/guest setpoints, hysteresis, pulse heating,
priority, ...) stays reachable via its dedicated entity.
"""

from __future__ import annotations

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODE_ALARM, MODE_OFF
from .coordinator import RoomHeatingCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RoomHeatingCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RoomClimate(coordinator, entry)])


class RoomClimate(ClimateEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    # Covers both the room (10-30) and floor (10-35) setpoint ranges so the
    # dial works regardless of which one "regulering" currently uses.
    _attr_min_temp = 10.0
    _attr_max_temp = 35.0
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: RoomHeatingCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_climate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title
        )
        self._attr_hvac_mode = HVACMode.HEAT
        self._attr_hvac_action = HVACAction.IDLE
        self._attr_current_temperature = None
        self._attr_target_temperature = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._coordinator.register_climate(self)

    def async_update_state(
        self,
        *,
        mode: str,
        heat_call: bool,
        primary_temp: float | None,
        primary_sp: float | None,
        room_temp: float | None,
        floor_temp: float | None,
    ) -> None:
        self._attr_current_temperature = primary_temp
        self._attr_target_temperature = primary_sp
        self._attr_hvac_mode = HVACMode.OFF if mode == MODE_OFF else HVACMode.HEAT
        if mode in (MODE_OFF, MODE_ALARM):
            self._attr_hvac_action = HVACAction.OFF
        else:
            self._attr_hvac_action = HVACAction.HEATING if heat_call else HVACAction.IDLE
        self._attr_extra_state_attributes = {
            "room_temperature": room_temp,
            "floor_temperature": floor_temp,
            "heating_mode": mode,
        }
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._coordinator.async_set_beboet_setpoint(temperature)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self._coordinator.async_set_forced_off(hvac_mode == HVACMode.OFF)

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
