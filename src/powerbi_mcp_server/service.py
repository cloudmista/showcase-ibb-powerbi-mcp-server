import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from .catalog import is_configured
from .pbir import Field, build_report_parts
from .powerbi import PowerBiError
from .query import QueryError

log = logging.getLogger("powerbi_mcp_server.audit")

USER_RE = re.compile(r"^[A-Za-z0-9._@-]{1,100}$")
OWNER_TAG_RE = re.compile(r" \[agent:([A-Za-z0-9._@-]+):(\d{4}-\d{2}-\d{2})\]$")
NAME_FORBIDDEN_RE = re.compile(r"[\[\]\x00-\x1f]")
MAX_REPORT_NAME_CHARS = 60
MAX_REPORTS_PER_USER = 10
MIN_REFRESH_INTERVAL = timedelta(minutes=10)
PUSH_BATCH_SIZE = 1000
DATA_TABLE = "Daten"
AGGREGATIONS = {"sum": ("Summe", "SUM"), "average": ("Durchschnitt", "AVERAGE")}
CATALOG_NOT_READY = {"error": "Katalog enthält noch Platzhalter-GUIDs, siehe README", "error_type": "configuration"}


def _denied() -> dict[str, object]:
    """Same answer for a dataset that does not exist and one the user may not use, so nothing leaks."""
    return {"error": "Nicht verfügbar oder keine Berechtigung", "error_type": "denied"}


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def owner_tag(user: str, day: str) -> str:
    """
    Build the suffix that marks a generated report as belonging to a user.

    :param user str: Trusted username
    :param day str: Creation date as YYYY-MM-DD
    :return: Suffix like " [agent:alice:2026-09-25]"
    """
    return f" [agent:{user}:{day}]"


def parse_owner(report_name: str) -> tuple[str, str] | None:
    """
    Read the owner and creation date from a generated report name.

    :param report_name str: Full report name as listed by Power BI
    :return: (user, date) or None if the name carries no tag
    """
    match = OWNER_TAG_RE.search(report_name)
    return (match.group(1), match.group(2)) if match else None


