import logging

import sqlglot
from sqlglot import exp

logging.getLogger("sqlglot").setLevel(logging.ERROR)

DIALECT = "hive"
DENIED_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.Grant,
    exp.Merge,
    exp.TruncateTable,
)


class SqlValidationError(ValueError):
    """Raised when a query fails validation. Message is safe to show the caller."""


def validate_select(sql: str) -> exp.Expression:
    """
    Parse and validate a single read-only SELECT (CTEs included). Same rules as the SQL server, without the SHOW forms.

    :param sql str: The SQL text to validate
    :return: The parsed expression tree
    :raises SqlValidationError: If the SQL is empty, does not parse, has more than one statement or is not a SELECT
    """
    if not sql or not sql.strip():
        raise SqlValidationError("SQL ist leer")
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise SqlValidationError(f"SQL konnte nicht geparst werden: {exc}") from exc
    if not statements:
        raise SqlValidationError("SQL ist leer")
    if len(statements) > 1:
        raise SqlValidationError("Nur ein einzelnes Statement erlaubt")
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        raise SqlValidationError(f"Nur SELECT ist erlaubt, nicht: {type(tree).__name__}")
    for node in tree.walk():
        if isinstance(node, DENIED_TYPES):
            raise SqlValidationError(f"Statement-Typ nicht erlaubt (verschachtelt): {type(node).__name__}")
    return tree
