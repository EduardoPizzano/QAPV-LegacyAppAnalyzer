"""Scans decompiled .cs source for connection strings, local file I/O, and
SQL/Oracle usage (queries and stored procedures)."""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .confidence import resolve_confidence
from .evidence import Evidence


# ---------- Connection strings & other settings (Settings.cs) ----------

SETTING_BLOCK = re.compile(
    r"""
    (?P<attrs>(?:\[[^\]]*\]\s*)+)          # one or more [Attribute(...)] lines
    (?:internal\s+|public\s+)?             # optional access modifier on property
    (?:sealed\s+)?
    (?:static\s+)?
    \w[\w<>\[\],\.\?]*\s+                  # return type
    (?P<name>\w+)\s*                       # property name
    (?=\{|=>)                              # followed by { get; } or =>
    """,
    re.VERBOSE,
)

DEFAULT_VALUE = re.compile(r'DefaultSettingValue\(\s*"((?:[^"\\]|\\.)*)"\s*\)')
IS_CONN_STRING = re.compile(r"SpecialSetting\s*\(\s*SpecialSetting\.ConnectionString\s*\)")
LOOKS_LIKE_DB_CONN = re.compile(r"(?i)(Server|Data Source|Database|User Id|UID)\s*=")
LOOKS_LIKE_PATH = re.compile(r"^[A-Za-z]:\\|^\\\\|/")


@dataclass
class SettingEntry:
    name: str
    default_value: str
    is_connection_string: bool
    category: str  # "sql_or_oracle" | "local_path" | "other"
    source_file: str
    # Fase 1 (VALIDATION_FRAMEWORK.md seccion 0): complementa, no reemplaza,
    # los campos de arriba -- nadie construye esto con datos reales todavia
    # (eso es Fase 2+), asi que todo SettingEntry de hoy recibe el default,
    # que documenta explicitamente "no instrumentado" en vez de inventar un
    # valor. Ver analyzer/evidence.py.
    evidence: Evidence = field(default_factory=Evidence)


def _classify_setting(value: str, marked_as_conn_string: bool) -> str:
    if marked_as_conn_string or LOOKS_LIKE_DB_CONN.search(value):
        return "sql_or_oracle"
    if LOOKS_LIKE_PATH.match(value):
        return "local_path"
    return "other"


CONNECTION_STRINGS_BLOCK = re.compile(r"<connectionStrings\b.*?</connectionStrings>", re.DOTALL)
CONFIG_ADD_TAG = re.compile(r"<add\b[^>]*/>", re.DOTALL)
CONFIG_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _appconfig_add_tag_locations(config_text: str) -> list[tuple[int, str]]:
    """Ubica, en orden de documento, cada tag <add/> ACTIVO (no comentado)
    dentro del bloque <connectionStrings> del texto crudo. xml.etree.ElementTree
    no expone el numero de linea de un elemento (confirmado: en esta version
    de Python el XMLParser acelerado por C ni siquiera expone el parser expat
    interno vía el truco `_start`/`self.parser` documentado para versiones
    anteriores) -- se recupera buscando el mismo texto que ET ya valido como
    XML real, acotado al bloque <connectionStrings> para no confundirse con
    otros <add> del archivo (ej. <appSettings>). El orden de este resultado
    coincide con el de `conn_strings_el.findall("add")` porque ambos recorren
    el mismo bloque en el mismo orden de documento, ignorando los mismos
    comentarios -- se empareja por indice en vez de por nombre/valor."""
    block_match = CONNECTION_STRINGS_BLOCK.search(config_text)
    if not block_match:
        return []
    block_text = block_match.group(0)
    comment_spans = [m.span() for m in CONFIG_COMMENT.finditer(block_text)]

    def _inside_a_comment(pos: int) -> bool:
        return any(start <= pos < end for start, end in comment_spans)

    locations: list[tuple[int, str]] = []
    for tag_match in CONFIG_ADD_TAG.finditer(block_text):
        if _inside_a_comment(tag_match.start()):
            continue
        absolute_start = block_match.start() + tag_match.start()
        line_number = config_text.count("\n", 0, absolute_start) + 1
        snippet = " ".join(tag_match.group(0).split())
        locations.append((line_number, snippet))
    return locations


