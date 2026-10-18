"""Core state machine for a single room, modeled on a Schneider Electric IHC
dual-sensor thermostat function block: room + floor regulation with
selectable priority, hysteresis, pulse (duty-cycle) heating in the
maintenance zone, frost/night/guest setback, and max-temperature safety.
"""

from __future__ import annotations

import logging
import time
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
        self._climates: dict[str, Any] = {}

        self._room_satisfied: bool | None = None
        self._floor_satisfied: bool | None = None
        self._room_far_over: bool | None = None
        self._floor_far_over: bool | None = None
        # Last-observed reading of whichever sensor drives regulation, used
        # to detect "still rising" (residual heat working through a floor
        # slab after the relay closes) before starting a fresh pulse cycle.
        self._last_seen_temp: float | None = None
        # Tracks a residual-heat observation in progress: set when an ON
        # phase ends (baseline = temp at that moment), updated with the
        # highest reading seen while still rising, and blended into the
        # learned pulsvarme_eftervarme_graders once the rise stops.
        self._pending_overshoot_baseline: float | None = None
        self._pending_overshoot_peak: float | None = None
        # The driving sensor's own last_updated at the point we last confirmed
        # a genuinely fresh reading for this observation (set when baseline is
        # created, advanced each time a newer reading comes in). Finalizing is
        # gated on this moving forward -- see the finalize check for why.
        self._pending_overshoot_last_updated = None
        # Whether the *previous* async_evaluate() call was in the real
        # (hysteresis-triggered, "not target_satisfied") heat-call branch --
        # used to detect the moment a real cycle ends, which is the only
        # thing eftervarme learns from (see async_evaluate).
        self._real_heat_active: bool = False
        # time.monotonic() a real cycle last ended -- pulsvarme holds off
        # starting a fresh ON phase for pulsvarme_pause_efter_cyklus_min
        # minutes after this, so its own pulses can't creep in and skew the
        # residual-heat observation above while it's still settling.
        self._real_cycle_ended_at: float | None = None

        self._pulse_phase: str | None = None  # None / "on" / "off"
        # time.monotonic() when the current phase began -- enforces
        # pulsvarme_min_fase_min as a hard floor on how long a phase is
        # allowed to actually run, not just on the theoretical on/off split
        # computed at cycle start (see the eftervarme cutoff guard below).
        self._phase_started_at: float | None = None
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

    def register_climate(self, target: str, entity: Any) -> None:
        self._climates[target] = entity

    # -- setters used by the climate entities (each dial only edits its own
    # "occupied" setpoint and the forced-off local mode; everything else
    # stays reachable via the dedicated number/select entities) --
    async def async_set_beboet_setpoint(self, target: str, value: float) -> None:
        key = "setpunkt_gulv_beboet" if target == "gulv" else "setpunkt_rum_beboet"
        entity = self._numbers.get(key)
        if entity:
            await entity.async_set_native_value(value)

    async def async_set_forced_off(self, off: bool) -> None:
        entity = self._selects.get("lokal_tilstand")
        if entity is None:
            return
        current = entity.current_option
        if off:
            await entity.async_select_option("Tvunget fra")
        elif current == "Tvunget fra":
            await entity.async_select_option("Følger husets tilstand")

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

    def _get_last_updated(self, entity_id: str | None):
        """The driving sensor's own last_updated timestamp, used to tell a
        genuinely new reading apart from a re-evaluation triggered by some
        other entity (the heater switch echoing its own state, a window
        sensor, ...) while the sensor itself hasn't reported anything new.
        See the eftervarme finalize check for why that distinction matters."""
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        return state.last_updated if state is not None else None

    async def async_setup_listeners(self) -> None:
        entities = [self.room_temp_sensor, self.heater_switch]
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
        self._phase_started_at = None
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

    def _adjust_eftervarme(self, observed: float) -> None:
        """Blends a freshly observed residual-heat overshoot (degrees the
        primary sensor kept climbing after an ON phase ended) into the
        learned per-room estimate. Moves a fifth of the way toward the new
        observation each time -- smooths out single-cycle noise while still
        adapting within a handful of cycles. Same direct-write pattern as
        _adjust_bias, for the same re-entrancy reason."""
        entity = self._numbers.get("pulsvarme_eftervarme_graders")
        if entity is None:
            return
        current = entity.native_value or 0.0
        new_value = current + 0.2 * (observed - current)
        new_value = max(0.0, min(5.0, round(new_value, 2)))
        entity._attr_native_value = new_value  # noqa: SLF001 -- internal coordinator<->entity link
        if entity.hass is not None:
            entity.async_write_ha_state()

    def _note_real_heat_transition(
        self, in_real_heat_branch: bool, trend_temp: float | None, trend_last_updated
    ) -> bool:
        """Called once per async_evaluate() call with whether *this* call is
        in the real (hysteresis-triggered, "not target_satisfied") heat-call
        branch. Detects the moment a real cycle ends (self._real_heat_active
        True -> in_real_heat_branch False) and starts a fresh residual-heat
        observation right then.

        Must run BEFORE pulsvarme's own pulse-eligibility logic for this same
        call -- it's what sets self._real_cycle_ended_at, and
        pulsvarme_pause_efter_cyklus_min (_pause_after_real_cycle_active)
        needs that to already reflect "just now" on the very call the real
        cycle ends, not one call later.

        Returns True if this call just started tracking (the caller folds
        this into started_overshoot_tracking, same as the pulse-cutoff sites
        used to before they stopped feeding eftervarme)."""
        started = False
        if self._real_heat_active and not in_real_heat_branch:
            self._real_cycle_ended_at = time.monotonic()
            if trend_temp is not None:
                self._pending_overshoot_baseline = trend_temp
                self._pending_overshoot_peak = trend_temp
                self._pending_overshoot_last_updated = trend_last_updated
                started = True
        elif (
            not self._real_heat_active
            and in_real_heat_branch
            and self._pending_overshoot_baseline is not None
        ):
            # A new real cycle is starting (hysteresis dropped below setpoint
            # again) while the previous one's residual-heat observation
            # hadn't finished settling yet -- the hysteresis band normally
            # prevents this kind of quick back-to-back cycling, but if it
            # happens anyway, actively heating again would swamp whatever
            # pure residual drift was left to measure. Discard the
            # unfinished observation rather than let active heat get
            # mistaken for residual heat and skew the learned value.
            self._pending_overshoot_baseline = None
            self._pending_overshoot_peak = None
            self._pending_overshoot_last_updated = None
        self._real_heat_active = in_real_heat_branch
        return started

    def _pause_after_real_cycle_active(self) -> bool:
        """True while pulsvarme should hold off starting a fresh ON phase
        because a real heating cycle ended too recently -- see
        pulsvarme_pause_efter_cyklus_min in const.py."""
        if self._real_cycle_ended_at is None:
            return False
        pause_min = self._number("pulsvarme_pause_efter_cyklus_min")
        if pause_min is None:
            pause_min = 30.0
        if pause_min <= 0:
            return False
        return (time.monotonic() - self._real_cycle_ended_at) < pause_min * 60.0

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
        if regulation == "Gulv":
            trend_temp, trend_sp = floor_temp, floor_sp
            trend_entity_id = self.floor_temp_sensor
        elif regulation == "Rum":
            trend_temp, trend_sp = room_temp, room_sp
            trend_entity_id = self.room_temp_sensor
        else:  # "Rum og gulv" -- track whichever sensor is actually driving
            # the pulse: the one with the smaller margin above its own
            # setpoint (i.e. not yet overshot / still needing heat). Picking
            # room_temp unconditionally here meant a room that overshot from
            # e.g. sun through a window could cut a pulse cycle short that
            # was only running to top up the floor, which was still near its
            # setpoint the whole time.
            room_margin = (
                room_temp - room_sp if room_temp is not None and room_sp is not None else None
            )
            floor_margin = (
                floor_temp - floor_sp if floor_temp is not None and floor_sp is not None else None
            )
            if room_margin is None:
                trend_temp, trend_sp = floor_temp, floor_sp
                trend_entity_id = self.floor_temp_sensor
            elif floor_margin is None:
                trend_temp, trend_sp = room_temp, room_sp
                trend_entity_id = self.room_temp_sensor
            elif floor_margin < room_margin:
                trend_temp, trend_sp = floor_temp, floor_sp
                trend_entity_id = self.floor_temp_sensor
            else:
                trend_temp, trend_sp = room_temp, room_sp
                trend_entity_id = self.room_temp_sensor
        still_rising = (
            self._last_seen_temp is not None
            and trend_temp is not None
            and trend_temp > self._last_seen_temp
        )
        # The driving sensor's own last_updated for this call -- used below to
        # tell a genuinely new reading apart from a re-evaluation triggered by
        # something else (heater switch echo, window sensor, ...) while the
        # sensor itself hasn't reported anything new since baseline.
        trend_last_updated = self._get_last_updated(trend_entity_id)

        heat_call = False
        status_extra = ""
        # Set True below whenever this call is the one that *starts* a new
        # residual-heat observation (baseline := trend_temp). still_rising
        # was computed above from the previous call's last_seen_temp, which
        # by now is almost always already equal to this call's trend_temp
        # (every evaluate() call -- including the pulse timer's own
        # phase_elapsed callback -- unconditionally syncs last_seen_temp to
        # trend_temp at the end, whether or not a genuinely new sensor
        # reading came in). That makes still_rising spuriously False on the
        # very call that just set baseline == peak == trend_temp, which used
        # to finalize instantly with observed == 0 -- the thermostat was
        # "learning" an overshoot of zero every single cycle. Finalizing
        # needs to wait for at least one later call, once a fresh sensor
        # reading has actually moved trend_temp away from the baseline.
        started_overshoot_tracking = False

        if mode in (MODE_OFF, MODE_ALARM):
            self._cancel_pulse()
            heat_call = False
            # Not the real heat-call branch -- if it was active last call,
            # this ends it just as much as target_satisfied would (see
            # _note_real_heat_transition).
            started_overshoot_tracking = self._note_real_heat_transition(
                False, trend_temp, trend_last_updated
            )
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
            # This needs its own hysteresis, not a single hard-coded
            # setpoint+band line: a floor sensor that only reports in 0.1°C
            # steps can sit balanced exactly on that line for hours,
            # flipping pulse_eligible on every reading. Reuse
            # _hysteresis_satisfied with a pivot of setpoint+band, so it
            # takes a full extra band beyond that line before "far over"
            # latches, and a full band back below it before it releases.
            self._room_far_over = self._hysteresis_satisfied(
                room_temp,
                room_sp + band if room_sp is not None else None,
                band,
                self._room_far_over,
            )
            self._floor_far_over = self._hysteresis_satisfied(
                floor_temp,
                floor_sp + band if floor_sp is not None else None,
                band,
                self._floor_far_over,
            )
            room_near_target = room_temp is None or room_sp is None or not self._room_far_over
            floor_near_target = floor_temp is None or floor_sp is None or not self._floor_far_over
            if regulation == "Gulv":
                pulse_eligible = floor_near_target
            elif regulation == "Rum":
                pulse_eligible = room_near_target
            elif priority == "Begge temperaturer opfyldt":
                # Both sensors must reach setpoint+band before we're allowed
                # to consider things satisfied at all -- so it takes BOTH
                # being well past that (not just one) before there is truly
                # no need for any further heat. If only one has overshot
                # while the other is merely holding at/near its setpoint,
                # gentle pulsing should continue for that one's sake.
                pulse_eligible = room_near_target or floor_near_target
            else:  # "1 temperatur opfyldt"
                # Either sensor alone was enough to call things satisfied,
                # mirroring that: stop pulsing as soon as either one has
                # safely overshot, same as target_satisfied needs only one.
                pulse_eligible = room_near_target and floor_near_target

            max_room = self._number("max_temp_rum")
            max_floor = self._number("max_temp_gulv")
            max_blocked = (
                max_room is not None and room_temp is not None and room_temp >= max_room
            ) or (max_floor is not None and floor_temp is not None and floor_temp >= max_floor)

            # "en rigtig cyklus" -- a real, hysteresis-triggered heat call --
            # is exactly the elif not target_satisfied branch below (mirrors
            # its own condition, incl. max_blocked taking priority over it).
            # Checked here, before pulse_eligible is acted on below, so that
            # if a real cycle just ended this very call, pulsvarme's own
            # "should I start a new ON phase" decision a few lines down
            # already sees the fresh self._real_cycle_ended_at and holds off
            # immediately -- not one call too late.
            in_real_heat_branch = not max_blocked and not target_satisfied
            started_overshoot_tracking = self._note_real_heat_transition(
                in_real_heat_branch, trend_temp, trend_last_updated
            )

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
                min_phase = self._number("pulsvarme_min_fase_min") or 8.0
                phase_age = (
                    time.monotonic() - self._phase_started_at
                    if self._phase_started_at is not None
                    else min_phase * 60.0
                )
                if self._pulse_phase == "on" and phase_age < min_phase * 60.0:
                    # Same actuator-travel-time reasoning as the eftervarme
                    # cutoff below: a single sensor tick just over the
                    # setpoint+band line shouldn't slam an ON phase shut
                    # seconds after it opened. Let it run its minimum
                    # course; the next evaluation re-checks pulse_eligible.
                    heat_call = True
                    status_extra = " (Langt over sætpunkt, afslutter fase)"
                else:
                    # Comfortably satisfied and then some -- well above
                    # setpoint, no maintenance pulsing needed.
                    self._cancel_pulse()
                    heat_call = False
                    status_extra = " (Langt over sætpunkt)"
            else:
                pulsvarme = self._select("pulsvarme")
                if pulsvarme != "Aktiveret":
                    self._cancel_pulse()
                    heat_call = False
                elif self._pulse_phase is None and (
                    still_rising or self._pause_after_real_cycle_active()
                ):
                    # A floor slab keeps releasing residual heat for well
                    # after the relay closes -- if the temperature is still
                    # climbing from a previous cycle, starting a fresh ON
                    # phase now just compounds that overshoot. Hold off and
                    # let it plateau; the next sensor update re-checks this.
                    #
                    # Also hold off for pulsvarme_pause_efter_cyklus_min
                    # minutes after a *real* heating cycle ended, even once
                    # still_rising has already cleared -- that cycle's
                    # eftervarme observation (see the finalize check near the
                    # end of this method) needs a clean, pulsvarme-free
                    # window to settle in, or pulsvarme's own small ON pulses
                    # would add extra heat on top of pure residual heat and
                    # skew what gets learned.
                    heat_call = False
                    status_extra = " (Afventer eftervarme)"
                else:
                    if self._pulse_phase is None:
                        cycle_minutes = self._number("pulsvarme_cyklus_min") or 40
                        duty_pct, duty_ff, bias = self._compute_duty_percent()
                        self._current_duty_pct = duty_pct
                        # Never let a phase run shorter than the actuator's
                        # own travel time -- otherwise it gets told to close
                        # again before it has even finished opening (or vice
                        # versa), delivering little to no real heat despite
                        # the relay reporting "on".
                        min_phase = self._number("pulsvarme_min_fase_min") or 8.0
                        self._cycle_on_minutes = max(min_phase, cycle_minutes * duty_pct / 100.0)
                        self._cycle_off_minutes = max(min_phase, cycle_minutes - self._cycle_on_minutes)
                        self._pulse_phase = "on"
                        self._phase_started_at = time.monotonic()
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
                        # ON -> OFF is just a phase toggle now -- pulsvarme's
                        # own cycles no longer feed eftervarme (see the real-
                        # cycle-end tracking below); they only ever *use* the
                        # already-learned value to end an ON phase early.
                        self._pulse_phase = "off" if self._pulse_phase == "on" else "on"
                        self._phase_started_at = time.monotonic()
                        self._schedule_pulse_timer()
                    elif self._pulse_phase == "on":
                        eftervarme = self._number("pulsvarme_eftervarme_graders") or 0.0
                        min_phase = self._number("pulsvarme_min_fase_min") or 8.0
                        phase_age = (
                            time.monotonic() - self._phase_started_at
                            if self._phase_started_at is not None
                            else min_phase * 60.0
                        )
                        if (
                            phase_age >= min_phase * 60.0
                            and trend_temp is not None
                            and trend_sp is not None
                            and trend_temp + eftervarme >= trend_sp + band
                        ):
                            # Learned residual heat says this room typically
                            # keeps climbing ~eftervarme degrees after the
                            # relay closes -- cut the ON phase short now so
                            # that rise lands on the setpoint instead of past
                            # it, rather than waiting for the actual reading
                            # to cross the threshold. Never before min_phase
                            # has actually elapsed, though -- the actuator
                            # needs that long to do anything regardless of
                            # how many times we get re-evaluated in the
                            # meantime (e.g. our own turn_on echoing back as
                            # a state-change event on the heater switch).
                            # This cutoff only *uses* the learned eftervarme
                            # value -- it doesn't start a new observation;
                            # pulsvarme cycles no longer feed eftervarme.
                            if self._pulse_cancel:
                                self._pulse_cancel()
                                self._pulse_cancel = None
                            self._pulse_phase = "off"
                            self._phase_started_at = time.monotonic()
                            self._schedule_pulse_timer()
                    heat_call = self._pulse_phase == "on"
                    duty_txt = f", {self._current_duty_pct:.0f}% duty" if self._current_duty_pct else ""
                    status_extra = f" (Pulsvarme: {self._pulse_phase.upper()}{duty_txt})"

        # (Real-cycle-end detection for eftervarme happens earlier, before
        # pulsvarme's own pulse-eligibility logic runs -- see
        # _note_real_heat_transition.)

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

        if self._pending_overshoot_baseline is not None and trend_temp is not None:
            if trend_temp > self._pending_overshoot_peak:
                self._pending_overshoot_peak = trend_temp
            # still_rising only tells us the current reading isn't higher than
            # the last one we recorded -- and _last_seen_temp below is synced
            # on EVERY evaluate() call, including ones with no new sensor data
            # at all (the heater switch echoing its own state, a window
            # sensor, a number/select edit, ...). Without this guard, the very
            # next such call after baseline is set sees an unchanged reading,
            # reads that as "stopped climbing", and finalizes with
            # observed == 0 -- which is exactly why eftervarme never actually
            # learned anything. Only trust "stopped climbing" once the driving
            # sensor's own last_updated has actually moved past where it was
            # when this observation started.
            has_fresh_reading = (
                trend_last_updated is not None
                and self._pending_overshoot_last_updated is not None
                and trend_last_updated > self._pending_overshoot_last_updated
            )
            if has_fresh_reading:
                self._pending_overshoot_last_updated = trend_last_updated
            if not still_rising and not started_overshoot_tracking and has_fresh_reading:
                # Temperature has stopped climbing -- residual heat from the
                # last ON phase has fully surfaced. Blend the observed
                # overshoot into the learned estimate.
                observed = max(0.0, self._pending_overshoot_peak - self._pending_overshoot_baseline)
                self._adjust_eftervarme(observed)
                self._pending_overshoot_baseline = None
                self._pending_overshoot_peak = None
                self._pending_overshoot_last_updated = None

        if trend_temp is not None:
            self._last_seen_temp = trend_temp

        status_sensor = self._sensors.get("varme_tilstand")
        if status_sensor:
            status_sensor.async_update_state(f"{mode}{status_extra}", heat_call=heat_call)
        for key, value in (("varme_setpunkt_rum", room_sp), ("varme_setpunkt_gulv", floor_sp)):
            sensor = self._sensors.get(key)
            if sensor and value is not None:
                sensor.async_update_state(value)

        for target, climate_entity in self._climates.items():
            temp, setpoint = (room_temp, room_sp) if target == "rum" else (floor_temp, floor_sp)
            climate_entity.async_update_state(
                mode=mode,
                # The dial's "Opvarmning"/"Inaktiv" label should reflect
                # what the relay is actually doing right now, not our
                # decision from this cycle -- they're usually the same
                # instant, but this stays correct even if the physical
                # switch lags behind or gets toggled independently.
                heat_call=switch_is_on,
                temp=temp,
                setpoint=setpoint,
                room_temp=room_temp,
                floor_temp=floor_temp,
            )
