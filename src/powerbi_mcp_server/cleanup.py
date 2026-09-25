from datetime import date, timedelta

from .service import parse_owner

DEFAULT_MAX_AGE_DAYS = 30


def find_expired(reports: list[dict[str, object]], today: date, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> list[dict[str, object]]:
    """
    Pick generated reports older than the retention period, judged by the date in their owner tag.

    Reports without a tag were not made by this server and are never returned.

    :param reports list: Reports of the agent workspace as listed by Power BI
    :param today date: Reference day
    :param max_age_days int: Reports created before today minus this many days are expired
    :return: The expired reports
    """
    cutoff = today - timedelta(days=max_age_days)
    expired = []
    for report in reports:
        owner = parse_owner(report.get("name", ""))
        if owner and date.fromisoformat(owner[1]) < cutoff:
            expired.append(report)
    return expired
