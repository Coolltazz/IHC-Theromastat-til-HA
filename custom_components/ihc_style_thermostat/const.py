"""Constants for the IHC-style Room Thermostat integration."""

DOMAIN = "ihc_style_thermostat"

CONF_ROOM_TEMP_SENSOR = "room_temp_sensor"
CONF_FLOOR_TEMP_SENSOR = "floor_temp_sensor"
CONF_HEATER_SWITCH = "heater_switch"
CONF_WINDOW_SENSOR = "window_sensor"
CONF_HOUSE_MODE_ENTITY = "house_mode_entity"
CONF_OUTDOOR_TEMP_ENTITY = "outdoor_temp_entity"
CONF_WIND_SPEED_ENTITY = "wind_speed_entity"
CONF_SOLAR_ENTITY = "solar_entity"

OPTION_WINDOW_DELAY_MINUTES = "window_delay_minutes"
DEFAULT_WINDOW_DELAY_MINUTES = 10

MODE_OCCUPIED = "Beboet"
MODE_UNOCCUPIED = "Ubeboet"
MODE_FROST = "Frostsikring"
MODE_GUEST = "Gæster"
MODE_NIGHT = "Nedsænkning"
MODE_OFF = "Slukket"
MODE_ALARM = "Alarm"
MODE_WINDOW_OPEN = "Dør/vindue åben"

HOUSE_MODE_MAP = {
    "Beboet": MODE_OCCUPIED,
    "Ubeboet": MODE_UNOCCUPIED,
    "Frostsikring": MODE_FROST,
    "Gæster": MODE_GUEST,
}

REGULATION_OPTIONS = [
    "Gulv",
    "Rum og gulv",
    "Rum",
    "Rum (gulv min.)",
    "Rum (kaskade via gulv)",
]
PRIORITY_OPTIONS = ["1 temperatur opfyldt", "Begge temperaturer opfyldt"]
LOCAL_MODE_OPTIONS = [
    "Følger husets tilstand",
    "Tvunget fra",
    "Tvunget frostsikring",
    "Tvunget nedsænkning",
]
PULSVARME_OPTIONS = ["Aktiveret", "Ikke Aktiveret"]

