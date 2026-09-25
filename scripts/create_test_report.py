"""
Live test of report creation from a PBIR definition, as the service principal. Creates one report in the workspace,
bound to an existing semantic model, and prints its id and link. Needs PBI_TENANT_ID, PBI_CLIENT_ID and
PBI_CLIENT_SECRET in the environment. Never prints a token or the secret.

Usage:
python3 scripts/create_test_report.py <workspace_id> <semantic_model_id> <Tabelle.Kennzahl> <Tabelle.Spalte> "<Titel>" [Tabelle.Datumsspalte]
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from powerbi_mcp_server.fabric import FabricClient  # noqa: E402
from powerbi_mcp_server.pbir import Field, build_report_parts  # noqa: E402
from powerbi_mcp_server.powerbi import PowerBiError  # noqa: E402


def field(text: str) -> Field:
    entity, _, name = text.partition(".")
    return Field(entity, name)


def main(argv: list[str]) -> int:
    if len(argv) not in (5, 6):
        print(__doc__, file=sys.stderr)
        return 1
    missing = [n for n in ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET") if not os.environ.get(n)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1
    workspace_id, model_id, measure, dimension, title = argv[:5]
    date_column = field(argv[5]) if len(argv) == 6 else None
    client = FabricClient(os.environ["PBI_TENANT_ID"], os.environ["PBI_CLIENT_ID"], os.environ["PBI_CLIENT_SECRET"])
    parts = build_report_parts(model_id, title, field(measure), field(dimension), date_column)
    try:
        report = client.create_report(workspace_id, title, parts)
    except PowerBiError as exc:
        print(f"FAILED: HTTP {exc.status} {exc}")
        return 1
    print(f"OK  report {report.get('id')}")
    print(f"    https://app.powerbi.com/groups/{workspace_id}/reports/{report.get('id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