def _find_appconfig_connection_strings(root: Path) -> list[SettingEntry]:
    """Algunos apps leen su connection string directamente de app.config vía
    ConfigurationManager.ConnectionStrings["Name"] (patron ADO.NET clasico) sin
    pasar nunca por el Settings.cs generado por el designer -- invisible para el
    escaneo de arriba (confirmado en ReportViewer/InterConfig/InterAFL/SGI/
    ReferenceControlWpf: apps con decenas de sql_findings pero 0 settings
    capturados). Se parsea con XML real, no regex, para que las entradas
    comentadas (<!-- <add .../> -->, dejadas ahi por versiones de desarrollo
    anteriores) se ignoren automaticamente en vez de tener que replicar esa
    logica a mano.

    Fase 2 (VALIDATION_FRAMEWORK.md seccion 0): esta es la primera extraccion
    real que construye un Evidence con datos reales -- no un elemento XML
    declarado explicitamente en <connectionStrings>, sin ambiguedad de
    parsing, es la fuente de mayor confianza de este extractor
    (APP_CONFIG_EXPLICIT_CONNECTION, ver analyzer/confidence.py)."""
    entries: list[SettingEntry] = []
    for config_file in root.rglob("*.config"):
        try:
            tree = ET.parse(config_file)
        except ET.ParseError:
            continue
        conn_strings_el = tree.getroot().find("connectionStrings")
        if conn_strings_el is None:
            continue
        config_text = config_file.read_text(encoding="utf-8", errors="ignore")
        locations = _appconfig_add_tag_locations(config_text)
        add_elements = conn_strings_el.findall("add")
        source_file = str(config_file.relative_to(root))
        for index, add_el in enumerate(add_elements):
            name = add_el.get("name")
            value = add_el.get("connectionString")
            if not name or not value:
                continue
            line_number, snippet = locations[index] if index < len(locations) else (None, None)
            evidence = Evidence(
                source_file=source_file,
                line_number=line_number,
                snippet=snippet,
                extractor="APP_CONFIG_EXPLICIT_CONNECTION",
                pattern="connectionStrings/add",
                confidence=resolve_confidence("APP_CONFIG_EXPLICIT_CONNECTION"),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            entries.append(
                SettingEntry(
                    name=name,
                    default_value=value,
                    is_connection_string=True,
                    category=_classify_setting(value, True),
                    source_file=source_file,
                    evidence=evidence,
                )
            )
    return entries


def find_settings(root: Path) -> list[SettingEntry]:
    """Incremento Funcional 2 (VALIDATION_FRAMEWORK.md seccion 0/3): el
    mecanismo DefaultSettingValue -- el "dominante de descubrimiento de
    conexiones en este portafolio" segun analyzer/confidence.py -- ahora
    construye un Evidence real, mismo patron ya usado en
    _find_appconfig_connection_strings(). Se aplica a TODA SettingEntry que
    sale de este loop (conexion, ruta local u otra), no solo a las
    conexiones: es el mismo punto de extraccion para las tres categorias, no
    tendria sentido instrumentar solo una."""
    entries: list[SettingEntry] = []
    seen_values: set[str] = set()
    for cs_file in root.rglob("*.cs"):
        if "Settings" not in cs_file.name:
            continue
        text = cs_file.read_text(encoding="utf-8", errors="ignore")
        if "DefaultSettingValue" not in text:
            continue
        source_file = str(cs_file.relative_to(root))
        for match in SETTING_BLOCK.finditer(text):
            attrs = match.group("attrs")
            default_match = DEFAULT_VALUE.search(attrs)
            if not default_match:
                continue
            value = default_match.group(1)
            is_conn = bool(IS_CONN_STRING.search(attrs))

            # Linea/snippet reales del atributo [DefaultSettingValue(...)] --
            # `attrs` es un substring de `text` (match.group("attrs")), asi
            # que su offset absoluto es match.start("attrs") + el offset del
            # match DENTRO de attrs. Mismo mecanismo de conteo de "\n" ya
            # usado en _appconfig_add_tag_locations(), nunca inventado.
            absolute_offset = match.start("attrs") + default_match.start()
            line_number = text.count("\n", 0, absolute_offset) + 1
            snippet = text.splitlines()[line_number - 1].strip()

            evidence = Evidence(
                source_file=source_file,
                line_number=line_number,
                snippet=snippet,
                extractor="SETTINGS_DEFAULT_VALUE",
                pattern="DefaultSettingValue",
                confidence=resolve_confidence("SETTINGS_DEFAULT_VALUE"),
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            entries.append(
                SettingEntry(
                    name=match.group("name"),
                    default_value=value,
                    is_connection_string=is_conn,
                    category=_classify_setting(value, is_conn),
                    source_file=source_file,
                    evidence=evidence,
                )
            )
            seen_values.add(value.strip())

    # app.config a veces mirrorea el mismo valor bajo otro nombre (ej.
    # "Namespace.Properties.Settings.CX") -- se deduplica por VALOR, no por
    # nombre, para no reportar la misma conexion real dos veces.
    for entry in _find_appconfig_connection_strings(root):
        if entry.default_value.strip() in seen_values:
            continue
        entries.append(entry)
        seen_values.add(entry.default_value.strip())

    return entries


# ---------- Method / class tracking ----------

METHOD_SIG = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)*"
    r"(?:public|private|protected|internal)\s+"
    r"(?:static\s+)?(?:async\s+)?(?:virtual\s+|override\s+)?"
    r"[\w<>\[\],\.\?]+\s+"
    r"(\w+)\s*\([^;{]*\)\s*(\{|=>|$)"
)

