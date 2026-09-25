import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "catalog.json"

GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
OBJECT_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
KEY_RE = re.compile(r"^[a-z0-9-]+$")
PLACEHOLDER_GUID = "00000000-0000-0000-0000-000000000000"
MAX_REFRESHES_LIMIT = 8


class CatalogError(ValueError):
    """Raised when the catalog file is malformed. The message names the offending entry."""


def _guid(value: object, where: str) -> None:
    if not isinstance(value, str) or not GUID_RE.match(value):
        raise CatalogError(f"{where}: keine gültige GUID")


def validate_catalog(catalog: dict[str, object]) -> None:
    """
    Check the structure of the catalog, not whether the GUIDs point at real Power BI items.

    Every dataset must declare at least one Impala object in "requires": that list is the only thing
    that decides who may see or use the dataset, so an empty list would make it public.

    :param catalog dict: Parsed catalog.json
    :raises CatalogError: If anything is missing, malformed or points at an unknown dataset
    """
    _guid(catalog.get("agent_workspace_id"), "agent_workspace_id")
    datasets = catalog.get("datasets")
    templates = catalog.get("templates")
    if not isinstance(datasets, dict) or not datasets:
        raise CatalogError("datasets: mindestens ein Dataset erforderlich")
    if not isinstance(templates, dict):
        raise CatalogError("templates: Objekt erforderlich")
    for key, dataset in datasets.items():
        where = f"datasets.{key}"
        if not KEY_RE.match(key):
            raise CatalogError(f"{where}: Schlüssel nur a-z, 0-9 und Bindestrich")
        _guid(dataset.get("workspace_id"), f"{where}.workspace_id")
        _guid(dataset.get("dataset_id"), f"{where}.dataset_id")
        for field in ("name", "description"):
            if not isinstance(dataset.get(field), str) or not dataset[field]:
                raise CatalogError(f"{where}.{field}: Text erforderlich")
        requires = dataset.get("requires")
        if not isinstance(requires, list) or not requires or not all(isinstance(r, str) and OBJECT_RE.match(r) for r in requires):
            raise CatalogError(f"{where}.requires: nicht leere Liste aus db.objekt erforderlich")
        limit = dataset.get("max_refreshes_per_day", 4)
        if not isinstance(limit, int) or not 1 <= limit <= MAX_REFRESHES_LIMIT:
            raise CatalogError(f"{where}.max_refreshes_per_day: Ganzzahl von 1 bis {MAX_REFRESHES_LIMIT}")
    for key, template in templates.items():
        where = f"templates.{key}"
        if not KEY_RE.match(key):
            raise CatalogError(f"{where}: Schlüssel nur a-z, 0-9 und Bindestrich")
        _guid(template.get("workspace_id"), f"{where}.workspace_id")
        _guid(template.get("report_id"), f"{where}.report_id")
        for field in ("name", "description"):
            if not isinstance(template.get(field), str) or not template[field]:
                raise CatalogError(f"{where}.{field}: Text erforderlich")
        keys = template.get("dataset_keys")
        if not isinstance(keys, list) or not keys or any(k not in datasets for k in keys):
            raise CatalogError(f"{where}.dataset_keys: nicht leere Liste bekannter Dataset-Schlüssel erforderlich")


def is_configured(catalog: dict[str, object]) -> bool:
    """
    Tell whether every GUID in the catalog has been replaced with a real value.

    :param catalog dict: A validated catalog
    :return: False while any GUID is still the all-zero placeholder
    """
    return PLACEHOLDER_GUID not in json.dumps(catalog)


def workspace_configured(catalog: dict[str, object]) -> bool:
    """
    Tell whether the agent workspace id has been replaced with a real value.

    :param catalog dict: A validated catalog
    :return: False while the agent workspace is still the all-zero placeholder
    """
    return catalog["agent_workspace_id"] != PLACEHOLDER_GUID


def load_catalog(path: Path | None = None) -> dict[str, object]:
    """
    Read and validate the catalog file.

    :param path Path: Catalog location, defaults to POWERBI_CATALOG_PATH or catalog.json in the repo root
    :return: The validated catalog
    :raises CatalogError: If the file is missing, not JSON or fails validation
    """
    location = path or Path(os.environ.get("POWERBI_CATALOG_PATH", str(DEFAULT_PATH)))
    try:
        catalog = json.loads(location.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogError(f"Katalog {location} nicht lesbar: {exc}") from exc
    validate_catalog(catalog)
    return catalog
