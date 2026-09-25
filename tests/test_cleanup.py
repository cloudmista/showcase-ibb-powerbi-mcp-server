from datetime import date

from powerbi_mcp_server.cleanup import find_expired
from powerbi_mcp_server.service import owner_tag


def test_only_tagged_reports_older_than_the_retention_are_expired() -> None:
    reports = [
        {"id": "alt", "name": "A" + owner_tag("alice", "2026-08-01")},
        {"id": "grenze", "name": "B" + owner_tag("alice", "2026-08-26")},
        {"id": "neu", "name": "C" + owner_tag("bob", "2026-09-20")},
        {"id": "fremd", "name": "Handgemacht von 2020"},
    ]
    assert [r["id"] for r in find_expired(reports, date(2026, 9, 25), 30)] == ["alt"]