CLASS_SIG = re.compile(r"^\s*(?:public|private|internal)\s+(?:partial\s+|sealed\s+|static\s+)*class\s+(\w+)")


# ---------- SQL / Oracle triggers ----------

SQL_TRIGGER = re.compile(
    r"new\s+(SqlConnection|OracleConnection)\s*\(|"
    r"\bCommandText\s*=|"
    r"new\s+(SqlCommand|OracleCommand)\s*\("
)

# Incremento Funcional 3A -- variables que hoy disparan SQL_TRIGGER sin
# ningun literal inline (`raw` sin '"') pero SI referencian una variable/
# StringBuilder resoluble DENTRO del mismo metodo. El patron mas comun en el
# portafolio real (`new SqlCommand(cmdText, connection)`, dos argumentos) NO
# lo reconocia ninguno de los patrones anteriores -- confirmado con la
# clasificacion real de los 522 grupos sin resolver (ver VALIDATION_STRATEGY.md
# Incremento 3A): 96.1% de esos grupos SI tenian el valor resuelto un par de
# lineas antes, simplemente nunca se intentaba buscarlo.
VAR_IN_COMMANDTEXT_ASSIGN = re.compile(r"CommandText\s*=\s*(\w+)\s*;")
VAR_AS_COMMAND_CTOR_ARG = re.compile(r"new\s+(?:Sql|Oracle)Command\s*\(\s*(\w+)\s*[,)]")
TOSTRING_IN_TRIGGER = re.compile(r"\b(\w+)\.ToString\s*\(\s*\)")

# Abre un literal de C# en cualquiera de sus 4 formas (interpolado-verbatim,
# verbatim, interpolado, regular) -- el orden importa: los prefijos de 2
# caracteres deben probarse antes que los de 1 para no cortar el match a
# medias (ej. "$@" antes que "$").
STRING_OPENER = re.compile(r'\$@"|@\$"|@"|\$"|"')

# Una linea de control de flujo (if/else/for/foreach/while/switch) entre la
# asignacion/declaracion y el uso significa que el valor final depende de una
# condicion que este analizador NO evalua (alcance explicito: nada de
# ejecucion simbolica) -- mejor no reconstruir que fabricar un valor que solo
# es cierto en una de varias rutas posibles.
CONDITIONAL_LINE = re.compile(r"^\s*(if\s*\(|else\b|for\s*\(|foreach\s*\(|while\s*\(|switch\s*\()", re.MULTILINE)
# Operador ternario inline (`cond ? a : b`) -- misma razon que arriba, pero
# no ancla a inicio de linea porque normalmente aparece a mitad de una
# asignacion (`var = cond ? "a" : "b";`).
TERNARY_HINT = re.compile(r"\)\s*\?\s*\(|[^=!<>]\?[^:.]*:")

SB_DECLARE = re.compile(r"\bStringBuilder\s+(\w+)\b|\b(\w+)\s*=\s*new\s+StringBuilder\s*\(")

STORED_PROC_TYPE = re.compile(r"CommandType\.StoredProcedure")

SQL_KEYWORDS = re.compile(
    r"(?i)\b(select|insert\s+into|update|delete\s+from|with|create|alter|drop|exec(ute)?)\b"
)

TABLE_FROM = re.compile(r"(?i)\bfrom\s+\[?([\w\.]+)\]?")
TABLE_INTO = re.compile(r"(?i)\binto\s+\[?([\w\.]+)\]?")
TABLE_UPDATE = re.compile(r"(?i)\bupdate\s+\[?([\w\.]+)\]?")
FIRST_STRING_LITERAL = re.compile(r'"([^"]*)"')
BARE_PROC_NAME = re.compile(r"^\s*(\w+)\s*(?:'|$)")
SCHEMA_QUALIFIED_PROC = re.compile(r"^\[?(\w+)\]?\.\[?(\w+)\]?$")
ORACLE_PKG_CALL = re.compile(r"(\w+_pkg)\.(\w+)\s*\(")

