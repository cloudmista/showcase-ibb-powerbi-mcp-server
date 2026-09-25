"""
Create a push dataset from a CSV file in a workspace, with DAX measures, and load the rows. Column types are inferred
(whole numbers, decimals, text). The dataset id printed at the end is the semantic model id for report creation.
Needs PBI_TENANT_ID, PBI_CLIENT_ID and PBI_CLIENT_SECRET in the environment. Never prints a token or the secret.

Usage:
python3 scripts/push_csv_dataset.py <workspace_id> <csv_file> <dataset_name> "<Measure>=<DAX>" ["<Measure>=<DAX>" ...]
"""

import csv
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from powerbi_mcp_server.powerbi import PowerBiClient, PowerBiError  # noqa: E402

BATCH_SIZE = 1000


def infer_type(values: list[str]) -> str:
    """
    Pick the Power BI column type for a list of CSV values.

    :param values list: Cell texts of one column
    :return: Int64 for whole numbers, Double for decimals, string otherwise
    """
    filled = [v for v in values if v != ""]
    if not filled:
        return "string"
    try:
        for value in filled:
            int(value)
        return "Int64"
    except ValueError:
        pass
    try:
        for value in filled:
            float(value)
        return "Double"
    except ValueError:
        return "string"


def convert(value: str, data_type: str) -> object:
    if value == "":
        return None
    if data_type == "Int64":
        return int(value)
    if data_type == "Double":
        return float(value)
    return value


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print(__doc__, file=sys.stderr)
        return 1
    missing = [n for n in ("PBI_TENANT_ID", "PBI_CLIENT_ID", "PBI_CLIENT_SECRET") if not os.environ.get(n)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1
    workspace_id, csv_path, dataset_name = argv[:3]
    measures = []
    for spec in argv[3:]:
        name, _, expression = spec.partition("=")
        measures.append({"name": name, "expression": expression})

    with open(csv_path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        raw_rows = list(reader)
    types = {c: infer_type([r[c] for r in raw_rows]) for c in columns}
    rows = [{c: convert(r[c], types[c]) for c in columns} for r in raw_rows]
    table = Path(csv_path).stem

    client = PowerBiClient(os.environ["PBI_TENANT_ID"], os.environ["PBI_CLIENT_ID"], os.environ["PBI_CLIENT_SECRET"])
    definition = [{"name": table, "columns": [{"name": c, "dataType": types[c]} for c in columns], "measures": measures}]
    try:
        dataset = client.create_push_dataset(workspace_id, dataset_name, definition)
        for start in range(0, len(rows), BATCH_SIZE):
            client.add_rows(workspace_id, dataset["id"], table, rows[start : start + BATCH_SIZE])
    except PowerBiError as exc:
        print(f"FAILED: HTTP {exc.status} {exc}")
        return 1
    print(f"OK  dataset {dataset['id']}  table {table}  rows {len(rows)}")
    print("    columns " + ", ".join(f"{c}:{types[c]}" for c in columns))
    print("    measures " + ", ".join(m["name"] for m in measures))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
