"""SQLite persistence layer — accumulates every analyzed app into one searchable DB."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .extract import LocalIOFinding, SettingEntry, SqlFinding
from .security import SecurityFlag
from .techstack import TechStack

DB_PATH = Path(__file__).parent.parent / "qapv_analyzer.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_path TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    dotnet_target TEXT,
    ui_framework TEXT,
    db_drivers TEXT,
    companion_assemblies TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    name TEXT,
    default_value TEXT,
    is_connection_string INTEGER,
    category TEXT,
    source_file TEXT
);

CREATE TABLE IF NOT EXISTS sql_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    file TEXT,
    class_name TEXT,
    method TEXT,
    kind TEXT,
    category TEXT,
    target TEXT,
    is_stored_procedure INTEGER,
    raw TEXT,
    resolved TEXT,
    parameters TEXT,
    result_columns TEXT
);

CREATE TABLE IF NOT EXISTS io_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    file TEXT,
    class_name TEXT,
    method TEXT,
    operation TEXT,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS security_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    severity TEXT,
    description TEXT,
    location TEXT
);

CREATE TABLE IF NOT EXISTS db_procedures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    schema_name TEXT,
    object_name TEXT,
    status TEXT,
    definition TEXT,
    parameters_json TEXT,
    result_columns_json TEXT
);

CREATE TABLE IF NOT EXISTS db_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id INTEGER NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    schema_name TEXT,
    table_name TEXT,
    columns_json TEXT,
    foreign_keys_json TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name TEXT NOT NULL,
    severity TEXT,
    title TEXT,
    description TEXT,
    created_at TEXT
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Migration: add columns introduced after the table already existed.
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(apps)")}
        if "companion_assemblies" not in existing_cols:
            conn.execute("ALTER TABLE apps ADD COLUMN companion_assemblies TEXT")
        if "db_intro_notes" not in existing_cols:
            conn.execute("ALTER TABLE apps ADD COLUMN db_intro_notes TEXT")
        if "review_status" not in existing_cols:
            conn.execute("ALTER TABLE apps ADD COLUMN review_status TEXT DEFAULT 'borrador'")
        if "review_notes" not in existing_cols:
            conn.execute("ALTER TABLE apps ADD COLUMN review_notes TEXT")
        sql_findings_cols = {row["name"] for row in conn.execute("PRAGMA table_info(sql_findings)")}
        if "parameters" not in sql_findings_cols:
            conn.execute("ALTER TABLE sql_findings ADD COLUMN parameters TEXT")
        if "result_columns" not in sql_findings_cols:
            conn.execute("ALTER TABLE sql_findings ADD COLUMN result_columns TEXT")
        db_procedures_cols = {row["name"] for row in conn.execute("PRAGMA table_info(db_procedures)")}
        if "parameters_json" not in db_procedures_cols:
            conn.execute("ALTER TABLE db_procedures ADD COLUMN parameters_json TEXT")
        if "result_columns_json" not in db_procedures_cols:
            conn.execute("ALTER TABLE db_procedures ADD COLUMN result_columns_json TEXT")


def save_analysis(
    name: str,
    source_path: str,
    tech: TechStack,
    settings: list[SettingEntry],
    sql_findings: list[SqlFinding],
    io_findings: list[LocalIOFinding],
    flags: list[SecurityFlag],
    companion_assemblies: list[str] | None = None,
) -> int:
    with get_conn() as conn:
        # Upsert by name: re-analyzing the same app (same name) replaces its
        # previous analysis instead of piling up duplicates. Cascade delete
        # takes care of its settings/sql_findings/io_findings/security_flags.
        # The manual business-logic review status/notes are a human judgment
        # about the app's behavior, not a byproduct of the extractor — they
        # don't go stale just because we re-ran the scanner, so preserve them
        # across the delete+reinsert instead of resetting to "borrador".
        existing = conn.execute(
            "SELECT review_status, review_notes FROM apps WHERE name = ?", (name,)
        ).fetchone()
        review_status = existing["review_status"] if existing else "borrador"
        review_notes = existing["review_notes"] if existing else None

        conn.execute("DELETE FROM apps WHERE name = ?", (name,))

        cur = conn.execute(
            "INSERT INTO apps (name, source_path, analyzed_at, dotnet_target, ui_framework, db_drivers, "
            "companion_assemblies, review_status, review_notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                source_path,
                datetime.now().isoformat(timespec="seconds"),
                tech.dotnet_target,
                ", ".join(tech.ui_framework),
                ", ".join(tech.db_drivers),
                ", ".join(companion_assemblies or []),
                review_status,
                review_notes,
            ),
        )
        app_id = cur.lastrowid

        conn.executemany(
            "INSERT INTO settings (app_id, name, default_value, is_connection_string, category, source_file) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (app_id, s.name, s.default_value, int(s.is_connection_string), s.category, s.source_file)
                for s in settings
            ],
        )

        conn.executemany(
            "INSERT INTO sql_findings (app_id, file, class_name, method, kind, category, target, "
            "is_stored_procedure, raw, resolved, parameters, result_columns) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    app_id, f.file, f.class_name, f.method, f.kind, f.category, f.target,
                    int(f.is_stored_procedure), f.raw, f.resolved, json.dumps(f.parameters),
                    json.dumps(f.result_columns),
                )
                for f in sql_findings
            ],
        )

        conn.executemany(
            "INSERT INTO io_findings (app_id, file, class_name, method, operation, raw) VALUES (?, ?, ?, ?, ?, ?)",
            [(app_id, f.file, f.class_name, f.method, f.operation, f.raw) for f in io_findings],
        )

        conn.executemany(
            "INSERT INTO security_flags (app_id, severity, description, location) VALUES (?, ?, ?, ?)",
            [(app_id, f.severity, f.description, f.location) for f in flags],
        )

        return app_id


REVIEW_STATUSES = ("borrador", "logica_revisada", "listo_para_migrar")


def set_review(app_id: int, status: str, notes: str) -> None:
    """Records the manual business-logic review pass for an app — a human
    (or Claude-assisted) judgment call about validations/workflows/edge cases
    that no static scanner can fully automate. Purely bookkeeping in our own
    tracking DB; never touches the legacy app itself."""
    if status not in REVIEW_STATUSES:
        raise ValueError(f"Estado invalido: {status}")
    with get_conn() as conn:
        conn.execute(
            "UPDATE apps SET review_status = ?, review_notes = ? WHERE id = ?",
            (status, notes, app_id),
        )


def save_db_objects(
    app_id: int,
    procedures: list[dict],
    tables: list[dict],
    connection_errors: list[str] | None = None,
) -> None:
    """Stores the results of a read-only DB introspection pass (see
    analyzer/enrich.py) for one app. Replaces any previous pass for that app
    so re-running enrichment doesn't pile up duplicates, same as save_analysis."""
    with get_conn() as conn:
        conn.execute("DELETE FROM db_procedures WHERE app_id = ?", (app_id,))
        conn.execute("DELETE FROM db_tables WHERE app_id = ?", (app_id,))
        conn.execute(
            "UPDATE apps SET db_intro_notes = ? WHERE id = ?",
            (" | ".join(connection_errors or []), app_id),
        )

        conn.executemany(
            "INSERT INTO db_procedures (app_id, schema_name, object_name, status, definition, "
            "parameters_json, result_columns_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    app_id, p["schema"], p["name"], p["status"], p.get("definition"),
                    json.dumps(p.get("parameters") or []),
                    json.dumps(p.get("result_columns")) if p.get("result_columns") is not None else None,
                )
                for p in procedures
            ],
        )
        conn.executemany(
            "INSERT INTO db_tables (app_id, schema_name, table_name, columns_json, foreign_keys_json) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    app_id, t["schema"], t["name"],
                    json.dumps(t.get("columns", [])), json.dumps(t.get("foreign_keys", [])),
                )
                for t in tables
            ],
        )


