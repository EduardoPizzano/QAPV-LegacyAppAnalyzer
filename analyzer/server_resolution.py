"""Resolucion formal de servidor/base de datos para los targets de escritura
SQL que ya encontro extract.py (sql_findings.target), reemplazando el script
de investigacion ad hoc (ver server_resolution_scratchpad/resolve_table_servers.py
en el historial de la sesion Huella de Datos, 2026-08-17/18 -- se mantiene
como evidencia historica, la aplicacion NUNCA depende de el).

Que hace: para cada escritura (INSERT/UPDATE/DELETE/MERGE) ya detectada,
localiza el archivo fuente YA DECOMPILADO (nunca vuelve a decompilar) en
decompiled/<raiz>/, y camina hacia atras desde esa linea, dentro del mismo
metodo, buscando la construccion de conexion (SqlConnection/OracleConnection/
OleDbConnection) o asignacion de .ConnectionString mas cercana, tra
zandola hasta un `settings.name` conocido. Si esa conexion es un campo de
clase asignado en otro lado, cae a un respaldo de archivo completo (unico
setting de ese tipo referenciado via ConnectionStrings[...] en todo el
archivo).

Regla arquitectonica (explicita, pedida 2026-08-18): un servidor SOLO se
atribuye con evidencia real de CODIGO (connection string / setting / sql
finding / patron ya resuelto). Este modulo NUNCA consulta ningun inventario
externo de servidores para inventar, completar o inferir una resolucion
faltante -- de hecho, no existe ningun codigo aqui que siquiera SEPA leer ese
tipo de archivo. Cuando el codigo no da evidencia suficiente, el resultado es
uno de los estados "unresolved_*"/"not_applicable" de analyzer.unknown --
nunca un servidor inventado.

Los 6 fixes portados desde la investigacion ad hoc (cada uno con su propio
test en tests/test_server_resolution.py):
  1. El filtro de "es una escritura" se aplica sobre resolved+raw combinados,
     nunca solo sobre raw (el verbo real vive casi siempre en resolved).
  2. TERNARY_RE no exige parentesis envolvente -- un ternario con parentesis
     anidados en su condicion (`((a=="2") ? CX2 : CX)`) SI se detecta.
  3. Nunca se confia en SettingEntry.is_connection_string -- parse_connection_string()
     valida independientemente que el VALOR tenga forma de connection string real.
  4. `new SqlConnection()` sin argumento + `.ConnectionString = X;` en una
     linea separada se detecta como si fuera un ctor con argumento.
  5. Los nombres de setting se comparan normalizados (sin '_' inicial, sin
     distinguir mayusculas) -- "_cx"/"_connectionString" vs "CX"/"connectionString".
  6. `algo.ConnectionString = ConfigurationManager.ConnectionStrings["X"].ConnectionString;`
     (el indexer completo del lado derecho de una asignacion de propiedad) se
     detecta con un fallback dedicado.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import confidence, db
from .__version__ import ANALYZER_VERSION
from .evidence import Evidence
from .pipeline import DECOMPILED_DIR

REFERENCE_DATA_DIR = Path(__file__).parent / "reference_data"

WRITE_KEYWORDS = re.compile(r"(?i)\b(insert\s+into|update|delete\s+from|merge\s+into|merge\s+)")

SERVER_RE = re.compile(r"(?i)\b(?:Server|Data Source)\s*=\s*([^;]+)")
DATABASE_RE = re.compile(r"(?i)\b(?:Database|Initial Catalog)\s*=\s*([^;]+)")
ORACLE_HOST_RE = re.compile(r"(?i)HOST\s*=\s*([\w.\-]+)")
ORACLE_SID_RE = re.compile(r"(?i)(?:SID|SERVICE_NAME)\s*=\s*(\w+)")
BACKSLASH_RUN_RE = re.compile(r"\\+")

CONN_CTOR_RE = re.compile(r"new\s+(Sql|Oracle|OleDb)Connection\s*\(\s*([A-Za-z_][\w.]*)\s*\)")
# Patron real (LabelPrint2/Form1.cs: UpdateAppSettings): "new SqlConnection()"
# sin argumentos, seguido de una linea separada
# "sqlConnection.ConnectionString = CX;" -- fix #4.
CONN_STRING_PROP_RE = re.compile(r"\.ConnectionString\s*=\s*([A-Za-z_][\w.]*)\s*;")
CONN_STRINGS_REF_RE = re.compile(r'ConnectionStrings\s*\[\s*"(?:[^".]*\.)*([A-Za-z_]\w*)"\s*\]')
LOCAL_ASSIGN_RE = re.compile(r"\b([A-Za-z_][\w]*)\s*=\s*(.+?);\s*$")
# Fix #2: sin exigir que el ternario completo este envuelto en un solo par de
# parentesis -- una condicion con sus propios parentesis anidados
# (`((Planta == "2") ? CX2 : CX)`) rompia esa exigencia.
TERNARY_RE = re.compile(r"\?\s*([A-Za-z_][\w.]*)\s*:\s*([A-Za-z_][\w.]*)")

KIND_BY_CTOR = {"Sql": "sqlserver", "Oracle": "oracle", "OleDb": "sqlserver"}


def _normalize_backslashes(value: str) -> str:
    return BACKSLASH_RUN_RE.sub(r"\\", value)


def _normalize_identifier(name: str) -> str:
    """Fix #5: quita el guion bajo inicial (convencion de campo privado,
    ej. "_cx"/"_connectionString") y compara sin distinguir mayusculas --
    el codigo real usa "_cx"/"_connectionString"/"connectionString" para un
    campo asignado en otro lado desde el setting real ("CX"), casi nunca
    literalmente el mismo nombre con las mismas mayusculas."""
    return name.lstrip("_").lower()


def parse_connection_string(raw_value: str | None) -> dict | None:
    """Fix #3: nunca confia en SettingEntry.is_connection_string (esa
    bandera la pone _classify_setting() en extract.py y puede fallar --
    caso real confirmado: Monitor1 "CX2" = "Server=NAAMRT-QCS12;
    Database=QAPVMLN2;..." con is_connection_string=0). Valida
    independientemente que el VALOR mismo tenga forma de connection string
    real (Server=/Data Source=). Devuelve None si no es un valor de conexion
    reconocible."""
    value = (raw_value or "").strip()
    if not value:
        return None
    match = SERVER_RE.search(value)
    if not match:
        return None
    server_value = match.group(1).strip()
    if not server_value:
        return None
    if server_value.lower().startswith("(localdb)"):
        return {"server": _normalize_backslashes(server_value), "db": "?", "kind": "localdb"}
    if server_value.startswith("("):
        host_match = ORACLE_HOST_RE.search(value)
        sid_match = ORACLE_SID_RE.search(value)
        return {
            "server": host_match.group(1) if host_match else "?",
            "db": sid_match.group(1) if sid_match else "?",
            "kind": "oracle",
        }
    db_match = DATABASE_RE.search(value)
    return {
        "server": _normalize_backslashes(server_value),
        "db": db_match.group(1).strip() if db_match else "?",
        "kind": "sqlserver",
    }


def build_setting_lookup(settings: list[dict]) -> dict[str, dict]:
    """settings: filas de `settings` tal como las devuelve analyzer.db.get_app()
    (dicts con al menos 'name'/'default_value'). No filtra por
    is_connection_string=1 -- ver parse_connection_string()."""
    lookup: dict[str, dict] = {}
    for entry in settings:
        parsed = parse_connection_string(entry.get("default_value"))
        if parsed:
            lookup[entry["name"]] = parsed
    return lookup


def _resolve_expr_to_settings(expr: str, setting_names: set[str]) -> tuple[list[str], str | None]:
    """expr es lo que esta dentro de new SqlConnection(...), o el lado
    derecho de una asignacion previa a una variable local. Devuelve
    (nombres_de_setting_candidatos, provenance) -- provenance es "ternary"
    si vino de un operador ternario (2+ candidatos posibles), "direct" si es
    un solo identificador resuelto directamente."""
    expr = expr.strip()
    norm_map = {_normalize_identifier(n): n for n in setting_names}

    ternary_match = TERNARY_RE.search(expr)
    if ternary_match:
        candidates = []
        for group in ternary_match.groups():
            original = norm_map.get(_normalize_identifier(group))
            if original:
                candidates.append(original)
        if candidates:
            return candidates, "ternary"

    tail = expr.split(".")[-1]
    original = norm_map.get(_normalize_identifier(tail))
    if original:
        return [original], "direct"
    original = norm_map.get(_normalize_identifier(expr))
    if original:
        return [original], "direct"
    return [], None


def _find_method_body(lines: list[str], method_name: str | None) -> tuple[int, int] | None:
    """Devuelve (start_idx, end_idx) de las lineas del cuerpo del metodo
    dado, usando conteo de llaves simple desde la primera ocurrencia de
    'method_name(' que parezca una firma de metodo real."""
    if not method_name or method_name == "(top-level)":
        return None
    signature_re = re.compile(r"\b" + re.escape(method_name) + r"\s*\(")
    modifiers = ("void", "private", "public", "protected", "internal")
    for i, line in enumerate(lines):
        if signature_re.search(line) and any(m in line for m in modifiers):
            depth = 0
            started = False
            for j in range(i, min(i + 3000, len(lines))):
                depth += lines[j].count("{") - lines[j].count("}")
                if "{" in lines[j]:
                    started = True
                if started and depth == 0:
                    return i, j
            return i, min(i + 400, len(lines) - 1)
    return None


def _build_file_settings_by_kind(file_text: str, setting_lookup: dict[str, dict]) -> dict[str, set[str]]:
    """Escanea el ARCHIVO COMPLETO (no un metodo) por referencias
    ConnectionStrings["...NOMBRE"] a settings ya conocidos, agrupadas por
    tipo (sqlserver/oracle) -- respaldo para conexiones guardadas en un
    campo de clase asignado una sola vez fuera del metodo que las usa."""
    by_kind: dict[str, set[str]] = {"sqlserver": set(), "oracle": set()}
    for match in CONN_STRINGS_REF_RE.finditer(file_text):
        name = match.group(1)
        setting = setting_lookup.get(name)
        if setting:
            kind = setting["kind"] if setting["kind"] != "localdb" else "sqlserver"
            by_kind.setdefault(kind, set()).add(name)
    return by_kind


@dataclass(frozen=True)
class _ConnectionMatch:
    setting_names: list[str]
    provenance: str  # "direct" | "ternary" | "file_fallback"
    line_index: int  # indice dentro de method_lines donde se encontro
    snippet: str


def _resolve_target_connection(
    method_lines: list[str],
    target_line_idx: int,
    setting_names: set[str],
    file_settings_by_kind: dict[str, set[str]],
) -> _ConnectionMatch | None:
    """Escanea hacia atras desde target_line_idx (inclusive) buscando la
    conexion mas cercana. Si el argumento del constructor no resuelve a
    nivel de metodo, cae a file_settings_by_kind (fix de respaldo por campo
    de clase): si el archivo completo solo menciona UN setting de ese mismo
    tipo, se asume que es ese -- nunca si hay 2+ candidatos del mismo tipo."""
    # nombre de variable local -> (candidatos, provenance) -- ej.
    # `string connectionString = (Planta == "2") ? CX2 : CX;` seguido, en
    # otra linea, de `new SqlConnection(connectionString)` (patron real,
    # Monitor1/Form1.cs:179-180). La procedencia ("ternary") se preserva a
    # traves de esta indireccion -- perderla haria que un caso ambiguo
    # quedara mal etiquetado como si fuera una resolucion directa.
    local_vars: dict[str, tuple[list[str], str | None]] = {}
    last: _ConnectionMatch | None = None

    for i in range(0, target_line_idx + 1):
        line = method_lines[i]

        ctor_match = CONN_CTOR_RE.search(line)
        if ctor_match:
            ctor_kind, expr = ctor_match.groups()
            cands, provenance = _resolve_expr_to_settings(expr, setting_names)
            if not cands and expr in local_vars:
                cands, provenance = local_vars[expr]
            if not cands:
                kind = KIND_BY_CTOR.get(ctor_kind)
                fallback = file_settings_by_kind.get(kind, set())
                if len(fallback) == 1:
                    cands, provenance = list(fallback), "file_fallback"
            if cands:
                last = _ConnectionMatch(cands, provenance or "direct", i, line.strip())
            continue

        prop_match = CONN_STRING_PROP_RE.search(line)
        if prop_match:
            expr = prop_match.group(1)
            cands, provenance = _resolve_expr_to_settings(expr, setting_names)
            if not cands and expr in local_vars:
                cands, provenance = local_vars[expr]
            if cands:
                last = _ConnectionMatch(cands, provenance or "direct", i, line.strip())
            continue

        # Fix #6: `algo.ConnectionString = ConfigurationManager.ConnectionStrings["X"].ConnectionString;`
        # -- el lado derecho no es un identificador simple (CONN_STRING_PROP_RE
        # no lo captura), es el mismo indexer de CONN_STRINGS_REF_RE, dentro
        # de una asignacion de propiedad en vez de una declaracion de campo.
        if ".ConnectionString" in line:
            ref_match = CONN_STRINGS_REF_RE.search(line)
            if ref_match:
                cands, provenance = _resolve_expr_to_settings(ref_match.group(1), setting_names)
                if cands:
                    last = _ConnectionMatch(cands, provenance or "direct", i, line.strip())
                continue

        assign_match = LOCAL_ASSIGN_RE.search(line.strip())
        if assign_match:
            varname, rhs = assign_match.groups()
            cands, provenance = _resolve_expr_to_settings(rhs, setting_names)
            if cands:
                local_vars[varname] = (cands, provenance)

    return last


def _locate_source_file(decompiled_root: Path, file_rel: str) -> Path | None:
    candidate = decompiled_root / file_rel.replace("\\", "/")
    if candidate.is_file():
        return candidate
    if not decompiled_root.exists():
        return None
    matches = list(decompiled_root.rglob(Path(file_rel.replace("\\", "/")).name))
    return matches[0] if matches else None


@dataclass(frozen=True)
class ServerCandidate:
    server: str
    database: str
    setting_name: str
    kind: str  # "sqlserver" | "oracle" | "localdb"


@dataclass(frozen=True)
class ConnectionResolution:
    """Una fila por target de escritura (tabla o SP) distinto encontrado
    para una app. `resolution_status` es uno de analyzer.unknown.RESOLUTION_STATUSES
    -- nunca se inventa un servidor cuando el estado no es "resolved"."""

    target: str
    is_stored_procedure: bool
    resolution_status: str
    candidates: tuple[ServerCandidate, ...] = ()
    evidence: Evidence = field(default_factory=Evidence)
    is_oracle_erp_table: bool = False


def resolve_write_targets(
    app_name: str,
    settings: list[dict],
    sql_findings: list[dict],
    decompiled_root: Path,
) -> list[ConnectionResolution]:
    """Funcion pura (sin BD, sin red): dado lo que analyzer.db.get_app()
    devuelve para una app (settings/sql_findings) y la raiz donde ya viven
    sus archivos decompilados (nunca se decompila de nuevo aqui), devuelve
    una ConnectionResolution por target de escritura distinto."""
    setting_lookup = build_setting_lookup(settings)
    if not setting_lookup:
        return []
    setting_names = set(setting_lookup)

    # Fix #1: el filtro de "es una escritura" se aplica en Python sobre
    # resolved+raw combinados, NUNCA solo sobre `raw` a nivel de query --
    # `raw` es el boilerplate C# (ej. "using (SqlCommand sqlCommand = new
    # SqlCommand(cmdText, sqlConnection))..."), casi nunca contiene el verbo
    # INSERT/UPDATE/DELETE en si mismo; el SQL reconstruido con el verbo
    # real vive en `resolved`.
    write_rows = [
        row for row in sql_findings
        if row.get("target")
        and WRITE_KEYWORDS.search((row.get("resolved") or "") + " " + (row.get("raw") or ""))
    ]
    if not write_rows:
        return []

    file_lines_cache: dict[str, list[str] | None] = {}
    file_kind_cache: dict[str, dict[str, set[str]]] = {}
    per_target: dict[str, dict] = {}

    for row in write_rows:
        target = row["target"]
        entry = per_target.setdefault(target, {
            "is_stored_procedure": bool(row.get("is_stored_procedure")),
            "candidate_names": {},  # setting name -> ServerCandidate
            "match": None,  # el primer _ConnectionMatch encontrado, para Evidence
            "status_override": None,
        })

        file_key = row.get("file")
        if file_key not in file_lines_cache:
            path = _locate_source_file(decompiled_root, file_key) if file_key else None
            text = path.read_text(encoding="utf-8", errors="ignore") if path else None
            file_lines_cache[file_key] = text.splitlines() if text is not None else None
            file_kind_cache[file_key] = (
                _build_file_settings_by_kind(text, setting_lookup) if text is not None else {}
            )
        lines = file_lines_cache[file_key]

        if lines is None:
            entry["status_override"] = entry["status_override"] or "unresolved_no_source_file"
            continue

        body = _find_method_body(lines, row.get("method"))
        if body is None:
            entry["status_override"] = entry["status_override"] or "unresolved_no_literal"
            continue

        start, end = body
        method_lines = lines[start:end + 1]

        needle = (row.get("raw") or "").strip()[:40]
        target_idx = len(method_lines) - 1
        if needle:
            for i, candidate_line in enumerate(method_lines):
                if needle[:25] and needle[:25] in candidate_line:
                    target_idx = i
                    break

        match = _resolve_target_connection(
            method_lines, target_idx, setting_names, file_kind_cache.get(file_key, {})
        )
        if match:
            for name in match.setting_names:
                setting = setting_lookup[name]
                entry["candidate_names"][name] = ServerCandidate(
                    server=setting["server"], database=setting["db"],
                    setting_name=name, kind=setting["kind"],
                )
            if entry["match"] is None:
                entry["match"] = (match, start)

    results: list[ConnectionResolution] = []
    for target, entry in per_target.items():
        candidates = tuple(entry["candidate_names"].values())
        match_info = entry["match"]

        if entry["status_override"] and not candidates:
            status = entry["status_override"]
            evidence = Evidence(extractor="UNKNOWN", confidence=confidence.UNKNOWN)
        elif not candidates:
            status = "unresolved_no_literal"
            evidence = Evidence(extractor="UNKNOWN", confidence=confidence.UNKNOWN)
        elif len(candidates) == 1:
            status = "resolved"
            evidence = _build_evidence(match_info, "CONNECTION_CTOR_DIRECT_SETTING", file_lines_cache)
        else:
            status = "unresolved_ambiguous_conditional"
            evidence = _build_evidence(match_info, "CONNECTION_AMBIGUOUS_CONDITIONAL", file_lines_cache)

        if match_info and match_info[0].provenance == "file_fallback" and status == "resolved":
            evidence = _build_evidence(match_info, "SETTINGS_CLASS_LITERAL", file_lines_cache)

        results.append(ConnectionResolution(
            target=target,
            is_stored_procedure=entry["is_stored_procedure"],
            resolution_status=status,
            candidates=candidates,
            evidence=evidence,
            is_oracle_erp_table=(not entry["is_stored_procedure"]) and is_oracle_erp_table(target),
        ))
    return results


def _build_evidence(match_info, extractor_key: str, file_lines_cache) -> Evidence:
    if not match_info:
        return Evidence(extractor=extractor_key, confidence=confidence.resolve_confidence(extractor_key))
    match, method_start = match_info
    return Evidence(
        line_number=method_start + match.line_index + 1,
        snippet=match.snippet,
        extractor=extractor_key,
        pattern=match.provenance,
        confidence=confidence.resolve_confidence(extractor_key),
        analyzer_version=ANALYZER_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


_ORACLE_ERP_TABLES_CACHE: frozenset[str] | None = None


def _load_oracle_erp_tables() -> frozenset[str]:
    global _ORACLE_ERP_TABLES_CACHE
    if _ORACLE_ERP_TABLES_CACHE is None:
        path = REFERENCE_DATA_DIR / "oracle_erp_tables.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        _ORACLE_ERP_TABLES_CACHE = frozenset(name.upper() for name in data["tables"])
    return _ORACLE_ERP_TABLES_CACHE


def is_oracle_erp_table(table_name: str) -> bool:
    """Coincidencia EXACTA (sin distinguir mayusculas) contra el catalogo
    real de 29 tablas del DDL -- deliberadamente NO una regla de patron tipo
    "empieza con XXAFL_QAPV_TESTS_" (pedido explicito del usuario,
    2026-08-18): una tabla como XXAFL_QAPV_TESTS_BINNA_202406 o
    XXAFL_QAPV_MPOENDFACE_VALIDATIONS se PARECE por nombre pero no es
    ninguna de las 29 tablas reales del catalogo, asi que NO se marca ERP."""
    return (table_name or "").strip().upper() in _load_oracle_erp_tables()


@dataclass(frozen=True)
class AppResolution:
    app_id: int
    app_name: str
    review_status: str | None
    targets: tuple[ConnectionResolution, ...]


def resolve_app(app_id: int) -> AppResolution | None:
    """Wrapper delgado que toca la BD: obtiene los datos ya persistidos de
    una app (analyzer.db.get_app()) y delega toda la logica de resolucion en
    resolve_write_targets() (pura, testeable sin BD)."""
    data = db.get_app(app_id)
    if not data:
        return None
    app_name = data["app"]["name"]
    decompiled_root = DECOMPILED_DIR / app_name.split("/")[0]
    targets = resolve_write_targets(app_name, data["settings"], data["sql_findings"], decompiled_root)
    return AppResolution(
        app_id=app_id, app_name=app_name,
        review_status=data["app"].get("review_status"),
        targets=tuple(targets),
    )


def resolve_portfolio() -> list[AppResolution]:
    """Una AppResolution por app del portafolio que tiene al menos un target
    de escritura resoluble (apps sin ninguna conexion/escritura detectada se
    omiten, igual que el resto de vistas de portafolio -- ver db.list_apps())."""
    results = []
    for row in db.list_apps():
        resolution = resolve_app(row["id"])
        if resolution and resolution.targets:
            results.append(resolution)
    return results
