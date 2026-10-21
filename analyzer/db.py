"""SQLite persistence layer — accumulates every analyzed app into one searchable DB."""

import json
import re
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .__version__ import ANALYZER_VERSION
from .activity import ActivityEvidence
from .artifact import UNKNOWN as ARTIFACT_UNKNOWN
from .artifact import ArtifactEvidence
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

-- ADR-0004: Artifact = el assembly/binario TECNICO analizado -- nunca un
-- archivo .cs, nunca "toda la carpeta decompilada", nunca necesariamente
-- el archivo principal de negocio, y NUNCA una ApplicationIdentity (esa
-- sigue siendo, sin cambios, ADR-0000/0001/0002). binary_hash/source_hash
-- NULL en filas historicas significa "analizada antes de que esta
-- capacidad existiera" -- distinto de binary_hash="UNKNOWN", que significa
-- "se intento calcular y el binario no era accesible" (ver
-- analyzer/artifact.py). anchor_file es metadata analitica de
-- comparacion, NUNCA la definicion de identidad del Artifact.
CREATE TABLE IF NOT EXISTS artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    binary_hash TEXT,
    source_hash TEXT,
    build_date TEXT,
    assembly_version TEXT,
    product_version TEXT,
    file_version TEXT,
    anchor_file TEXT
);

-- Relacion TECNICA entre dos Artifacts -- NUNCA decide ApplicationIdentity
-- por si sola (ADR-0004, politica de resolucion humana: eso sigue
-- requiriendo confirmacion humana explicita via human_resolution_state).
-- artifact_a_id < artifact_b_id (impuesto por CHECK) para que (A,B) y (B,A)
-- nunca se registren como dos filas logicamente duplicadas.
CREATE TABLE IF NOT EXISTS artifact_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_a_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    artifact_b_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL,
    evidence TEXT,
    confidence INTEGER NOT NULL,
    detection_method TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    human_resolution_state TEXT,
    CHECK (artifact_a_id < artifact_b_id),
    UNIQUE (artifact_a_id, artifact_b_id)
);

-- Data Lineage (2026-09): un "lineage_run" es una corrida completa y ATOMICA
-- del Build/Refresh (analyzer/data_lineage.run_lineage_build()) -- NUNCA se
-- sobreescribe una corrida anterior, cada refresh crea una fila nueva aqui y
-- sus propias filas en lineage_edges/lineage_app_classifications/
-- lineage_object_stats, todas scoped por lineage_run_id. "cual corrida es la
-- vigente" tiene UN SOLO punto de decision: get_latest_completed_lineage_run()
-- -- ninguna vista/consulta lo decide por su cuenta (Principio 1: el grafo es
-- regenerable, se reemplaza en cada refresh; nunca se edita a mano).
CREATE TABLE IF NOT EXISTS lineage_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    app_count INTEGER,
    edge_count INTEGER,
    triggered_by TEXT,
    error_message TEXT,
    analyzer_version TEXT
);

-- Una arista app->app sobre un objeto de datos (tabla/SP/paquete Oracle),
-- derivada de la MISMA clasificacion de analyzer/data_flow.py -- nunca una
-- segunda clasificacion. confidence/confidence_label siguen la banda
-- centralizada de analyzer/confidence.py (CONFIRMED/PROBABLE/POSSIBLE/
-- NOT_CONFIRMED), nunca un corte propio.
CREATE TABLE IF NOT EXISTS lineage_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lineage_run_id INTEGER NOT NULL REFERENCES lineage_runs(id) ON DELETE CASCADE,
    from_app_id INTEGER REFERENCES apps(id) ON DELETE CASCADE,
    from_app_name TEXT NOT NULL,
    to_app_id INTEGER REFERENCES apps(id) ON DELETE CASCADE,
    to_app_name TEXT NOT NULL,
    object_name TEXT NOT NULL,
    object_kind TEXT NOT NULL,
    operation TEXT,
    server_kind TEXT NOT NULL DEFAULT 'unknown',
    server_name TEXT,
    database_name TEXT,
    is_oracle_erp_table INTEGER NOT NULL DEFAULT 0,
    confidence INTEGER NOT NULL,
    confidence_label TEXT NOT NULL,
    detection_method TEXT NOT NULL,
    evidence_json TEXT,
    observed_at TEXT NOT NULL
);

-- Clasificacion de linaje por app para UNA corrida (DIRECT_ORACLE/
-- ORACLE_DERIVED/DOWNSTREAM_CONSUMER/INDIRECT_DOWNSTREAM/UNRELATED) --
-- ver analyzer/data_lineage.py para la regla de nivel/BFS que la produce.
CREATE TABLE IF NOT EXISTS lineage_app_classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lineage_run_id INTEGER NOT NULL REFERENCES lineage_runs(id) ON DELETE CASCADE,
    app_id INTEGER REFERENCES apps(id) ON DELETE CASCADE,
    app_name TEXT NOT NULL,
    classification TEXT NOT NULL,
    lineage_level INTEGER,
    origin_app_name TEXT,
    fan_in INTEGER NOT NULL DEFAULT 0,
    fan_out INTEGER NOT NULL DEFAULT 0,
    is_critical_app INTEGER NOT NULL DEFAULT 0,
    evidence_summary TEXT
);

-- Estadisticas de fan-in/fan-out por objeto de datos para UNA corrida --
-- base de "Critical / Hub Objects" (fan_in/fan_out altos = data hub).
CREATE TABLE IF NOT EXISTS lineage_object_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lineage_run_id INTEGER NOT NULL REFERENCES lineage_runs(id) ON DELETE CASCADE,
    object_name TEXT NOT NULL,
    object_kind TEXT NOT NULL,
    server_kind TEXT,
    fan_in INTEGER NOT NULL DEFAULT 0,
    fan_out INTEGER NOT NULL DEFAULT 0,
    is_critical_object INTEGER NOT NULL DEFAULT 0,
    is_oracle_erp_table INTEGER NOT NULL DEFAULT 0
);