FINDING_SEVERITIES = ("critica", "alta", "media", "info")
_SEVERITY_ORDER = {s: i for i, s in enumerate(FINDING_SEVERITIES)}


def add_finding(app_name: str, severity: str, title: str, description: str) -> int:
    """Cumulative cross-app findings registry — keyed by app NAME, not app_id,
    so findings survive re-analysis (save_analysis's upsert-by-name deletes
    and reinserts the apps row, which would cascade-delete anything FK'd to
    the old app_id). Never touches the legacy app itself — pure bookkeeping."""
    if severity not in FINDING_SEVERITIES:
        raise ValueError(f"Severidad invalida: {severity}")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO findings (app_name, severity, title, description, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (app_name, severity, title, description, datetime.now().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def list_findings() -> list[dict]:
    """All findings, left-joined against the current apps table so a finding
    still displays (with a working link) after its app has been re-analyzed
    and gotten a new id — only loses the link if the app was deleted outright."""
    with get_conn() as conn:
        rows = [
            dict(r) for r in conn.execute(
                """
                SELECT f.id, f.app_name, f.severity, f.title, f.description, f.created_at,
                       a.id AS current_app_id, a.source_path
                FROM findings f
                LEFT JOIN apps a ON a.name = f.app_name
                ORDER BY f.app_name, f.id
                """
            )
        ]
    rows.sort(key=lambda r: _SEVERITY_ORDER.get(r["severity"], 99))
    return rows


def delete_finding(finding_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM findings WHERE id = ?", (finding_id,))


def list_apps() -> list[sqlite3.Row]:
    """Listado usado por la barra lateral (todas las vistas). Incluye sp_count
    (cuantos SqlFinding de categoria 'stored_procedure' tiene la app) para
    mostrar una marca visual de "esta app llama Stored Procedures" sin tener
    que abrir el reporte completo — se calcula desde lo ya extraido del
    codigo, no depende de que la introspeccion de BD haya podido conectarse."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT apps.id, apps.name, apps.source_path, apps.analyzed_at, apps.dotnet_target,
                   apps.ui_framework, apps.db_drivers, apps.review_status,
                   (SELECT COUNT(*) FROM sql_findings sf
                    WHERE sf.app_id = apps.id AND sf.category = 'stored_procedure') AS sp_count
            FROM apps
            ORDER BY apps.analyzed_at DESC
            """
        ).fetchall()


def group_apps_for_sidebar() -> list[dict]:
    """Agrupa list_apps() para la barra lateral usando la convencion de nombre
    'CarpetaRaiz/Modulo' (ver app.py: _batch_name()). Solo una raiz con 2+
    modulos analizados se convierte en un grupo colapsable (hoy: VINS1,
    AFL.Dashboard, INVENTA2-2TEST) — una raiz con un solo modulo se aplana de
    vuelta a un item suelto (agrupar un solo elemento no aporta nada, solo
    agrega un clic extra), deduplicando el nombre visible cuando raiz y
    modulo son literalmente el mismo texto (ej. 'ItemTrack/ItemTrack' ->
    'ItemTrack'). Todo (grupos y apps sueltas por igual) se ordena por
    actividad mas reciente, igual que list_apps() — un grupo usa el
    analyzed_at mas reciente de sus miembros para su posicion."""
    from collections import defaultdict

    apps = [dict(r) for r in list_apps()]
    by_root: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    standalone: list[dict] = []
    for a in apps:
        if "/" in a["name"]:
            root, module = a["name"].split("/", 1)
            by_root[root].append((module, a))
        else:
            standalone.append(a)

    reviewed_statuses = {"logica_revisada", "listo_para_migrar"}
    items: list[dict] = []

    for root, members in by_root.items():
        if len(members) > 1:
            member_list = []
            for module, a in members:
                a2 = dict(a)
                a2["module_display"] = module
                member_list.append(a2)
            reviewed = sum(1 for a in member_list if a["review_status"] in reviewed_statuses)
            items.append({
                "kind": "group",
                "root": root,
                "members": member_list,
                "reviewed_count": reviewed,
                "total_count": len(member_list),
                "sort_key": max(a["analyzed_at"] for a in member_list),
            })
        else:
            module, a = members[0]
            display_name = module if module == root else a["name"]
            items.append({
                "kind": "single", "app": a, "display_name": display_name, "sort_key": a["analyzed_at"],
            })

    for a in standalone:
        items.append({
            "kind": "single", "app": a, "display_name": a["name"], "sort_key": a["analyzed_at"],
        })

    items.sort(key=lambda it: it["sort_key"], reverse=True)
    return items


def get_app(app_id: int) -> dict:
    with get_conn() as conn:
        app = conn.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
        if not app:
            return None
        return {
            "app": dict(app),
            "settings": [dict(r) for r in conn.execute(
                "SELECT * FROM settings WHERE app_id = ? ORDER BY is_connection_string DESC, name", (app_id,)
            )],
            "sql_findings": [dict(r) for r in conn.execute(
                "SELECT * FROM sql_findings WHERE app_id = ? ORDER BY class_name, method", (app_id,)
            )],
            "io_findings": [dict(r) for r in conn.execute(
                "SELECT * FROM io_findings WHERE app_id = ? ORDER BY class_name, method", (app_id,)
            )],
            "security_flags": [dict(r) for r in conn.execute(
                "SELECT * FROM security_flags WHERE app_id = ?", (app_id,)
            )],
            "db_procedures": [dict(r) for r in conn.execute(
                "SELECT * FROM db_procedures WHERE app_id = ? ORDER BY schema_name, object_name", (app_id,)
            )],
            "db_tables": [dict(r) for r in conn.execute(
                "SELECT * FROM db_tables WHERE app_id = ? ORDER BY schema_name, table_name", (app_id,)
            )],
        }


def delete_app(app_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))


def search_by_table(term: str) -> list[sqlite3.Row]:
    """Which apps touch a given table/SP name (substring match)."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT DISTINCT apps.id, apps.name, sql_findings.target, sql_findings.category
            FROM sql_findings
            JOIN apps ON apps.id = sql_findings.app_id
            WHERE sql_findings.target LIKE ?
            ORDER BY apps.name
            """,
            (f"%{term}%",),
        ).fetchall()


def search_by_connection(term: str) -> list[sqlite3.Row]:
    """Which apps share a given connection string value (substring match, e.g. a server or DB name)."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT DISTINCT apps.id, apps.name, settings.name AS setting_name, settings.default_value
            FROM settings
            JOIN apps ON apps.id = settings.app_id
            WHERE settings.is_connection_string = 1 AND settings.default_value LIKE ?
            ORDER BY apps.name
            """,
            (f"%{term}%",),
        ).fetchall()
