from datetime import datetime

import pytz


def to_utc(value, zone):
    """Parse 'YYYY-MM-DD HH:MM' in the given time zone and return an ISO string in UTC."""
    local = pytz.timezone(zone).localize(datetime.strptime(value, "%Y-%m-%d %H:%M"))
    return local.astimezone(pytz.utc).isoformat()


def known_zone(zone):
    return zone in pytz.all_timezones_set