-- Portafolio Funcional (2026-09) -- "Funcionales en Piso" (VISION.md discusion
-- 2026-09-08/09): los 67 registros de negocio del inventario Excel validado,
-- persistidos por primera vez (antes solo existian como Excel + JSON fuera
-- del repo). Identidad = inventory_application (nombre de negocio), NUNCA un
-- analyzer_app_id -- una fila funcional puede no tener ninguna app tecnica
-- resuelta y sigue siendo una de las 67 igualmente (ver functional_app_mapping
-- para la relacion, nunca aqui). Se recarga completa (replace, no upsert
-- incremental) desde analyzer/reference_data/functional_inventory.json via
-- import_functional_portfolio() -- ese JSON es el registro versionado de las
-- decisiones ya validadas del Human Validation Pack, nunca recalculado por
-- este modulo.
CREATE TABLE IF NOT EXISTS functional_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inventory_application TEXT NOT NULL UNIQUE,
    functional_area_raw TEXT,
    functional_overview TEXT,
    business_owner TEXT,
    integration_notes TEXT,
    source_code_path_excel TEXT,
    cross_functional INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);

-- Relacion N:M entre una fila funcional y una app tecnica de `apps` -- mismo
-- espiritu que artifact_relationships (evidencia + confianza + metodo), pero
-- SIN el CHECK de par ordenado porque esta relacion no es simetrica (funcional
-- -> tecnica, nunca al reves). analyzer_app_name_snapshot se guarda ademas del
-- FK (mismo patron que findings.app_name / lineage_edges.from_app_name):
-- sobrevive si la app tecnica se re-analiza y cambia de id, o si se elimina.
--
-- is_primary distingue DOS preguntas distintas que NUNCA deben mezclarse:
--   is_primary = 1 (SELECTED): la relacion que se usa para clasificar la app
--     tecnica dentro de "Funcionales en Piso" (ASSOCIATED_CONFIRMED/
--     ASSOCIATED_NEEDS_REVIEW, ver vw_technical_app_classification).
--   is_primary = 0 (ALTERNATIVE_CONSIDERED): candidato evaluado y NO elegido.
--     Es evidencia de auditoria (vw_candidates_considered) -- NUNCA participa
--     en la particion tecnica, sin importar su mapping_status.
--
-- mapping_status:
--   CONFIRMED     = esta app tecnica ES la implementacion de esta fila
--                   funcional (match inequivoco o decision humana documentada).
--   NEEDS_REVIEW  = existe relacion confirmada con un conjunto de candidatos
--                   (ej. SHARED_CODEBASE ya probado), pero falta confirmacion
--                   humana de CUAL candidato especifico es el correcto. NUNCA
--                   significa "quizas no hay relacion".
--   UNMAPPED      = sin candidato tecnico resuelto para esta fila funcional.
--
-- El UNIQUE de abajo evita duplicar la misma relacion si
-- import_functional_portfolio() se re-ejecuta.
CREATE TABLE IF NOT EXISTS functional_app_mapping (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    functional_inventory_id INTEGER NOT NULL REFERENCES functional_inventory(id) ON DELETE CASCADE,
    analyzer_app_id INTEGER REFERENCES apps(id) ON DELETE SET NULL,
    analyzer_app_name_snapshot TEXT NOT NULL,
    mapping_method TEXT NOT NULL,
    mapping_confidence TEXT NOT NULL,
    mapping_status TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    evidence TEXT,
    imported_at TEXT NOT NULL,
    UNIQUE (functional_inventory_id, analyzer_app_name_snapshot)
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

-- Aristas de la ULTIMA corrida de Data Lineage completada -- equivalente de
-- vw_dependency_graph pero DIRIGIDA, TIPADA y con confianza. "cual es la
-- corrida vigente" tiene un unico punto de decision (misma subquery que
-- get_latest_completed_lineage_run() usa) para que ninguna consulta ad hoc
-- lo re-derive por su cuenta.
DROP VIEW IF EXISTS vw_lineage_latest_edges;
CREATE VIEW vw_lineage_latest_edges AS
SELECT le.*
FROM lineage_edges le
WHERE le.lineage_run_id = (
    SELECT lr.id FROM lineage_runs lr
    WHERE lr.status = 'completed'
    ORDER BY lr.completed_at DESC, lr.id DESC LIMIT 1
);

-- Vista A (Funcionales en Piso): alias de solo-lectura sobre
-- functional_inventory, para que consumidores futuros no tengan que saber que
-- la tabla base y la "poblacion oficial" son literalmente la misma cosa --
-- pertenecer a Funcionales en Piso NUNCA depende de mapping_status/is_primary
-- (ver comentario de la tabla).
DROP VIEW IF EXISTS vw_functional_floor;
CREATE VIEW vw_functional_floor AS
SELECT * FROM functional_inventory;

-- Particion tecnica de las 137 apps (Vista B/C combinadas para clasificar):
-- CADA fila de `apps` cae en EXACTAMENTE un bucket -- CASE WHEN evalua en
-- orden, asi que la precedencia CONFIRMED > NEEDS_REVIEW > TECHNICAL_REMAINDER
-- es automatica y no requiere logica adicional. Solo mira filas
-- is_primary=1: un candidato alternativo (is_primary=0) NUNCA saca una app de
-- TECHNICAL_REMAINDER, sin importar su mapping_status (ver comentario de
-- functional_app_mapping).
DROP VIEW IF EXISTS vw_technical_app_classification;
CREATE VIEW vw_technical_app_classification AS
SELECT
    a.id AS app_id,
    a.name AS app_name,
    CASE
        WHEN EXISTS (
            SELECT 1 FROM functional_app_mapping m
            WHERE m.analyzer_app_id = a.id AND m.is_primary = 1 AND m.mapping_status = 'CONFIRMED'
        ) THEN 'ASSOCIATED_CONFIRMED'
        WHEN EXISTS (
            SELECT 1 FROM functional_app_mapping m
            WHERE m.analyzer_app_id = a.id AND m.is_primary = 1 AND m.mapping_status = 'NEEDS_REVIEW'
        ) THEN 'ASSOCIATED_NEEDS_REVIEW'
        ELSE 'TECHNICAL_REMAINDER'
    END AS floor_classification
FROM apps a;

-- Vista B (Resto del Portafolio Tecnico): las apps SIN ninguna relacion
-- primaria -- complemento exacto de vw_technical_app_classification. Nunca
-- oculta ni elimina filas de `apps`; es un filtro de lectura, igual que
-- vw_lineage_latest_edges lo es sobre lineage_edges.
DROP VIEW IF EXISTS vw_technical_remainder;
CREATE VIEW vw_technical_remainder AS
SELECT a.*
FROM apps a
WHERE NOT EXISTS (
    SELECT 1 FROM functional_app_mapping m
    WHERE m.analyzer_app_id = a.id AND m.is_primary = 1
);