class PowerBiService:
    """
    Business rules of the Power BI MCP server, independent of transport and of the real Power BI API.

    Who may use a dataset is decided by Impala: the user must be allowed to query every object the dataset
    declares in "requires". Power BI is only ever called as the service principal, and only for datasets,
    templates and the one agent workspace listed in the catalog.
    """

    def __init__(
        self,
        catalog: dict[str, object],
        client,
        access_checker,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        fabric=None,
        query_runner=None,
    ) -> None:
        self._catalog = catalog
        self._client = client
        self._checker = access_checker
        self._now = now
        self._fabric = fabric
        self._query_runner = query_runner
        self._agent_workspace = catalog["agent_workspace_id"]

    def _valid_user(self, user: str) -> dict[str, object] | None:
        if not USER_RE.match(user or ""):
            return {"error": "Keine vertrauenswürdige Nutzerkennung vorhanden", "error_type": "validation"}
        return None

    def _visible_datasets(self, user: str) -> dict[str, dict[str, object]]:
        datasets = self._catalog["datasets"]
        needed = {name for dataset in datasets.values() for name in dataset["requires"]}
        permitted = self._checker.allowed_objects(user, needed)
        return {key: d for key, d in datasets.items() if set(d["requires"]) <= permitted}

    def _dataset_for(self, user: str, dataset_key: str) -> dict[str, object] | None:
        return self._visible_datasets(user).get(dataset_key)

    def _own_reports(self, user: str) -> list[dict[str, object]]:
        reports = self._client.list_reports(self._agent_workspace)
        return [r for r in reports if (owner := parse_owner(r.get("name", ""))) and owner[0] == user]

    def _guard(self, tool: str, user: str, call: Callable[[], dict[str, object]]) -> dict[str, object]:
        invalid = self._valid_user(user)
        if invalid:
            return invalid
        try:
            result = call()
        except PowerBiError as exc:
            log.info("tool=%s user=%s decision=error status=%s", tool, user, exc.status)
            error = {"error": str(exc), "error_type": "powerbi"}
            if exc.retry_after:
                error["retry_after"] = exc.retry_after
            return error
        log.info("tool=%s user=%s decision=%s", tool, user, "deny" if result.get("error_type") == "denied" else "allow")
        return result

    def list_datasets(self, user: str) -> dict[str, object]:
        """
        List the datasets the user may use, with their description and compatible report templates.

        :param user str: Trusted username
        :return: {"datasets": [...]} or an error dict
        """
        def call() -> dict[str, object]:
            if not is_configured(self._catalog):
                return CATALOG_NOT_READY
            templates = self._catalog["templates"]
            return {
                "datasets": [
                    {
                        "key": key,
                        "name": d["name"],
                        "description": d["description"],
                        "templates": [t for t, entry in templates.items() if key in entry["dataset_keys"]],
                    }
                    for key, d in self._visible_datasets(user).items()
                ]
            }

        return self._guard("list_powerbi_datasets", user, call)

    def semantic_model(self, user: str, dataset_key: str) -> dict[str, object]:
        """
        Describe a dataset's tables and measures as documented in the catalog.

        Power BI's REST API cannot read a model's structure (Execute Queries rejects INFO functions and DMV
        queries), so the BI team documents the model in the catalog next to the dataset.

        :param user str: Trusted username
        :param dataset_key str: Catalog key of the dataset
        :return: Name, description, tables and measures, or a denied error
        """
        def call() -> dict[str, object]:
            if not is_configured(self._catalog):
                return CATALOG_NOT_READY
            dataset = self._dataset_for(user, dataset_key)
            if dataset is None:
                return _denied()
            return {
                "key": dataset_key,
                "name": dataset["name"],
                "description": dataset["description"],
                "tables": dataset.get("tables", []),
                "measures": dataset.get("measures", []),
            }

        return self._guard("get_powerbi_semantic_model", user, call)

    def list_templates(self, user: str, dataset_key: str | None = None) -> dict[str, object]:
        """
        List the report templates usable with the user's datasets.

        :param user str: Trusted username
        :param dataset_key str: Optional dataset to filter by
        :return: {"templates": [...]} with key, name, description and dataset keys the user may use
        """
        def call() -> dict[str, object]:
            if not is_configured(self._catalog):
                return CATALOG_NOT_READY
            visible = self._visible_datasets(user)
            if dataset_key is not None and dataset_key not in visible:
                return _denied()
            found = []
            for key, t in self._catalog["templates"].items():
                usable = [k for k in t["dataset_keys"] if k in visible]
                if usable and (dataset_key is None or dataset_key in usable):
                    found.append({"key": key, "name": t["name"], "description": t["description"], "dataset_keys": usable})
            return {"templates": found}

        return self._guard("list_report_templates", user, call)

    def create_report(self, user: str, template_key: str, dataset_key: str, report_name: str) -> dict[str, object]:
        """
        Create a report in the agent workspace by cloning a template and binding it to a dataset.

        :param user str: Trusted username
        :param template_key str: Catalog key of the template report
        :param dataset_key str: Catalog key of the dataset to bind
        :param report_name str: Human readable name, tagged with the owner and date by the server
        :return: {report_id, name, web_url} or an error dict
        """
        def call() -> dict[str, object]:
            if not is_configured(self._catalog):
                return CATALOG_NOT_READY
            dataset = self._dataset_for(user, dataset_key)
            template = self._catalog["templates"].get(template_key)
            if dataset is None or template is None or dataset_key not in template["dataset_keys"]:
                return _denied()
            name = NAME_FORBIDDEN_RE.sub("", report_name or "").strip()[:MAX_REPORT_NAME_CHARS]
            if not name:
                return {"error": "Berichtsname fehlt", "error_type": "validation"}
            if len(self._own_reports(user)) >= MAX_REPORTS_PER_USER:
                return {
                    "error": f"Höchstens {MAX_REPORTS_PER_USER} erzeugte Berichte je Nutzer, bitte zuerst einen löschen",
                    "error_type": "limit",
                }
            full_name = name + owner_tag(user, self._now().strftime("%Y-%m-%d"))
            report = self._client.clone_report(
                template["workspace_id"], template["report_id"], full_name, self._agent_workspace, dataset["dataset_id"]
            )
            return {"report_id": report["id"], "name": full_name, "web_url": report.get("webUrl", "")}

        return self._guard("create_powerbi_report", user, call)

    def create_report_from_query(
        self,
        user: str,
        sql: str,
        title: str,
        dimension_column: str,
        measure_column: str,
        date_column: str | None = None,
        aggregation: str = "sum",
    ) -> dict[str, object]:
        """
        Run one SELECT as the user, push exactly its rows into a new push dataset in the agent workspace and create a
        report from the PBIR template: a card and a bar chart of the measure by the dimension and, optionally, a date
        slicer. The data is the user's query result, so Ranger decides what ends up in Power BI.

        :param user str: Trusted username, the query runs as this user
        :param sql str: A single read-only SELECT, aggregated so it stays well below the row limit
        :param title str: Report title, tagged with the owner and date by the server
        :param dimension_column str: Result column shown as categories of the bar chart
        :param measure_column str: Numeric result column that is aggregated
        :param date_column str: Optional result column for a slicer
        :param aggregation str: sum or average
        :return: {report_id, name, web_url, rows} or an error dict
        """
        def call() -> dict[str, object]:
            if self._fabric is None or self._query_runner is None:
                return {"error": "Berichte aus Abfragen sind nicht konfiguriert", "error_type": "configuration"}
            if aggregation not in AGGREGATIONS:
                return {"error": "aggregation muss sum oder average sein", "error_type": "validation"}
            name = NAME_FORBIDDEN_RE.sub("", title or "").strip()[:MAX_REPORT_NAME_CHARS]
            if not name:
                return {"error": "Berichtstitel fehlt", "error_type": "validation"}
            if len(self._own_reports(user)) >= MAX_REPORTS_PER_USER:
                return {
                    "error": f"Höchstens {MAX_REPORTS_PER_USER} erzeugte Berichte je Nutzer, bitte zuerst einen löschen",
                    "error_type": "limit",
                }
            try:
                columns, types, rows = self._query_runner.run(user, sql)
            except QueryError as exc:
                return {"error": str(exc), "error_type": "query"}
            lookup = {c.lower(): i for i, c in enumerate(columns)}
            wanted = {"dimension_column": dimension_column, "measure_column": measure_column}
            if date_column:
                wanted["date_column"] = date_column
            for label, column in wanted.items():
                if (column or "").lower() not in lookup:
                    return {"error": f"{label} {column!r} ist keine Spalte der Abfrage, Spalten: {', '.join(columns)}", "error_type": "validation"}
            dimension, measure = columns[lookup[dimension_column.lower()]], columns[lookup[measure_column.lower()]]
            if types[lookup[measure.lower()]] not in ("Int64", "Double"):
                return {"error": f"measure_column {measure!r} ist nicht numerisch", "error_type": "validation"}
            prefix, dax = AGGREGATIONS[aggregation]
            measure_name = f"{prefix} {measure}"
            escaped = measure.replace("]", "]]")
            full_name = name + owner_tag(user, self._now().strftime("%Y-%m-%d"))
            table = {
                "name": DATA_TABLE,
                "columns": [{"name": c, "dataType": t} for c, t in zip(columns, types)],
                "measures": [{"name": measure_name, "expression": f"{dax}({DATA_TABLE}[{escaped}])"}],
            }
            dataset = self._client.create_push_dataset(self._agent_workspace, full_name, [table])
            try:
                for start in range(0, len(rows), PUSH_BATCH_SIZE):
                    batch = [dict(zip(columns, row)) for row in rows[start : start + PUSH_BATCH_SIZE]]
                    self._client.add_rows(self._agent_workspace, dataset["id"], DATA_TABLE, batch)
                date_field = Field(DATA_TABLE, columns[lookup[date_column.lower()]]) if date_column else None
                parts = build_report_parts(dataset["id"], name, Field(DATA_TABLE, measure_name), Field(DATA_TABLE, dimension), date_field)
                report = self._fabric.create_report(self._agent_workspace, full_name, parts)
            except PowerBiError:
                self._client.delete_dataset(self._agent_workspace, dataset["id"])
                raise
            return {
                "report_id": report["id"],
                "name": name,
                "web_url": f"https://app.powerbi.com/groups/{self._agent_workspace}/reports/{report['id']}",
                "rows": len(rows),
            }

        return self._guard("create_report_from_query", user, call)

    def list_reports(self, user: str) -> dict[str, object]:
        """
        List the reports this user generated.

        :param user str: Trusted username
        :return: {"reports": [{report_id, name, created, web_url}]}
        """
        def call() -> dict[str, object]:
            return {
                "reports": [
                    {
                        "report_id": r["id"],
                        "name": OWNER_TAG_RE.sub("", r["name"]),
                        "created": parse_owner(r["name"])[1],
                        "web_url": r.get("webUrl", ""),
                    }
                    for r in self._own_reports(user)
                ]
            }

        return self._guard("list_my_reports", user, call)

    def report_status(self, user: str, report_id: str) -> dict[str, object]:
        """
        Report the link and the latest refresh of the dataset behind one of the user's generated reports.

        :param user str: Trusted username
        :param report_id str: Report id as returned by create or list
        :return: {report_id, name, web_url, last_refresh} or a denied error
        """
        def call() -> dict[str, object]:
            report = next((r for r in self._own_reports(user) if r["id"] == report_id), None)
            if report is None:
                return _denied()
            dataset = next((d for d in self._catalog["datasets"].values() if d["dataset_id"] == report.get("datasetId")), None)
            last = None
            if dataset is not None:
                history = self._client.refresh_history(dataset["workspace_id"], dataset["dataset_id"], top=1)
                if history:
                    entry = history[0]
                    last = {
                        "status": entry.get("status"),
                        "started": entry.get("startTime"),
                        "finished": entry.get("endTime"),
                        "error": entry.get("serviceExceptionJson"),
                    }
            return {
                "report_id": report_id,
                "name": OWNER_TAG_RE.sub("", report["name"]),
                "web_url": report.get("webUrl", ""),
                "last_refresh": last,
            }

        return self._guard("get_report_status", user, call)

    def delete_report(self, user: str, report_id: str) -> dict[str, object]:
        """
        Delete one of the user's generated reports. Reports of other users and any report outside the agent
        workspace are unreachable.

        :param user str: Trusted username
        :param report_id str: Report id as returned by create or list
        :return: {"deleted": True} or a denied error
        """
        def call() -> dict[str, object]:
            report = next((r for r in self._own_reports(user) if r["id"] == report_id), None)
            if report is None:
                return _denied()
            self._client.delete_report(self._agent_workspace, report_id)
            for dataset in self._client.list_datasets(self._agent_workspace):
                owner = parse_owner(dataset.get("name", ""))
                if dataset.get("name") == report["name"] and owner and owner[0] == user:
                    self._client.delete_dataset(self._agent_workspace, dataset["id"])
            return {"deleted": True}

        return self._guard("delete_generated_report", user, call)

    def refresh_dataset(self, user: str, dataset_key: str) -> dict[str, object]:
        """
        Trigger a refresh of a catalog dataset, within the limits taken from Power BI's own refresh history.

        The history is the source of truth, so the limits hold across restarts and include scheduled refreshes:
        no refresh while one runs, none within ten minutes of the last, and at most the dataset's daily cap in
        the last 24 hours (Power BI allows eight per day on shared capacity, including scheduled ones).

        :param user str: Trusted username
        :param dataset_key str: Catalog key of the dataset
        :return: {refresh_id, status} or an error dict
        """
        def call() -> dict[str, object]:
            if not is_configured(self._catalog):
                return CATALOG_NOT_READY
            dataset = self._dataset_for(user, dataset_key)
            if dataset is None:
                return _denied()
            history = self._client.refresh_history(dataset["workspace_id"], dataset["dataset_id"])
            now = self._now()
            if any(e.get("status") == "Unknown" and not e.get("endTime") for e in history):
                return {"error": "Es läuft bereits ein Refresh", "error_type": "limit"}
            starts = [_parse_time(e["startTime"]) for e in history if e.get("startTime")]
            if starts and now - max(starts) < MIN_REFRESH_INTERVAL:
                return {"error": "Der letzte Refresh liegt weniger als 10 Minuten zurück", "error_type": "limit"}
            cap = dataset.get("max_refreshes_per_day", 4)
            if sum(1 for s in starts if now - s < timedelta(hours=24)) >= cap:
                return {"error": f"Tageslimit von {cap} Refreshes erreicht", "error_type": "limit"}
            request_id = self._client.refresh_dataset(dataset["workspace_id"], dataset["dataset_id"])
            return {"refresh_id": request_id, "status": "Accepted"}

        return self._guard("refresh_powerbi_dataset", user, call)
