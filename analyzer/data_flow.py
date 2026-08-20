"""Mapa de Flujo de Datos (2026-08-19): clasifica, por evidencia estatica
real, el papel de cada app sobre cada tabla que toca -- quien ESCRIBE la
medicion numerica real del equipo, quien solo escribe PASS/FAIL, quien
CONSUME la medicion, y quien solo consume el resultado ya resuelto.

Unidad primaria de evidencia (nunca se colapsa): Application + Table +
Operation + Columns. El "rol" es una DERIVACION deterministica de esa
evidencia, nunca un dato asignado a mano.

Regla explicita (2026-08-19): este modulo NO asume que escribir una columna
de medicion signifique "esta app habla directo con el equipo de prueba" --
solo demuestra que el CODIGO escribe esa columna. De donde viene el dato
fisicamente (equipo->app directo vs equipo->archivo->app, etc.) queda
deliberadamente fuera de alcance de este incremento."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import confidence, db
from .__version__ import ANALYZER_VERSION
from .evidence import Evidence

REFERENCE_DATA_DIR = Path(__file__).parent / "reference_data"

# Orden de prioridad de deteccion: un INSERT...SELECT (copia entre tablas,
# ver XXAFL_QAPV_RL1_PROCESS_202510 real) debe clasificarse por su INSERT,
# no confundirse con un SELECT de lectura -- por eso insert se revisa primero.
OPERATION_PATTERNS = (
    ("insert", re.compile(r"(?i)\binsert\s+into\b")),
    ("update", re.compile(r"(?i)\bupdate\s+\S+\s+set\b")),
    ("delete", re.compile(r"(?i)\bdelete\s+from\b")),
    ("merge", re.compile(r"(?i)\bmerge\s+into\b|\bmerge\s+\S+\s+as\b")),
    ("select", re.compile(r"(?i)\bselect\b")),
)

# Entre el parentesis de columnas y VALUES/SELECT puede haber una clausula
# OUTPUT (ej. "OUTPUT inserted.IDExfo", real: InterConfig/PruebaExfo) --
# no se exige adyacencia inmediata, solo que VALUES/SELECT aparezca despues.
INSERT_COLUMNS_RE = re.compile(r"(?i)insert\s+into\s+\S+\s*\(([^)]*)\).*?(?:values\s*\(|select\b)", re.DOTALL)
UPDATE_SET_RE = re.compile(r"(?i)update\s+\S+\s+set\s+(.*?)(?:\bwhere\b|$)", re.DOTALL)
UPDATE_ASSIGNMENT_RE = re.compile(r"(?i)\b([A-Za-z_][\w]*)\s*=")
SELECT_COLUMNS_RE = re.compile(r"(?i)select\s+(?:top\s+\d+\s+)?(.*?)\s+from\b", re.DOTALL)
ALIAS_SUFFIX_RE = re.compile(r"(?i)\s+as\s+\S+$")


ESCAPED_WHITESPACE_RE = re.compile(r"\\[rnt]")


def normalize_escaped_whitespace(text: str) -> str:
    """Algunos SqlFinding.raw/resolved traen el texto fuente tal como
    aparecia en un string C# multilinea -- con \\r\\n LITERALMENTE escapados
    (los 2 caracteres backslash+r, no un salto de linea real), ej. real:
    'UPDATE ValeAutorizacionesDetalle \\r\\n   SET Estado=...' (portafolio,
    INVENTA2-2TEST/InventaVales - rebuild). `\\s` no matchea esa secuencia de
    2 caracteres, asi que un regex como `update\\s+\\S+\\s+set` fallaba con
    columnas y hasta la deteccion de operacion. Se normaliza UNA vez, antes
    de detectar operacion o extraer columnas -- nunca un fix puntual por caso."""
    return ESCAPED_WHITESPACE_RE.sub(" ", text)


def detect_operation(sql_text: str) -> str | None:
    """Devuelve "insert"|"update"|"delete"|"merge"|"select", o None si no se
    reconoce ninguna. insert/update/delete/merge se revisan antes que
    select (un INSERT...SELECT es un INSERT desde la perspectiva de la
    tabla destino, no una lectura)."""
    sql_text = normalize_escaped_whitespace(sql_text)
    for name, pattern in OPERATION_PATTERNS:
        if pattern.search(sql_text):
            return name
    return None


def _normalize_column_token(token: str) -> str | None:
    """Quita alias de tabla (g.SERIAL_NUMBER -> SERIAL_NUMBER), sufijo AS,
    espacios y corchetes. Devuelve None si el token no es un simple nombre
    de columna (ej. una expresion con parentesis, "*", o vacio) -- estos NO
    cuentan como columna reconstruida (ni catalogada ni descartada, se
    ignoran para no fabricar un nombre de columna que no es tal)."""
    token = ALIAS_SUFFIX_RE.sub("", token.strip()).strip()
    token = token.strip("[]").strip()
    if not token or token == "*":
        return None
    if "(" in token or ")" in token:
        return None
    if "." in token:
        token = token.rsplit(".", 1)[-1].strip("[]")
    if not re.match(r"^[A-Za-z_]\w*$", token):
        return None
    return token


def _split_top_level_commas(text: str) -> list[str]:
    """Separa por comas ignorando las que estan dentro de parentesis (ej.
    ISNULL(x, 0) no debe partirse en dos)."""
    parts = []
    depth = 0
    current = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def extract_insert_columns(sql_text: str) -> list[str] | None:
    match = INSERT_COLUMNS_RE.search(normalize_escaped_whitespace(sql_text))
    if not match:
        return None
    columns = [_normalize_column_token(c) for c in _split_top_level_commas(match.group(1))]
    columns = [c for c in columns if c]
    return columns or None


def extract_update_columns(sql_text: str) -> list[str] | None:
    match = UPDATE_SET_RE.search(normalize_escaped_whitespace(sql_text))
    if not match:
        return None
    # No se separa por coma primero (el lado derecho de una asignacion puede
    # traer sus propias comas, ej. funciones) -- se buscan directamente los
    # patrones "identificador =" dentro de la clausula SET completa.
    columns = [m.group(1) for m in UPDATE_ASSIGNMENT_RE.finditer(match.group(1))]
    columns = [c for c in columns if _normalize_column_token(c)]
    return columns or None


def extract_select_columns(sql_text: str) -> list[str] | None:
    match = SELECT_COLUMNS_RE.search(normalize_escaped_whitespace(sql_text))
    if not match:
        return None
    columns = [_normalize_column_token(c) for c in _split_top_level_commas(match.group(1))]
    columns = [c for c in columns if c]
    return columns or None


_MEASUREMENT_COLUMNS_CACHE: frozenset[str] | None = None


def _load_measurement_columns() -> frozenset[str]:
    global _MEASUREMENT_COLUMNS_CACHE
    if _MEASUREMENT_COLUMNS_CACHE is None:
        path = REFERENCE_DATA_DIR / "measurement_columns.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        _MEASUREMENT_COLUMNS_CACHE = frozenset(c["name"].upper() for c in data["columns"])
    return _MEASUREMENT_COLUMNS_CACHE


def is_measurement_column(name: str) -> bool:
    """Coincidencia EXACTA (sin distinguir mayusculas) contra el catalogo
    respaldado por evidencia real -- nunca un patron de nombre generico."""
    return (name or "").strip().upper() in _load_measurement_columns()


_STATUS_METADATA_COLUMNS_CACHE: frozenset[str] | None = None


def _load_status_metadata_columns() -> frozenset[str]:
    global _STATUS_METADATA_COLUMNS_CACHE
    if _STATUS_METADATA_COLUMNS_CACHE is None:
        path = REFERENCE_DATA_DIR / "status_and_test_metadata_columns.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        _STATUS_METADATA_COLUMNS_CACHE = frozenset(c["name"].upper() for c in data["columns"])
    return _STATUS_METADATA_COLUMNS_CACHE


def is_status_or_test_metadata_column(name: str) -> bool:
    """Coincidencia EXACTA contra el catalogo de STATUS/PASS-FAIL/DEVICE_STATUS
    y metadata directamente asociada a un evento de prueba (quien/cuando/que
    se probo) -- respaldado por evidencia real, nunca por patron de nombre.
    Auditoria generica de fila (CREATION_DATE, LAST_UPDATE_DATE, etc.) queda
    deliberadamente fuera -- ver known_exclusions en el catalogo."""
    return (name or "").strip().upper() in _load_status_metadata_columns()


# --- test_context (2026-08-19, incremento Fase 8) ---------------------------
#
# Auditoria previa demostro con evidencia real (DJItem/Employees/etc.) que
# columnas AMBIGUAS del catalogo status/metadata (STATUS, DEVICE, CONNECTOR,
# ENDS, SERIAL_NUMBER, EMPLOYEE_NUMBER, WIP_ENTITY_NAME) tambien existen, con
# igual nombre, en tablas de catalogo ERP/RH/logistica/config -- una columna
# generica aislada NO demuestra que la tabla sea de resultados de prueba.
#
# test_context(tabla) se construye UNA SOLA VEZ, agregando evidencia de TODAS
# las apps del portafolio (reads Y writes, solo columnas efectivamente
# reconstruidas) -- nunca a partir del nombre de la tabla ni de una columna
# aislada en una sola lectura. Confirmado unicamente por señales FUERTES
# (independientes, no ambiguas): TEST_DATE, TEST_*_ID, DEVICE_STATUS,
# PassFail, o una columna de measurement_columns.json.

TEST_CONTEXT_CONFIRMADO = "confirmado"
TEST_CONTEXT_DESCONOCIDO = "desconocido"

_STRONG_TEST_CONTEXT_LITERALS = frozenset({"TEST_DATE", "DEVICE_STATUS", "PASSFAIL"})
TEST_ID_RE = re.compile(r"(?i)^TEST_[A-Z0-9_]+_ID$")

# Targets que no son una tabla real: variable/declaracion de codigo C#
# capturada por error en la extraccion (bug preexistente, fuera de alcance de
# este incremento -- ver auditoria Fase 7). Ejemplo real:
# 'using SqlCommand sqlCommand = new SqlCommand();'
_CODE_LIKE_TARGET_RE = re.compile(r"(?i)commandtext|^\s*using\b")
# Unico prefijo de esquema generico confirmado con evidencia real
# (ej. 'dbo.TWaveLength'). No se adivinan otros esquemas sin evidencia.
_SCHEMA_PREFIX_RE = re.compile(r"(?i)^DBO\.")


def _is_strong_test_context_column(name: str) -> bool:
    """Señal fuerte e independiente de contexto de prueba: nunca aparecio,
    en la auditoria de portafolio completo, en una tabla de catalogo
    ERP/RH/logistica/config -- a diferencia de STATUS/DEVICE/CONNECTOR/etc."""
    upper = (name or "").strip().upper()
    if not upper:
        return False
    if upper in _STRONG_TEST_CONTEXT_LITERALS:
        return True
    if TEST_ID_RE.match(upper):
        return True
    return is_measurement_column(upper)


def normalize_table_key(target: str) -> str | None:
    """Normaliza un target a la clave usada para agregar evidencia de
    test_context. Devuelve None si el target no representa una tabla real
    (codigo capturado por error -- ver Fase 7). NUNCA se usa el nombre
    resultante como evidencia de contexto de prueba, solo como clave de
    indexacion -- DJITEM_PRUEBAS normaliza igual que cualquier otra tabla y
    permanece 'desconocido' si su evidencia observada no trae señal fuerte."""
    if not target:
        return None
    stripped = target.strip()
    if not stripped or _CODE_LIKE_TARGET_RE.search(stripped):
        return None
    key = _SCHEMA_PREFIX_RE.sub("", stripped.upper())
    return key or None


def build_test_context_index(apps_sql_findings: "list[tuple[str, list[dict]]]") -> dict[str, str]:
    """Agrega, sobre TODAS las apps recibidas, las columnas efectivamente
    reconstruidas (via los mismos extractores que resolve_data_flow) por
    tabla normalizada -- reads Y writes, cualquier app. Excluye filas
    is_stored_procedure=1 (un procedimiento no tiene "columnas" de tabla) y
    targets que no son tablas reales (ver normalize_table_key). Devuelve
    {tabla_normalizada: "confirmado"|"desconocido"}."""
    observed_columns: dict[str, set[str]] = {}
    for _app_name, sql_findings in apps_sql_findings:
        for row in sql_findings:
            if row.get("is_stored_procedure"):
                continue
            key = normalize_table_key(row.get("target"))
            if key is None:
                continue
            combined = (row.get("resolved") or "") + " " + (row.get("raw") or "")
            operation = detect_operation(combined)
            if operation is None:
                continue

            cols: list[str] | None = None
            if operation in ("insert", "update"):
                extractor = extract_insert_columns if operation == "insert" else extract_update_columns
                cols = extractor(combined)
            elif operation == "select":
                cols, _extractor_key = _resolve_select_columns(row)
            # "merge"/"delete" no aportan lista de columnas reconstruible,
            # igual que en resolve_data_flow -- no contribuyen evidencia.

            if cols:
                observed_columns.setdefault(key, set()).update(c.upper() for c in cols)

    return {
        table: (TEST_CONTEXT_CONFIRMADO if any(_is_strong_test_context_column(c) for c in cols) else TEST_CONTEXT_DESCONOCIDO)
        for table, cols in observed_columns.items()
    }


def resolve_test_context(test_context_index: dict[str, str] | None, target: str) -> str:
    """Consulta el indice ya construido para una tabla especifica. Un target
    que no normaliza a una tabla real, o que no aparece en el indice
    (sin evidencia agregada disponible), es 'desconocido' -- nunca se infiere
    'confirmado' por ausencia de datos."""
    if not test_context_index:
        return TEST_CONTEXT_DESCONOCIDO
    key = normalize_table_key(target)
    if key is None:
        return TEST_CONTEXT_DESCONOCIDO
    return test_context_index.get(key, TEST_CONTEXT_DESCONOCIDO)


@dataclass(frozen=True)
class OperationEvidence:
    operation: str  # "insert" | "update" | "delete" | "merge" | "select"
    columns: tuple[str, ...]
    evidence: Evidence


@dataclass(frozen=True)
class DataFlowEdge:
    """Application + Table como unidad de clasificacion; `writes`/`reads`
    conservan CADA operacion y sus columnas por separado -- nunca se
    colapsan en una sola etiqueta, ni siquiera para "mixto"."""

    app_name: str
    target: str
    role: str  # productor_numerico | productor_passfail | consumidor_medicion | consumidor_resultado | consumidor_general | mixto | indeterminado
    writes: tuple[OperationEvidence, ...] = ()
    reads: tuple[OperationEvidence, ...] = ()
    resolution_status: str = "resolved"


def _build_evidence(extractor_key: str) -> Evidence:
    return Evidence(
        extractor=extractor_key,
        confidence=confidence.resolve_confidence(extractor_key),
        analyzer_version=ANALYZER_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _resolve_select_columns(row: dict) -> tuple[list[str] | None, str]:
    """Prioridad: result_columns (ya poblado por extract.py desde accesos
    reales reader["X"]) -> regex literal sobre resolved/raw. Devuelve
    (columnas, extractor_key) o (None, "") si no se pudo reconstruir."""
    raw_result_columns = row.get("result_columns")
    if raw_result_columns:
        try:
            parsed = json.loads(raw_result_columns) if isinstance(raw_result_columns, str) else raw_result_columns
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if parsed:
            return list(parsed), "DATA_ROLE_COLUMNS_FROM_RESULT_COLUMNS"
    combined = (row.get("resolved") or "") + " " + (row.get("raw") or "")
    cols = extract_select_columns(combined)
    if cols:
        return cols, "DATA_ROLE_COLUMNS_FROM_LITERAL_SQL"
    return None, ""


def resolve_data_flow(
    app_name: str,
    sql_findings: list[dict],
    test_context_index: dict[str, str] | None = None,
) -> list[DataFlowEdge]:
    """Funcion pura: dado el nombre de una app y sus sql_findings (tal como
    los devuelve analyzer.db.get_app()), devuelve una DataFlowEdge por
    target distinto tocado por esa app.

    test_context_index (opcional, ver build_test_context_index): evidencia
    agregada de TODO el portafolio sobre que tablas tienen señal fuerte de
    contexto de prueba. Si se omite, toda tabla se trata como
    'desconocido' -- las columnas ambiguas del catalogo (STATUS, DEVICE,
    CONNECTOR, ENDS, SERIAL_NUMBER, EMPLOYEE_NUMBER, WIP_ENTITY_NAME, etc.)
    nunca bastan solas para consumidor_resultado sin esa confirmacion; una
    señal fuerte en la propia lectura (TEST_DATE/TEST_*_ID/DEVICE_STATUS/
    PassFail/medicion) sigue siendo suficiente por si sola."""
    per_target: dict[str, dict] = {}

    for row in sql_findings:
        target = row.get("target")
        if not target:
            continue
        combined = (row.get("resolved") or "") + " " + (row.get("raw") or "")
        operation = detect_operation(combined)
        if operation is None:
            continue

        entry = per_target.setdefault(target, {"writes": [], "reads": []})

        if operation in ("insert", "update", "merge"):
            extractor = extract_insert_columns if operation == "insert" else extract_update_columns
            cols = extractor(combined) if operation != "merge" else None
            if cols:
                ev = _build_evidence("DATA_ROLE_COLUMNS_FROM_LITERAL_SQL")
                entry["writes"].append(OperationEvidence(operation, tuple(cols), ev))
            else:
                entry["writes"].append(OperationEvidence(operation, (), _build_evidence("UNKNOWN")))
        elif operation == "delete":
            entry["writes"].append(OperationEvidence("delete", (), _build_evidence("UNKNOWN")))
        elif operation == "select":
            cols, extractor_key = _resolve_select_columns(row)
            if cols:
                entry["reads"].append(OperationEvidence("select", tuple(cols), _build_evidence(extractor_key)))
            else:
                entry["reads"].append(OperationEvidence("select", (), _build_evidence("UNKNOWN")))

    results: list[DataFlowEdge] = []
    for target, entry in per_target.items():
        writes: list[OperationEvidence] = entry["writes"]
        reads: list[OperationEvidence] = entry["reads"]

        write_cols = {c for op in writes for c in op.columns}
        read_cols = {c for op in reads for c in op.columns}
        write_has_known_columns = any(op.columns for op in writes) or any(op.operation == "delete" for op in writes)
        read_has_known_columns = any(op.columns for op in reads)

        has_write = bool(writes)
        has_read = bool(reads)

        if has_write and has_read:
            role = "mixto"
            status = "resolved"
        elif has_write:
            if any(is_measurement_column(c) for c in write_cols):
                role = "productor_numerico"
                status = "resolved"
            elif write_has_known_columns:
                role = "productor_passfail"
                status = "resolved"
            else:
                role = "indeterminado"
                status = "unresolved_no_columns"
        elif has_read:
            # Regla revisada (2026-08-19, incremento test_context): "SELECT +
            # no medicion = consumidor_resultado" quedo ELIMINADA en el
            # incremento anterior -- producia falsos positivos reales (78% de
            # los 938 originales, ej. Polaridad/Release leyendo
            # PartNo/TYPES/digits de CustomItems). La correccion subsiguiente
            # (columna del catalogo status/metadata => consumidor_resultado)
            # demostro tener SU PROPIO falso positivo: columnas AMBIGUAS del
            # mismo catalogo (STATUS, DEVICE, CONNECTOR, ENDS, SERIAL_NUMBER,
            # EMPLOYEE_NUMBER, WIP_ENTITY_NAME) tambien existen, con evidencia
            # real, en tablas de catalogo ERP/RH/logistica/config (DJItem,
            # Employees, XXAFL_QAPV_LINE_OPERATIONS, etc. -- ~95 edges reales
            # auditados). Ahora se distingue:
            #   (A) señal FUERTE e independiente (TEST_DATE, TEST_*_ID,
            #       DEVICE_STATUS, PassFail, medicion) en la propia lectura
            #       -> consumidor_resultado, sin depender de test_context.
            #   (B) columna AMBIGUA del catalogo + test_context(tabla) ==
            #       confirmado (evidencia agregada de TODO el portafolio,
            #       nunca el nombre de la tabla) -> consumidor_resultado.
            #   (C) columna ambigua + test_context desconocido ->
            #       consumidor_general, NUNCA indeterminado -- SI hay
            #       columnas reales conocidas, solo no hay evidencia de que
            #       la tabla sea de resultados de prueba (conservacion de
            #       evidencia: indeterminado significa ausencia de columnas
            #       reconstruidas, no incertidumbre semantica).
            if any(is_measurement_column(c) for c in read_cols):
                role = "consumidor_medicion"
                status = "resolved"
            elif any(_is_strong_test_context_column(c) for c in read_cols):
                role = "consumidor_resultado"
                status = "resolved"
            elif (any(is_status_or_test_metadata_column(c) for c in read_cols)
                    and resolve_test_context(test_context_index, target) == TEST_CONTEXT_CONFIRMADO):
                role = "consumidor_resultado"
                status = "resolved"
            elif read_has_known_columns:
                role = "consumidor_general"
                status = "resolved"
            else:
                role = "indeterminado"
                status = "unresolved_no_columns"
        else:
            role = "indeterminado"
            status = "unresolved_no_columns"

        results.append(DataFlowEdge(
            app_name=app_name, target=target, role=role,
            writes=tuple(writes), reads=tuple(reads),
            resolution_status=status,
        ))
    return results


def resolve_data_flow_portfolio() -> list[DataFlowEdge]:
    """Capa de COMPOSICION portfolio-wide (2026-08-19, incremento
    "exposicion en UI") -- equivalente conceptual a
    server_resolution.resolve_portfolio(): toca la BD para enumerar apps y
    sus sql_findings ya persistidos, construye test_context_index UNA SOLA
    VEZ sobre todo el portafolio, y delega toda la clasificacion en
    resolve_data_flow() (la unica logica de clasificacion -- esta funcion no
    reimplementa ni un solo criterio, solo ensambla evidencia y llama a la
    funcion pura ya existente y ya testeada)."""
    apps_sql_findings: list[tuple[str, list[dict]]] = []
    for row in db.list_apps():
        data = db.get_app(row["id"])
        if data:
            apps_sql_findings.append((data["app"]["name"], data["sql_findings"]))

    test_context_index = build_test_context_index(apps_sql_findings)

    results: list[DataFlowEdge] = []
    for app_name, sql_findings in apps_sql_findings:
        results.extend(resolve_data_flow(app_name, sql_findings, test_context_index=test_context_index))
    return results


def resolve_data_flow_for_app(app_id: int) -> list[DataFlowEdge]:
    """Capa de COMPOSICION ACOTADA (2026-08-19, incremento de rendimiento):
    equivalente en resultado a filtrar resolve_data_flow_portfolio() para
    esta app (ver tests/test_data_flow_ui.py -- TestBoundedContextEquivalence
    prueba esta equivalencia edge por edge, incluida la validacion contra
    las 117 apps reales del portafolio), pero SIN el costo de recorrer TODO
    el portafolio: solo agrega evidencia de las tablas que esta app
    realmente toca, incluyendo otras apps que compartan esas mismas tablas
    (db.list_sql_findings_for_targets()) -- nunca de apps que no comparten
    ninguna tabla con ella, cuya evidencia jamas se consulta de todos modos
    (ver resolve_test_context: solo mira la clave de tabla de CADA target de
    esta app). Sigue delegando el 100% de la clasificacion en
    build_test_context_index()/resolve_data_flow(), nunca reimplementa
    ningun criterio."""
    data = db.get_app(app_id)
    if not data:
        return []
    app_name = data["app"]["name"]
    sql_findings = data["sql_findings"]

    own_target_keys = {normalize_table_key(row.get("target")) for row in sql_findings}
    own_target_keys.discard(None)

    apps_sql_findings: dict[str, list[dict]] = {app_name: list(sql_findings)}
    if own_target_keys:
        for row in db.list_sql_findings_for_targets(own_target_keys):
            apps_sql_findings.setdefault(row["app_name"], []).append(row)

    test_context_index = build_test_context_index(list(apps_sql_findings.items()))
    return resolve_data_flow(app_name, sql_findings, test_context_index=test_context_index)