# Extracts the SqlCommand/OracleCommand variable name a CommandText assignment
# or "new SqlCommand" declaration is acting on, so its .Parameters.Add(...)
# calls (usually a few lines below) can be tied back to the right finding.
CMD_VAR = re.compile(r"(\w+)\.CommandText\s*=|(?:Sql|Oracle)Command\s+(\w+)\s*=")

PARAM_ADD = re.compile(
    r'(\w+)\.Parameters\.Add\(\s*"(@\w+)"\s*(?:,\s*(?:Sql|Oracle)DbType\.(\w+))?(?:,\s*\d+)?\s*\)'
    r'\.Value\s*=\s*(?:\(object\)\s*)?(.+?);'
)
PARAM_ADD_WITH_VALUE = re.compile(r'(\w+)\.Parameters\.AddWithValue\(\s*"(@\w+)"\s*,\s*(.+?)\);')
EXECUTE_CALL = re.compile(r"(\w+)\.Execute(?:Reader|NonQuery|Scalar)\w*\s*\(")

EXECUTE_READER_ASSIGN = re.compile(r"(\w+)\s*=\s*(\w+)\.ExecuteReader\s*\(")
READER_COLUMN_ACCESS = re.compile(r'(\w+)\s*\[\s*"([^"]+)"\s*\]')


def _find_method_end(lines: list[str], method_start_idx: int) -> int:
    """Returns the line index where the method starting at method_start_idx
    closes its body (brace depth returns to 0), capped at a generous window.
    Used to stop forward-scans (parameters, result columns) from bleeding into
    the NEXT method — which matters because reader/command variable names
    like "sqlDataReader" are routinely reused across unrelated methods in
    this codebase, so an unbounded scan window misattributes columns."""
    depth = 0
    started = False
    limit = min(method_start_idx + 500, len(lines))
    for i in range(method_start_idx, limit):
        depth += lines[i].count("{") - lines[i].count("}")
        if "{" in lines[i]:
            started = True
        if started and depth <= 0:
            return i
    return limit


def _extract_parameters(lines: list[str], start_idx: int, cmd_var: str, end_idx: int) -> list[str]:
    """Scans forward from a CommandText/SqlCommand line for .Parameters.Add(...)
    / .AddWithValue(...) calls on the same command variable, stopping once that
    command actually executes (or at the end of the enclosing method)."""
    params: list[str] = []
    for i in range(start_idx, min(start_idx + 80, end_idx, len(lines))):
        line = lines[i]
        exec_match = EXECUTE_CALL.search(line)
        if exec_match and exec_match.group(1) == cmd_var:
            break
        m = PARAM_ADD.search(line)
        if m and m.group(1) == cmd_var:
            name, sqltype, value = m.group(2), m.group(3), m.group(4).strip()
            type_part = f" ({sqltype})" if sqltype else ""
            params.append(f"{name}{type_part} <- {value}")
            continue
        m2 = PARAM_ADD_WITH_VALUE.search(line)
        if m2 and m2.group(1) == cmd_var:
            params.append(f"{m2.group(2)} <- {m2.group(3).strip()}")
    return params


def _extract_result_columns(lines: list[str], start_idx: int, cmd_var: str, end_idx: int) -> list[str]:
    """Scans forward from a CommandText/SqlCommand line to find its
    ExecuteReader() call and the resulting reader variable, then collects
    every reader["ColumnName"] access on that variable — documents the shape
    of the result set the calling code actually consumes, best effort. Never
    scans past end_idx (the enclosing method's closing brace)."""
    reader_var = None
    columns: list[str] = []
    seen: set[str] = set()
    for i in range(start_idx, min(start_idx + 150, end_idx, len(lines))):
        line = lines[i]
        if reader_var is None:
            m = EXECUTE_READER_ASSIGN.search(line)
            if m and m.group(2) == cmd_var:
                reader_var = m.group(1)
            continue
        for m in READER_COLUMN_ACCESS.finditer(line):
            if m.group(1) == reader_var and m.group(2) not in seen:
                seen.add(m.group(2))
                columns.append(m.group(2))
    return columns


# ---------- Local file / process / printer / serial / network I/O triggers ----------
# Beyond plain file access, these legacy apps integrate with label printers
# (BarTender/PrintDocument), barcode/serial hardware, other executables, and
# occasionally HTTP/SMTP — all invisible to a scanner that only looks for
# File./Directory. calls.

