import base64
import hashlib
import json
import re
from dataclasses import dataclass

SCHEMA_BASE = "https://developer.microsoft.com/json-schemas/fabric/item/report"
NAME_PATTERN = re.compile(r"^[\w-]+$")
PAGE_WIDTH = 1280
PAGE_HEIGHT = 720


@dataclass(frozen=True)
class Field:
    """
    A reference to a semantic model field, as the report definition addresses it.

    :param entity str: Table name in the semantic model
    :param name str: Column or measure name in that table
    """

    entity: str
    name: str

    @property
    def query_ref(self) -> str:
        return f"{self.entity}.{self.name}"


def _object_name(*seed: str) -> str:
    return hashlib.sha1("|".join(seed).encode()).hexdigest()[:20]


def _source(field: Field, kind: str) -> dict[str, object]:
    return {kind: {"Expression": {"SourceRef": {"Entity": field.entity}}, "Property": field.name}}


def _projection(field: Field, kind: str) -> dict[str, object]:
    return {"field": _source(field, kind), "queryRef": field.query_ref}


def _title(text: str) -> dict[str, object]:
    literal = "'" + text.replace("'", "''") + "'"
    return {"title": [{"properties": {"text": {"expr": {"Literal": {"Value": literal}}}}}]}


def _visual(page: str, key: str, visual_type: str, buckets: dict[str, list[dict[str, object]]], box: tuple[int, int, int, int], title: str) -> tuple[str, dict[str, object]]:
    name = _object_name(page, key)
    x, y, width, height = box
    body = {
        "$schema": f"{SCHEMA_BASE}/definition/visualContainer/2.0.0/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "z": 1000, "height": height, "width": width, "tabOrder": 1000},
        "visual": {
            "visualType": visual_type,
            "query": {"queryState": {bucket: {"projections": projections} for bucket, projections in buckets.items()}},
            "visualContainerObjects": _title(title),
            "drillFilterOtherVisuals": True,
        },
    }
    return f"definition/pages/{page}/visuals/{name}/visual.json", body


def build_report_parts(
    semantic_model_id: str,
    title: str,
    measure: Field,
    dimension: Field,
    date_column: Field | None = None,
) -> list[dict[str, str]]:
    """
    Build the definition parts of a one page report: a card for the measure, a bar chart of the measure by the
    dimension and, if given, a slicer on a date column. The report binds to an existing semantic model by id.

    File names, schema versions and field shapes follow the PBIR documentation of Microsoft Learn and the public
    json-schemas repository, checked against the schemas of report 3.1.0, page 2.0.0, visualContainer 2.0.0,
    pagesMetadata 1.0.0 and versionMetadata 1.0.0.

    :param semantic_model_id str: Id of the semantic model in the target workspace
    :param title str: Page name and the title of the bar chart
    :param measure Field: Measure shown on the card and the bar chart
    :param dimension Field: Column for the bar chart categories
    :param date_column Field: Optional date column for a slicer
    :return: Parts for the Fabric Create Report request, each with path, payload and payloadType
    """
    page = _object_name(semantic_model_id, title, "page")
    files: dict[str, dict[str, object]] = {
        "definition.pbir": {
            "$schema": f"{SCHEMA_BASE}/definitionProperties/2.0.0/schema.json",
            "version": "4.0",
            "datasetReference": {"byConnection": {"connectionString": f"semanticmodelid={semantic_model_id}"}},
        },
        "definition/version.json": {"$schema": f"{SCHEMA_BASE}/definition/versionMetadata/1.0.0/schema.json", "version": "2.0.0"},
        "definition/report.json": {
            "$schema": f"{SCHEMA_BASE}/definition/report/3.1.0/schema.json",
            "themeCollection": {
                "baseTheme": {
                    "name": "CY25SU12",
                    "reportVersionAtImport": {"visual": "2.5.0", "report": "3.1.0", "page": "2.3.0"},
                    "type": "SharedResources",
                }
            },
            "resourcePackages": [
                {
                    "name": "SharedResources",
                    "type": "SharedResources",
                    "items": [{"name": "CY25SU12", "path": "BaseThemes/CY25SU12.json", "type": "BaseTheme"}],
                }
            ],
        },
        "definition/pages/pages.json": {
            "$schema": f"{SCHEMA_BASE}/definition/pagesMetadata/1.0.0/schema.json",
            "pageOrder": [page],
            "activePageName": page,
        },
        f"definition/pages/{page}/page.json": {
            "$schema": f"{SCHEMA_BASE}/definition/page/2.0.0/schema.json",
            "name": page,
            "displayName": title,
            "displayOption": "FitToPage",
            "height": PAGE_HEIGHT,
            "width": PAGE_WIDTH,
        },
    }
    visuals = [
        _visual(page, "card", "card", {"Values": [_projection(measure, "Measure")]}, (40, 40, 300, 140), measure.name),
        _visual(
            page,
            "bar",
            "barChart",
            {"Category": [_projection(dimension, "Column")], "Y": [_projection(measure, "Measure")]},
            (40, 200, 840, 480),
            title,
        ),
    ]
    if date_column is not None:
        visuals.append(_visual(page, "date", "slicer", {"Values": [_projection(date_column, "Column")]}, (920, 40, 320, 140), date_column.name))
    files.update(dict(visuals))

    return [
        {"path": path, "payload": base64.b64encode(json.dumps(body, ensure_ascii=False).encode()).decode(), "payloadType": "InlineBase64"}
        for path, body in files.items()
    ]
