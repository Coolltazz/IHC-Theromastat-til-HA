"""Mode-selection entities for the IHC-style Room Thermostat."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, SELECT_DEFS
from .coordinator import RoomHeatingCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: RoomHeatingCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        RoomSelect(coordinator, entry, key) for key in SELECT_DEFS
    )


class RoomSelect(RestoreEntity, SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: RoomHeatingCoordinator, entry: ConfigEntry, key: str) -> None:
        self._coordinator = coordinator
        self._key = key
        options, default, icon = SELECT_DEFS[key]
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_options = options
        self._attr_icon = icon
        self._attr_current_option = default
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self._attr_options:
            self._attr_current_option = last_state.state
        self._coordinator.register_select(self._key, self)

    async def async_select_option(self, option: str) -> None:
        self._attr_current_option = option
        self.async_write_ha_state()
        await self._coordinator.async_evaluate()
