import requests
import time
import signal
import sys
from datetime import datetime, date

# ============================================================
# CONFIGURE THESE FOR YOUR OWN SETUP BEFORE RUNNING
# ============================================================
BLYNK_AUTH = "YOUR_BLYNK_AUTH_TOKEN"          # from your Blynk device settings
BLYNK_SERVER = "https://blynk.cloud"
LOCAL_API = "http://localhost"                # your melnor_decloudify server
PUMP_API = "http://192.168.1.XXX"             # your pump controller's local IP
# ============================================================

# Event codes - must match Event codes created in the Blynk console
EVENT_EARLY_SHUTOFF = "early_shutoff"
EVENT_PUMP_FAILED = "pump_failed"

# Default schedule settings
DEFAULT_START_HOUR = 6
DEFAULT_START_MIN = 0
DEFAULT_DURATION = 15
DEFAULT_REST_DAYS = 0

# Zone rotation — only enabled zones participate
ZONE_ORDER = [1, 4, 2, 3]

# State tracking
last_water_date = None
last_zone_index = -1
rain_delay_until = None
battery_counter = 0
pump_on = False
last_keepalive_time = 0
pump_fail_count = 0

# Scheduled-run watchdog state
scheduled_active = False
scheduled_zone = None
scheduled_expected_end = None

def blynk_get(pin):
    try:
        r = requests.get(f"{BLYNK_SERVER}/external/api/get?token={BLYNK_AUTH}&{pin}", timeout=5)
        return r.text.strip()
    except:
        return None

def blynk_set(pin, value):
    try:
        requests.get(f"{BLYNK_SERVER}/external/api/update?token={BLYNK_AUTH}&{pin}={value}", timeout=5)
    except:
        pass

def notify_blynk_event(code):
    """Trigger a Blynk Event (must be configured in the Blynk console with this code)"""
    try:
        requests.get(f"{BLYNK_SERVER}/external/api/logEvent?token={BLYNK_AUTH}&code={code}", timeout=5)
        print(f"Notification sent: {code}")
    except Exception as e:
        print(f"Failed to send notification {code}: {e}")

def local_get_status():
    try:
        r = requests.get(f"{LOCAL_API}/REST", timeout=5)
        return r.json()
    except:
        return None

def local_set_valve(channel, minutes):
    try:
        r = requests.get(f"{LOCAL_API}/REST?channel={channel}&min={minutes}", timeout=5)
        return "OK" in r.text
    except:
        return False

def pump_turn_on():
    global pump_on
    for attempt in range(3):
        try:
            r = requests.get(f"{PUMP_API}/pump/on", timeout=5)
            if "OK" in r.text:
                pump_on = True
                print(f"Pump ON (attempt {attempt + 1})")
                return True
        except:
            print(f"Pump attempt {attempt + 1} failed")
        time.sleep(3)
    print("Pump unreachable after 3 attempts - aborting watering")
    pump_on = False
    return False

def pump_turn_off():
    global pump_on
    try:
        requests.get(f"{PUMP_API}/pump/off", timeout=5)
    except:
        pass
    pump_on = False
    print("Pump OFF")

def pump_keepalive():
    """Returns True if the pump controller acknowledged, False otherwise"""
    try:
        r = requests.get(f"{PUMP_API}/keepalive", timeout=5)
        return "OK" in r.text
    except:
        return False

def any_valve_on():
    """Check if any valve is currently running"""
    status = local_get_status()
    if not status:
        return False
    valves = status.get("valves", {})
    systime = int(status.get("systime", 0))
    for i in range(8):
        v = int(valves.get(f"V{i}", 0))
        if v > systime:
            return True
    return False

def all_zones_off():
    for i in range(1, 5):
        local_set_valve(i, 0)
    pump_turn_off()
    print("All zones OFF")

def get_schedule():
    """Read schedule settings from Blynk"""
    auto = blynk_get("v7")
    auto = int(auto) if auto and auto.isdigit() else 0

    rain = blynk_get("v8")
    rain = int(rain) if rain and rain.isdigit() else 0

    start_time = blynk_get("v13")

    duration = blynk_get("v14")
    duration = int(duration) if duration and duration.isdigit() else DEFAULT_DURATION

    rest = blynk_get("v15")
    rest = int(rest) if rest and rest.isdigit() else DEFAULT_REST_DAYS

    z1 = blynk_get("v9")
    z1 = int(z1) if z1 and z1.isdigit() else 0
    z4 = blynk_get("v10")
    z4 = int(z4) if z4 and z4.isdigit() else 0
    z2 = blynk_get("v11")
    z2 = int(z2) if z2 and z2.isdigit() else 0
    z3 = blynk_get("v12")
    z3 = int(z3) if z3 and z3.isdigit() else 0

    zone_enabled = {1: z1, 4: z4, 2: z2, 3: z3}

    start_hour = DEFAULT_START_HOUR
    start_min = DEFAULT_START_MIN
    if start_time:
        try:
            seconds = int(start_time.split('\x00')[0])
            start_hour = seconds // 3600
            start_min = (seconds % 3600) // 60
        except:
            pass

    return {
        "auto": auto,
        "rain": rain,
        "start_hour": start_hour,
        "start_min": start_min,
        "duration": duration,
        "rest": rest,
        "zone_enabled": zone_enabled
    }

