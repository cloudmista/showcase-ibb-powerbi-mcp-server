"""
Read-only: print how Power BI stored a report. For every visual it shows the visual type, position and the fields per
bucket. Needs PBI_TENANT_ID, PBI_CLIENT_ID and PBI_CLIENT_SECRET in the environment.

Usage:
python3 scripts/show_report.py <workspace_id> <report_id>
"""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from powerbi_mcp_server.fabric import FabricClient  # noqa: E402
from powerbi_mcp_server.powerbi import PowerBiError  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1
    missing = [n for n in ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET") if not os.environ.get(n)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1
    client = FabricClient(os.environ["PBI_TENANT_ID"], os.environ["PBI_CLIENT_ID"], os.environ["PBI_CLIENT_SECRET"])
    try:
        parts = client.get_report_definition(argv[0], argv[1])
    except PowerBiError as exc:
        print(f"FAILED: HTTP {exc.status} {exc}")
        return 1
    print("parts:", ", ".join(p["path"] for p in parts))
    for part in parts:
        if not part["path"].endswith("/visual.json"):
            continue
        body = json.loads(base64.b64decode(part["payload"]))
        visual = body.get("visual", {})
        position = body.get("position", {})
        print(f"\n{body.get('name')}  type={visual.get('visualType')}  x={position.get('x')} y={position.get('y')} w={position.get('width')} h={position.get('height')}")
        for bucket, state in visual.get("query", {}).get("queryState", {}).items():
            print(f"  {bucket}: " + ", ".join(p.get("queryRef", "?") for p in state.get("projections", [])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
