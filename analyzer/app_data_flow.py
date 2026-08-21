"""Application Flow -- Data Flow Integration, Incremento D (2026-08-20):
CAPA DE COMPOSICION que conecta la evidencia ya existente de Application
Structure (metodos), Call Flow (llamadas intra-clase) y Data Flow
(clasificacion SQL/tabla) para responder "¿que parte de la aplicacion
ejecuta que acceso a datos?". NO extrae SQL de nuevo, NO reclasifica
producer/consumer/mixed/indeterminado -- ambas cosas ya existen
(analyzer.extract/analyzer.data_flow) y se reutilizan tal cual.

Deliberadamente NO es Functional Flow: nunca interpreta "por que" ocurre un
acceso a datos, solo documenta que evidencia estatica lo conecta con que
metodo. PRECISION > COBERTURA -- una relacion Method->SqlFinding que no
pueda demostrarse con seguridad (class_name+method exactos, mismo archivo)
se marca unresolved_method_sql_mapping, nunca se inventa."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath

from . import app_interactions, app_structure, confidence, data_flow, db
from .__version__ import ANALYZER_VERSION
from .evidence import Evidence
from .unknown import UnknownRecord


@dataclass(frozen=True)
class ApplicationDataOperation:
    """Una operacion de datos atribuida a un metodo de la aplicacion --
    DIRECTA (el metodo contiene el SqlFinding) o INDIRECTA (un metodo de la
    MISMA clase llama, via CallEdge ya resuelto, al metodo que la ejecuta;
    ver `via_method`). `data_flow_role` es el rol YA CLASIFICADO por
    analyzer.data_flow.resolve_data_flow_for_app() para `table` -- nunca se
    recalcula aqui."""

    app_name: str
    class_name: str
    method_name: str
    operation: str | None  # "select"|"insert"|"update"|"delete"|"merge"|"stored_procedure"|"oracle_package_call"|None
    table: str | None
    data_flow_role: str  # mismo vocabulario que DataFlowEdge.role, o "indeterminado" si no aplica
    access_kind: str  # "direct" | "indirect"
    via_method: str | None  # solo para access_kind="indirect": el metodo que EJECUTA directamente la operacion
    resolution_status: str  # "resolved" | "unresolved_method_sql_mapping"
    evidence: Evidence


@dataclass(frozen=True)
class ApplicationDataFlow:
    app_name: str
    operations: tuple[ApplicationDataOperation, ...]
    # Heredados de ApplicationStructure (ej. limitacion WPF de wiring en
    # BAML) -- nunca se duplica el concepto de Unknown.
    unknowns: tuple[UnknownRecord, ...]


def _build_evidence(extractor_key: str, source_file: str | None, line_number: int | None,
                     snippet: str | None) -> Evidence:
    return Evidence(
        source_file=source_file, line_number=line_number, snippet=snippet,
        extractor=extractor_key, confidence=confidence.resolve_confidence(extractor_key),
        analyzer_version=ANALYZER_VERSION, created_at=datetime.now(timezone.utc).isoformat(),
    )


def _paths_match(method_info_file: str | None, sql_finding_file: str | None) -> bool:
    """Concilia la diferencia de RAIZ entre extract.py (SqlFinding.file,
    relativo a output_dir = DECOMPILED_DIR/app_name COMPLETO) y
    app_structure.py (MethodInfo.file, relativo a resolve_decompiled_root =
    DECOMPILED_DIR/primer segmento de app_name) -- confirmado empiricamente
    contra RefControl real: ambas rutas son CORRECTAS relativas a su propia
    raiz, pero difieren en segmentos de prefijo cuando app_name contiene
    "/". Se compara por SUFIJO de partes de ruta (nunca por igualdad de
    string, nunca por nombre de archivo solo) para no depender de
    reconstruir la convencion de nombre de cada app."""
    if not method_info_file or not sql_finding_file:
        return False
    mi_parts = PurePosixPath(method_info_file.replace("\\", "/")).parts
    sf_parts = PurePosixPath(sql_finding_file.replace("\\", "/")).parts
    if not sf_parts or len(sf_parts) > len(mi_parts):
        return False
    return mi_parts[-len(sf_parts):] == sf_parts


def _index_methods(structure: app_structure.ApplicationStructure) -> dict[tuple[str, str], list]:
    index: dict[tuple[str, str], list] = {}
    for m in structure.methods:
        index.setdefault((m.class_name, m.method_name), []).append(m)
    return index


def _resolve_method_for_finding(row: dict, methods_index: dict) -> tuple:
    """Devuelve (MethodInfo|None, "resolved"|"no_match"|"ambiguous").
    Precision estricta: (class_name, method) exacto (nunca substring, nunca
    solo nombre de metodo), filtrado por archivo (ver _paths_match). Si eso
    deja 2+ candidatos, se deduplica por FIRMA IDENTICA -- la duplicacion
    fisica ya conocida (Geometria/Release: Release/ + app.publish/, mismo
    codigo clonado) produce candidatos con firma identica y debe colapsar a
    un solo resultado; una sobrecarga REAL (firmas distintas) permanece
    ambigua, nunca se elige arbitrariamente."""
    candidates = methods_index.get((row.get("class_name"), row.get("method")), [])
    if not candidates:
        return None, "no_match"
    file_matched = [m for m in candidates if _paths_match(m.file, row.get("file"))]
    if not file_matched:
        return None, "no_match"
    distinct_signatures = {m.signature for m in file_matched}
    if len(distinct_signatures) == 1:
        return file_matched[0], "resolved"
    return None, "ambiguous"


def _resolve_operation(row: dict) -> str | None:
    """Reutiliza SqlFinding.category (ya calculado por extract.py) para
    stored_procedure/oracle_package_call -- solo recurre a
    data_flow.detect_operation() (funcion YA EXISTENTE, no reimplementada)
    para el caso 'query' generico, donde category no distingue
    select/insert/update/delete/merge."""
    category = row.get("category")
    if category == "stored_procedure":
        return "stored_procedure"
    if category == "oracle_package_call":
        return "oracle_package_call"
    combined = (row.get("resolved") or "") + " " + (row.get("raw") or "")
    return data_flow.detect_operation(combined)


def _build_role_index(edges: list) -> dict[str, str]:
    index: dict[str, str] = {}
    for edge in edges:
        key = data_flow.normalize_table_key(edge.target)
        if key:
            index[key] = edge.role
    return index


def _resolve_role(role_index: dict, target: str | None) -> str:
    """'indeterminado' (mismo vocabulario que DataFlowEdge.role) cuando no
    hay target, no normaliza a una tabla real, o -- caso limite -- no
    aparece en el indice de esta app (no deberia ocurrir para hallazgos de
    la propia app, ya que resolve_data_flow_for_app() clasifica CADA target
    no vacio de sus propios sql_findings; se mantiene como salvaguarda
    honesta, nunca se infiere un rol)."""
    if not target:
        return "indeterminado"
    key = data_flow.normalize_table_key(target)
    if key is None:
        return "indeterminado"
    return role_index.get(key, "indeterminado")


def discover_application_data_flow(app_id: int) -> ApplicationDataFlow | None:
    """Punto de entrada del incremento -- COMPOSICION pura sobre evidencia
    ya derivada por Incrementos A/C y por analyzer.data_flow (Fase Mapa de
    Flujo de Datos). NUNCA usa resolve_data_flow_portfolio() ni recorre el
    portafolio completo -- el costo esta acotado a los archivos/hallazgos
    de ESTA app (mismo principio de rendimiento que B/C)."""
    structure = app_structure.discover_application_structure(app_id)
    if structure is None:
        return None

    data = db.get_app(app_id)
    if not data:
        return None
    app_name = data["app"]["name"]
    sql_findings = data["sql_findings"]

    data_flow_edges = data_flow.resolve_data_flow_for_app(app_id)
    role_index = _build_role_index(data_flow_edges)
    methods_index = _index_methods(structure)

    direct_ops: list[ApplicationDataOperation] = []
    for row in sql_findings:
        operation = _resolve_operation(row)
        if operation is None and not row.get("target"):
            # Sin operacion reconocible NI target -- ej. la propia
            # instanciacion de SqlConnection/SqlCommand ("using SqlCommand
            # sqlCommand = new SqlCommand();"), evidencia de que EXISTE
            # acceso a datos en el metodo pero sin ningun dato observable
            # sobre QUE operacion u sobre QUE tabla. No aporta señal (no es
            # "metodo sin SQL", pero tampoco es una operacion identificable
            # sobre datos) -- se omite en vez de producir un registro vacio.
            continue
        method_info, status = _resolve_method_for_finding(row, methods_index)
        role = _resolve_role(role_index, row.get("target"))
        if method_info is not None:
            class_name, method_name = method_info.class_name, method_info.method_name
        else:
            # Sin metodo confirmado -- se conservan las etiquetas crudas del
            # propio SqlFinding (mejor evidencia disponible), nunca se
            # inventa una atribucion de metodo.
            class_name = row.get("class_name") or "(desconocida)"
            method_name = row.get("method") or "(desconocido)"
        resolution_status = "resolved" if method_info is not None else "unresolved_method_sql_mapping"
        extractor_key = (
            "APP_DATA_METHOD_SQL_DIRECT" if resolution_status == "resolved"
            else "APP_DATA_METHOD_SQL_MAPPING_AMBIGUOUS"
        )
        # El snippet capturado en la extraccion (row['snippet'], la linea de
        # C# real) es mejor evidencia que el texto SQL resuelto -- se usa
        # tal cual cuando existe; solo se cae al SQL resuelto/crudo para
        # filas sin snippet propio (ej. instanciacion de SqlConnection/
        # SqlCommand, capturadas por el extractor UNKNOWN sin snippet).
        snippet = row.get("snippet") or (row.get("resolved") or row.get("raw") or "")[:200] or None
        direct_ops.append(ApplicationDataOperation(
            app_name=app_name, class_name=class_name, method_name=method_name,
            operation=operation, table=row.get("target"), data_flow_role=role,
            access_kind="direct", via_method=None,
            resolution_status=resolution_status,
            evidence=_build_evidence(extractor_key, row.get("file"), row.get("line_number"), snippet),
        ))

    # Propagacion de UN SOLO salto via Call Flow intra-class (Incremento C)
    # -- SOLO desde operaciones DIRECTAS y resueltas (nunca desde mapeos
    # ambiguos: no hay nada solido que propagar desde una atribucion
    # incierta). Nunca se encadena mas alla de 1 salto (A->B->SQL permitido,
    # A->B->C->SQL no) -- ciclos (A->B, B->A) no producen loop porque esto
    # es una busqueda por diccionario de UN SOLO nivel, nunca una travesia
    # recursiva del grafo de llamadas.
    interactions = app_interactions.discover_interactions(app_id)
    resolved_call_edges = [e for e in interactions.call_edges if e.resolution_status == "resolved"]

    indirect_ops: list[ApplicationDataOperation] = []
    for op in direct_ops:
        if op.resolution_status != "resolved":
            continue
        callers = [
            e for e in resolved_call_edges
            if e.source_class == op.class_name and e.target_method == op.method_name
        ]
        for caller_edge in callers:
            indirect_ops.append(ApplicationDataOperation(
                app_name=app_name, class_name=caller_edge.source_class, method_name=caller_edge.source_method,
                operation=op.operation, table=op.table, data_flow_role=op.data_flow_role,
                access_kind="indirect", via_method=op.method_name,
                resolution_status="resolved",
                evidence=_build_evidence(
                    "APP_DATA_INDIRECT_VIA_CALL_FLOW", op.evidence.source_file,
                    op.evidence.line_number, op.evidence.snippet,
                ),
            ))

    return ApplicationDataFlow(
        app_name=app_name,
        operations=tuple(direct_ops) + tuple(indirect_ops),
        unknowns=structure.unknowns,
    )
