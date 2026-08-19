"""Config flow for the IHC-style Room Thermostat integration.

One config entry per room. All wiring is picked via entity selectors --
no free-text entity IDs.

Core wiring (name, room sensor, floor sensor, heater switch) is fixed at
creation, in the "user" step, and stored in `entry.data`. Everything more
likely to change your mind about later -- window/door contact, house-mode
entity, outdoor "feels like" sensor, window delay -- lives in the options
flow (`entry.options`), reachable any time from the integration's
"Configure" button, so it can be added/changed on an existing room without
recreating it.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .const import (
    CONF_FLOOR_TEMP_SENSOR,
    CONF_HEATER_SWITCH,
    CONF_HOUSE_MODE_ENTITY,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_ROOM_TEMP_SENSOR,
    CONF_WINDOW_SENSOR,
    DEFAULT_WINDOW_DELAY_MINUTES,
    DOMAIN,
    OPTION_WINDOW_DELAY_MINUTES,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required("name"): TextSelector(),
        vol.Required(CONF_ROOM_TEMP_SENSOR): EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        vol.Optional(CONF_FLOOR_TEMP_SENSOR): EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="temperature")
        ),
        vol.Required(CONF_HEATER_SWITCH): EntitySelector(
            EntitySelectorConfig(domain=["switch", "input_boolean"])
        ),
    }
)


class IhcStyleThermostatConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            heater_switch = user_input[CONF_HEATER_SWITCH]
            await self.async_set_unique_id(heater_switch)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=user_input["name"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return IhcStyleThermostatOptionsFlow()


class IhcStyleThermostatOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        schema_dict: dict[Any, Any] = {}

        window_sensor_selector = EntitySelector(EntitySelectorConfig(domain="binary_sensor"))
        if options.get(CONF_WINDOW_SENSOR):
            schema_dict[
                vol.Optional(CONF_WINDOW_SENSOR, default=options[CONF_WINDOW_SENSOR])
            ] = window_sensor_selector
        else:
            schema_dict[vol.Optional(CONF_WINDOW_SENSOR)] = window_sensor_selector

        house_mode_selector = EntitySelector(
            EntitySelectorConfig(domain=["select", "input_select"])
        )
        if options.get(CONF_HOUSE_MODE_ENTITY):
            schema_dict[
                vol.Optional(CONF_HOUSE_MODE_ENTITY, default=options[CONF_HOUSE_MODE_ENTITY])
            ] = house_mode_selector
        else:
            schema_dict[vol.Optional(CONF_HOUSE_MODE_ENTITY)] = house_mode_selector

        outdoor_temp_selector = EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="temperature")
        )
        if options.get(CONF_OUTDOOR_TEMP_ENTITY):
            schema_dict[
                vol.Optional(CONF_OUTDOOR_TEMP_ENTITY, default=options[CONF_OUTDOOR_TEMP_ENTITY])
            ] = outdoor_temp_selector
        else:
            schema_dict[vol.Optional(CONF_OUTDOOR_TEMP_ENTITY)] = outdoor_temp_selector

        schema_dict[
            vol.Required(
                OPTION_WINDOW_DELAY_MINUTES,
                default=options.get(OPTION_WINDOW_DELAY_MINUTES, DEFAULT_WINDOW_DELAY_MINUTES),
            )
        ] = NumberSelector(NumberSelectorConfig(min=1, max=120, step=1, mode=NumberSelectorMode.BOX))

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_dict))
