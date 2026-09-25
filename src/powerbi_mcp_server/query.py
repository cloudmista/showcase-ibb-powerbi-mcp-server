import os
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import quote

import certifi
from impala.dbapi import connect

from .sql_guard import validate_select

MAX_ROWS = 20_000


class QueryError(RuntimeError):
    """Raised when a query is refused or fails. The message is safe to show the caller."""


def _column_type(values: list[object]) -> str:
    sample = next((v for v in values if v is not None), None)
    if isinstance(sample, bool) or sample is None:
        return "string"
    if isinstance(sample, int):
        return "Int64"
    if isinstance(sample, (float, Decimal)):
        return "Double"
    if isinstance(sample, (date, datetime)):
        return "DateTime"
    return "string"


def _cell(value: object, column_type: str) -> object:
    if value is None:
        return None
    if column_type == "Int64":
        return int(value)
    if column_type == "Double":
        return float(value)
    if column_type == "DateTime":
        return value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    return str(value)


class ImpalaQueryRunner:
    """
    Run one validated SELECT as the acting user, with the same delegation as the SQL server: the service user logs in
    with LDAP and adds doAs, so Ranger authorizes the statement as the acting user.
    """

    def __init__(self, max_rows: int = MAX_ROWS) -> None:
        self._max_rows = max_rows

    def run(self, acting_as_user: str, sql: str) -> tuple[list[str], list[str], list[list[object]]]:
        """
        Execute a read-only query and return typed rows.

        :param acting_as_user str: Trusted username to delegate to
        :param sql str: A single SELECT
        :return: Column names, Power BI column types (Int64, Double, DateTime, string) and rows
        :raises QueryError: If the SQL is refused, fails, returns nothing or returns more rows than allowed
        """
        try:
            validate_select(sql)
        except ValueError as exc:
            raise QueryError(str(exc)) from exc
        password = os.environ.get("IMPALA_PROXY_PASSWORD", "")
        connection = None
        try:
            connection = connect(
                host=os.environ["IMPALA_HOST"],
                port=int(os.environ.get("IMPALA_PORT", "443")),
                use_ssl=True,
                ca_cert=os.environ.get("IMPALA_CA_CERT") or certifi.where(),
                verify_cert=os.environ.get("IMPALA_VERIFY_TLS", "true").lower() != "false",
                use_http_transport=True,
                http_path=f"{os.environ.get('IMPALA_HTTP_PATH', 'cliservice').rstrip('/')}?doAs={quote(acting_as_user, safe='')}",
                auth_mechanism="LDAP",
                user=os.environ["IMPALA_PROXY_USER"],
                password=password,
            )
            cursor = connection.cursor()
            cursor.execute(sql)
            columns = [c[0] for c in (cursor.description or [])]
            fetched = cursor.fetchmany(self._max_rows + 1)
        except Exception as exc:  # noqa: BLE001 - every driver failure becomes one safe message
            message = f"{type(exc).__name__}: {exc}"
            if password:
                message = message.replace(password, "***")
            raise QueryError(f"Abfrage fehlgeschlagen: {message[:300]}") from exc
        finally:
            if connection is not None:
                connection.close()
        if len(fetched) > self._max_rows:
            raise QueryError(f"Die Abfrage liefert mehr als {self._max_rows} Zeilen, bitte stärker aggregieren")
        if not fetched:
            raise QueryError("Die Abfrage liefert keine Zeilen")
        types = [_column_type([row[i] for row in fetched]) for i in range(len(columns))]
        rows = [[_cell(value, types[i]) for i, value in enumerate(row)] for row in fetched]
        return columns, types, rows