LOCAL_IO_TRIGGER = re.compile(
    r"\bFile\.(Exists|ReadAllText|WriteAllText|AppendAllText|Copy|Move|Delete|Open)\s*\(|"
    r"\bDirectory\.(Exists|CreateDirectory|GetFiles|Delete)\s*\(|"
    r"\bnew\s+(StreamReader|StreamWriter|FileStream)\s*\(|"
    r"\bnew\s+DirectoryInfo\s*\(|"
    r"\bProcess\.Start\s*\(|"
    r"\bnew\s+(PrintDocument|PrintDialog|PrinterSettings)\s*\(|"
    r"\bnew\s+SerialPort\s*\(|"
    r"\bnew\s+(HttpClient|WebClient|HttpWebRequest|SmtpClient)\s*\(|"
    r"\bWebRequest\.Create\s*\(|"
    r"\bnew\s+BarTender\.Application\s*\(|"
    r"\.PrintOut\s*\("
)


@dataclass
class SqlFinding:
    file: str
    class_name: str
    method: str
    kind: str                    # "SqlConnection" | "OracleConnection" | "CommandText" | "SqlCommand" | "OracleCommand"
    raw: str
    resolved: Optional[str] = None
    category: str = "query"      # "query" | "stored_procedure" | "oracle_package_call"
    target: Optional[str] = None  # table name / SP name / package.function, best effort
    is_stored_procedure: bool = False
    parameters: list[str] = field(default_factory=list)  # "@Name (SqlType) <- csharpExpr", best effort
    result_columns: list[str] = field(default_factory=list)  # column names read via reader["X"], best effort
    # Fase 1: ver comentario identico en SettingEntry, arriba.
    evidence: Evidence = field(default_factory=Evidence)


@dataclass
class LocalIOFinding:
    file: str
    class_name: str
    method: str
    operation: str   # e.g. "File.ReadAllText", "new StreamWriter"
    raw: str
    # Fase 1: ver comentario identico en SettingEntry, arriba.
    evidence: Evidence = field(default_factory=Evidence)


def _capture_statement(lines: list[str], start_idx: int) -> str:
    depth = 0
    collected = []
    for i in range(start_idx, min(start_idx + 40, len(lines))):
        line = lines[i]
        collected.append(line.strip())
        depth += line.count("(") - line.count(")")
        if ";" in line and depth <= 0:
            break
    return " ".join(collected)


def _skip_string_literal(text: str, i: int) -> int:
    """`i` debe apuntar al inicio de un literal (ya confirmado via
    STRING_OPENER.match). Regresa el indice justo despues de su comilla de
    cierre, saltando el contenido correctamente para cada forma (verbatim
    usa "" como escape, regular usa \\; ambas formas de string literal en C#
    admiten saltos de linea reales dentro de [^"] sin ningun manejo especial
    -- por eso este mecanismo ya soporta multilinea de forma natural)."""
    m = STRING_OPENER.match(text, i)
    is_verbatim = "@" in m.group(0)
    j, n = m.end(), len(text)
    while j < n:
        if is_verbatim and text[j] == '"' and j + 1 < n and text[j + 1] == '"':
            j += 2
            continue
        if not is_verbatim and text[j] == "\\" and j + 1 < n:
            j += 2
            continue
        if text[j] == '"':
            return j + 1
        j += 1
    return n


def _unescape_csharp_literal(raw: str) -> str:
    """Solo maneja los 2 escapes que realmente importan para mostrar texto
    SQL (comilla y backslash) -- suficiente para snippets legibles, sin
    reimplementar un lexer completo de C#."""
    if raw.startswith(("@\"", "$@\"", "@$\"")):
        start = raw.index('"') + 1
        return raw[start:-1].replace('""', '"')
    start = raw.index('"') + 1
    return raw[start:-1].replace('\\"', '"').replace("\\\\", "\\")


def _find_statement_end(text: str, start: int) -> Optional[int]:
    """Regresa el indice del ';' que termina la sentencia que empieza en
    `start`, saltando el contenido de literales (para que un ';' o '"'
    dentro de un string no se confunda con sintaxis) y respetando el anidado
    de parentesis. None si nunca cierra dentro de `text`."""
    i, n, depth = start, len(text), 0
    while i < n:
        if STRING_OPENER.match(text, i):
            i = _skip_string_literal(text, i)
            continue
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == ";" and depth <= 0:
            return i
        i += 1
    return None


