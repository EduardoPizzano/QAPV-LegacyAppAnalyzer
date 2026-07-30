"""Scans decompiled .cs source for connection strings, local file I/O, and
SQL/Oracle usage (queries and stored procedures)."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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


def _classify_setting(value: str, marked_as_conn_string: bool) -> str:
    if marked_as_conn_string or LOOKS_LIKE_DB_CONN.search(value):
        return "sql_or_oracle"
    if LOOKS_LIKE_PATH.match(value):
        return "local_path"
    return "other"


def find_settings(root: Path) -> list[SettingEntry]:
    entries: list[SettingEntry] = []
    for cs_file in root.rglob("*.cs"):
        if "Settings" not in cs_file.name:
            continue
        text = cs_file.read_text(encoding="utf-8", errors="ignore")
        if "DefaultSettingValue" not in text:
            continue
        for match in SETTING_BLOCK.finditer(text):
            attrs = match.group("attrs")
            default_match = DEFAULT_VALUE.search(attrs)
            if not default_match:
                continue
            value = default_match.group(1)
            is_conn = bool(IS_CONN_STRING.search(attrs))
            entries.append(
                SettingEntry(
                    name=match.group("name"),
                    default_value=value,
                    is_connection_string=is_conn,
                    category=_classify_setting(value, is_conn),
                    source_file=str(cs_file.relative_to(root)),
                )
            )
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

STRING_VAR_ASSIGN = re.compile(
    r'\b(\w+)\s*=\s*(\$?"(?:[^"\\]|\\.)*"|\$@"(?:[^"]|"")*"|@"(?:[^"]|"")*")\s*;'
)

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


@dataclass
class LocalIOFinding:
    file: str
    class_name: str
    method: str
    operation: str   # e.g. "File.ReadAllText", "new StreamWriter"
    raw: str


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


def _resolve_variable(lines: list[str], method_start: int, trigger_idx: int, var_name: str) -> Optional[str]:
    best = None
    for i in range(method_start, trigger_idx):
        for m in STRING_VAR_ASSIGN.finditer(lines[i]):
            if m.group(1) == var_name:
                best = m.group(2)
    return best


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
            if '"' not in raw:
                var_match = re.search(r"=\s*(\w+)\s*[,;)]", raw) or re.search(r"\((\w+)\)", raw)
                if var_match:
                    resolved = _resolve_variable(lines, method_start_idx, idx, var_match.group(1))

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
