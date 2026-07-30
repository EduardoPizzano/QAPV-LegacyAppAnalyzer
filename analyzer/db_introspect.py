"""Read-only introspection against the SQL Server / Oracle databases the
legacy apps connect to.

STRICT INVARIANT: every function in this module issues SELECT-only queries
against system catalogs (sys.*, INFORMATION_SCHEMA.*) or read-only metadata
functions (OBJECT_DEFINITION). Nothing here ever executes a stored procedure,
and nothing here ever issues INSERT/UPDATE/DELETE/ALTER/CREATE/DROP. This
module must never gain a write path — that is the whole point of it existing
separately from the rest of the analyzer.
"""

import re

import pyodbc

CONN_STRING_FIELD = re.compile(
    r"(?i)\b(Server|Data Source|Database|Initial Catalog|User Id|Uid|Password|Pwd)\s*=\s*([^;]*)"
)


def parse_dotnet_connection_string(net_conn_str: str) -> dict:
    """Parses a .NET-style 'Server=X;Database=Y;User Id=Z;Password=W;' string."""
    fields = {}
    for m in CONN_STRING_FIELD.finditer(net_conn_str):
        fields[m.group(1).lower()] = m.group(2).strip()
    return {
        "server": fields.get("server") or fields.get("data source"),
        "database": fields.get("database") or fields.get("initial catalog"),
        "user": fields.get("user id") or fields.get("uid"),
        "password": fields.get("password") or fields.get("pwd"),
    }


def connect(net_conn_str: str, driver: str = "ODBC Driver 17 for SQL Server"):
    """Opens a connection with ApplicationIntent=ReadOnly (a server-side hint).
    The real read-only guarantee is architectural: this module only ever
    builds SELECT statements — never trust the hint alone."""
    parts = parse_dotnet_connection_string(net_conn_str)
    odbc_str = (
        f"DRIVER={{{driver}}};SERVER={parts['server']};DATABASE={parts['database']};"
        f"UID={parts['user']};PWD={parts['password']};ApplicationIntent=ReadOnly;"
        f"Encrypt=no;TrustServerCertificate=yes;"
    )
    return pyodbc.connect(odbc_str, timeout=10)


def get_procedure_definition(conn, schema: str, name: str) -> str | None:
    """Returns the CREATE PROCEDURE source text via OBJECT_DEFINITION (a
    read-only metadata function), or None if the object doesn't exist or the
    login lacks VIEW DEFINITION permission — fails gracefully either way,
    never raises for a permissions issue."""
    cur = conn.cursor()
    cur.execute("SELECT OBJECT_DEFINITION(OBJECT_ID(? + '.' + ?))", (schema, name))
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def get_procedure_parameters(conn, schema: str, name: str) -> list[dict]:
    """Formal input/output parameter list from sys.parameters/sys.types — the
    SP's declared signature (name, type, length, output flag, has-default),
    read from catalog metadata only."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.name AS param_name, t.name AS type_name, p.max_length,
               p.is_output, p.has_default_value, p.default_value
        FROM sys.parameters p
        JOIN sys.types t ON t.user_type_id = p.user_type_id
        WHERE p.object_id = OBJECT_ID(? + '.' + ?)
        ORDER BY p.parameter_id
        """,
        (schema, name),
    )
    return [
        {
            "name": row.param_name,
            "type": row.type_name,
            "max_length": row.max_length,
            "is_output": bool(row.is_output),
            "has_default": bool(row.has_default_value),
            "default": row.default_value,
        }
        for row in cur.fetchall()
    ]


def get_procedure_result_columns(conn, schema: str, name: str) -> list[dict] | None:
    """Best-effort output/result-set shape via sys.dm_exec_describe_first_result_set_for_object
    — a read-only metadata function that STATICALLY ANALYZES the procedure's
    query plan to describe its first result set. It never executes the
    procedure's body. Returns None (not an error) if SQL Server can't
    statically determine the shape (e.g. dynamic SQL, temp tables, multiple
    heterogeneous result sets) — common and expected, not a failure."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT name, column_ordinal, system_type_name, is_nullable "
            "FROM sys.dm_exec_describe_first_result_set_for_object(OBJECT_ID(? + '.' + ?), 0) "
            "ORDER BY column_ordinal",
            (schema, name),
        )
        rows = cur.fetchall()
    except Exception:
        return None
    if not rows:
        return None
    return [
        {"name": row.name, "type": row.system_type_name, "nullable": bool(row.is_nullable)}
        for row in rows
    ]


def get_table_columns(conn, schema: str, table: str) -> list[dict]:
    """Column name/type/nullable/length/default from INFORMATION_SCHEMA.COLUMNS."""
    cur = conn.cursor()
    cur.execute(
        "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, CHARACTER_MAXIMUM_LENGTH, COLUMN_DEFAULT "
        "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? "
        "ORDER BY ORDINAL_POSITION",
        (schema, table),
    )
    return [
        {
            "name": row.COLUMN_NAME,
            "type": row.DATA_TYPE,
            "nullable": row.IS_NULLABLE,
            "max_length": row.CHARACTER_MAXIMUM_LENGTH,
            "default": row.COLUMN_DEFAULT,
        }
        for row in cur.fetchall()
    ]


def list_foreign_keys(conn, schema: str, table: str) -> list[dict]:
    """FK relationships where this table is the child (references another table)."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            fk.name AS fk_name,
            OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS ref_schema,
            OBJECT_NAME(fk.referenced_object_id) AS ref_table,
            COL_NAME(fkc.parent_object_id, fkc.parent_column_id) AS column_name,
            COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id) AS ref_column
        FROM sys.foreign_keys fk
        JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
        WHERE fk.parent_object_id = OBJECT_ID(? + '.' + ?)
        """,
        (schema, table),
    )
    return [
        {
            "fk_name": row.fk_name,
            "ref_table": f"{row.ref_schema}.{row.ref_table}",
            "column": row.column_name,
            "ref_column": row.ref_column,
        }
        for row in cur.fetchall()
    ]