def _find_matching_close_paren(text: str, start: int) -> Optional[int]:
    """`start` apunta justo despues del '(' de apertura de una llamada (ej.
    tras `Append(`). Regresa el indice del ')' que la cierra, saltando
    literales y parentesis anidados. None si nunca cierra."""
    i, n, depth = start, len(text), 1
    while i < n:
        if STRING_OPENER.match(text, i):
            i = _skip_string_literal(text, i)
            continue
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _tokenize_string_expression(expr_text: str) -> Optional[list[tuple[str, str]]]:
    """Descompone una expresion `"lit1" + expr + "lit2" + ...` en tokens
    ("literal", texto) / ("expr", texto-c#-tal-cual). Nunca lanza excepcion;
    regresa lista vacia si no encontro nada reconocible (el llamador decide
    si eso cuenta como "no se pudo reconstruir")."""
    tokens: list[tuple[str, str]] = []
    i, n = 0, len(expr_text)
    while i < n:
        if STRING_OPENER.match(expr_text, i):
            end = _skip_string_literal(expr_text, i)
            tokens.append(("literal", _unescape_csharp_literal(expr_text[i:end])))
            i = end
            continue
        if expr_text[i] == "+":
            i += 1
            continue
        j = i
        # Se detiene tambien si lo que sigue ya es el inicio de un literal
        # (@"/$"/$@"/@$") -- de lo contrario el '@'/'$' se traga como parte
        # de este chunk "expr" y el literal que sigue se corrompe (bug real
        # encontrado con el fixture verbatim_multiline_case: `@"..."` se
        # tokenizaba como expr="@" + literal="..." en vez de un solo literal).
        while j < n and expr_text[j] not in "\"+" and not STRING_OPENER.match(expr_text, j):
            j += 1
        chunk = expr_text[i:j].strip()
        if chunk:
            tokens.append(("expr", chunk))
        i = j if j > i else i + 1
    return tokens


def _render_tokens(tokens: list[tuple[str, str]]) -> tuple[str, str]:
    """Regresa (texto_reconstruido, confidence_key). Si TODOS los tokens son
    literales, es un valor 100% conocido -- HARDCODED_METHOD_LITERAL (mismo
    peso que un `var = "...";` simple, ver analyzer/confidence.py). Si queda
    algun segmento dinamico (una variable/expresion C# cuyo valor en tiempo
    de ejecucion no conocemos), se marca entre llaves y el resultado es
    PARTIAL_RECONSTRUCTION -- sabemos el esqueleto literal exacto, no los
    valores reales. Nunca se inventa un valor para el segmento dinamico."""
    parts = []
    has_dynamic = False
    for kind, value in tokens:
        if kind == "literal":
            parts.append(value)
        else:
            has_dynamic = True
            parts.append(f"{{{value}}}")
    confidence_key = "PARTIAL_RECONSTRUCTION" if has_dynamic else "HARDCODED_METHOD_LITERAL"
    return "".join(parts), confidence_key


