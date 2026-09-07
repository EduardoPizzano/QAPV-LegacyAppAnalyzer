"""Data Lineage (2026-09): recorre la cadena completa Oracle -> app -> tabla
-> app -> tabla -> ... -> consumidor terminal, clasificando cada app segun su
relacion con Oracle y permitiendo Impact Analysis / Critical Objects/Apps.

Regla de integracion (pedida explicitamente): esto NO es una segunda
clasificacion. Reutiliza:
  - analyzer.data_flow.resolve_data_flow()/resolve_app_relations() para la
    direccion productor->consumidor sobre cada tabla (nunca reimplementada).
  - analyzer.server_resolution para "a que servidor/BD pertenece esta
    escritura" y "es una tabla real del ERP Oracle" (nunca reimplementado).
  - analyzer.confidence para el score numerico de cada hop, y
    analyzer.confidence.label_for_score() para la banda CONFIRMED/PROBABLE/
    POSSIBLE/NOT_CONFIRMED.
  - analyzer.unknown.RESOLUTION_STATUSES para el vocabulario de ambiguedad.

Persistencia hibrida (decision explicita del usuario, ver plan aprobado):
  - resolve_lineage_for_app()/resolve_lineage_for_object() son ACOTADAS y
    recalculan en cada llamada (igual patron que /app_map), NUNCA tocan las
    tablas lineage_*.
  - compute_full_lineage_snapshot()/run_lineage_build() son el camino caro,
    portfolio-wide, invocado solo por la accion explicita "Construir/
    Refrescar" -- su resultado se PERSISTE (analyzer/db.py: lineage_runs/
    lineage_edges/lineage_app_classifications/lineage_object_stats) porque
    recorrer el grafo completo en cada request no es viable sobre ~100+ apps.

Modelo de clasificacion (BFS multi-origen por distancia minima de saltos
desde cada app DIRECT_ORACLE -- interpretacion explicita, documentada en el
plan aprobado, no una regla dictada literalmente por el usuario):
  Nivel 0 -> DIRECT_ORACLE       (conexion Oracle directa)
  Nivel 1 -> ORACLE_DERIVED      (consume DIRECTAMENTE un objeto de un DIRECT_ORACLE)
  Nivel 2 -> DOWNSTREAM_CONSUMER
  Nivel >=3 -> INDIRECT_DOWNSTREAM
  Sin ruta -> UNRELATED (nunca inferido, solo cuando el BFS no encuentra camino)
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from . import confidence, data_flow, db, server_resolution

# Distancia maxima de seguridad para el BFS portfolio-wide -- NO es un limite
# de negocio (el portafolio tiene ~100-117 apps, nunca deberia acercarse a
# esto), es una cota contra un bug de grafo mal formado que produjera una
# cadena mas larga que el propio portafolio.
_MAX_SAFETY_DEPTH = 20

_ORACLE_CONNECTION_KIND = "ORACLE_CONNECTION_KIND"
_ORACLE_PACKAGE_CALL = "ORACLE_PACKAGE_CALL"
_ORACLE_CONNECTION_STRING_TNS = "ORACLE_CONNECTION_STRING_TNS"

_LEVEL_TO_CLASSIFICATION = {
    0: "DIRECT_ORACLE",
    1: "ORACLE_DERIVED",
    2: "DOWNSTREAM_CONSUMER",
}


@dataclass(frozen=True)
class OracleOriginEvidence:
    app_id: int
    app_name: str
    detection_method: str
    confidence: int
    confidence_label: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class LineageHop:
    from_app_id: int | None
    from_app_name: str
    to_app_id: int | None
    to_app_name: str
    object_name: str
    object_kind: str  # "table" | "stored_procedure" | "oracle_package_call"
    operation: str | None
    server_kind: str  # "oracle" | "sqlserver" | "localdb" | "unknown"
    server_name: str | None
    database_name: str | None
    is_oracle_erp_table: bool
    confidence: int
    confidence_label: str
    detection_method: str
    evidence: tuple["data_flow.RelationEvidence", ...] = ()
    # Auditoria iteracion 2 (2026-09): resolution_status REAL devuelto por
    # server_resolution para el target del productor, y los kinds de TODOS
    # sus candidatos -- se conservan aqui (nunca solo en server_kind, que ya
    # colapsa a 'unknown' cuando no hay un unico candidato) para que
    # _confidence_for_hop() pueda distinguir "ambiguo Oracle-vs-otro" de
    # "sin evidencia de servidor" al fijar confidence/confidence_label.
    # Default 'resolved'/() para _walk_bounded (Explorer on-demand), que
    # deliberadamente NO hace atribucion de servidor -- ver su docstring.
    resolution_status: str = "resolved"
    candidate_kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class LineagePath:
    hops: tuple[LineageHop, ...]
    terminal: bool


@dataclass(frozen=True)
class LineageTree:
    root_kind: str  # "app" | "object"
    root_name: str
    direction: str  # "upstream" | "downstream" | "both"
    paths: tuple[LineagePath, ...]
    truncated: bool


@dataclass(frozen=True)
class AppClassification:
    app_id: int
    app_name: str
    classification: str
    lineage_level: int | None
    origin_app_name: str | None
    fan_in: int
    fan_out: int
    is_critical_app: bool
    evidence_summary: str


@dataclass(frozen=True)
class ObjectStats:
    object_name: str
    object_kind: str
    server_kind: str | None
    fan_in: int
    fan_out: int
    is_critical_object: bool
    is_oracle_erp_table: bool
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class LineageSnapshot:
    edges: tuple[LineageHop, ...]
    classifications: tuple[AppClassification, ...]
    object_stats: tuple[ObjectStats, ...]

    @property
    def critical_objects(self) -> tuple[ObjectStats, ...]:
        return tuple(o for o in self.object_stats if o.is_critical_object)

    @property
    def critical_apps(self) -> tuple[AppClassification, ...]:
        return tuple(a for a in self.classifications if a.is_critical_app)


@dataclass(frozen=True)
class LineageRunSummary:
    run_id: int
    app_count: int
    edge_count: int


@dataclass(frozen=True)
class ImpactAnalysisResult:
    subject_kind: str  # "app" | "object"
    subject_name: str
    direct_consumers: tuple[str, ...]
    indirect_consumers: tuple[str, ...]
    depth_reached: int


def _confidence_pair(extractor_key: str) -> tuple[int, str]:
    score = confidence.resolve_confidence(extractor_key)
    return score, confidence.label_for_score(score)


def detect_oracle_origin_apps(conn: sqlite3.Connection) -> dict[int, OracleOriginEvidence]:
    """3 senales INDEPENDIENTES de toque directo a Oracle -- nunca vía
    server_resolution.resolve_write_targets() (esa filtra a WRITE_KEYWORDS y
    puede no ver una llamada a paquete Oracle sin verbo de escritura literal).
    Una app con cualquiera de las 3 es DIRECT_ORACLE (nivel 0 del BFS)."""
    signals: dict[int, dict] = {}

    def _entry(app_id: int, app_name: str) -> dict:
        return signals.setdefault(app_id, {"app_name": app_name, "methods": set(), "evidence": []})

    for row in conn.execute(
        "SELECT sf.app_id, a.name AS app_name, sf.file, sf.class_name, sf.method "
        "FROM sql_findings sf JOIN apps a ON a.id = sf.app_id WHERE sf.kind = 'OracleConnection'"
    ):
        e = _entry(row["app_id"], row["app_name"])
        e["methods"].add(_ORACLE_CONNECTION_KIND)
        e["evidence"].append(f"{row['file']}::{row['class_name']}.{row['method']} (OracleConnection)")

    for row in conn.execute(
        "SELECT sf.app_id, a.name AS app_name, sf.file, sf.class_name, sf.method, sf.target "
        "FROM sql_findings sf JOIN apps a ON a.id = sf.app_id WHERE sf.category = 'oracle_package_call'"
    ):
        e = _entry(row["app_id"], row["app_name"])
        e["methods"].add(_ORACLE_PACKAGE_CALL)
        e["evidence"].append(f"{row['file']}::{row['class_name']}.{row['method']} llama {row['target']}")

    for row in conn.execute(
        "SELECT s.app_id, a.name AS app_name, s.name AS setting_name, s.default_value "
        "FROM settings s JOIN apps a ON a.id = s.app_id WHERE s.category = 'sql_or_oracle'"
    ):
        parsed = server_resolution.parse_connection_string(row["default_value"])
        if parsed and parsed.get("kind") == "oracle":
            e = _entry(row["app_id"], row["app_name"])
            e["methods"].add(_ORACLE_CONNECTION_STRING_TNS)
            e["evidence"].append(f"setting '{row['setting_name']}' (TNS: {row['default_value']})")

    score, label = _confidence_pair("LINEAGE_ORACLE_ORIGIN_DIRECT_CONNECTION")
    results: dict[int, OracleOriginEvidence] = {}
    for app_id, data in signals.items():
        results[app_id] = OracleOriginEvidence(
            app_id=app_id,
            app_name=data["app_name"],
            detection_method="+".join(sorted(data["methods"])),
            confidence=score,
            confidence_label=label,
            evidence=tuple(data["evidence"]),
        )
    return results


def build_portfolio_produce_consume_edges(
    edges_by_app: dict[str, list["data_flow.DataFlowEdge"]],
) -> list[LineageHop]:
    """Generalizacion PORTFOLIO-WIDE, sin restriccion a "tablas de una sola
    app", de la MISMA logica de producers_by_table/consumers_by_table que
    data_flow.resolve_app_relations() ya usa de forma acotada -- nunca una
    segunda clasificacion de rol. from_app_id/to_app_id quedan None aqui
    (se resuelven despues, en compute_full_lineage_snapshot, contra la BD)."""
    producers_by_table: dict[str, dict[str, "data_flow.DataFlowEdge"]] = defaultdict(dict)
    consumers_by_table: dict[str, dict[str, "data_flow.DataFlowEdge"]] = defaultdict(dict)
    display_name: dict[str, str] = {}

    for app_name, edges in edges_by_app.items():
        for e in edges:
            key = data_flow.normalize_table_key(e.target)
            if key is None:
                continue
            display_name.setdefault(key, e.target)
            is_producer = e.role in data_flow._PRODUCER_ROLES or (e.role == "mixto" and e.writes)
            is_consumer = e.role in data_flow._CONSUMER_ROLES or (e.role == "mixto" and e.reads)
            if is_producer:
                producers_by_table[key][app_name] = e
            if is_consumer:
                consumers_by_table[key][app_name] = e

    score, label = _confidence_pair("LINEAGE_HOP_STRONG_DATA_FLOW")
    hops: list[LineageHop] = []
    for key, producers in producers_by_table.items():
        consumers = consumers_by_table.get(key, {})
        if not consumers:
            continue
        table_label = display_name.get(key, key)
        for producer_name, producer_edge in producers.items():
            for consumer_name, consumer_edge in consumers.items():
                if producer_name == consumer_name:
                    continue  # self-loop -- caracteristica de esa app, nunca una arista de linaje
                operation = producer_edge.writes[0].operation if producer_edge.writes else None
                hops.append(LineageHop(
                    from_app_id=None, from_app_name=producer_name,
                    to_app_id=None, to_app_name=consumer_name,
                    object_name=table_label, object_kind="table", operation=operation,
                    server_kind="unknown", server_name=None, database_name=None,
                    is_oracle_erp_table=server_resolution.is_oracle_erp_table(table_label),
                    confidence=score, confidence_label=label,
                    detection_method="DATA_FLOW_PRODUCER_CONSUMER",
                    evidence=(data_flow.RelationEvidence(
                        table=table_label, write_ops=producer_edge.writes, read_ops=consumer_edge.reads,
                    ),),
                ))
    return hops


@dataclass(frozen=True)
class ServerAttribution:
    """Auditoria iteracion 2: antes _attribute_server colapsaba cualquier
    resolution_status != 'resolved' a un simple server_kind='unknown',
    descartando la informacion que server_resolution SI calculo (cual era el
    estado exacto, y de que 'kind' eran los candidatos en conflicto). Esa
    informacion es indispensable para que _confidence_for_hop() distinga un
    hop genuinamente ambiguo entre Oracle y otro servidor (que SI amenaza la
    narrativa de linaje Oracle) de uno simplemente sin evidencia de servidor
    (donde la relacion Data Flow sigue siendo igual de fuerte)."""

    server_kind: str
    server_name: str | None
    database_name: str | None
    is_oracle_erp_table: bool
    resolution_status: str
    candidate_kinds: tuple[str, ...]


def _attribute_server(
    app_id: int | None,
    table_key: str,
    resolution_cache: dict[int, dict[str, "server_resolution.ConnectionResolution"]],
) -> ServerAttribution:
    """Delega en server_resolution.resolve_app() (nunca re-parsea connection
    strings aqui, nunca reimplementa la deteccion de conexion). Un
    resolution_status distinto de 'resolved' sigue devolviendo
    server_kind='unknown' -- nunca un kind adivinado (Principio 3) -- pero
    ahora se conserva el resolution_status real y los kinds de candidatos
    para que quien construye el hop pueda fijar su confidence con esa
    evidencia, en vez de perderla en este punto."""
    if app_id is None:
        # Sin app_id conocido no hay forma de consultar resolve_app() --
        # mismo cubo que 'no se encontro ninguna conexion candidata'
        # (unresolved_no_literal es el estado real mas cercano; no se
        # inventa un estado nuevo para este caso).
        return ServerAttribution(
            "unknown", None, None, server_resolution.is_oracle_erp_table(table_key),
            resolution_status="unresolved_no_literal", candidate_kinds=(),
        )

    resolutions = resolution_cache.get(app_id)
    if resolutions is None:
        resolutions = {}
        app_resolution = server_resolution.resolve_app(app_id)
        if app_resolution:
            for target_resolution in app_resolution.targets:
                key = data_flow.normalize_table_key(target_resolution.target)
                if key:
                    resolutions[key] = target_resolution
        resolution_cache[app_id] = resolutions

    resolution = resolutions.get(table_key)
    if resolution is None:
        # Ningun ConnectionResolution para este target: ni WRITE_KEYWORDS lo
        # detecto como escritura, ni la app tiene settings de conexion --
        # mismo cubo que unresolved_no_literal (sin evidencia de servidor,
        # sin ambiguedad).
        return ServerAttribution(
            "unknown", None, None, server_resolution.is_oracle_erp_table(table_key),
            resolution_status="unresolved_no_literal", candidate_kinds=(),
        )

    candidate_kinds = tuple(c.kind for c in resolution.candidates)
    if resolution.resolution_status != "resolved" or not resolution.candidates:
        return ServerAttribution(
            "unknown", None, None, resolution.is_oracle_erp_table,
            resolution_status=resolution.resolution_status, candidate_kinds=candidate_kinds,
        )

    candidate = resolution.candidates[0]
    return ServerAttribution(
        candidate.kind, candidate.server, candidate.database, resolution.is_oracle_erp_table,
        resolution_status=resolution.resolution_status, candidate_kinds=candidate_kinds,
    )


def _confidence_for_hop(resolution_status: str, candidate_kinds: tuple[str, ...]) -> tuple[int, str]:
    """Auditoria iteracion 2: deriva confidence/label de un hop a partir del
    resolution_status REAL de server_resolution -- nunca un valor fijo. Solo
    mapea los 4 valores que server_resolution.resolve_write_targets() puede
    producir hoy ('resolved', 'unresolved_ambiguous_conditional',
    'unresolved_no_literal', 'unresolved_no_source_file'), mas la ausencia
    total de ConnectionResolution (tratada como 'unresolved_no_literal' por
    _attribute_server) -- nunca inventa un estado nuevo.

    Caso A -- servidor resuelto sin ambiguedad: la relacion Data Flow Y el
    servidor estan confirmados -> LINEAGE_HOP_STRONG_DATA_FLOW (85/CONFIRMED).

    Caso B -- ambiguedad ESPECIFICAMENTE Oracle-vs-no-Oracle (2+ candidatos,
    uno de ellos 'oracle', al menos otro no): esta es la unica ambiguedad que
    amenaza la propia narrativa de linaje Oracle (ver auditoria: caso real
    DataCenter2ORA -> XXAFL_QAPV_TESTS_JGR -> DataTransfer, candidatos
    oracle+sqlserver) -> LINEAGE_HOP_WEAK_OR_UNCONFIRMED_DIRECTION (45/POSSIBLE).

    Caso C -- cualquier otra causa de servidor no verificado: sin evidencia
    de conexion (unresolved_no_literal/unresolved_no_source_file), o
    ambiguedad entre 2+ candidatos que NO involucra Oracle (ej. dos SQL
    Server distintos) -- la relacion Data Flow sigue siendo igual de fuerte,
    solo no se pudo verificar el servidor de forma independiente ->
    LINEAGE_HOP_SERVER_MEDIATED (70/PROBABLE), nunca CONFIRMED."""
    if resolution_status == "resolved":
        return _confidence_pair("LINEAGE_HOP_STRONG_DATA_FLOW")

    if (
        resolution_status == "unresolved_ambiguous_conditional"
        and "oracle" in candidate_kinds
        and any(kind != "oracle" for kind in candidate_kinds)
    ):
        return _confidence_pair("LINEAGE_HOP_WEAK_OR_UNCONFIRMED_DIRECTION")

    return _confidence_pair("LINEAGE_HOP_SERVER_MEDIATED")


def compute_full_lineage_snapshot() -> LineageSnapshot:
    """EL calculo caro, portfolio-wide y recursivo -- solo se invoca desde
    run_lineage_build() (accion explicita "Construir/Refrescar"), nunca por
    request de lectura normal. Pasos: 1) deteccion de origenes Oracle, 2)
    aristas produce/consume portfolio-wide (misma clasificacion Data Flow,
    una sola vez), 3) atribucion de servidor por hop, 4) BFS multi-origen
    desde cada DIRECT_ORACLE, 5) clasificacion por nivel, 6) fan-in/fan-out
    para Critical Objects/Apps (mismo criterio de percentiles que
    db._factor_dependencias)."""
    with db.get_conn() as conn:
        oracle_origins = detect_oracle_origin_apps(conn)
        apps_rows = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM apps")}

    apps_sql_findings: list[tuple[str, list[dict]]] = []
    app_id_by_name: dict[str, int] = {}
    for app_id, app_name in apps_rows.items():
        data = db.get_app(app_id)
        if data:
            apps_sql_findings.append((app_name, data["sql_findings"]))
            app_id_by_name[app_name] = app_id

    test_context_index = data_flow.build_test_context_index(apps_sql_findings)
    edges_by_app: dict[str, list["data_flow.DataFlowEdge"]] = {
        name: data_flow.resolve_data_flow(name, findings, test_context_index=test_context_index)
        for name, findings in apps_sql_findings
    }

    raw_hops = build_portfolio_produce_consume_edges(edges_by_app)

    resolution_cache: dict[int, dict[str, "server_resolution.ConnectionResolution"]] = {}
    hops: list[LineageHop] = []
    for hop in raw_hops:
        producer_id = app_id_by_name.get(hop.from_app_name)
        consumer_id = app_id_by_name.get(hop.to_app_name)
        table_key = data_flow.normalize_table_key(hop.object_name) or hop.object_name.upper()
        attribution = _attribute_server(producer_id, table_key, resolution_cache)
        score, label = _confidence_for_hop(attribution.resolution_status, attribution.candidate_kinds)
        hops.append(replace(
            hop, from_app_id=producer_id, to_app_id=consumer_id,
            server_kind=attribution.server_kind, server_name=attribution.server_name,
            database_name=attribution.database_name, is_oracle_erp_table=attribution.is_oracle_erp_table,
            confidence=score, confidence_label=label,
            resolution_status=attribution.resolution_status, candidate_kinds=attribution.candidate_kinds,
        ))

    # BFS multi-origen: adjacency solo entre apps con id conocido en ambos lados.
    adjacency: dict[int, set[int]] = defaultdict(set)
    for hop in hops:
        if hop.from_app_id is not None and hop.to_app_id is not None:
            adjacency[hop.from_app_id].add(hop.to_app_id)

    level_by_app: dict[int, int] = {}
    origin_by_app: dict[int, str] = {}
    queue: deque[int] = deque()
    # Auditoria iteracion 2: sembrar en orden de app_id ascendente, NUNCA en
    # el orden de insercion de oracle_origins (que depende del orden fisico
    # en que SQLite devuelve filas de sql_findings/settings sin ORDER BY --
    # ver detect_oracle_origin_apps -- y por tanto podia cambiar entre
    # re-analisis sin que la evidencia de negocio cambiara). Cuando 2+
    # origenes DIRECT_ORACLE alcanzan la misma app al mismo nivel minimo, el
    # BFS FIFO le atribuye el origen a quien se encole PRIMERO -- ordenar la
    # siembra por app_id hace ese desempate deterministico y reproducible
    # (menor app_id gana), sin tocar el NIVEL calculado (que sigue siendo el
    # minimo numero de saltos real, identico con o sin este orden).
    for app_id, evidence in sorted(oracle_origins.items(), key=lambda item: item[0]):
        level_by_app[app_id] = 0
        origin_by_app[app_id] = evidence.app_name
        queue.append(app_id)

    while queue:
        current = queue.popleft()
        current_level = level_by_app[current]
        if current_level >= _MAX_SAFETY_DEPTH:
            continue
        for neighbor in adjacency.get(current, ()):
            if neighbor not in level_by_app:
                level_by_app[neighbor] = current_level + 1
                origin_by_app[neighbor] = origin_by_app[current]
                queue.append(neighbor)

    fan_out_by_app: dict[int, int] = defaultdict(int)
    fan_in_by_app: dict[int, int] = defaultdict(int)
    for hop in hops:
        if hop.from_app_id is not None:
            fan_out_by_app[hop.from_app_id] += 1
        if hop.to_app_id is not None:
            fan_in_by_app[hop.to_app_id] += 1

    raw_classifications: list[dict] = []
    for app_id, app_name in apps_rows.items():
        level = level_by_app.get(app_id)
        classification = _LEVEL_TO_CLASSIFICATION.get(level, "INDIRECT_DOWNSTREAM") if level is not None else "UNRELATED"
        if app_id in oracle_origins:
            evidence_summary = f"Origen Oracle directo: {oracle_origins[app_id].detection_method}"
        elif level is not None:
            evidence_summary = f"Alcanzada desde origen Oracle '{origin_by_app[app_id]}' en {level} salto(s)"
        else:
            evidence_summary = "Sin ruta demostrable hacia un origen Oracle conocido en este portafolio"
        raw_classifications.append({
            "app_id": app_id, "app_name": app_name, "classification": classification,
            "lineage_level": level, "origin_app_name": origin_by_app.get(app_id),
            "fan_in": fan_in_by_app.get(app_id, 0), "fan_out": fan_out_by_app.get(app_id, 0),
            "evidence_summary": evidence_summary,
        })

    app_fan_totals = [c["fan_in"] + c["fan_out"] for c in raw_classifications]
    classifications = tuple(
        AppClassification(
            app_id=c["app_id"], app_name=c["app_name"], classification=c["classification"],
            lineage_level=c["lineage_level"], origin_app_name=c["origin_app_name"],
            fan_in=c["fan_in"], fan_out=c["fan_out"],
            is_critical_app=db._bucket_by_percentile(app_fan_totals, c["fan_in"] + c["fan_out"]) == "Alta",
            evidence_summary=c["evidence_summary"],
        )
        for c in raw_classifications
    )

    object_degree: dict[str, dict] = {}
    for h in hops:
        entry = object_degree.setdefault(h.object_name, {
            "object_kind": h.object_kind, "server_kind": h.server_kind,
            "is_oracle_erp_table": h.is_oracle_erp_table, "producers": set(), "consumers": set(),
        })
        entry["producers"].add(h.from_app_name)
        entry["consumers"].add(h.to_app_name)
        if h.server_kind != "unknown":
            entry["server_kind"] = h.server_kind
        if h.is_oracle_erp_table:
            entry["is_oracle_erp_table"] = True

    object_fan_totals = [len(e["producers"]) + len(e["consumers"]) for e in object_degree.values()]
    object_stats = tuple(
        ObjectStats(
            object_name=name, object_kind=e["object_kind"], server_kind=e["server_kind"],
            fan_in=len(e["consumers"]), fan_out=len(e["producers"]),
            is_critical_object=db._bucket_by_percentile(
                object_fan_totals, len(e["producers"]) + len(e["consumers"])
            ) == "Alta",
            is_oracle_erp_table=e["is_oracle_erp_table"],
            evidence=tuple(sorted(e["producers"] | e["consumers"])),
        )
        for name, e in object_degree.items()
    )

    return LineageSnapshot(edges=tuple(hops), classifications=classifications, object_stats=object_stats)


def _serialize_relation_evidence(evidence: tuple["data_flow.RelationEvidence", ...]) -> list[dict]:
    def _op(op: "data_flow.OperationEvidence") -> dict:
        return {"operation": op.operation, "columns": list(op.columns), "confidence": op.evidence.confidence}

    return [
        {"table": ev.table, "write_ops": [_op(o) for o in ev.write_ops], "read_ops": [_op(o) for o in ev.read_ops]}
        for ev in evidence
    ]


def run_lineage_build(triggered_by: str | None = None) -> LineageRunSummary:
    """UNICA funcion que escribe las tablas lineage_* -- envuelve el calculo
    puro (compute_full_lineage_snapshot) con start/complete/fail_lineage_run
    para que una corrida interrumpida quede visiblemente 'failed', nunca
    'completed' a medias (Principio 3)."""
    run_id = db.start_lineage_run(triggered_by)
    try:
        snapshot = compute_full_lineage_snapshot()
        now = datetime.now(timezone.utc).isoformat()

        db.save_lineage_edges(run_id, [
            {
                "from_app_id": h.from_app_id, "from_app_name": h.from_app_name,
                "to_app_id": h.to_app_id, "to_app_name": h.to_app_name,
                "object_name": h.object_name, "object_kind": h.object_kind, "operation": h.operation,
                "server_kind": h.server_kind, "server_name": h.server_name, "database_name": h.database_name,
                "is_oracle_erp_table": h.is_oracle_erp_table,
                "confidence": h.confidence, "confidence_label": h.confidence_label,
                "detection_method": h.detection_method,
                # Auditoria iteracion 2: se agrega el bloque "server_resolution"
                # (resolution_status real + kinds de TODOS los candidatos en
                # conflicto, ej. ["oracle","sqlserver"]) para que quien audite
                # un hop despues pueda ver POR QUE su confidence quedo en
                # PROBABLE/POSSIBLE -- nunca se serializan server/database
                # (ya viven en sus propias columnas server_name/database_name)
                # ni el valor crudo del connection string (que puede traer
                # credenciales, ver settings.default_value) -- solo el "kind".
                "evidence": {
                    "relations": _serialize_relation_evidence(h.evidence),
                    "server_resolution": {
                        "resolution_status": h.resolution_status,
                        "candidate_kinds": list(h.candidate_kinds),
                    },
                },
                "observed_at": now,
            }
            for h in snapshot.edges
        ])
        db.save_lineage_classifications(run_id, [
            {
                "app_id": c.app_id, "app_name": c.app_name, "classification": c.classification,
                "lineage_level": c.lineage_level, "origin_app_name": c.origin_app_name,
                "fan_in": c.fan_in, "fan_out": c.fan_out,
                "is_critical_app": c.is_critical_app, "evidence_summary": c.evidence_summary,
            }
            for c in snapshot.classifications
        ])
        db.save_lineage_object_stats(run_id, [
            {
                "object_name": o.object_name, "object_kind": o.object_kind, "server_kind": o.server_kind,
                "fan_in": o.fan_in, "fan_out": o.fan_out,
                "is_critical_object": o.is_critical_object, "is_oracle_erp_table": o.is_oracle_erp_table,
            }
            for o in snapshot.object_stats
        ])

        db.complete_lineage_run(run_id, app_count=len(snapshot.classifications), edge_count=len(snapshot.edges))
        return LineageRunSummary(run_id=run_id, app_count=len(snapshot.classifications), edge_count=len(snapshot.edges))
    except Exception as e:
        db.fail_lineage_run(run_id, str(e))
        raise


def _walk_bounded(
    start_id: int, start_name: str, *, max_depth: int, max_nodes: int, direction: str,
) -> tuple[LineagePath, ...]:
    """Expansion ACOTADA on-demand via resolve_app_relations() por nodo
    visitado -- misma tecnica que /app_map, NUNCA resolve_data_flow_portfolio()
    completo ni las tablas lineage_*. Cycle-safe: un app ya visitado a
    profundidad <= la actual no se vuelve a expandir."""
    visited_depth: dict[str, int] = {start_name: 0}
    paths: list[LineagePath] = []
    node_count = 1
    score, label = _confidence_pair("LINEAGE_HOP_STRONG_DATA_FLOW")

    def _relations_to_expand(app_id: int):
        relations = data_flow.resolve_app_relations(app_id)
        if relations is None:
            return None
        out = []
        if direction in ("downstream", "both"):
            out.extend((rel, "downstream") for rel in relations.produces_to)
        if direction in ("upstream", "both"):
            out.extend((rel, "upstream") for rel in relations.consumes_from)
        return out

    def _walk(current_id: int, current_name: str, depth: int, hops_so_far: tuple[LineageHop, ...]) -> None:
        nonlocal node_count
        if depth >= max_depth or node_count >= max_nodes:
            if hops_so_far:
                paths.append(LineagePath(hops=hops_so_far, terminal=True))
            return

        candidates = _relations_to_expand(current_id)
        if not candidates:
            if hops_so_far:
                paths.append(LineagePath(hops=hops_so_far, terminal=True))
            return

        expanded_any = False
        for rel, rel_direction in candidates:
            if node_count >= max_nodes:
                break
            if rel.other_app in visited_depth and visited_depth[rel.other_app] <= depth + 1:
                continue
            other_id = db.get_app_id_by_name(rel.other_app)
            table_label = ", ".join(sorted({ev.table for ev in rel.evidence}))
            hop = LineageHop(
                from_app_id=current_id if rel_direction == "downstream" else other_id,
                from_app_name=current_name if rel_direction == "downstream" else rel.other_app,
                to_app_id=other_id if rel_direction == "downstream" else current_id,
                to_app_name=rel.other_app if rel_direction == "downstream" else current_name,
                object_name=table_label, object_kind="table", operation=None,
                server_kind="unknown", server_name=None, database_name=None, is_oracle_erp_table=False,
                confidence=score, confidence_label=label,
                detection_method="DATA_FLOW_PRODUCER_CONSUMER", evidence=rel.evidence,
            )
            visited_depth[rel.other_app] = depth + 1
            node_count += 1
            expanded_any = True
            if other_id is not None:
                _walk(other_id, rel.other_app, depth + 1, hops_so_far + (hop,))
            else:
                paths.append(LineagePath(hops=hops_so_far + (hop,), terminal=True))

        if not expanded_any and hops_so_far:
            paths.append(LineagePath(hops=hops_so_far, terminal=True))

    _walk(start_id, start_name, 0, ())
    return tuple(paths)


def resolve_lineage_for_app(
    app_id: int, max_depth: int = 3, direction: str = "downstream", max_nodes: int = 80,
) -> LineageTree | None:
    """Lineage Explorer, vista ENFOCADA por app -- on-demand, NUNCA toca las
    tablas lineage_* (esas son exclusivas del snapshot persistido de
    run_lineage_build())."""
    root = db.get_app(app_id)
    if not root:
        return None
    if direction not in ("upstream", "downstream", "both"):
        direction = "downstream"
    root_name = root["app"]["name"]
    paths = _walk_bounded(app_id, root_name, max_depth=max_depth, max_nodes=max_nodes, direction=direction)
    truncated = any(len(p.hops) >= max_depth for p in paths)
    return LineageTree(root_kind="app", root_name=root_name, direction=direction, paths=paths, truncated=truncated)


def resolve_lineage_for_object(
    object_name: str, max_depth: int = 3, direction: str = "both", max_nodes: int = 80,
) -> LineageTree | None:
    """Mismo mecanismo acotado, pero rooteado en un objeto: arranca desde
    TODAS las apps que db.search_by_table() encuentra tocando ese nombre
    (coincidencia por substring, igual que /search), y fusiona sus arboles
    acotados, deduplicando caminos identicos."""
    matches = db.search_by_table(object_name)
    app_ids = sorted({row["id"] for row in matches})
    if not app_ids:
        return None

    seen_paths: set[tuple] = set()
    merged_paths: list[LineagePath] = []
    total_nodes = 0
    for app_id in app_ids:
        if total_nodes >= max_nodes:
            break
        row = db.get_app(app_id)
        if not row:
            continue
        sub_paths = _walk_bounded(
            app_id, row["app"]["name"], max_depth=max_depth, max_nodes=max_nodes - total_nodes, direction=direction,
        )
        for path in sub_paths:
            key = tuple((h.from_app_name, h.to_app_name, h.object_name) for h in path.hops)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            merged_paths.append(path)
        total_nodes += len(sub_paths)

    return LineageTree(
        root_kind="object", root_name=object_name, direction=direction,
        paths=tuple(merged_paths), truncated=total_nodes >= max_nodes,
    )


def compute_impact_analysis(
    run_id: int, *, app_id: int | None = None, object_name: str | None = None,
) -> ImpactAnalysisResult | None:
    """Impact Analysis: lee las aristas YA PERSISTIDAS de `run_id` (nunca
    recalcula el grafo) y hace un BFS pequeno en memoria sobre ese conjunto
    ya acotado, distinguiendo consumidores DIRECTOS de INDIRECTOS."""
    if app_id is None and object_name is None:
        return None

    edges = db.get_lineage_edges(run_id)
    adjacency: dict[str, set[str]] = defaultdict(set)
    for e in edges:
        adjacency[e["from_app_name"]].add(e["to_app_name"])

    if app_id is not None:
        cls = db.get_lineage_classification(run_id, app_id)
        if cls is None:
            return None
        subject_kind, subject_name = "app", cls["app_name"]
        start_nodes = {subject_name}
    else:
        subject_kind, subject_name = "object", object_name
        start_nodes = {e["from_app_name"] for e in edges if e["object_name"] == object_name}
        if not start_nodes:
            return ImpactAnalysisResult(subject_kind, subject_name, (), (), 0)

    direct: set[str] = set()
    for node in start_nodes:
        direct |= adjacency.get(node, set())
    direct -= start_nodes

    visited = set(start_nodes) | direct
    frontier = set(direct)
    indirect: set[str] = set()
    depth_reached = 1 if direct else 0
    while frontier:
        next_frontier: set[str] = set()
        for node in frontier:
            next_frontier |= adjacency.get(node, set()) - visited
        if not next_frontier:
            break
        indirect |= next_frontier
        visited |= next_frontier
        frontier = next_frontier
        depth_reached += 1

    return ImpactAnalysisResult(
        subject_kind=subject_kind, subject_name=subject_name,
        direct_consumers=tuple(sorted(direct)), indirect_consumers=tuple(sorted(indirect)),
        depth_reached=depth_reached,
    )
