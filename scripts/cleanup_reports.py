"""
Delete generated reports older than N days from the agent workspace. Dry run unless --apply is given.

Usage: PBI_TENANT_ID=... PBI_CLIENT_ID=... PBI_CLIENT_SECRET=... python3 scripts/cleanup_reports.py [--days 30] [--apply]
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from powerbi_mcp_server.catalog import load_catalog  # noqa: E402
from powerbi_mcp_server.cleanup import DEFAULT_MAX_AGE_DAYS, find_expired  # noqa: E402
from powerbi_mcp_server.powerbi import PowerBiClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    missing = [n for n in ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET") if not os.environ.get(n)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1
    workspace = load_catalog()["agent_workspace_id"]
    client = PowerBiClient(os.environ["PBI_TENANT_ID"], os.environ["PBI_CLIENT_ID"], os.environ["PBI_CLIENT_SECRET"])
    expired = find_expired(client.list_reports(workspace), date.today(), args.days)
    for report in expired:
        print(f"{'delete' if args.apply else 'would delete'}: {report['name']}")
        if args.apply:
            client.delete_report(workspace, report["id"])
    print(f"{len(expired)} expired report(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
