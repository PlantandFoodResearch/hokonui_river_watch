import os
import requests
import xmltodict
from datetime import datetime, timedelta

FLOW_THRESHOLD = 200.0  # m3/sec - adjust as needed
LOWER_THRESHOLD = 190.0  # m3/sec - adjust as needed
FIRST_RUN_LOOKBACK_MINUTES = 15
LAST_RUN_TIME_FILE = os.path.join(os.path.dirname(__file__), "last_run_time.txt")
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"

API_URL = (
    "http://odp.es.govt.nz/ariver.hts"
    "?Service=Hilltop&Request=GetData"
    "&site=Mataura%20River%20at%20Gore"
    "&measurement=Flow"
    "&From={from_date}&To={to_date}"
)


def send_alert(flow_rate, timestamp):
    print(
        f"ALERT: Flow rate {flow_rate:.3f} m3/sec exceeded threshold of "
        f"{FLOW_THRESHOLD} m3/sec at {timestamp}"
    )


def read_last_run_time():
    try:
        with open(LAST_RUN_TIME_FILE) as f:
            return datetime.strptime(f.read().strip(), TIMESTAMP_FORMAT)
    except (FileNotFoundError, ValueError):
        return datetime.now() - timedelta(minutes=FIRST_RUN_LOOKBACK_MINUTES)


def save_last_run_time(timestamp_str):
    with open(LAST_RUN_TIME_FILE, "w") as f:
        f.write(timestamp_str)


def check_river_flow():
    # API time res is one day and the time is seen as midnight of the day, 
    # so we need to add a day to get the correct date range
    now = datetime.now() + timedelta(days=1)  
    two_days_ago = now - timedelta(days=1)

    from_date = two_days_ago.strftime("%-d/%-m/%Y")
    to_date = now.strftime("%-d/%-m/%Y")

    last_run_time = read_last_run_time()

    response = requests.get(API_URL.format(from_date=from_date, to_date=to_date))
    response.raise_for_status()

    data = xmltodict.parse(response.content)

    readings = data["Hilltop"]["Measurement"]["Data"]["E"]

    # Ensure readings is always a list (single result comes back as a dict)
    if isinstance(readings, dict):
        readings = [readings]

    save_last_run_time(readings[-1]["T"])

    # Only consider readings since the last run
    new_readings = [
        r for r in readings
        if datetime.strptime(r["T"], TIMESTAMP_FORMAT) > last_run_time
    ]

    if not new_readings:
        return

    # Check if the final new reading crossed the threshold from below
    # The reading just before new_readings is the last known state
    new_start_index = readings.index(new_readings[0])
    prev_above = (
        float(readings[new_start_index - 1]["I1"]) > FLOW_THRESHOLD
        if new_start_index > 0
        else False
    )

    

    crossed = None
    for reading in new_readings:
        flow_rate = float(reading["I1"])
        currently_above = flow_rate > FLOW_THRESHOLD
        if currently_above and not prev_above:
            send_alert(flow_rate, reading["T"])
        prev_above = currently_above


