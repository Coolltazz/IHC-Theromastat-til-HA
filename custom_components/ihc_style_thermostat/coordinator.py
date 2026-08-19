"""Core state machine for a single room, modeled on a Schneider Electric IHC
dual-sensor thermostat function block: room + floor regulation with
selectable priority, hysteresis, pulse (duty-cycle) heating in the
maintenance zone, frost/night/guest setback, and max-temperature safety.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    CONF_FLOOR_TEMP_SENSOR,
    CONF_HEATER_SWITCH,
    CONF_HOUSE_MODE_ENTITY,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_ROOM_TEMP_SENSOR,
    CONF_WINDOW_SENSOR,
    DEFAULT_WINDOW_DELAY_MINUTES,
    HOUSE_MODE_MAP,
    MODE_ALARM,
    MODE_FROST,
    MODE_GUEST,
    MODE_NIGHT,
    MODE_OCCUPIED,
    MODE_OFF,
    MODE_UNOCCUPIED,
    MODE_WINDOW_OPEN,
    OPTION_WINDOW_DELAY_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

UNAVAILABLE = (None, "unknown", "unavailable", "")


class RoomHeatingCoordinator:
    """Owns the heat-call decision for one room (one config entry)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry

        self.room_temp_sensor: str = entry.data[CONF_ROOM_TEMP_SENSOR]
        self.floor_temp_sensor: str | None = entry.data.get(CONF_FLOOR_TEMP_SENSOR)
        self.heater_switch: str = entry.data[CONF_HEATER_SWITCH]
        # window_sensor / house_mode_entity / outdoor_temp_entity live in
        # entry.options (editable any time via "Configure"), but fall back to
        # entry.data for rooms added before that move -- they were briefly
        # part of the initial "user" step.
        self.window_sensor: str | None = None
        self.house_mode_entity: str | None = None
        self.outdoor_temp_entity: str | None = None
        self.window_delay_minutes: float = DEFAULT_WINDOW_DELAY_MINUTES
        self._read_options()

        self._numbers: dict[str, Any] = {}
        self._selects: dict[str, Any] = {}
        self._sensors: dict[str, Any] = {}

        self._room_satisfied: bool | None = None
        self._floor_satisfied: bool | None = None

        self._pulse_phase: str | None = None  # None / "on" / "off"
        self._pulse_cancel = None
        # Weather-compensated + self-adjusting duty split for the current
        # cycle -- computed once when a cycle starts, reused for both its
        # phases (settings/weather changes take effect next cycle).
        self._cycle_on_minutes: float | None = None
        self._cycle_off_minutes: float | None = None
        self._current_duty_pct: float | None = None

        self._window_open_since = None
        self._window_cancel = None

        self._remove_listener = None

    def _read_options(self) -> None:
        options = self.entry.options
        data = self.entry.data
        self.window_sensor = options.get(CONF_WINDOW_SENSOR) or data.get(CONF_WINDOW_SENSOR)
        self.house_mode_entity = options.get(CONF_HOUSE_MODE_ENTITY) or data.get(
            CONF_HOUSE_MODE_ENTITY
        )
        self.outdoor_temp_entity = options.get(CONF_OUTDOOR_TEMP_ENTITY) or data.get(
            CONF_OUTDOOR_TEMP_ENTITY
        )
        self.window_delay_minutes = options.get(
            OPTION_WINDOW_DELAY_MINUTES, DEFAULT_WINDOW_DELAY_MINUTES
        )

    async def async_update_options(self) -> None:
        """Called when the user saves the options flow ("Configure"). The
        watched-entity set may have changed (window sensor / house mode
        entity added, removed, or swapped), so listeners are torn down and
        re-registered against the fresh values."""
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None
        self._read_options()
        await self.async_setup_listeners()
        await self.async_evaluate()

    # -- entity registration, called from each platform's async_added_to_hass --
    def register_number(self, key: str, entity: Any) -> None:
        self._numbers[key] = entity

    def register_select(self, key: str, entity: Any) -> None:
        self._selects[key] = entity

    def register_sensor(self, key: str, entity: Any) -> None:
        self._sensors[key] = entity

    def _number(self, key: str) -> float | None:
        entity = self._numbers.get(key)
        return entity.native_value if entity else None

    def _select(self, key: str) -> str | None:
        entity = self._selects.get(key)
        return entity.current_option if entity else None

    # -- external entities (not owned by this integration) --
    def _get_float(self, entity_id: str | None) -> float | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in UNAVAILABLE:
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _get_str(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        return state.state

    async def async_setup_listeners(self) -> None:
        entities = [self.room_temp_sensor]
        if self.floor_temp_sensor:
            entities.append(self.floor_temp_sensor)
        if self.house_mode_entity:
            entities.append(self.house_mode_entity)
        if self.window_sensor:
            entities.append(self.window_sensor)
        self._remove_listener = async_track_state_change_event(
            self.hass, entities, self._handle_external_change
        )

    def async_unload(self) -> None:
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None
        if self._pulse_cancel:
            self._pulse_cancel()
            self._pulse_cancel = None
        if self._window_cancel:
            self._window_cancel()
            self._window_cancel = None

    @callback
    def _handle_external_change(self, event: Event) -> None:
        if event.data["entity_id"] == self.window_sensor:
            self._on_window_change(event.data["new_state"])
        self.hass.async_create_task(self.async_evaluate())

    def _on_window_change(self, new_state) -> None:
        if self._window_cancel:
            self._window_cancel()
            self._window_cancel = None
        if new_state is not None and new_state.state == "on":
            self._window_cancel = async_call_later(
                self.hass, self.window_delay_minutes * 60, self._window_delay_elapsed
            )
        else:
            self._window_open_since = None

    @callback
    def _window_delay_elapsed(self, _now) -> None:
        self._window_open_since = _now
        self._window_cancel = None
        self.hass.async_create_task(self.async_evaluate())

    # -- pulse (duty-cycle) heating --
    def _cancel_pulse(self) -> None:
        if self._pulse_cancel:
            self._pulse_cancel()
            self._pulse_cancel = None
        self._pulse_phase = None
        self._cycle_on_minutes = None
        self._cycle_off_minutes = None

    def _compute_duty_percent(self) -> tuple[float, float, float]:
        """Weather-compensated ON share (%) of the pulse cycle, plus a
        slowly self-adjusting bias trimmed by how well past cycles actually
        held the temperature (see async_adjust_bias). Returns
        (duty, feedforward_duty, bias)."""
        outdoor = self._get_float(self.outdoor_temp_entity) if self.outdoor_temp_entity else None
        t_low = self._number("ude_temp_lav")
        t_high = self._number("ude_temp_hoej")
        duty_min = self._number("pulsvarme_duty_min")
        duty_max = self._number("pulsvarme_duty_max")
        if duty_min is None:
            duty_min = 15.0
        if duty_max is None:
            duty_max = 90.0

        if outdoor is None or t_low is None or t_high is None or t_high <= t_low:
            duty_ff = (duty_min + duty_max) / 2.0
        else:
            fraction = (t_high - outdoor) / (t_high - t_low)
            fraction = max(0.0, min(1.0, fraction))
            duty_ff = duty_min + fraction * (duty_max - duty_min)

        bias = self._number("pulsvarme_adaptiv_bias") or 0.0
        duty = max(duty_min, min(duty_max, duty_ff + bias))
        return duty, duty_ff, bias

    def _adjust_bias(self, delta: float) -> None:
        """Nudges the persisted adaptive bias (percentage points). Positive
        delta = pulse heating ran too cold last time, pull more ON time in.
        Negative delta = it held fine, can probably ease off slightly.

        Writes the entity's state directly (not via async_set_native_value,
        which itself triggers a fresh async_evaluate() -- calling that here
        would re-enter the evaluation we're already inside)."""
        entity = self._numbers.get("pulsvarme_adaptiv_bias")
        if entity is None:
            return
        bias = entity.native_value or 0.0
        bias = max(-30.0, min(30.0, bias + delta))
        entity._attr_native_value = round(bias, 1)  # noqa: SLF001 -- internal coordinator<->entity link
        if entity.hass is not None:
            entity.async_write_ha_state()

    def _schedule_pulse_timer(self) -> None:
        if self._pulse_cancel:
            self._pulse_cancel()
        minutes = self._cycle_on_minutes if self._pulse_phase == "on" else self._cycle_off_minutes
        self._pulse_cancel = async_call_later(self.hass, minutes * 60, self._pulse_timer_elapsed)

    @callback
    def _pulse_timer_elapsed(self, _now) -> None:
        self._pulse_cancel = None
        self.hass.async_create_task(self.async_evaluate(phase_elapsed=True))

    # -- mode / setpoint resolution (mirrors the AppDaemon implementation) --
    def _determine_mode(self, room_temp: float | None, floor_temp: float | None) -> str:
        if room_temp is None and floor_temp is None:
            return MODE_ALARM

        local_mode = self._select("lokal_tilstand")
        if local_mode == "Tvunget fra":
            return MODE_OFF
        if local_mode == "Tvunget frostsikring":
            return MODE_FROST

        house_mode = self._get_str(self.house_mode_entity) if self.house_mode_entity else "Beboet"

        if local_mode == "Tvunget nedsænkning" and house_mode == "Beboet":
            base_mode = MODE_NIGHT
        else:
            base_mode = HOUSE_MODE_MAP.get(house_mode, MODE_UNOCCUPIED)

        if (
            self.window_sensor
            and self._get_str(self.window_sensor) == "on"
            and self._window_open_since is not None
            and base_mode != MODE_OFF
        ):
            return MODE_WINDOW_OPEN

        return base_mode

    def _setpoints_for_mode(self, mode: str) -> tuple[float | None, float | None]:
        if mode in (MODE_OFF, MODE_ALARM):
            return None, None
        if mode == MODE_OCCUPIED:
            room_sp = self._number("setpunkt_rum_beboet")
            floor_sp = self._number("setpunkt_gulv_beboet")
        elif mode == MODE_UNOCCUPIED:
            room_sp = floor_sp = self._number("setpunkt_ubeboet")
        elif mode in (MODE_GUEST, MODE_NIGHT):
            room_sp = floor_sp = self._number("setpunkt_gaest_nat")
        else:  # MODE_FROST, MODE_WINDOW_OPEN
            room_sp = floor_sp = self._number("setpunkt_frost")

        max_room = self._number("max_temp_rum")
        max_floor = self._number("max_temp_gulv")
        if room_sp is not None and max_room is not None:
            room_sp = min(room_sp, max_room)
        if floor_sp is not None and max_floor is not None:
            floor_sp = min(floor_sp, max_floor)
        return room_sp, floor_sp

    @staticmethod
    def _hysteresis_satisfied(
        temp: float | None, setpoint: float | None, band: float, previous: bool | None
    ) -> bool:
        if temp is None or setpoint is None:
            return previous if previous is not None else True
        if temp >= setpoint + band:
            return True
        if temp <= setpoint - band:
            return False
        return previous if previous is not None else (temp >= setpoint)

    # -- main evaluation --
    async def async_evaluate(self, phase_elapsed: bool = False) -> None:
        room_temp = self._get_float(self.room_temp_sensor)
        floor_temp = self._get_float(self.floor_temp_sensor) if self.floor_temp_sensor else None

        mode = self._determine_mode(room_temp, floor_temp)
        room_sp, floor_sp = self._setpoints_for_mode(mode)
        regulation = self._select("regulering")
        priority = self._select("prioritet")
        band = self._number("hysterese") or 0.3

        heat_call = False
        status_extra = ""

        if mode in (MODE_OFF, MODE_ALARM):
            self._cancel_pulse()
            heat_call = False
        else:
            self._room_satisfied = self._hysteresis_satisfied(
                room_temp, room_sp, band, self._room_satisfied
            )
            self._floor_satisfied = self._hysteresis_satisfied(
                floor_temp, floor_sp, band, self._floor_satisfied
            )

            if regulation == "Gulv":
                target_satisfied = self._floor_satisfied
            elif regulation == "Rum":
                target_satisfied = self._room_satisfied
            else:  # "Rum og gulv"
                if priority == "Begge temperaturer opfyldt":
                    target_satisfied = self._room_satisfied and self._floor_satisfied
                else:
                    target_satisfied = self._room_satisfied or self._floor_satisfied

            # Pulse heating is a maintenance aid for holding steady right at
            # the setpoint -- it is not meant to keep cycling the relay when
            # the room has simply overshot by several degrees (e.g. sun
            # through a window). Require the relevant sensor(s) to still be
            # within one hysteresis band above their setpoint before we
            # consider pulsing at all; further above than that, there is no
            # need for any heat, pulsed or not.
            room_near_target = (
                room_temp is None or room_sp is None or room_temp <= room_sp + band
            )
            floor_near_target = (
                floor_temp is None or floor_sp is None or floor_temp <= floor_sp + band
            )
            if regulation == "Gulv":
                pulse_eligible = floor_near_target
            elif regulation == "Rum":
                pulse_eligible = room_near_target
            else:  # "Rum og gulv"
                pulse_eligible = room_near_target and floor_near_target

            max_room = self._number("max_temp_rum")
            max_floor = self._number("max_temp_gulv")
            max_blocked = (
                max_room is not None and room_temp is not None and room_temp >= max_room
            ) or (max_floor is not None and floor_temp is not None and floor_temp >= max_floor)

            if max_blocked:
                self._cancel_pulse()
                heat_call = False
                status_extra = " (Max temp nået)"
            elif not target_satisfied:
                if self._pulse_phase is not None:
                    # We had to drop out of pulse heating because the
                    # temperature fell through anyway -- duty was too low.
                    # React faster than we ease off (better too warm than
                    # too cold).
                    self._adjust_bias(3.0)
                self._cancel_pulse()
                heat_call = True
            elif not pulse_eligible:
                # Comfortably satisfied and then some -- well above setpoint,
                # no maintenance pulsing needed.
                self._cancel_pulse()
                heat_call = False
                status_extra = " (Langt over sætpunkt)"
            else:
                pulsvarme = self._select("pulsvarme")
                if pulsvarme != "Aktiveret":
                    self._cancel_pulse()
                    heat_call = False
                else:
                    if self._pulse_phase is None:
                        cycle_minutes = self._number("pulsvarme_cyklus_min") or 40
                        duty_pct, duty_ff, bias = self._compute_duty_percent()
                        self._current_duty_pct = duty_pct
                        self._cycle_on_minutes = max(1.0, cycle_minutes * duty_pct / 100.0)
                        self._cycle_off_minutes = max(1.0, cycle_minutes - self._cycle_on_minutes)
                        self._pulse_phase = "on"
                        self._schedule_pulse_timer()
                        _LOGGER.info(
                            "%s: new pulse cycle -- duty=%.0f%% (weather=%.0f%%, bias=%+.1f), "
                            "ON=%.1fmin OFF=%.1fmin",
                            self.entry.title,
                            duty_pct,
                            duty_ff,
                            bias,
                            self._cycle_on_minutes,
                            self._cycle_off_minutes,
                        )
                    elif phase_elapsed:
                        if self._pulse_phase == "off":
                            # A full cycle completed without a genuine heat
                            # call taking over -- held fine, ease off a bit.
                            self._adjust_bias(-1.0)
                        self._pulse_phase = "off" if self._pulse_phase == "on" else "on"
                        self._schedule_pulse_timer()
                    heat_call = self._pulse_phase == "on"
                    duty_txt = f", {self._current_duty_pct:.0f}% duty" if self._current_duty_pct else ""
                    status_extra = f" (Pulsvarme: {self._pulse_phase.upper()}{duty_txt})"

        # Compare against the heater switch's actual reported state rather
        # than an internally-cached "last sent" value. On HA startup the KNX
        # switch entity is often not yet available when the first evaluation
        # runs (its service call then fails silently), which used to leave
        # the relay stuck out of sync forever since we never retried. Reading
        # the live state instead makes this self-correcting.
        heater_state = self.hass.states.get(self.heater_switch)
        switch_is_on = heater_state is not None and heater_state.state == "on"
        if switch_is_on != heat_call:
            heater_domain = self.heater_switch.split(".", 1)[0]
            await self.hass.services.async_call(
                heater_domain,
                "turn_on" if heat_call else "turn_off",
                {"entity_id": self.heater_switch},
                blocking=False,
            )
            _LOGGER.info(
                "%s: heat_call -> %s (mode=%s, room=%s, floor=%s, room_sp=%s, floor_sp=%s)",
                self.entry.title,
                heat_call,
                mode,
                room_temp,
                floor_temp,
                room_sp,
                floor_sp,
            )

        status_sensor = self._sensors.get("varme_tilstand")
        if status_sensor:
            status_sensor.async_update_state(f"{mode}{status_extra}", heat_call=heat_call)
        for key, value in (("varme_setpunkt_rum", room_sp), ("varme_setpunkt_gulv", floor_sp)):
            sensor = self._sensors.get(key)
            if sensor and value is not None:
                sensor.async_update_state(value)