def _reconstruct_dynamic_sql(
    lines: list[str], method_start_idx: int, trigger_idx: int, var_name: str
) -> Optional[tuple[str, str, str, int]]:
    """Reconstruccion best-effort, DENTRO DEL MISMO METODO, del valor de
    `var_name` (o de `var_name.ToString()` si es un StringBuilder) referenciado
    en el trigger de SQL en `trigger_idx`. Incremento Funcional 3A -- cubre
    literal simple, literal multilinea, verbatim strings, concatenacion con
    '+', y StringBuilder.Append()/AppendLine()/ToString(), siempre que la
    construccion completa sea lineal (sin if/else/for/foreach/while/switch/
    ternario de por medio -- eso implicaria que el valor final depende de una
    condicion que este analizador no evalua, ver CONDITIONAL_LINE arriba).
    Nunca cruza a otro metodo: la busqueda esta acotada a
    lines[method_start_idx:trigger_idx].

    Regresa (texto_reconstruido, confidence_key, pattern, indice_de_linea
    absoluto de donde nace el valor) o None si no hay nada seguro que
    reconstruir -- nunca fabrica un resultado parcial silencioso fuera de lo
    que _render_tokens ya documenta explicitamente."""
    method_text = "\n".join(lines[method_start_idx:trigger_idx])

    sb_decl_match = None
    for m in SB_DECLARE.finditer(method_text):
        if (m.group(1) or m.group(2)) == var_name:
            sb_decl_match = m  # si se redeclara, usar la ULTIMA declaracion
    if sb_decl_match is not None:
        block = method_text[sb_decl_match.end():]
        if CONDITIONAL_LINE.search(block) or TERNARY_HINT.search(block):
            return None
        append_re = re.compile(rf"\b{re.escape(var_name)}\.(Append|AppendLine)\s*\(")
        tokens: list[tuple[str, str]] = []
        for m in append_re.finditer(block):
            arg_end = _find_matching_close_paren(block, m.end())
            if arg_end is None:
                return None
            arg_tokens = _tokenize_string_expression(block[m.end():arg_end])
            if not arg_tokens:
                return None
            tokens.extend(arg_tokens)
            if m.group(1) == "AppendLine":
                tokens.append(("literal", "\n"))
        if not tokens:
            return None
        text, confidence_key = _render_tokens(tokens)
        line_idx = method_start_idx + method_text.count("\n", 0, sb_decl_match.start())
        return text, confidence_key, "STRINGBUILDER_APPEND", line_idx

    # Caso variable simple/concatenada: recolectar EN ORDEN cada sentencia
    # `var_name = expr;` / `var_name += expr;` antes del trigger -- cubre
    # tanto la asignacion unica (`cmdText = "a" + b;`) como la construccion
    # incremental multi-sentencia (`q = "a"; q += "b";`) con el mismo
    # mecanismo, sin necesitar dos caminos de codigo distintos.
    assign_re = re.compile(rf"\b{re.escape(var_name)}\s*(\+=|=(?!=))")
    matches = list(assign_re.finditer(method_text))
    if not matches:
        return None
    if CONDITIONAL_LINE.search(method_text[matches[0].start():]):
        return None
    if TERNARY_HINT.search(method_text[matches[0].start():]):
        return None

    tokens = []
    for m in matches:
        stmt_end = _find_statement_end(method_text, m.end())
        if stmt_end is None:
            return None
        rhs_tokens = _tokenize_string_expression(method_text[m.end():stmt_end])
        if not rhs_tokens:
            return None
        tokens.extend(rhs_tokens)
    if not tokens:
        return None
    text, confidence_key = _render_tokens(tokens)
    line_idx = method_start_idx + method_text.count("\n", 0, matches[0].start())
    return text, confidence_key, "STRING_VAR_ASSIGN", line_idx


def _classify_sql(text: str, command_type_is_sp_nearby: bool) -> tuple[str, Optional[str], bool]:
    """Returns (category, target, is_stored_procedure)."""
    pkg_match = ORACLE_PKG_CALL.search(text)
    if pkg_match:
        return "oracle_package_call", f"{pkg_match.group(1)}.{pkg_match.group(2)}", False

    # `text` here is the whole raw C# statement (e.g. `sqlCommand.CommandText =
    # "SpName '" + arg + "'";`), not just the query literal — so proc-name
    # detection must run against the first quoted literal, not the full line,
    # otherwise it matches on the C# variable name (`sqlCommand`) instead.
    literal_match = FIRST_STRING_LITERAL.search(text)
    literal_prefix = literal_match.group(1) if literal_match else text.lstrip('"$')

    has_keyword = SQL_KEYWORDS.search(text)
    if command_type_is_sp_nearby or not has_keyword:
        # Two SP-name shapes seen in these apps: "SpName 'arg1','arg2'" (SQL
        # Server allows calling a proc without EXEC when CommandType is Text)
        # and the standard "[schema].[SpName]" / "schema.SpName" shape used
        # with CommandType.StoredProcedure.
        schema_match = SCHEMA_QUALIFIED_PROC.match(literal_prefix.strip())
        if schema_match:
            return "stored_procedure", f"{schema_match.group(1)}.{schema_match.group(2)}", True
        name_match = BARE_PROC_NAME.search(literal_prefix)
        if name_match and not has_keyword:
            return "stored_procedure", name_match.group(1), True
        if command_type_is_sp_nearby:
            return "stored_procedure", (name_match.group(1) if name_match else text.strip()[:60]), True

    for pattern in (TABLE_UPDATE, TABLE_FROM, TABLE_INTO):
        m = pattern.search(text)
        if m:
            return "query", m.group(1), False

    return "query", None, False