-- Vista D (Functional <-> Technical Mapping): detalle completo, primarias Y
-- alternativas, navegable en ambos sentidos sin colapsar multiplicidad (1
-- funcional -> N tecnicas, N funcionales -> 1 tecnica).
DROP VIEW IF EXISTS vw_functional_technical_mapping;
CREATE VIEW vw_functional_technical_mapping AS
SELECT
    fi.id AS functional_inventory_id,
    fi.inventory_application,
    fi.cross_functional,
    m.id AS mapping_id,
    m.analyzer_app_id,
    m.analyzer_app_name_snapshot,
    m.mapping_method,
    m.mapping_confidence,
    m.mapping_status,
    m.is_primary,
    m.evidence
FROM functional_app_mapping m
JOIN functional_inventory fi ON fi.id = m.functional_inventory_id;

-- Vista E (Candidates Considered): SOLO alternativas descartadas
-- (is_primary=0) -- responde "que se considero y no se eligio" sin que estas
-- filas puedan filtrarse jamas a la particion tecnica (esa solo lee
-- vw_technical_app_classification / vw_technical_remainder, nunca esta vista).
DROP VIEW IF EXISTS vw_candidates_considered;
CREATE VIEW vw_candidates_considered AS
SELECT * FROM vw_functional_technical_mapping WHERE is_primary = 0;
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

        # Incremento Lifecycle (2026-08-13): evidencia de ciclo de vida de la
        # app -- todas NULLABLE y aditivas, mismo patron que las columnas de
        # Evidence de mas abajo. NULL en una fila ya existente significa
        # "analizada antes de que esta capacidad existiera", NUNCA "sin
        # evidencia" (ese caso real se representa con
        # last_activity_source='no_evidence' + confidence=UNKNOWN, nunca con
        # NULL). build_date es un PROXY (mtime del ensamblado, ver
        # analyzer/activity.py) -- no una fecha de compilacion verificada.
        apps_cols_lifecycle = {row["name"] for row in conn.execute("PRAGMA table_info(apps)")}
        for column, coltype in (
            ("build_date", "TEXT"),
            ("last_activity_date", "TEXT"),
            ("last_activity_source", "TEXT"),
            ("last_activity_confidence", "INTEGER"),
        ):
            if column not in apps_cols_lifecycle:
                conn.execute(f"ALTER TABLE apps ADD COLUMN {column} {coltype}")

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

        # ADR-0004: enlaza un analisis (Deployment, la fila `apps` existente)
        # con el Artifact tecnico que le corresponde -- NULL en filas
        # historicas hasta que se re-analicen o se resuelvan retroactivamente
        # (nunca se asume/inventa un artifact_id para datos viejos).
        apps_cols_artifact = {row["name"] for row in conn.execute("PRAGMA table_info(apps)")}
        if "artifact_id" not in apps_cols_artifact:
            conn.execute("ALTER TABLE apps ADD COLUMN artifact_id INTEGER REFERENCES artifacts(id)")

        # Indices (ADR-0004): busqueda de coincidencia tecnica por hash, y
        # los FK de artifact_relationships/apps.artifact_id -- ninguno es
        # UNIQUE sobre binary_hash/source_hash (ver Paso 3 de la fase de
        # implementacion: "UNKNOWN" no debe forzar unicidad, y los datos
        # historicos incompletos deben poder convivir sin violar constraints).
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_binary_hash ON artifacts(binary_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_source_hash ON artifacts(source_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_relationships_a ON artifact_relationships(artifact_a_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifact_relationships_b ON artifact_relationships(artifact_b_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_artifact_id ON apps(artifact_id)")

        # Data Lineage: indices sobre las 4 tablas nuevas, todas creadas
        # directamente en SCHEMA (tablas nuevas, no requieren ALTER).
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lineage_edges_run ON lineage_edges(lineage_run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lineage_edges_from_app ON lineage_edges(lineage_run_id, from_app_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lineage_edges_to_app ON lineage_edges(lineage_run_id, to_app_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lineage_edges_object ON lineage_edges(lineage_run_id, object_name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lineage_classifications_run ON lineage_app_classifications(lineage_run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lineage_classifications_app ON lineage_app_classifications(app_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lineage_object_stats_run ON lineage_object_stats(lineage_run_id)")

        # Portafolio Funcional: indices sobre las 2 tablas nuevas, creadas
        # directamente en SCHEMA (tablas nuevas, no requieren ALTER).
        conn.execute("CREATE INDEX IF NOT EXISTS idx_functional_app_mapping_inventory ON functional_app_mapping(functional_inventory_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_functional_app_mapping_app ON functional_app_mapping(analyzer_app_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_functional_app_mapping_primary ON functional_app_mapping(analyzer_app_id, is_primary)")

        # Las vistas se recrean siempre (DROP + CREATE) en cada arranque, no
        # solo la primera vez -- son baratas de regenerar y asi nunca quedan
        # desincronizadas si su definicion cambia entre versiones del codigo.
        conn.executescript(VIEWS)


# ADR-0004: taxonomia UNICA de relacion tecnica -- se eligio la forma legible
# ("identical"/"derived"/etc.) sobre las categorias A-E de la investigacion
# de Fase 2/3 porque es auto-descriptiva (no requiere una leyenda externa
# para interpretarla). Nunca se mezclan ambos sistemas.
ARTIFACT_RELATIONSHIP_TYPES = ("identical", "derived", "fork", "variant", "similar", "unknown")

# ADR-0004: estados de resolucion HUMANA sobre si una relacion tecnica
# implica o no la misma ApplicationIdentity -- nunca se autoasigna
# "confirmed_*", coherente con ADR-0002 (Candidate/Resolved siempre
# requieren confirmacion humana explicita).
ARTIFACT_HUMAN_RESOLUTION_STATES = (
    "pending", "confirmed_same_identity", "confirmed_different_identity", "not_applicable",
)


def get_or_create_artifact(conn: sqlite3.Connection, evidence: ArtifactEvidence) -> int:
    """Reutiliza un Artifact existente si su binary_hash coincide EXACTAMENTE
    (evidencia primaria, ADR-0004) -- "UNKNOWN" NUNCA se trata como
    coincidencia entre si (cada analisis con binario inaccesible obtiene su
    propio Artifact, nunca se fusionan por compartir el mismo valor
    centinela). Si no hay binary_hash real pero SI hay source_hash real, se
    reutiliza por esa coincidencia (evidencia secundaria fuerte) -- nunca
    equivale a declarar la misma ApplicationIdentity (eso sigue siendo
    exclusivamente humano, ADR-0002)."""
    if evidence.binary_hash and evidence.binary_hash != ARTIFACT_UNKNOWN:
        row = conn.execute(
            "SELECT id FROM artifacts WHERE binary_hash = ?", (evidence.binary_hash,)
        ).fetchone()
        if row:
            return row["id"]
    elif evidence.source_hash:
        row = conn.execute(
            "SELECT id FROM artifacts WHERE source_hash = ? AND (binary_hash IS NULL OR binary_hash = ?)",
            (evidence.source_hash, ARTIFACT_UNKNOWN),
        ).fetchone()
        if row:
            return row["id"]

    cur = conn.execute(
        "INSERT INTO artifacts (binary_hash, source_hash, build_date, assembly_version, "
        "product_version, file_version, anchor_file) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            evidence.binary_hash, evidence.source_hash, evidence.build_date,
            evidence.assembly_version, evidence.product_version, evidence.file_version,
            evidence.anchor_file,
        ),
    )
    return cur.lastrowid


def save_artifact_relationship(
    artifact_a_id: int,
    artifact_b_id: int,
    relationship_type: str,
    confidence: int,
    detection_method: str,
    observed_at: str,
    evidence: str | None = None,
    human_resolution_state: str | None = None,
) -> int:
    """Persiste una relacion TECNICA entre dos Artifacts (ADR-0004) -- nunca
    decide ApplicationIdentity por si sola. artifact_a_id/artifact_b_id se
    normalizan (menor, mayor) para que (A,B) y (B,A) nunca se registren como
    dos filas logicamente duplicadas (ver CHECK/UNIQUE del esquema)."""
    if relationship_type not in ARTIFACT_RELATIONSHIP_TYPES:
        raise ValueError(f"relationship_type invalido: {relationship_type}")
    if human_resolution_state is not None and human_resolution_state not in ARTIFACT_HUMAN_RESOLUTION_STATES:
        raise ValueError(f"human_resolution_state invalido: {human_resolution_state}")
    if artifact_a_id == artifact_b_id:
        raise ValueError("Un Artifact no puede relacionarse consigo mismo")
    a_id, b_id = sorted((artifact_a_id, artifact_b_id))

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO artifact_relationships (artifact_a_id, artifact_b_id, relationship_type, "
            "evidence, confidence, detection_method, observed_at, human_resolution_state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (a_id, b_id, relationship_type, evidence, confidence, detection_method, observed_at, human_resolution_state),
        )
        return cur.lastrowid


def save_analysis(
    name: str,
    source_path: str,
    tech: TechStack,
    settings: list[SettingEntry],
    sql_findings: list[SqlFinding],
    io_findings: list[LocalIOFinding],
    flags: list[SecurityFlag],
    companion_assemblies: list[str] | None = None,
    build_date: str | None = None,
    activity: ActivityEvidence | None = None,
    artifact_evidence: ArtifactEvidence | None = None,
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

        # Incremento Lifecycle: activity=None (llamador viejo que no lo pasa
        # todavia, ej. una prueba existente) se trata igual que "sin
        # evidencia" -- nunca se inventa una fecha ni una fuente.
        activity = activity or ActivityEvidence()

        # ADR-0004: artifact_evidence=None (llamador viejo, ej. un test
        # existente que no lo pasa todavia) -> artifact_id queda NULL, nunca
        # se inventa un Artifact sin evidencia real. Paso 13 (re-analisis):
        # se recalcula/reutiliza aqui EN CADA llamada -- nunca se reutiliza
        # ciegamente un artifact_id anterior, porque el binario pudo haber
        # cambiado aunque source_path/name/ApplicationIdentity sean iguales.
        artifact_id = get_or_create_artifact(conn, artifact_evidence) if artifact_evidence else None

        cur = conn.execute(
            "INSERT INTO apps (name, source_path, analyzed_at, dotnet_target, ui_framework, db_drivers, "
            "companion_assemblies, review_status, review_notes, build_date, last_activity_date, "
            "last_activity_source, last_activity_confidence, artifact_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                build_date,
                activity.date,
                activity.source,
                activity.confidence,
                artifact_id,
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


def update_lifecycle(app_id: int, build_date: str | None, activity: ActivityEvidence) -> None:
    """Backfill de Lifecycle para una app ya analizada -- actualiza UNICAMENTE
    build_date/last_activity_* a partir del ensamblado ya existente en disco
    (ver analyzer/activity.py), sin volver a decompilar ni a correr
    save_analysis() (que borraria e reinsertaria settings/sql_findings/
    io_findings/security_flags y podria desincronizarlos si el codigo fuente
    cambio desde el ultimo analisis). review_status/review_notes y todo lo
    demas de la fila quedan intocados."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE apps SET build_date = ?, last_activity_date = ?, last_activity_source = ?, "
            "last_activity_confidence = ? WHERE id = ?",
            (build_date, activity.date, activity.source, activity.confidence, app_id),
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


def _floor_classification_by_app_id() -> dict[int, str]:
    """Unica consulta contra vw_technical_app_classification -- fuente
    compartida por group_apps_for_sidebar() y get_portfolio_summary() para
    que el dashboard principal y el sidebar NUNCA puedan divergir en que
    cuenta como Piso/Resto (un solo mecanismo de clasificacion, nunca dos)."""
    with get_conn() as conn:
        return {
            row["app_id"]: row["floor_classification"]
            for row in conn.execute("SELECT app_id, floor_classification FROM vw_technical_app_classification")
        }


def _group_apps_by_root(apps: list[dict]) -> list[dict]:
    """Agrupa una lista de apps (ya filtrada por el llamador) usando la
    convencion de nombre 'CarpetaRaiz/Modulo' (ver app.py: _batch_name()).
    Extraido de group_apps_for_sidebar() (Portafolio Funcional, 2026-09) para
    poder correr el MISMO agrupamiento de forma independiente sobre
    "Operativas en Piso" y "Resto del Portafolio" -- nunca se agrupa primero
    y se clasifica despues, porque una raiz (ej. ServerMPO) puede tener
    miembros con clasificacion tecnica MIXTA (un miembro is_primary=1
    asociado, otro nunca elegido como primary en ningun lado) y el criterio
    de particion exige separar por aplicacion individual, no por grupo.

    Solo una raiz con 2+ modulos EN ESTA LISTA se convierte en un grupo
    colapsable -- si una raiz mixta se parte entre piso/resto, cada lado
    puede terminar con un solo miembro y se aplana a un item suelto
    (comportamiento identico al de antes, aplicado ahora por particion)."""
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


def group_apps_for_sidebar() -> list[dict]:
    """Barra lateral completa: particiona las apps activas (no-obsoletas) en
    "Operativas en Piso" (ASSOCIATED_CONFIRMED + ASSOCIATED_NEEDS_REVIEW,
    ver vw_technical_app_classification) y "Resto del Portafolio"
    (TECHNICAL_REMAINDER) ANTES de agrupar por raiz -- ver _group_apps_by_root
    para por que el orden importa. Cada particion se agrupa exactamente igual
    que antes (mismo helper), asi que el comportamiento visual de grupos/apps
    sueltas dentro de cada seccion no cambia.

    La particion es 100% derivada de functional_app_mapping (is_primary=1) --
    nunca una heuristica nueva de nombre/carpeta. En una BD sin el portafolio
    funcional importado (ej. bases de test sinteticas), TODAS las apps caen
    en TECHNICAL_REMAINDER por definicion de la vista (ninguna tiene mapping
    primario) -- "Operativas en Piso" queda vacia y simplemente no se incluye
    (ver template: una floor_section con count=0 no se renderiza).

    Las apps obsoletas (review_status='obsoleta') se siguen sacando ANTES de
    todo esto, exactamente igual que antes -- no participan de la
    clasificacion de piso/resto, van en su propia seccion al final."""
    apps = [dict(r) for r in list_apps()]
    obsolete_apps = [a for a in apps if a["review_status"] == "obsoleta"]
    apps = [a for a in apps if a["review_status"] != "obsoleta"]

    floor_by_app_id = _floor_classification_by_app_id()

    piso_apps = [a for a in apps if floor_by_app_id.get(a["id"]) in ("ASSOCIATED_CONFIRMED", "ASSOCIATED_NEEDS_REVIEW")]
    resto_apps = [a for a in apps if floor_by_app_id.get(a["id"]) == "TECHNICAL_REMAINDER"]

    items: list[dict] = []
    if piso_apps:
        items.append({
            "kind": "floor_section", "section": "piso", "icon": "🏭",
            "label_key": "Operativas en Piso", "count": len(piso_apps),
            "entries": _group_apps_by_root(piso_apps),
        })
    if resto_apps:
        items.append({
            "kind": "floor_section", "section": "resto", "icon": "🖥",
            "label_key": "Resto del Portafolio", "count": len(resto_apps),
            "entries": _group_apps_by_root(resto_apps),
        })

    if obsolete_apps:
        obsolete_apps.sort(key=lambda a: a["analyzed_at"], reverse=True)
        items.append({"kind": "obsolete_section", "apps": obsolete_apps})

    return items


def get_portfolio_summary() -> dict:
    """Conteos agregados para el dashboard principal (index.html). Pura
    lectura sobre list_apps(), mismo criterio 'reviewed_statuses' que ya usa
    group_apps_for_sidebar() -- una sola fuente de verdad para que decide
    "revisada", no una regla nueva ni duplicada.

    floor_count/rest_count (Portafolio Funcional, 2026-09): MISMA particion
    que group_apps_for_sidebar() -- via _floor_classification_by_app_id(),
    nunca una segunda heuristica -- y sobre las mismas apps NO-obsoletas
    (floor_count + rest_count + obsolete == total, NUNCA
    floor_count + rest_count == total mientras existan obsoletas)."""
    apps = [dict(r) for r in list_apps()]
    reviewed_statuses = {"logica_revisada", "listo_para_migrar"}
    total = len(apps)
    obsolete = sum(1 for a in apps if a["review_status"] == "obsoleta")
    reviewed = sum(1 for a in apps if a["review_status"] in reviewed_statuses)

    floor_by_app_id = _floor_classification_by_app_id()
    active_apps = [a for a in apps if a["review_status"] != "obsoleta"]
    floor_count = sum(
        1 for a in active_apps
        if floor_by_app_id.get(a["id"]) in ("ASSOCIATED_CONFIRMED", "ASSOCIATED_NEEDS_REVIEW")
    )
    rest_count = sum(1 for a in active_apps if floor_by_app_id.get(a["id"]) == "TECHNICAL_REMAINDER")

    return {
        "total": total,
        "reviewed": reviewed,
        "pending": total - obsolete - reviewed,
        "obsolete": obsolete,
        "floor_count": floor_count,
        "rest_count": rest_count,
    }


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


def list_sql_findings_for_targets(target_keys: set[str]) -> list[dict]:
    """Consulta DIRIGIDA (2026-08-19, incremento de rendimiento de Data
    Flow): filas de sql_findings de CUALQUIER app cuyo target coincide
    (case-insensitive, con o sin prefijo "dbo.") con alguna de las claves ya
    normalizadas dadas -- para que analyzer.data_flow pueda construir un
    test_context_index acotado a las tablas de UNA app sin llamar a
    list_apps()+get_app() por cada app del portafolio (ese camino completo
    cuesta ~6s medido contra 117 apps reales; esta consulta es una sola
    ronda). Excluye is_stored_procedure=1, mismo criterio que ya aplica
    build_test_context_index().

    NOTA de normalizacion: el filtro SQL de arriba (UPPER + variante con
    "DBO." como prefijo) es solo un PRE-FILTRO suficientemente amplio para
    ser rapido -- la fuente de verdad de que dos targets son "la misma
    tabla" sigue siendo exclusivamente analyzer.data_flow.normalize_table_key(),
    que el llamador aplica sobre estas filas antes de usarlas (esta funcion
    no reimplementa esa regla, y nunca decide sola que columnas cuentan como
    señal fuerte -- eso sigue siendo responsabilidad unica de
    build_test_context_index()/resolve_data_flow())."""
    if not target_keys:
        return []
    candidates = set(target_keys) | {f"DBO.{k}" for k in target_keys}
    placeholders = ", ".join("?" for _ in candidates)
    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT apps.name AS app_name, sf.target, sf.raw, sf.resolved,
                   sf.result_columns, sf.is_stored_procedure
            FROM sql_findings sf JOIN apps ON apps.id = sf.app_id
            WHERE sf.target IS NOT NULL AND sf.target != ''
              AND UPPER(sf.target) IN ({placeholders})
              AND (sf.is_stored_procedure IS NULL OR sf.is_stored_procedure = 0)
            """,
            tuple(candidates),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_app(app_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))


def get_app_id_by_name(name: str) -> int | None:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM apps WHERE name = ?", (name,)).fetchone()
        return row["id"] if row else None


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


# ---------------------------------------------------------------------------
# Data Lineage (2026-09) -- persistencia de las corridas de
# analyzer.data_lineage.run_lineage_build(). Un "lineage_run" es la unica
# unidad de escritura: nunca se actualiza una corrida ya completada, un
# refresh siempre crea una fila nueva en lineage_runs y sus propias filas
# hijas. get_latest_completed_lineage_run() es el UNICO punto de decision de
# "cual corrida es la vigente" -- toda funcion/ruta de lectura recibe run_id
# explicito, nunca re-deriva "la ultima" por su cuenta.
# ---------------------------------------------------------------------------

LINEAGE_RUN_STATUSES = ("running", "completed", "failed")
LINEAGE_CLASSIFICATIONS = (
    "DIRECT_ORACLE", "ORACLE_DERIVED", "DOWNSTREAM_CONSUMER", "INDIRECT_DOWNSTREAM", "UNRELATED",
)
LINEAGE_CONFIDENCE_LABELS = ("CONFIRMED", "PROBABLE", "POSSIBLE", "NOT_CONFIRMED")


def start_lineage_run(triggered_by: str | None = None) -> int:
    """Abre una corrida nueva en estado 'running' -- si el proceso truena a
    mitad de camino, la fila queda 'running' para siempre (nunca 'completed'
    a medias, Principio 3): quien lea lineage_runs puede distinguir una
    corrida interrumpida de una exitosa."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO lineage_runs (started_at, status, triggered_by, analyzer_version) "
            "VALUES (?, 'running', ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), triggered_by, ANALYZER_VERSION),
        )
        return cur.lastrowid


def complete_lineage_run(run_id: int, app_count: int, edge_count: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE lineage_runs SET status = 'completed', completed_at = ?, app_count = ?, edge_count = ? "
            "WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), app_count, edge_count, run_id),
        )


def fail_lineage_run(run_id: int, error_message: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE lineage_runs SET status = 'failed', completed_at = ?, error_message = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), error_message, run_id),
        )


def save_lineage_edges(run_id: int, edges: list[dict]) -> None:
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO lineage_edges (lineage_run_id, from_app_id, from_app_name, to_app_id, to_app_name, "
            "object_name, object_kind, operation, server_kind, server_name, database_name, "
            "is_oracle_erp_table, confidence, confidence_label, detection_method, evidence_json, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id, e.get("from_app_id"), e["from_app_name"], e.get("to_app_id"), e["to_app_name"],
                    e["object_name"], e["object_kind"], e.get("operation"), e.get("server_kind", "unknown"),
                    e.get("server_name"), e.get("database_name"), int(e.get("is_oracle_erp_table", False)),
                    e["confidence"], e["confidence_label"], e["detection_method"],
                    json.dumps(e.get("evidence")) if e.get("evidence") is not None else None,
                    e.get("observed_at") or datetime.now().isoformat(timespec="seconds"),
                )
                for e in edges
            ],
        )


def save_lineage_classifications(run_id: int, classifications: list[dict]) -> None:
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO lineage_app_classifications (lineage_run_id, app_id, app_name, classification, "
            "lineage_level, origin_app_name, fan_in, fan_out, is_critical_app, evidence_summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id, c.get("app_id"), c["app_name"], c["classification"], c.get("lineage_level"),
                    c.get("origin_app_name"), c.get("fan_in", 0), c.get("fan_out", 0),
                    int(c.get("is_critical_app", False)), c.get("evidence_summary"),
                )
                for c in classifications
            ],
        )


def save_lineage_object_stats(run_id: int, object_stats: list[dict]) -> None:
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO lineage_object_stats (lineage_run_id, object_name, object_kind, server_kind, "
            "fan_in, fan_out, is_critical_object, is_oracle_erp_table) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run_id, o["object_name"], o["object_kind"], o.get("server_kind"),
                    o.get("fan_in", 0), o.get("fan_out", 0),
                    int(o.get("is_critical_object", False)), int(o.get("is_oracle_erp_table", False)),
                )
                for o in object_stats
            ],
        )


def get_latest_completed_lineage_run() -> dict | None:
    """ORDER BY completed_at DESC, id DESC -- dos corridas pueden completarse
    dentro del mismo segundo (datetime.now() con resolucion de segundos), y
    sin un desempate explicito por id la corrida mas reciente no queda
    garantizada como la primera fila devuelta."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM lineage_runs WHERE status = 'completed' ORDER BY completed_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def list_lineage_runs(limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM lineage_runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)
            )
        ]


def get_lineage_edges(
    run_id: int, *, app_id: int | None = None, object_name: str | None = None, direction: str | None = None,
) -> list[dict]:
    """direction='downstream' -> aristas donde app_id es el ORIGEN
    (from_app_id); 'upstream' -> aristas donde app_id es el DESTINO
    (to_app_id); None -> ambas direcciones (o sin filtro de app si app_id
    tampoco se da)."""
    query = "SELECT * FROM lineage_edges WHERE lineage_run_id = ?"
    params: list = [run_id]
    if app_id is not None:
        if direction == "downstream":
            query += " AND from_app_id = ?"
            params.append(app_id)
        elif direction == "upstream":
            query += " AND to_app_id = ?"
            params.append(app_id)
        else:
            query += " AND (from_app_id = ? OR to_app_id = ?)"
            params.extend([app_id, app_id])
    if object_name is not None:
        query += " AND object_name = ?"
        params.append(object_name)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(query, tuple(params))]


def get_lineage_classification(run_id: int, app_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM lineage_app_classifications WHERE lineage_run_id = ? AND app_id = ?",
            (run_id, app_id),
        ).fetchone()
        return dict(row) if row else None


def list_lineage_classifications(run_id: int, *, classification: str | None = None) -> list[dict]:
    query = "SELECT * FROM lineage_app_classifications WHERE lineage_run_id = ?"
    params: list = [run_id]
    if classification is not None:
        query += " AND classification = ?"
        params.append(classification)
    query += " ORDER BY app_name"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(query, tuple(params))]


def get_lineage_object_stats(run_id: int, object_name: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM lineage_object_stats WHERE lineage_run_id = ? AND object_name = ?",
            (run_id, object_name),
        ).fetchone()
        return dict(row) if row else None


def list_critical_lineage_objects(run_id: int, top_n: int | None = None) -> list[dict]:
    query = (
        "SELECT * FROM lineage_object_stats WHERE lineage_run_id = ? AND is_critical_object = 1 "
        "ORDER BY (fan_in + fan_out) DESC"
    )
    if top_n is not None:
        query += f" LIMIT {int(top_n)}"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(query, (run_id,))]


def list_critical_lineage_apps(run_id: int, top_n: int | None = None) -> list[dict]:
    query = (
        "SELECT * FROM lineage_app_classifications WHERE lineage_run_id = ? AND is_critical_app = 1 "
        "ORDER BY (fan_in + fan_out) DESC"
    )
    if top_n is not None:
        query += f" LIMIT {int(top_n)}"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(query, (run_id,))]


def get_unique_app_degree(run_id: int) -> dict[str, dict[int, int]]:
    """Grado REAL en apps unicas conectadas, por deployment (auditoria
    semantica 2026-09-09) -- NUNCA confundir con fan_in/fan_out de
    lineage_app_classifications, que cuentan HOPS (una fila por objeto
    compartido, ver analyzer/data_lineage.py:474-480): si el deployment A
    comparte 5 tablas distintas con el deployment B, eso son 5 hops pero
    1 sola app conectada. Esta funcion deduplica por app_id de origen/destino
    directamente sobre lineage_edges -- no reemplaza fan_in/fan_out, los
    complementa. No es Application Identity: sigue siendo conteo de
    deployments (apps.id), no de aplicaciones de negocio (ver ADR-0000/0004
    -- ese mecanismo no existe hoy)."""
    with get_conn() as conn:
        unique_consumers = {
            row["app_id"]: row["n"] for row in conn.execute(
                "SELECT from_app_id AS app_id, COUNT(DISTINCT to_app_id) AS n "
                "FROM lineage_edges WHERE lineage_run_id = ? AND from_app_id IS NOT NULL "
                "GROUP BY from_app_id",
                (run_id,),
            )
        }
        unique_providers = {
            row["app_id"]: row["n"] for row in conn.execute(
                "SELECT to_app_id AS app_id, COUNT(DISTINCT from_app_id) AS n "
                "FROM lineage_edges WHERE lineage_run_id = ? AND to_app_id IS NOT NULL "
                "GROUP BY to_app_id",
                (run_id,),
            )
        }
    return {"unique_consumers": unique_consumers, "unique_providers": unique_providers}


def get_ecosystem_summary(top_n: int = 5) -> dict:
    """Resumen ejecutivo del Home (2026-09): Oracle Connectivity / Data
    Providers / Data Consumers / High Impact -- TODO derivado de la ULTIMA
    corrida completada de Data Lineage (lineage_app_classifications), nunca
    un calculo paralelo.

    fan_out/fan_in son HOPS (edges), NO aplicaciones -- demostrado con
    evidencia real (auditoria 2026-09-09): SUM(fan_in) == SUM(fan_out) ==
    total de lineage_edges de la corrida, y un caso real (DataTransfer
    v2.49/Release, app_id=432) tiene fan_in=148 hops pero solo 38 apps
    origen UNICAS. data_providers/data_consumers (conteo de deployments con
    fan_out>0 / fan_in>0) SI son correctos a nivel de app -- eso no cambia.
    Lo que SI cambia aqui: top_providers/top_consumers ahora se ordenan y
    muestran por apps unicas conectadas (get_unique_app_degree()), nunca por
    el hop-count crudo, para no presentar un numero de hops como si fuera
    un numero de aplicaciones. El hop-count sigue disponible en cada fila
    (fan_out/fan_in) para quien necesite esa dimension tecnica.

    is_critical_app = el mecanismo de impacto YA EXISTENTE (percentil
    "Alta" de fan_in+fan_out, ver analyzer/data_lineage.py) -- no se
    inventa un score nuevo aqui.

    Sin corrida completada todavia, devuelve run=None y todo en 0/vacio --
    el llamador debe mostrar un estado explicito, nunca ceros disfrazados
    de dato real."""
    run = get_latest_completed_lineage_run()
    empty = {
        "run": None, "oracle_connected": 0, "data_providers": 0, "data_consumers": 0,
        "top_providers": [], "top_consumers": [], "high_impact": [],
    }
    if not run:
        return empty

    classifications = list_lineage_classifications(run["id"])
    oracle_connected = sum(1 for c in classifications if c["classification"] == "DIRECT_ORACLE")
    providers = [dict(c) for c in classifications if c["fan_out"] > 0]
    consumers = [dict(c) for c in classifications if c["fan_in"] > 0]

    degree = get_unique_app_degree(run["id"])
    for c in providers:
        c["unique_consumers"] = degree["unique_consumers"].get(c["app_id"], 0)
    for c in consumers:
        c["unique_providers"] = degree["unique_providers"].get(c["app_id"], 0)

    providers.sort(key=lambda c: (c["unique_consumers"], c["fan_out"]), reverse=True)
    consumers.sort(key=lambda c: (c["unique_providers"], c["fan_in"]), reverse=True)

    return {
        "run": run,
        "oracle_connected": oracle_connected,
        "data_providers": len(providers),
        "data_consumers": len(consumers),
        "top_providers": providers[:top_n],
        "top_consumers": consumers[:top_n],
        "high_impact": list_critical_lineage_apps(run["id"], top_n=top_n),
    }


# ---------------------------------------------------------------------------
# Portafolio Funcional (2026-09) -- "Funcionales en Piso" vs "Resto del
# Portafolio Tecnico". Ver comentarios de SCHEMA (functional_inventory /
# functional_app_mapping) y VIEWS para la semantica completa. Ninguna funcion
# de esta seccion recalcula un mapping -- import_functional_portfolio() carga
# EXACTAMENTE las decisiones ya validadas (Human Validation Pack), tomadas de
# analyzer/reference_data/functional_inventory.json y
# analyzer/reference_data/functional_app_mapping.json.
# ---------------------------------------------------------------------------

FUNCTIONAL_MAPPING_STATUSES = ("CONFIRMED", "NEEDS_REVIEW", "UNMAPPED")
FLOOR_CLASSIFICATIONS = ("ASSOCIATED_CONFIRMED", "ASSOCIATED_NEEDS_REVIEW", "TECHNICAL_REMAINDER")


def import_functional_portfolio(inventory_rows: list[dict], mapping_rows: list[dict]) -> dict:
    """Reemplaza por completo functional_inventory/functional_app_mapping con
    los datos dados -- DELETE + INSERT, nunca un upsert incremental, porque
    esta tabla no se edita a mano: se regenera entera desde el registro
    versionado (mismo espiritu que un lineage_run, ADR de Data Lineage) cada
    vez que se re-ejecuta la carga. Re-ejecutar con el MISMO seed produce
    exactamente el mismo estado (idempotente).

    analyzer_app_id se resuelve por NOMBRE contra `apps` en el momento de la
    carga (nunca se hardcodea un id en el seed) -- si un nombre no existe hoy
    en `apps`, la fila se inserta igual con analyzer_app_id=NULL y se reporta
    en 'unresolved_app_names' para investigacion humana, en vez de fallar
    silenciosamente o inventar una resolucion.

    Devuelve un resumen de conteos para verificacion inmediata por el
    llamador (ver import_functional_portfolio.py)."""
    for row in mapping_rows:
        if row["mapping_status"] not in FUNCTIONAL_MAPPING_STATUSES:
            raise ValueError(f"mapping_status invalido: {row['mapping_status']!r} en {row['inventory_application']!r}")

    now = datetime.now().isoformat(timespec="seconds")
    unresolved_app_names: list[str] = []

    with get_conn() as conn:
        conn.execute("DELETE FROM functional_app_mapping")
        conn.execute("DELETE FROM functional_inventory")

        inventory_id_by_name: dict[str, int] = {}
        for row in inventory_rows:
            cur = conn.execute(
                "INSERT INTO functional_inventory (inventory_application, functional_area_raw, "
                "functional_overview, business_owner, integration_notes, source_code_path_excel, "
                "cross_functional, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["inventory_application"], row.get("functional_area_raw"),
                    row.get("functional_overview"), row.get("business_owner"),
                    row.get("integration_notes"), row.get("source_code_path_excel"),
                    int(row.get("cross_functional", False)), now,
                ),
            )
            inventory_id_by_name[row["inventory_application"]] = cur.lastrowid

        app_id_by_name = {
            r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM apps")
        }

        for row in mapping_rows:
            inv_id = inventory_id_by_name.get(row["inventory_application"])
            if inv_id is None:
                raise ValueError(
                    f"mapping para {row['inventory_application']!r} no tiene fila en functional_inventory "
                    "-- el seed de mapping y el de inventory estan desincronizados"
                )
            app_name = row["analyzer_app_name"]
            analyzer_app_id = app_id_by_name.get(app_name)
            if analyzer_app_id is None:
                unresolved_app_names.append(app_name)
            conn.execute(
                "INSERT INTO functional_app_mapping (functional_inventory_id, analyzer_app_id, "
                "analyzer_app_name_snapshot, mapping_method, mapping_confidence, mapping_status, "
                "is_primary, evidence, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    inv_id, analyzer_app_id, app_name, row["mapping_method"], row["mapping_confidence"],
                    row["mapping_status"], int(row["is_primary"]), row.get("evidence"), now,
                ),
            )

        inventory_count = conn.execute("SELECT COUNT(*) c FROM functional_inventory").fetchone()["c"]
        mapping_count = conn.execute("SELECT COUNT(*) c FROM functional_app_mapping").fetchone()["c"]
        primary_count = conn.execute(
            "SELECT COUNT(*) c FROM functional_app_mapping WHERE is_primary = 1"
        ).fetchone()["c"]

    return {
        "inventory_count": inventory_count,
        "mapping_count": mapping_count,
        "primary_count": primary_count,
        "alternative_count": mapping_count - primary_count,
        "unresolved_app_names": sorted(set(unresolved_app_names)),
    }


def list_functional_floor() -> list[dict]:
    """Vista A: exactamente las filas de functional_inventory -- Funcionales
    en Piso. Nunca filtra por mapping_status/is_primary (ver comentario de la
    tabla)."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM vw_functional_floor ORDER BY inventory_application"
        )]


def get_technical_partition() -> dict:
    """Particion de las 137 apps tecnicas en los 3 buckets (Vistas B/C),
    calculada EXCLUSIVAMENTE desde vw_technical_app_classification (que a su
    vez solo mira filas is_primary=1 -- ver comentario de la vista). Suma de
    los 3 buckets == total de `apps` siempre, por construccion (CASE WHEN
    cubre todos los casos, ELSE incluido)."""
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM vw_technical_app_classification ORDER BY app_name"
        )]
    buckets: dict[str, list[dict]] = {c: [] for c in FLOOR_CLASSIFICATIONS}
    for r in rows:
        buckets[r["floor_classification"]].append(r)
    return {
        "total": len(rows),
        "associated_confirmed": buckets["ASSOCIATED_CONFIRMED"],
        "associated_needs_review": buckets["ASSOCIATED_NEEDS_REVIEW"],
        "technical_remainder": buckets["TECHNICAL_REMAINDER"],
    }


def list_technical_remainder() -> list[dict]:
    """Vista B: apps de `apps` sin ninguna relacion funcional primaria."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM vw_technical_remainder ORDER BY name")]


def list_functional_technical_mapping(
    inventory_application: str | None = None, analyzer_app_id: int | None = None
) -> list[dict]:
    """Vista D: detalle completo (primarias Y alternativas), filtrable por
    fila funcional o por app tecnica, sin colapsar multiplicidad en ningun
    sentido."""
    query = "SELECT * FROM vw_functional_technical_mapping WHERE 1=1"
    params: list = []
    if inventory_application is not None:
        query += " AND inventory_application = ?"
        params.append(inventory_application)
    if analyzer_app_id is not None:
        query += " AND analyzer_app_id = ?"
        params.append(analyzer_app_id)
    query += " ORDER BY inventory_application, is_primary DESC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(query, params)]


def list_candidates_considered() -> list[dict]:
    """Vista E: SOLO candidatos alternativos (is_primary=0) -- que se
    considero y no se eligio. Nunca usada para calcular la particion tecnica."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM vw_candidates_considered ORDER BY inventory_application"
        )]
