"""SQLite persistence layer — accumulates every analyzed app into one searchable DB."""

import json
import re
import sqlite3
from collections import defaultdict
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
    raw TEXT,
    category TEXT NOT NULL DEFAULT 'io'
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
    created_at TEXT,
    status TEXT NOT NULL DEFAULT 'OPEN',
    status_changed_at TEXT,
    status_changed_by TEXT
);

-- Fase 1 del Validation Framework (VALIDATION_FRAMEWORK.md seccion 4.1).
-- Tabla nueva, vacia hasta que una fase futura instrumente extract.py para
-- generar Unknowns reales -- ver analyzer/unknown.py para la forma
-- conceptual (UnknownRecord) que esta tabla refleja. Keyed por app_name,
-- mismo patron que `findings`, para sobrevivir re-analisis (ADR-0001).
CREATE TABLE IF NOT EXISTS unknowns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name TEXT NOT NULL,
    category TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    impact TEXT NOT NULL,
    evidence_file TEXT,
    evidence_class TEXT,
    evidence_method TEXT,
    evidence_line INTEGER,
    suggested_action TEXT,
    priority TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""

# Read models (ADR-0003 / VISION.md decision #4): capas de agregacion de
# solo lectura sobre las tablas de analisis, pensadas como el contrato que
# consume la interfaz (y, a futuro, cualquier motor cliente-servidor) en vez
# de que cada consumidor repita sus propios JOINs contra las tablas base.
# Se crean/reemplazan en cada init_db() (CREATE VIEW no admite ALTER, asi
# que "migrar" una vista es simplemente recrearla).
VIEWS = """
DROP VIEW IF EXISTS vw_table_dictionary;
CREATE VIEW vw_table_dictionary AS
SELECT
    dt.schema_name,
    dt.table_name,
    dt.columns_json,
    dt.foreign_keys_json,
    a.id AS app_id,
    a.name AS app_name
FROM db_tables dt
JOIN apps a ON a.id = dt.app_id;

DROP VIEW IF EXISTS vw_dependency_graph;
CREATE VIEW vw_dependency_graph AS
SELECT DISTINCT
    a1.id AS app_a_id, a1.name AS app_a_name,
    a2.id AS app_b_id, a2.name AS app_b_name,
    'tabla_o_sp' AS resource_type,
    sf1.target AS resource_name
FROM sql_findings sf1
JOIN sql_findings sf2 ON sf2.target = sf1.target AND sf2.app_id > sf1.app_id
JOIN apps a1 ON a1.id = sf1.app_id
JOIN apps a2 ON a2.id = sf2.app_id
WHERE sf1.target IS NOT NULL AND sf1.category IN ('query', 'stored_procedure');
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # ADR-0003: WAL mejora la concurrencia lectura-mientras-escritura frente
    # al modo rollback-journal por defecto. No resuelve escritura-contra-
    # escritura simultanea -- ver ADR-0003 para el techo real y la politica
    # de evolucion hacia un motor cliente-servidor si la contencion persiste.
    conn.execute("PRAGMA journal_mode = WAL")
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
        # Fase 4 (KNOWN_LIMITATIONS.md L16/L17): discriminador de categoria
        # para io_findings, mismo proposito que sql_findings.category (ya
        # existente). A diferencia de las columnas de Evidence de abajo, este
        # default SI es correcto tambien para filas viejas -- todo
        # LocalIOFinding guardado antes de esta fase era, de hecho, un
        # hallazgo de I/O comun (archivo/impresora/serial/proceso/red); nunca
        # hubo reflection instrumentado antes, asi que 'io' no es una
        # suposicion, es el valor real conocido.
        io_findings_cols = {row["name"] for row in conn.execute("PRAGMA table_info(io_findings)")}
        if "category" not in io_findings_cols:
            conn.execute("ALTER TABLE io_findings ADD COLUMN category TEXT NOT NULL DEFAULT 'io'")

        findings_cols = {row["name"] for row in conn.execute("PRAGMA table_info(findings)")}
        if "status" not in findings_cols:
            conn.execute("ALTER TABLE findings ADD COLUMN status TEXT NOT NULL DEFAULT 'OPEN'")
        if "status_changed_at" not in findings_cols:
            conn.execute("ALTER TABLE findings ADD COLUMN status_changed_at TEXT")
        if "status_changed_by" not in findings_cols:
            conn.execute("ALTER TABLE findings ADD COLUMN status_changed_by TEXT")

        # Fase 1 del Validation Framework (VALIDATION_FRAMEWORK.md seccion
        # 0.2): columnas de Evidence, aditivas y NULLABLE -- NULL en una fila
        # ya existente significa "analizada antes de que esta capacidad
        # existiera", nunca "desconocido" (ver KNOWN_LIMITATIONS.md L9/L23 y
        # Paso 5 de esta fase). No se agrega una columna `source_file` propia
        # en sql_findings/io_findings porque ya existe `file` con el mismo
        # proposito (evidence.source_file mapea a esa columna cuando se
        # conecte en una fase futura); en `settings` ya existe literalmente
        # `source_file`. Ningun extractor escribe en estas columnas todavia
        # -- quedan NULL para toda fila nueva y vieja hasta Fase 2+.
        for table in ("sql_findings", "settings", "io_findings"):
            cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, coltype in (
                ("line_number", "INTEGER"),
                ("snippet", "TEXT"),
                ("extractor", "TEXT"),
                ("pattern", "TEXT"),
                ("confidence", "INTEGER"),
                ("analyzer_version", "TEXT"),
                ("created_at", "TEXT"),
            ):
                if column not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")

        # Las vistas se recrean siempre (DROP + CREATE) en cada arranque, no
        # solo la primera vez -- son baratas de regenerar y asi nunca quedan
        # desincronizadas si su definicion cambia entre versiones del codigo.
        conn.executescript(VIEWS)


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
        #
        # Also check by source_path (the physical .exe/.dll on disk), NOT just
        # name: the same assembly can get analyzed under two different names
        # (e.g. the single-file "/analyze" flow names it "GeoStatsInter", the
        # root-folder "/discover" batch flow used to name the same exe
        # "GeoStatsInter/GeoStatsInter") — without this, that produced two
        # separate rows for one real app instead of one being recognized as a
        # re-analysis of the other. When source_path already exists under a
        # different name, keep that existing name (don't let whichever flow
        # happens to run second silently rename an already-reviewed app).
        existing = conn.execute(
            "SELECT name, review_status, review_notes FROM apps WHERE source_path = ?", (source_path,)
        ).fetchone()
        if existing is None:
            existing = conn.execute(
                "SELECT name, review_status, review_notes FROM apps WHERE name = ?", (name,)
            ).fetchone()
        if existing is not None:
            name = existing["name"]
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
            "INSERT INTO settings (app_id, name, default_value, is_connection_string, category, source_file, "
            "line_number, snippet, extractor, pattern, confidence, analyzer_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    app_id, s.name, s.default_value, int(s.is_connection_string), s.category, s.source_file,
                    s.evidence.line_number, s.evidence.snippet, s.evidence.extractor, s.evidence.pattern,
                    s.evidence.confidence, s.evidence.analyzer_version, s.evidence.created_at,
                )
                for s in settings
            ],
        )

        conn.executemany(
            "INSERT INTO sql_findings (app_id, file, class_name, method, kind, category, target, "
            "is_stored_procedure, raw, resolved, parameters, result_columns, "
            "line_number, snippet, extractor, pattern, confidence, analyzer_version, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    app_id, f.file, f.class_name, f.method, f.kind, f.category, f.target,
                    int(f.is_stored_procedure), f.raw, f.resolved, json.dumps(f.parameters),
                    json.dumps(f.result_columns),
                    f.evidence.line_number, f.evidence.snippet, f.evidence.extractor, f.evidence.pattern,
                    f.evidence.confidence, f.evidence.analyzer_version, f.evidence.created_at,
                )
                for f in sql_findings
            ],
        )

        conn.executemany(
            "INSERT INTO io_findings (app_id, file, class_name, method, operation, raw, category) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(app_id, f.file, f.class_name, f.method, f.operation, f.raw, f.category) for f in io_findings],
        )

        conn.executemany(
            "INSERT INTO security_flags (app_id, severity, description, location) VALUES (?, ?, ?, ?)",
            [(app_id, f.severity, f.description, f.location) for f in flags],
        )

        return app_id


REVIEW_STATUSES = ("borrador", "logica_revisada", "listo_para_migrar", "obsoleta")


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

# Ciclo de vida de un hallazgo (mejora de diseno de bajo impacto, no un ADR
# -- ver conversacion del 2026-08-04 previa a v0.5). Deliberadamente un
# status TEXT con un conjunto cerrado de valores, no una columna booleana
# nueva por cada estado posible (evita la proliferacion de columnas que
# tendriamos si "resolved", "acknowledged", etc. fueran flags separados).
FINDING_STATUSES = ("OPEN", "ACKNOWLEDGED", "RESOLVED", "FALSE_POSITIVE", "IGNORED")


def add_finding(app_name: str, severity: str, title: str, description: str) -> int:
    """Cumulative cross-app findings registry — keyed by app NAME, not app_id,
    so findings survive re-analysis (save_analysis's upsert-by-name deletes
    and reinserts the apps row, which would cascade-delete anything FK'd to
    the old app_id). Never touches the legacy app itself — pure bookkeeping.
    Every finding starts as status='OPEN' — see FINDING_STATUSES."""
    if severity not in FINDING_SEVERITIES:
        raise ValueError(f"Severidad invalida: {severity}")
    with get_conn() as conn:
        now = datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT INTO findings (app_name, severity, title, description, created_at, "
            "status, status_changed_at) VALUES (?, ?, ?, ?, ?, 'OPEN', ?)",
            (app_name, severity, title, description, now, now),
        )
        return cur.lastrowid


def set_finding_status(finding_id: int, status: str, changed_by: str | None = None) -> None:
    """Registra un cambio de estado explicito sobre un hallazgo (Principio 3
    de ARCHITECTURAL_PRINCIPLES.md: ante la incertidumbre, preservar
    evidencia — nunca un hallazgo desaparece o se marca resuelto sin una
    accion explicita y su rastro). `changed_by` es nullable a proposito: no
    hay autenticacion de usuarios todavia, solo se deja el campo listo."""
    if status not in FINDING_STATUSES:
        raise ValueError(f"Estado de hallazgo invalido: {status}")
    with get_conn() as conn:
        conn.execute(
            "UPDATE findings SET status = ?, status_changed_at = ?, status_changed_by = ? WHERE id = ?",
            (status, datetime.now().isoformat(timespec="seconds"), changed_by, finding_id),
        )


def list_findings() -> list[dict]:
    """All findings, left-joined against the current apps table so a finding
    still displays (with a working link) after its app has been re-analyzed
    and gotten a new id — only loses the link if the app was deleted outright."""
    with get_conn() as conn:
        rows = [
            dict(r) for r in conn.execute(
                """
                SELECT f.id, f.app_name, f.severity, f.title, f.description, f.created_at,
                       f.status, f.status_changed_at, f.status_changed_by,
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
    codigo, no depende de que la introspeccion de BD haya podido conectarse.

    sin_bd_detectado (1/0): mismo patron que sp_count -- señal AUTOMATICA
    (no una marca manual) de que el extractor no encontro NINGUNA conexion ni
    hallazgo SQL para esta app. No afirma "esta app es obsoleta" (podria ser
    un launcher/watchdog legitimo sin BD propia, ej. CentiServerMPO) -- solo
    marca "revisar con mas atencion", igual que el caso real que lo motivo
    (LabelControl, una version historica de AFL.Dashboard de antes de que se
    conectara a BD)."""
    with get_conn() as conn:
        return conn.execute(
            """
            SELECT apps.id, apps.name, apps.source_path, apps.analyzed_at, apps.dotnet_target,
                   apps.ui_framework, apps.db_drivers, apps.review_status,
                   (SELECT COUNT(*) FROM sql_findings sf
                    WHERE sf.app_id = apps.id AND sf.category = 'stored_procedure') AS sp_count,
                   (SELECT COUNT(*) FROM settings s WHERE s.app_id = apps.id) = 0
                       AND (SELECT COUNT(*) FROM sql_findings sf2 WHERE sf2.app_id = apps.id) = 0
                       AS sin_bd_detectado
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
    analyzed_at mas reciente de sus miembros para su posicion.

    Las apps marcadas manualmente como obsoletas (review_status='obsoleta',
    ver set_review()) se sacan del listado principal ANTES de agrupar -- no
    compiten por espacio con las apps activas ni participan del agrupamiento
    por raiz (si TODOS los miembros de una raiz quedan obsoletos, como
    LabelControl, esa raiz simplemente deja de aparecer arriba). Van en su
    propia seccion plana al final, sin importar el orden de actividad."""
    apps = [dict(r) for r in list_apps()]
    obsolete_apps = [a for a in apps if a["review_status"] == "obsoleta"]
    apps = [a for a in apps if a["review_status"] != "obsoleta"]

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

    if obsolete_apps:
        obsolete_apps.sort(key=lambda a: a["analyzed_at"], reverse=True)
        items.append({"kind": "obsolete_section", "apps": obsolete_apps})

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


# ---------------------------------------------------------------------------
# Capacidades de portafolio (v0.5, VISION.md seccion 7) — agregaciones de
# solo lectura sobre datos ya extraidos por el pipeline de analisis. Ninguna
# de estas funciones ejecuta extraccion nueva; consumen vw_table_dictionary /
# vw_dependency_graph (ver VIEWS arriba) como su fuente de datos.
# ---------------------------------------------------------------------------

def get_table_dictionary() -> list[dict]:
    """Diccionario de datos consolidado (item 1 del orden de construccion de
    v0.5): una fila por tabla real (schema.tabla), deduplicada entre todas
    las apps que la usan, con la lista de apps y una advertencia explicita
    si dos apps reportan un esquema distinto para la "misma" tabla (senal
    real de drift entre analisis, no un error a ocultar — Principio 3)."""
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM vw_table_dictionary ORDER BY schema_name, table_name, app_name"
        )]

    by_table: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["schema_name"], r["table_name"])
        entry = by_table.setdefault(key, {
            "schema_name": r["schema_name"], "table_name": r["table_name"],
            "apps": [], "columns_json_variants": set(),
        })
        entry["apps"].append({"app_id": r["app_id"], "app_name": r["app_name"]})
        entry["columns_json_variants"].add(r["columns_json"] or "")

    result = []
    for entry in by_table.values():
        variants = entry.pop("columns_json_variants")
        entry["columns"] = json.loads(next(iter(variants))) if variants else []
        entry["schema_consistent"] = len(variants) <= 1
        entry["app_count"] = len(entry["apps"])
        result.append(entry)
    result.sort(key=lambda e: (e["schema_name"], e["table_name"]))
    return result


def get_dependency_graph() -> dict:
    """Grafo de dependencias del portafolio (item 2 del orden de construccion
    de v0.5): que apps comparten una tabla/SP, y que apps comparten servidor
    de base de datos. Dos tipos de arista distintos, calculados por caminos
    distintos: tabla/SP es una agregacion relacional pura (vw_dependency_graph);
    servidor requiere parsear el connection string (reutiliza
    db_introspect.parse_dotnet_connection_string — no se duplica ese regex)."""
    from . import db_introspect

    with get_conn() as conn:
        table_edges = [dict(r) for r in conn.execute(
            "SELECT * FROM vw_dependency_graph ORDER BY app_a_name, app_b_name, resource_name"
        )]
        conn_rows = [dict(r) for r in conn.execute(
            """
            SELECT apps.id AS app_id, apps.name AS app_name, settings.default_value
            FROM settings JOIN apps ON apps.id = settings.app_id
            WHERE settings.category = 'sql_or_oracle'
            """
        )]

    apps_by_server: dict[str, set[tuple[int, str]]] = {}
    for r in conn_rows:
        parsed = db_introspect.parse_dotnet_connection_string(r["default_value"])
        server = (parsed.get("server") or "").strip().lower()
        if not server:
            continue
        apps_by_server.setdefault(server, set()).add((r["app_id"], r["app_name"]))

    connection_edges = []
    seen_pairs = set()
    for server, apps_set in apps_by_server.items():
        if len(apps_set) < 2:
            continue
        apps_sorted = sorted(apps_set, key=lambda t: t[1])
        for i in range(len(apps_sorted)):
            for j in range(i + 1, len(apps_sorted)):
                a, b = apps_sorted[i], apps_sorted[j]
                pair_key = (a[0], b[0], server)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                connection_edges.append({
                    "app_a_id": a[0], "app_a_name": a[1],
                    "app_b_id": b[0], "app_b_name": b[1],
                    "resource_type": "servidor", "resource_name": server,
                })

    return {"table_edges": table_edges, "connection_edges": connection_edges}


# Catalogo de patrones recurrentes (item 3 del orden de construccion de v0.5).
# Categorias definidas a partir del texto real de los 95 hallazgos ya
# registrados (no inventadas) — coincidencia de palabras clave sobre
# titulo+descripcion, deliberadamente simple: no es clustering semantico ni
# NLP, es una heuristica de regex, igual de honesta sobre su propio limite
# que analyzer/extract.py lo es sobre el suyo. Un hallazgo puede coincidir
# con mas de una categoria; el que no coincide con ninguna queda visible en
# "Sin categorizar" en vez de forzarse u ocultarse (Principio 3).
PATTERN_CATEGORIES = {
    "Inyeccion SQL / concatenacion sin parametros": re.compile(
        r"(?i)sql injection|inyecci[oó]n sql|concatenaci[oó]n(?:\s+de\s+strings?)?\s*(?:sin|,)"
    ),
    "Credenciales en texto plano / hardcodeadas": re.compile(
        r"(?i)texto plano|hardcode|password.*(claro|visible)"
    ),
    "Manejo de errores silencioso": re.compile(
        r"(?i)silenc|catch\s+vac[ií]|traga(?:das|n)?\s+excepci|oculta.*(fall|error)"
    ),
    "Riesgo de caida por timer/thread sin manejo de excepciones": re.compile(
        r"(?i)timer.*sin manejo|riesgo de (ca[ií]da|crash)|async void"
    ),
    "Codigo muerto / nunca invocado": re.compile(
        r"(?i)c[oó]digo muerto|nunca (se usa|invocad|se invoca)|sin usar|hu[eé]rfan|inalcanzable"
    ),
    "Falta de transaccion / atomicidad": re.compile(
        r"(?i)sin transacci[oó]n|atomicidad|TransactionScope"
    ),
    "Certificacion de operador nunca validada": re.compile(
        r"(?i)certificaci[oó]n.*(nunca|no se valid)|cert_end_date"
    ),
    "Falso exito / perdida silenciosa de datos": re.compile(
        r"(?i)falso.?exito|p[eé]rdida silenciosa|no inserta nada"
    ),
    "Autenticacion debil / sin re-verificar permisos": re.compile(
        r"(?i)autenticaci[oó]n d[eé]bil|sin (volver a pedir|password de supervisor)|no distingue nivel"
    ),
    "Contaminacion de decompilacion (proyecto ajeno)": re.compile(
        r"(?i)contaminad|Roslyn Compiler Server"
    ),
    "Bypass de autorizacion / control de permisos ausente": re.compile(
        r"(?i)bypass|sin.*(autorizaci[oó]n|control de permisos)"
    ),
}


def get_pattern_catalog() -> list[dict]:
    """Agrupa los hallazgos ya existentes (list_findings()) por categoria
    recurrente conocida (PATTERN_CATEGORIES). Pura sintesis de solo lectura
    sobre datos ya guardados — no analiza nada nuevo, no toca ninguna app."""
    findings = list_findings()
    by_category: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        haystack = f"{f['title']} {f['description']}"
        matched_any = False
        for category, pattern in PATTERN_CATEGORIES.items():
            if pattern.search(haystack):
                by_category[category].append(f)
                matched_any = True
        if not matched_any:
            by_category["Sin categorizar"].append(f)

    result = []
    for category, items in by_category.items():
        apps = sorted({i["app_name"] for i in items})
        result.append({
            "category": category,
            "count": len(items),
            "app_count": len(apps),
            "apps": apps,
            "findings": items,
        })
    result.sort(key=lambda c: (c["category"] == "Sin categorizar", -c["count"]))
    return result


def _bucket_by_percentile(values: list[float], value: float) -> str:
    """Bucketea un valor en Baja/Media/Alta usando terciles calculados sobre
    el portafolio ACTUAL, no umbrales fijos — se recalculan solos conforme
    se analizan mas apps, en vez de numeros magicos que habria que reajustar
    a mano cada vez que crece el inventario."""
    if not values or max(values) == 0:
        return "Baja"
    ordered = sorted(values)
    p33 = ordered[len(ordered) // 3]
    p66 = ordered[(2 * len(ordered)) // 3]
    if value <= p33:
        return "Baja"
    if value <= p66:
        return "Media"
    return "Alta"


# ---------------------------------------------------------------------------
# Priority & Complexity Engine (item 4 del orden de construccion de v0.5 /
# factores de 0.3 en VISION.md). Sintesis de solo lectura sobre datos ya
# existentes (findings, security_flags, sql_findings, db_tables, io_findings,
# vw_dependency_graph, catalogo de patrones) -- no ejecuta extraccion nueva
# sobre ninguna app. Cero IA, cero heuristica opaca: cada factor es una
# cuenta o suma explicita sobre filas reales, con su evidencia adjunta para
# que la recomendacion se pueda auditar hasta la fila de origen (Principio 3).
#
# "Cantidad de usuarios" y "criticidad operacional" NUNCA se calculan aqui --
# no son derivables de ningun archivo .cs ni de la BD introspectada, quedan
# en manos de los analistas funcionales (0 bis de VISION.md) y se muestran
# siempre como PENDIENTE_DE_INFORMACION_DE_NEGOCIO, nunca como 0 o vacio.
# ---------------------------------------------------------------------------

PENDIENTE_DE_INFORMACION_DE_NEGOCIO = "PENDIENTE DE INFORMACION DE NEGOCIO"

# Peso de cada severidad al sumar el factor "riesgo" -- unico lugar donde se
# define. Mismo vocabulario para hallazgos curados (findings) y flags de
# seguridad automaticos (security_flags); security_flags nunca usa 'critica'.
SEVERITY_WEIGHT = {"critica": 3, "alta": 2, "media": 1, "info": 0}

# Peso de cada factor en el score de prioridad final -- unico lugar donde se
# define y documenta. Positivo = sube la prioridad de atenderla pronto;
# negativo = la baja levemente (a igual riesgo/dependencias, conviene
# priorizar primero lo mas simple de resolver -- misma logica de "retorno
# inmediato" de la regla 0.6 del roadmap, aplicada aqui a nivel de app
# individual). Cambiar estos numeros no requiere tocar el resto del motor.
FACTOR_WEIGHTS = {
    "riesgo": 2.0,
    "dependencias": 1.5,
    "reutilizacion_potencial": 0.5,
    "reglas_negocio": 0.5,
    "complejidad_tecnica": -0.5,
    "complejidad_integracion": -0.5,
}

_BUCKET_ORDINAL = {"Baja": 0, "Media": 1, "Alta": 2}

_IO_OPERATION_LABELS = [
    (re.compile(r"^(File|Directory|Stream)"), "Archivos"),
    (re.compile(r"Http|WebClient|WebRequest"), "Red / HTTP"),
    (re.compile(r"Print"), "Impresion"),
    (re.compile(r"SerialPort"), "Puerto serial"),
    (re.compile(r"Process\.Start"), "Proceso externo"),
    (re.compile(r"SmtpClient"), "Correo (SMTP)"),
]


def _label_io_operation(operation: str) -> str:
    for pattern, label in _IO_OPERATION_LABELS:
        if pattern.search(operation or ""):
            return label
    return "Otro"


def _factor_complejidad_tecnica(apps: list[dict], conn) -> dict[int, dict]:
    """Superficie de logica de datos a replicar: cuantas consultas/SP
    distintos detecta el codigo y cuantas tablas reales fueron introspectadas
    desde la BD (evidencia verificable hoy, no una estimacion)."""
    sql_rows = conn.execute(
        "SELECT app_id, target FROM sql_findings WHERE target IS NOT NULL"
    ).fetchall()
    table_rows = conn.execute("SELECT app_id, schema_name, table_name FROM db_tables").fetchall()

    targets_by_app: dict[int, set[str]] = defaultdict(set)
    for r in sql_rows:
        targets_by_app[r["app_id"]].add(r["target"])
    tables_by_app: dict[int, set[str]] = defaultdict(set)
    for r in table_rows:
        tables_by_app[r["app_id"]].add(f"{r['schema_name']}.{r['table_name']}")

    result = {}
    for a in apps:
        targets = sorted(targets_by_app.get(a["id"], set()))
        tables = sorted(tables_by_app.get(a["id"], set()))
        evidence = [f"{len(targets)} consulta(s)/SP distintos detectados en el codigo"]
        if targets:
            evidence[-1] += ": " + ", ".join(targets[:8]) + ("..." if len(targets) > 8 else "")
        evidence.append(f"{len(tables)} tabla(s) con esquema real introspectado desde la BD")
        if tables:
            evidence[-1] += ": " + ", ".join(tables[:8]) + ("..." if len(tables) > 8 else "")
        result[a["id"]] = {"raw": len(targets) + len(tables), "evidence": evidence}
    return result


def _factor_riesgo(apps: list[dict], conn) -> dict[int, dict]:
    """Suma ponderada (SEVERITY_WEIGHT) de hallazgos curados (findings,
    revision de logica de negocio cross-app) + flags de seguridad
    automaticos (security_flags, generados por analyzer/security.py en cada
    analisis) -- las dos fuentes de "riesgo" que ya existen en la BD."""
    findings_rows = conn.execute("SELECT app_name, severity, title FROM findings").fetchall()
    flag_rows = conn.execute("SELECT app_id, severity, description FROM security_flags").fetchall()

    findings_by_name: dict[str, list] = defaultdict(list)
    for r in findings_rows:
        findings_by_name[r["app_name"]].append(r)
    flags_by_app: dict[int, list] = defaultdict(list)
    for r in flag_rows:
        flags_by_app[r["app_id"]].append(r)

    result = {}
    for a in apps:
        fnd = findings_by_name.get(a["name"], [])
        flags = flags_by_app.get(a["id"], [])
        raw = sum(SEVERITY_WEIGHT.get(r["severity"], 0) for r in fnd)
        raw += sum(SEVERITY_WEIGHT.get(r["severity"], 0) for r in flags)
        evidence = [f"Hallazgo [{r['severity']}]: {r['title']}" for r in fnd]
        evidence += [f"Flag de seguridad automatico [{r['severity']}]: {r['description']}" for r in flags]
        result[a["id"]] = {"raw": raw, "evidence": evidence}
    return result


def _factor_dependencias(apps: list[dict], dep_graph: dict) -> dict[int, dict]:
    """Grado de cada app en el grafo de dependencias del portafolio ya
    calculado (get_dependency_graph()) -- tablas/SPs y servidores
    compartidos. No recalcula el grafo, solo lo cuenta por app."""
    edges_by_app: dict[int, list[str]] = defaultdict(list)
    for edge in dep_graph["table_edges"] + dep_graph["connection_edges"]:
        edges_by_app[edge["app_a_id"]].append(
            f"{edge['app_b_name']} (comparte {edge['resource_type']} '{edge['resource_name']}')"
        )
        edges_by_app[edge["app_b_id"]].append(
            f"{edge['app_a_name']} (comparte {edge['resource_type']} '{edge['resource_name']}')"
        )
    return {a["id"]: {"raw": len(edges_by_app.get(a["id"], [])), "evidence": edges_by_app.get(a["id"], [])} for a in apps}


def _factor_reglas_negocio(apps: list[dict], conn) -> dict[int, dict]:
    """Aproximacion honesta (VISION.md 0.3): todavia no existe un catalogo
    estructurado de reglas de negocio (esa es una capacidad futura, v0.9) --
    hasta entonces se cuenta cuantas lineas no vacias tiene la revision de
    logica de negocio (review_notes) como proxy explicito del volumen de
    reglas documentadas. Una app que aun no tuvo esa revision (review_status
    = 'borrador') no tiene evidencia todavia: se marca sin_evidencia en vez
    de asumir un valor de 0 (Principio 3 -- nunca inferir silenciosamente)."""
    rows = conn.execute("SELECT id, review_status, review_notes FROM apps").fetchall()
    by_id = {r["id"]: r for r in rows}

    result = {}
    for a in apps:
        r = by_id.get(a["id"])
        if not r or r["review_status"] == "borrador":
            result[a["id"]] = {
                "raw": None,
                "sin_evidencia": True,
                "evidence": ["Aun no se realizo la revision de logica de negocio de esta app (review_status='borrador')."],
            }
            continue
        n = len([line for line in (r["review_notes"] or "").splitlines() if line.strip()])
        result[a["id"]] = {
            "raw": n,
            "evidence": [
                f"Proxy: {n} linea(s) no vacias documentadas en la revision de logica de negocio "
                f"(review_notes) -- no es un conteo real de reglas discretas, ver limitacion en VISION.md 0.3."
            ],
        }
    return result


def _factor_complejidad_integracion(apps: list[dict], conn) -> dict[int, dict]:
    """Cuantas integraciones externas (archivos, red/HTTP, impresion,
    puerto serial, proceso externo, correo) detecta analyzer/extract.py --
    eje de complejidad distinto al de datos/SQL (VISION.md trata ambos como
    factores separados)."""
    io_rows = conn.execute("SELECT app_id, operation FROM io_findings").fetchall()
    ops_by_app: dict[int, list[str]] = defaultdict(list)
    for r in io_rows:
        ops_by_app[r["app_id"]].append(r["operation"])

    result = {}
    for a in apps:
        ops = ops_by_app.get(a["id"], [])
        by_label: dict[str, int] = defaultdict(int)
        for op in ops:
            by_label[_label_io_operation(op)] += 1
        evidence = [f"{count} integracion(es) de tipo '{label}'" for label, count in sorted(by_label.items(), key=lambda kv: -kv[1])]
        result[a["id"]] = {"raw": len(ops), "evidence": evidence}
    return result


def _factor_reutilizacion_potencial(apps: list[dict], pattern_catalog: list[dict]) -> dict[int, dict]:
    """Deriva la reutilizacion potencial exclusivamente del Catalogo de
    patrones recurrentes (get_pattern_catalog(), no se reanaliza nada aqui):
    si esta app tiene hallazgos en una categoria que TAMBIEN aparece en otras
    apps, resolverla/generalizarla aqui es conocimiento reutilizable en el
    resto del portafolio, no un problema aislado de esta app."""
    by_app: dict[str, list[dict]] = defaultdict(list)
    for category in pattern_catalog:
        if category["category"] == "Sin categorizar" or category["app_count"] < 2:
            continue
        apps_in_category = sorted({f["app_name"] for f in category["findings"]})
        for app_name in apps_in_category:
            others = [n for n in apps_in_category if n != app_name]
            by_app[app_name].append({"categoria": category["category"], "otras_apps": others})

    result = {}
    for a in apps:
        entries = by_app.get(a["name"], [])
        raw = sum(len(e["otras_apps"]) for e in entries)
        evidence = [
            f"Comparte el patron '{e['categoria']}' con {len(e['otras_apps'])} otra(s) app(s): {', '.join(e['otras_apps'])}"
            for e in entries
        ]
        result[a["id"]] = {"raw": raw, "evidence": evidence}
    return result


# Registro de factores automaticos (6 de los 8 acordados en VISION.md 0.3).
# Agregar un factor nuevo en el futuro = escribir una funcion con la firma
# (apps, ctx) -> {app_id: {"raw":.., "evidence":[...]}} y sumar una entrada
# aqui + su peso en FACTOR_WEIGHTS -- el resto del motor (bucketing, score,
# armado de la explicacion) no necesita cambiar (VISION.md seccion "Evolucion
# futura").
FACTOR_CALCULATORS = [
    ("complejidad_tecnica", lambda apps, ctx: _factor_complejidad_tecnica(apps, ctx["conn"])),
    ("riesgo", lambda apps, ctx: _factor_riesgo(apps, ctx["conn"])),
    ("dependencias", lambda apps, ctx: _factor_dependencias(apps, ctx["dep_graph"])),
    ("reglas_negocio", lambda apps, ctx: _factor_reglas_negocio(apps, ctx["conn"])),
    ("complejidad_integracion", lambda apps, ctx: _factor_complejidad_integracion(apps, ctx["conn"])),
    ("reutilizacion_potencial", lambda apps, ctx: _factor_reutilizacion_potencial(apps, ctx["pattern_catalog"])),
]


def get_priority_and_complexity() -> list[dict]:
    """Priority & Complexity Engine (item 4 del orden de construccion de
    v0.5). Combina los 6 factores automaticos de FACTOR_CALCULATORS en una
    recomendacion de prioridad por app, con la evidencia completa de cada
    factor y el desglose exacto del calculo (para que sea rastreable hasta
    la fila de origen, no una caja negra). "Cantidad de usuarios" y
    "criticidad operacional" nunca se calculan -- ver pendiente_negocio."""
    apps = [dict(r) for r in list_apps()]
    dep_graph = get_dependency_graph()
    pattern_catalog = get_pattern_catalog()

    with get_conn() as conn:
        ctx = {"conn": conn, "dep_graph": dep_graph, "pattern_catalog": pattern_catalog}
        factor_values = {key: calc(apps, ctx) for key, calc in FACTOR_CALCULATORS}

    # Terciles por factor calculados solo sobre apps CON evidencia -- una app
    # "sin_evidencia" no debe abaratar artificialmente el umbral de las demas.
    raw_values_by_factor: dict[str, list[float]] = {}
    for key, _ in FACTOR_CALCULATORS:
        raw_values_by_factor[key] = [
            factor_values[key][a["id"]]["raw"]
            for a in apps
            if not factor_values[key].get(a["id"], {}).get("sin_evidencia")
        ]

    rows = []
    for a in apps:
        app_id = a["id"]
        factors = {key: dict(factor_values[key].get(app_id, {"raw": 0, "evidence": []})) for key, _ in FACTOR_CALCULATORS}

        score = 0.0
        breakdown = []
        for key, weight in FACTOR_WEIGHTS.items():
            f = factors[key]
            if f.get("sin_evidencia"):
                f["bucket"] = None
                continue
            bucket = _bucket_by_percentile(raw_values_by_factor[key], f["raw"] or 0)
            f["bucket"] = bucket
            aporte = weight * _BUCKET_ORDINAL[bucket]
            score += aporte
            breakdown.append({"factor": key, "peso": weight, "bucket": bucket, "aporte": round(aporte, 2)})

        rows.append({
            "app_id": app_id,
            "app_name": a["name"],
            "factors": factors,
            "pendiente_negocio": {
                "cantidad_usuarios": PENDIENTE_DE_INFORMACION_DE_NEGOCIO,
                "criticidad_operacional": PENDIENTE_DE_INFORMACION_DE_NEGOCIO,
            },
            "priority_score": round(score, 2),
            "priority_score_breakdown": breakdown,
        })

    priority_vals = [r["priority_score"] for r in rows]
    for r in rows:
        r["prioridad"] = _bucket_by_percentile(priority_vals, r["priority_score"])

    rows.sort(key=lambda r: -r["priority_score"])
    return rows