def scan_file(cs_file: Path, root: Path) -> tuple[list[SqlFinding], list[LocalIOFinding]]:
    text = cs_file.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    sql_findings: list[SqlFinding] = []
    io_findings: list[LocalIOFinding] = []

    class_stack: list[tuple[str, int]] = [(cs_file.stem, 0)]
    depth = 0
    current_method = "(top-level)"
    method_start_idx = 0

    for idx, line in enumerate(lines):
        class_match = CLASS_SIG.match(line)
        if class_match:
            class_stack.append((class_match.group(1), depth + 1))

        depth += line.count("{") - line.count("}")

        while len(class_stack) > 1 and depth < class_stack[-1][1]:
            class_stack.pop()

        current_class = class_stack[-1][0]

        method_match = METHOD_SIG.match(line)
        if method_match:
            current_method = method_match.group(1)
            method_start_idx = idx

        sql_trig = SQL_TRIGGER.search(line)
        if sql_trig:
            raw = _capture_statement(lines, idx)
            kind = sql_trig.group(1) or sql_trig.group(2) or "CommandText"

            resolved = None
            evidence = Evidence()
            if '"' not in raw:
                # Orden de patrones: el mas especifico primero. `new
                # SqlCommand(cmdText, connection)` (dos argumentos) es el
                # patron dominante real del portafolio (ver VALIDATION_STRATEGY.md
                # Incremento 3A) -- ninguno de los otros lo reconocia.
                var_match = (
                    TOSTRING_IN_TRIGGER.search(raw)
                    or VAR_IN_COMMANDTEXT_ASSIGN.search(raw)
                    or VAR_AS_COMMAND_CTOR_ARG.search(raw)
                    or re.search(r"=\s*(\w+)\s*[,;)]", raw)
                    or re.search(r"\((\w+)\)", raw)
                )
                if var_match:
                    reconstruction = _reconstruct_dynamic_sql(lines, method_start_idx, idx, var_match.group(1))
                    if reconstruction:
                        resolved, confidence_key, pattern, source_line_idx = reconstruction
                        evidence = Evidence(
                            source_file=str(cs_file.relative_to(root)),
                            line_number=source_line_idx + 1,
                            snippet=lines[source_line_idx].strip()[:200],
                            extractor=confidence_key,
                            pattern=pattern,
                            confidence=resolve_confidence(confidence_key),
                            created_at=datetime.now(timezone.utc).isoformat(),
                        )

            text_for_classification = resolved if resolved else raw
            # CommandType.StoredProcedure is just as often set a few lines AFTER
            # CommandText as before it (e.g. new SqlCommand(); cmd.CommandText = ...;
            # cmd.CommandType = CommandType.StoredProcedure;) — scan a window on
            # both sides rather than only looking backward.
            context_window = " ".join(lines[max(0, idx - 5): min(len(lines), idx + 8)])
            sp_nearby = bool(STORED_PROC_TYPE.search(context_window))
            category, target, is_sp = _classify_sql(text_for_classification, sp_nearby)

            parameters: list[str] = []
            result_columns: list[str] = []
            cmd_var_match = CMD_VAR.search(raw)
            if cmd_var_match:
                cmd_var = cmd_var_match.group(1) or cmd_var_match.group(2)
                method_end_idx = _find_method_end(lines, method_start_idx)
                parameters = _extract_parameters(lines, idx, cmd_var, method_end_idx)
                result_columns = _extract_result_columns(lines, idx, cmd_var, method_end_idx)

            sql_findings.append(
                SqlFinding(
                    file=str(cs_file.relative_to(root)),
                    class_name=current_class,
                    method=current_method,
                    kind=kind,
                    raw=raw,
                    resolved=resolved,
                    category=category,
                    target=target,
                    is_stored_procedure=is_sp,
                    parameters=parameters,
                    result_columns=result_columns,
                    evidence=evidence,
                )
            )
            continue

        io_trig = LOCAL_IO_TRIGGER.search(line)
        if io_trig:
            io_findings.append(
                LocalIOFinding(
                    file=str(cs_file.relative_to(root)),
                    class_name=current_class,
                    method=current_method,
                    operation=io_trig.group(0).rstrip("("),
                    raw=_capture_statement(lines, idx),
                )
            )

    return sql_findings, io_findings


def scan_project(root: Path) -> tuple[list[SqlFinding], list[LocalIOFinding]]:
    all_sql: list[SqlFinding] = []
    all_io: list[LocalIOFinding] = []
    for cs_file in root.rglob("*.cs"):
        if "Settings" in cs_file.name:
            continue
        preview = cs_file.read_text(encoding="utf-8", errors="ignore")
        if not (SQL_TRIGGER.search(preview) or LOCAL_IO_TRIGGER.search(preview)):
            continue
        sql, io = scan_file(cs_file, root)
        all_sql.extend(sql)
        all_io.extend(io)
    return all_sql, all_io