def get_next_zone(zone_enabled):
    global last_zone_index
    enabled_zones = [z for z in ZONE_ORDER if zone_enabled.get(z, 0) == 1]
    if not enabled_zones:
        return None
    last_zone_index = (last_zone_index + 1) % len(enabled_zones)
    return enabled_zones[last_zone_index]

def should_water_today(schedule):
    global last_water_date, rain_delay_until

    today = date.today()

    if rain_delay_until and today <= rain_delay_until:
        days_left = (rain_delay_until - today).days + 1
        print(f"Rain delay active — {days_left} day(s) remaining")
        return False

    if rain_delay_until and today > rain_delay_until:
        rain_delay_until = None
        blynk_set("v8", 0)

    if last_water_date == today:
        return False

    if last_water_date:
        days_since = (today - last_water_date).days
        required = 1 + schedule["rest"]
        if days_since < required:
            print(f"Rest day — {required - days_since} day(s) remaining")
            return False

    return True

def check_emergency_stop():
    global scheduled_active
    val = blynk_get("v16")
    if val == "1":
        print("EMERGENCY STOP triggered!")
        all_zones_off()
        blynk_set("v7", 0)
        blynk_set("v16", 0)
        scheduled_active = False
        return True
    return False

def shutdown_handler(signum, frame):
    print(f"Shutting down - setting offline status (signal {signum})")
    all_zones_off()
    blynk_set("v5", "offline")
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

print("Blynk bridge starting...")

# Initialize button-state memory from Blynk's actual current values,
# instead of assuming "0" — prevents a stale "1" left over from a
# previous session being misread as a fresh button press after restart.
prev = {}
for i in range(1, 5):
    pin = f"v{i}"
    val = blynk_get(pin)
    prev[pin] = val if val is not None else "0"
print(f"Initial button states: {prev}")

schedule_checked_minute = -1

while True:
    try:
        if check_emergency_stop():
            time.sleep(1)
            continue

        for i in range(1, 5):
            pin = f"v{i}"
            val = blynk_get(pin)
            if val is not None and val != prev[pin]:
                prev[pin] = val
                if val == "1":
                    print(f"Zone {i} ON (manual)")
                    if pump_turn_on():
                        local_set_valve(i, 20)
                    else:
                        blynk_set(f"v{i}", 0)
                        print(f"Zone {i} NOT opened - pump failed")
                else:
                    print(f"Zone {i} OFF (manual)")
                    local_set_valve(i, 0)

        valve_active = any_valve_on()

        if pump_on and not valve_active:
            pump_turn_off()

        # Watchdog: did a scheduled run stop before its expected duration?
        if scheduled_active and not valve_active:
            scheduled_active = False
            if scheduled_expected_end and time.time() < scheduled_expected_end - 90:
                remaining_min = round((scheduled_expected_end - time.time()) / 60, 1)
                print(f"Scheduled watering for Zone {scheduled_zone} stopped early "
                      f"— {remaining_min} min remaining when it closed")
                notify_blynk_event(EVENT_EARLY_SHUTOFF)

        # Send keepalive based on REAL elapsed time, not loop iterations —
        # counting loop ticks assumes each iteration takes exactly 1 second,
        # which isn't reliable if any of the HTTP calls above run slow.
        if pump_on:
            if time.time() - last_keepalive_time >= 30:
                last_keepalive_time = time.time()
                if pump_keepalive():
                    pump_fail_count = 0
                else:
                    pump_fail_count += 1
                    print(f"Pump keepalive failed ({pump_fail_count})")
                    if pump_fail_count >= 2 and valve_active:
                        print("Pump not responding - closing all valves as safety measure")
                        all_zones_off()
                        scheduled_active = False
                        notify_blynk_event(EVENT_PUMP_FAILED)
        else:
            pump_fail_count = 0

        now = datetime.now()
        current_minute = now.hour * 60 + now.minute

        if current_minute != schedule_checked_minute:
            schedule_checked_minute = current_minute
            schedule = get_schedule()

            if schedule["auto"] == 1:
                if now.hour == schedule["start_hour"] and now.minute == schedule["start_min"]:
                    if should_water_today(schedule):
                        zone = get_next_zone(schedule["zone_enabled"])
                        if zone:
                            print(f"Schedule: watering Zone {zone} for {schedule['duration']} minutes")
                            if pump_turn_on():
                                if local_set_valve(zone, schedule["duration"]):
                                    last_water_date = date.today()
                                    scheduled_active = True
                                    scheduled_zone = zone
                                    scheduled_expected_end = time.time() + schedule["duration"] * 60
                                else:
                                    print("Schedule FAILED - valve command not accepted")
                                    pump_turn_off()
                            else:
                                print("Schedule SKIPPED - pump failed to start")

                            if schedule["rain"] > 0:
                                from datetime import timedelta
                                rain_hours = [0, 24, 48, 72]
                                rain_delay_until = date.today() + timedelta(hours=rain_hours[schedule["rain"]])

        status = local_get_status()
        if status:
            online = status.get("online", "unknown")
            blynk_set("v5", online)
            battery = status.get("battery", "?")
            if battery != "?" and battery_counter == 0:
                try:
                    bval = round(float(battery), 1)
                    blynk_set("v6", bval)
                except:
                    pass

        battery_counter = (battery_counter + 1) % 12

    except Exception as e:
        print(f"Error: {e}")

    time.sleep(1)