# key -> (default, min, max, step, unit, icon)
NUMBER_DEFS = {
    "setpunkt_rum_beboet": (21.0, 10.0, 30.0, 0.1, "°C", "mdi:home-thermometer"),
    "setpunkt_gulv_beboet": (24.0, 10.0, 35.0, 0.1, "°C", "mdi:heating-coil"),
    # Used by two regulation modes (see coordinator.py):
    # - "Rum (gulv min.)": the floor is not a regulation target at all --
    #   it only forces a heat call if it ever drops below this value (with
    #   the usual hysteresis band), regardless of what the room is doing.
    #   Pulse heating/adaptive bias/eftervarme/wind-sun all track the ROOM
    #   sensor in this mode -- the floor plays no part in any of it.
    # - "Rum (kaskade via gulv)": the lower clamp on the continuously
    #   recomputed floor target (see pulsvarme_gulv_kaskade_forstaerkning
    #   below and _cascade_floor_setpoint) -- the floor is never commanded
    #   below this even if the room is comfortably over its own setpoint.
    #   Here, pulse heating/eftervarme/wind-sun track the FLOOR sensor
    #   against that moving target (the floor is the actual actuator), but
    #   the cascade's own gain is tuned purely from the ROOM's stability --
    #   see pulsvarme_gulv_kaskade_forstaerkning.
    "gulv_min_temperatur": (18.0, 5.0, 30.0, 0.1, "°C", "mdi:heating-coil"),
    "setpunkt_ubeboet": (17.0, 5.0, 25.0, 0.1, "°C", "mdi:home-minus"),
    "setpunkt_gaest_nat": (19.0, 5.0, 25.0, 0.1, "°C", "mdi:weather-night"),
    "setpunkt_frost": (7.0, 0.0, 15.0, 0.1, "°C", "mdi:snowflake"),
    "max_temp_rum": (26.0, 20.0, 35.0, 0.1, "°C", "mdi:thermometer-alert"),
    "max_temp_gulv": (30.0, 20.0, 40.0, 0.1, "°C", "mdi:thermometer-alert"),
    "hysterese": (0.3, 0.1, 3.0, 0.1, "°C", "mdi:sine-wave"),
    # Total pulse-cycle length. The ON/OFF split within it is computed live
    # from the duty% (weather compensation + adaptive bias below) -- there
    # is deliberately no separate "ON duration"/"OFF duration" setting,
    # since only their sum would ever matter once duty% exists.
    "pulsvarme_cyklus_min": (40.0, 2.0, 240.0, 1.0, "min", "mdi:timer-sync"),
    # Floor under each ON/OFF phase, regardless of duty% -- a thermal
    # actuator (telestat) takes real time to fully open/close (often
    # several minutes), so a phase shorter than that delivers little to no
    # actual heat even though the relay reports "on". Default (8 min) covers
    # a typical 5-7 minute actuator travel time with a small margin.
    "pulsvarme_min_fase_min": (8.0, 1.0, 30.0, 1.0, "min", "mdi:timer-alert"),
    # Weather-compensated + self-adjusting pulse duty cycle (see coordinator.py
    # _compute_duty_percent / _adjust_bias). "pulsvarme_adaptiv_bias" is not
    # meant for routine manual editing -- the coordinator reads/writes it
    # itself, but it must be a real, restorable entity to persist across
    # HA restarts.
    "ude_temp_lav": (-10.0, -30.0, 10.0, 0.1, "°C", "mdi:snowflake-thermometer"),
    "ude_temp_hoej": (15.0, 0.0, 25.0, 0.1, "°C", "mdi:thermometer"),
    "pulsvarme_duty_min": (15.0, 0.0, 100.0, 1.0, "%", "mdi:percent-outline"),
    "pulsvarme_duty_max": (90.0, 0.0, 100.0, 1.0, "%", "mdi:percent"),
    "pulsvarme_adaptiv_bias": (0.0, -30.0, 30.0, 0.1, "%", "mdi:tune"),
    # Learned residual-heat overshoot: how many degrees this room's primary
    # sensor typically keeps climbing *after* the relay closes (thermal
    # lag/mass -- most pronounced in floor slabs). Nudged by the coordinator
    # only after a *real* heating cycle ends (hysteresis dropped below
    # setpoint, heat_call ran, then target_satisfied again -- see
    # _adjust_eftervarme) -- never from pulsvarme's own near-setpoint
    # maintenance pulsing, which only *uses* this value (to end its ON phase
    # that much early, so the residual rise lands on the setpoint instead of
    # past it) without feeding back into it. Starts at 0 (no correction) and
    # learns over a handful of real cycles.
    "pulsvarme_eftervarme_graders": (0.0, 0.0, 5.0, 0.1, "°C", "mdi:thermometer-chevron-up"),
    # How long pulsvarme must hold off after a real heating cycle ends before
    # it's allowed to start its own near-setpoint maintenance pulsing. Gives
    # that cycle's residual-heat measurement (above) time to plateau cleanly
    # before pulsvarme's own small ON pulses could add extra heat and skew
    # it. 0 disables the pause entirely.
    "pulsvarme_pause_efter_cyklus_min": (30.0, 0.0, 120.0, 1.0, "min", "mdi:timer-sand"),
    # Wind/sun refinement of the weather-compensated duty% above (see
    # coordinator.py _compute_duty_percent / _adjust_vind_faktor /
    # _adjust_sol_faktor). Only active when the matching
    # wind_speed_entity / solar_entity is configured in Options -- with
    # neither set, these four numbers exist but never affect anything.
    #
    # vind_lav/vind_hoej mark the wind-speed range (same entity's own
    # unit, e.g. m/s) over which pulsvarme_vind_faktor's extra duty% is
    # scaled in -- 0 at/below vind_lav, full faktor at/above vind_hoej.
    "vind_lav": (0.0, 0.0, 20.0, 0.5, "m/s", "mdi:weather-windy"),
    "vind_hoej": (6.0, 0.0, 30.0, 0.5, "m/s", "mdi:weather-windy-variant"),
    # Learned extra duty% added on top of the weather compensation while
    # wind is in the vind_lav..vind_hoej range -- nudged up when a cycle
    # fell through to a real heat call while windy, nudged back down when
    # a windy cycle held fine anyway. Starts at 0 (no effect) and only
    # grows if windy conditions actually correlate with needing more heat.
    "pulsvarme_vind_faktor": (0.0, 0.0, 20.0, 0.1, "%", "mdi:weather-windy"),
    # sol_lav/sol_hoej mark the solar/light-level range (same entity's own
    # unit, e.g. lx) over which pulsvarme_sol_faktor's duty% reduction is
    # scaled in -- 0 at/below sol_lav, full faktor at/above sol_hoej.
    "sol_lav": (2000.0, 0.0, 10000.0, 100.0, "lx", "mdi:weather-cloudy"),
    "sol_hoej": (15000.0, 0.0, 50000.0, 500.0, "lx", "mdi:weather-sunny"),
    # Learned duty% SUBTRACTED while sun/light is in the sol_lav..sol_hoej
    # range -- nudged up when a cycle held fine (or better) while sunny
    # (free solar heat helped), nudged back down when a sunny cycle still
    # fell through anyway. Starts at 0 and only grows if sun actually
    # correlates with needing less heat.
    "pulsvarme_sol_faktor": (0.0, 0.0, 30.0, 0.1, "%", "mdi:weather-sunny"),
    # Only used by regulation mode "Rum (kaskade via gulv)" (see
    # coordinator.py _cascade_floor_setpoint / _adjust_kaskade_gain_from_room).
    # How many degrees the floor's target setpoint shifts per degree the
    # room currently sits away from ITS OWN setpoint -- room too cold
    # raises the floor target (demand more heat into the slab), room at/
    # over setpoint lowers it again, continuously, instead of the floor
    # just sitting at one fixed number. Self-tuned purely from how stable
    # the ROOM stays against its own setpoint: nudged up when the room
    # sits below setpoint for a long stretch with floor headroom still
    # left (cascade isn't pushing hard enough), nudged back down when the
    # room swings into a comfortable overshoot (cascade pushed too hard).
    # Starts conservative (0.5) rather than at 0, since a gain of 0 would
    # make this mode behave exactly like a fixed floor setpoint with no
    # room compensation at all -- defeating the point from the start.
    "pulsvarme_gulv_kaskade_forstaerkning": (0.5, 0.0, 3.0, 0.05, "°C/°C", "mdi:chart-line-variant"),
}

# key -> (options, default, icon)
SELECT_DEFS = {
    "regulering": (REGULATION_OPTIONS, "Rum og gulv", "mdi:thermostat"),
    "prioritet": (PRIORITY_OPTIONS, "1 temperatur opfyldt", "mdi:priority-high"),
    "lokal_tilstand": (LOCAL_MODE_OPTIONS, "Følger husets tilstand", "mdi:home-switch"),
    "pulsvarme": (PULSVARME_OPTIONS, "Aktiveret", "mdi:pulse"),
}
