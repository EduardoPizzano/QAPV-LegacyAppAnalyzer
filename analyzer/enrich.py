"""Orchestrates read-only DB introspection for one already-analyzed app: takes
the SP/table names already found in its decompiled code, connects (read-only)
to the connection strings also already found, and fetches their real
definitions from the database itself.

Reuses db_introspect.py's strict SELECT-only functions — this module adds no
new query capability, just wiring: which connection string to use, which
objects to look up, and graceful handling when a lookup fails."""

import re

from . import db, db_introspect

VALID_SQL_OBJECT_NAME = re.compile(r"^\[?(\w+)\]?(?:\.\[?(\w+)\]?)?$")
ORACLE_HINT = re.compile(r"(?i)Data Source\s*=\s*\(|TNS|Oracle")

# Servers confirmed unreachable (checked both over VPN and on-site at the
# plant network on 2026-07-30 — genuinely dead/orphaned, not a routing
# artifact). Skip connecting entirely instead of eating a ~10s TCP handshake
# timeout per app every time enrichment runs; just mark it clearly instead.
KNOWN_UNREACHABLE_SERVERS = {"naamrt-qcs11"}


def _server_from_conn_str(conn_str: str) -> str | None:
    m = re.search(r"(?i)\b(?:Server|Data Source)\s*=\s*([^;]+)", conn_str)
    return m.group(1).strip() if m else None


def _looks_like_sqlserver(conn_str: str) -> bool:
    return bool(re.search(r"(?i)\bServer\s*=|\bUser Id\s*=|\bUid\s*=", conn_str)) and not ORACLE_HINT.search(conn_str)


def _short_error(e: Exception) -> str:
    """Collapses a verbose pyodbc/ODBC driver exception into a short, report-
    friendly note instead of dumping the full multi-line driver message."""
    text = str(e)
    sqlstate = text.split("'")[1] if text.startswith("(") and "'" in text else None
    if sqlstate:
        return f"no se pudo conectar (SQLSTATE {sqlstate}) — revisar con infraestructura/DBA"
    return "no se pudo conectar — revisar con infraestructura/DBA"


def _split_object_name(target: str) -> tuple[str, str] | None:
    m = VALID_SQL_OBJECT_NAME.match(target.strip())
    if not m:
        return None
    if m.group(2):
        return m.group(1), m.group(2)
    return "dbo", m.group(1)


def enrich_app(app_id: int) -> dict:
    """Returns {"procedures": [...], "tables": [...], "connection_errors": [...]}."""
    data = db.get_app(app_id)
    if not data:
        raise ValueError(f"App {app_id} no existe")

    # Use category (not the is_connection_string flag) — some apps' decompiled
    # Settings.cs never had the [SpecialSetting(ConnectionString)] attribute
    # even though the value is clearly a connection string (category already
    # accounts for this via LOOKS_LIKE_DB_CONN), e.g. CopyJDSU's Cx/CxQAPVR2.
    conn_strings = [
        s["default_value"] for s in data["settings"]
        if s["category"] == "sql_or_oracle" and _looks_like_sqlserver(s["default_value"])
    ]

    sp_names = sorted({
        f["target"] for f in data["sql_findings"]
        if f["category"] == "stored_procedure" and f["target"] and _split_object_name(f["target"])
    })
    table_names = sorted({
        f["target"] for f in data["sql_findings"]
        if f["category"] == "query" and f["target"] and _split_object_name(f["target"])
    })

    procedures: list[dict] = []
    tables: list[dict] = []
    connection_errors: list[str] = []

    seen_procs = set()
    seen_tables = set()

    for conn_str in conn_strings:
        server = _server_from_conn_str(conn_str)
        if server and server.lower() in KNOWN_UNREACHABLE_SERVERS:
            connection_errors.append(
                f"Server={server}: servidor conocido como no disponible — no se intento conectar "
                f"(confirmado caido tanto por VPN como en planta, revisar con infraestructura)"
            )
            continue

        try:
            conn = db_introspect.connect(conn_str)
        except Exception as e:
            connection_errors.append(f"{conn_str.split(';')[0]}: {_short_error(e)}")
            continue

        try:
            for sp_name in sp_names:
                schema, name = _split_object_name(sp_name)
                # SQL Server identifiers are case-insensitive by default, but the
                # C# source isn't consistent about casing — dedupe case-insensitively
                # so e.g. "Employees" and "employees" don't produce two rows.
                key = (schema.lower(), name.lower())
                if key in seen_procs:
                    continue
                seen_procs.add(key)
                definition = db_introspect.get_procedure_definition(conn, schema, name)
                sp_parameters = db_introspect.get_procedure_parameters(conn, schema, name)
                result_columns = db_introspect.get_procedure_result_columns(conn, schema, name)
                procedures.append({
                    "schema": schema, "name": name,
                    "status": "ok" if definition else "not_found",
                    "definition": definition,
                    "parameters": sp_parameters,
                    "result_columns": result_columns,
                })

            for table_name in table_names:
                schema, name = _split_object_name(table_name)
                key = (schema.lower(), name.lower())
                if key in seen_tables:
                    continue
                columns = db_introspect.get_table_columns(conn, schema, name)
                if not columns:
                    continue  # not a real table (or no permission) — skip silently, don't mark "seen"
                seen_tables.add(key)
                fks = db_introspect.list_foreign_keys(conn, schema, name)
                tables.append({"schema": schema, "name": name, "columns": columns, "foreign_keys": fks})
        finally:
            conn.close()

    return {"procedures": procedures, "tables": tables, "connection_errors": connection_errors}
