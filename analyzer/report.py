"""Renders a full analysis bundle into a Markdown report."""

import json
import re
from collections import defaultdict

from . import confidence
from .activity import ActivityEvidence
from .evidence import Evidence
from .extract import LocalIOFinding, SettingEntry, SqlFinding
from .i18n import _
from .security import SecurityFlag
from .techstack import TechStack

# Incremento Lifecycle: traduccion legible del numero 0-100 de confidence.py
# SOLO para mostrar -- el dato guardado sigue siendo el entero (una sola
# fuente de verdad de confianza en todo el proyecto, ver analyzer/confidence.py).
def _confidence_label(value: int | None) -> str:
    if value is None or value <= 20:  # confidence.UNKNOWN = piso de la escala = "sin evidencia"
        return "unknown"
    if value >= 70:
        return "high"
    if value >= 55:
        return "medium"
    return "low"

CONN_VAR = re.compile(r"new\s+(?:Sql|Oracle)Connection\s*\(\s*([\w.]+)\s*\)")


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _group_by_method(findings: list[SqlFinding]) -> dict[tuple[str, str], list[SqlFinding]]:
    groups: dict[tuple[str, str], list[SqlFinding]] = defaultdict(list)
    for f in findings:
        groups[(f.class_name, f.method)].append(f)
    return groups


def _rows_for_method(group: list[SqlFinding]):
    """Yields (query_text, connection_hint, category, target, parameters,
    result_columns, evidence) rows for one method, collapsing SqlConnection/
    SqlCommand boilerplate once the real query is resolved."""
    conn_hint = "?"
    for f in group:
        m = CONN_VAR.search(f.raw)
        if m:
            conn_hint = m.group(1)
            break

    literal_rows = []
    for f in group:
        text = f.resolved if f.resolved else f.raw
        # "Tiene contenido real que mostrar" solia significar simplemente
        # 'contiene una comilla' porque `resolved`/`raw` SIEMPRE retenian las
        # comillas literales de C# verbatim -- eso deja de ser cierto con el
        # Incremento 3A: `_reconstruct_dynamic_sql()` devuelve SQL ya limpio
        # (sin las comillas de C#, ver _unescape_csharp_literal), asi que una
        # query resuelta sin ninguna comilla EMBEBIDA en su propio texto
        # (ej. "SELECT JobId, PartNo FROM DJItem WHERE Active = 1") fallaba
        # este chequeo por accidente. La senal correcta es simplemente si el
        # finding fue resuelto en absoluto, o si el raw ya trae un literal
        # inline (el caso preexistente que este chequeo si debia cubrir).
        if f.resolved is not None or '"' in f.raw:
            literal_rows.append((text, f.category, f.target, f.parameters, f.result_columns, f.evidence))

    if literal_rows:
        seen = set()
        for text, category, target, parameters, result_columns, evidence in literal_rows:
            if text not in seen:
                seen.add(text)
                yield text, conn_hint, category, target, parameters, result_columns, evidence
    else:
        yield (
            _("(conexion detectada, query no resuelta automaticamente — revisar manualmente)"),
            conn_hint, "?", None, [], [], Evidence(),
        )


def render(
    app_name: str,
    tech: TechStack,
    settings: list[SettingEntry],
    sql_findings: list[SqlFinding],
    io_findings: list[LocalIOFinding],
    security_flags: list[SecurityFlag],
    companion_assemblies: list[str] | None = None,
    db_procedures: list[dict] | None = None,
    db_tables: list[dict] | None = None,
    db_intro_notes: str | None = None,
    build_date: str | None = None,
    activity: ActivityEvidence | None = None,
) -> str:
    activity = activity or ActivityEvidence()
    lines: list[str] = []
    lines.append(f"# {app_name} — " + _("Inventario automatico (borrador)"))
    lines.append("")
    lines.append(
        "> " + _(
            "Generado por QAPV-LegacyAppAnalyzer (ilspycmd + extractor Python). "
            "Este es un primer borrador — revisar antes de usarlo como fuente final."
        )
    )
    lines.append("")

    # Incremento Lifecycle (2026-08-13): build_date es un PROXY (mtime del
    # ensamblado), NUNCA una fecha de compilacion verificada -- ver
    # analyzer/activity.py. last_activity es "ultima evidencia encontrada",
    # NUNCA "ultima ejecucion confirmada" -- ausencia de evidencia se
    # muestra como "unknown", jamas se afirma "no se usa".
    lines.append("## " + _("Ciclo de vida"))
    lines.append("")
    lines.append(f"- **Build date** ({_('proxy, fecha de modificacion del ensamblado')}): {build_date or 'unknown'}")
    lines.append(f"- **Last activity evidence**: {activity.date or 'unknown'}")
    lines.append(f"- **Activity source**: {activity.source}")
    lines.append(f"- **Activity confidence**: {_confidence_label(activity.confidence)}")
    lines.append("")

    lines.append("## " + _("Tecnologia"))
    lines.append("")
    lines.append(f"- **{_('Lenguaje')}**: {tech.language}")
    lines.append(f"- **Target .NET**: {tech.dotnet_target}")
    lines.append(f"- **UI Framework**: {', '.join(tech.ui_framework)}")
    lines.append(f"- **{_('Drivers de BD detectados')}**: {', '.join(tech.db_drivers)}")
    if companion_assemblies:
        lines.append(
            f"- **{_('Ensamblados adicionales decompilados')}** "
            f"({_('referenciados por el .exe, ej. una ClassLib con settings/logica propia')}): "
            f"{', '.join(companion_assemblies)}"
        )
    lines.append("")

    if security_flags:
        lines.append("## ⚠️ " + _("Alertas de seguridad"))
        lines.append("")
        lines.append(f"| {_('Severidad')} | {_('Descripcion')} | {_('Ubicacion')} |")
        lines.append("|---|---|---|")
        for flag in security_flags:
            lines.append(f"| {_(flag.severity)} | {_escape(flag.description)} | {_escape(flag.location)} |")
        lines.append("")

    lines.append("## Connection strings")
    lines.append("")
    conn_settings = [s for s in settings if s.category == "sql_or_oracle"]
    path_settings = [s for s in settings if s.category == "local_path"]
    other_settings = [s for s in settings if s.category == "other"]

    if conn_settings:
        lines.append(f"| Setting | {_('Valor por defecto')} | {_('Archivo')} | {_('Evidencia')} | {_('Confianza')} |")
        lines.append("|---|---|---|---|---|")
        for s in conn_settings:
            ev = s.evidence
            evidencia = f"{ev.extractor}, {_('linea')} {ev.line_number}" if ev.line_number else ev.extractor
            lines.append(
                f"| `{s.name}` | `{_escape(s.default_value)}` | {s.source_file} | {evidencia} | {ev.confidence}% |"
            )
    else:
        lines.append("_" + _(
            "No se encontraron connection strings. Puede que el Settings.cs no se haya incluido "
            "en la decompilacion, o que la app no use `ApplicationSettingsBase` para su conexion."
        ) + "_")
    lines.append("")

    lines.append("## " + _("Rutas / archivos locales configurados"))
    lines.append("")
    if path_settings:
        lines.append(f"| Setting | {_('Valor por defecto')} |")
        lines.append("|---|---|")
        for s in path_settings:
            lines.append(f"| `{s.name}` | `{_escape(s.default_value)}` |")
    else:
        lines.append("_" + _("No se encontraron settings que parezcan rutas de archivo/carpeta.") + "_")
    lines.append("")

    if other_settings:
        lines.append("### " + _("Otras configuraciones"))
        lines.append("")
        lines.append(f"| Setting | {_('Valor por defecto')} |")
        lines.append("|---|---|")
        for s in other_settings:
            lines.append(f"| `{s.name}` | `{_escape(s.default_value)}` |")
        lines.append("")

    lines.append("## " + _("Funciones -> SQL / Stored Procedures"))
    lines.append("")
    if not sql_findings:
        lines.append("_" + _(
            "No se detecto ningun uso de SqlConnection/OracleConnection/CommandText en el codigo "
            "decompilado. Revisar manualmente — puede ser una app sin SQL propio (launcher, watchdog, "
            "o vista MVVM sin el ViewModel incluido)."
        ) + "_")
    else:
        # markdown="1" (extension md_in_html, ver app.py) -- necesario para que
        # la tabla markdown de adentro se siga procesando como tabla real en
        # vez de quedar como texto crudo; el div solo existe para poder darle
        # a la columna SQL/Query mas espacio vía CSS (.table-sql-findings en
        # static/style.css) sin afectar las demas tablas del reporte.
        lines.append('<div class="table-sql-findings" markdown="1">')
        lines.append("")
        lines.append(
            f"| {_('Clase')} | {_('Funcion')} | {_('Conexion')} | {_('Tipo')} | {_('Tabla / SP')} | "
            f"SQL / Query | {_('Parametros')} | {_('Columnas de resultado')} | {_('Evidencia')} | {_('Confianza')} |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for (class_name, method), group in _group_by_method(sql_findings).items():
            for row_text, conn_hint, category, target, params, result_columns, evidence in _rows_for_method(group):
                tipo = _("Stored Procedure") if category == "stored_procedure" else (
                    "PL/SQL (Oracle)" if category == "oracle_package_call" else _("Query")
                )
                params_cell = "<br>".join(f"`{_escape(p)}`" for p in params) if params else ""
                cols_cell = ", ".join(f"`{_escape(c)}`" for c in result_columns) if result_columns else ""
                evidencia = f"{evidence.extractor}, {_('linea')} {evidence.line_number}" if evidence.line_number else evidence.extractor
                lines.append(
                    f"| `{class_name}` | `{method}` | {conn_hint} | {tipo} | {target or '?'} "
                    f"| `{_escape(row_text)}` | {params_cell} | {cols_cell} | {evidencia} | {evidence.confidence}% |"
                )
        lines.append("")
        lines.append("</div>")
    lines.append("")

    if db_procedures or db_tables or db_intro_notes:
        lines.append("## " + _("Extraccion de esquema desde la base de datos (solo lectura)"))
        lines.append("")
        if db_intro_notes:
            lines.append("> ⚠️ " + _("No se pudo conectar a alguna(s) de las conexiones: %(notes)s") % {"notes": db_intro_notes})
            lines.append("")

    if db_procedures:
        lines.append("### " + _("Definiciones de Stored Procedures"))
        lines.append("")
        for p in db_procedures:
            full_name = f"{p['schema_name']}.{p['object_name']}"
            if p["status"] == "ok" and p["definition"]:
                lines.append(f"#### `{full_name}`")
                lines.append("")
                if p.get("parameters"):
                    lines.append("**" + _("Parametros de entrada/salida") + ":**")
                    lines.append("")
                    lines.append(f"| {_('Parametro')} | {_('Tipo')} | {_('Longitud')} | {_('Salida')} | Default |")
                    lines.append("|---|---|---|---|---|")
                    for prm in p["parameters"]:
                        lines.append(
                            f"| {prm['name']} | {prm['type']} | {prm.get('max_length') or ''} "
                            f"| {_('Si') if prm['is_output'] else ''} "
                            f"| {_escape(str(prm['default'])) if prm.get('has_default') else ''} |"
                        )
                    lines.append("")
                if p.get("result_columns"):
                    lines.append("**" + _("Columnas que devuelve (determinado por SQL Server, solo lectura)") + ":**")
                    lines.append("")
                    lines.append(f"| {_('Columna')} | {_('Tipo')} | {_('Nulo')} |")
                    lines.append("|---|---|---|")
                    for col in p["result_columns"]:
                        lines.append(f"| {col['name']} | {col['type']} | {_('Si') if col['nullable'] else _('No')} |")
                    lines.append("")
                elif p.get("result_columns") is None:
                    lines.append("_" + _(
                        "SQL Server no pudo determinar automaticamente las columnas de salida de este SP "
                        "(comun si usa SQL dinamico, tablas temporales o multiples result sets) — revisar "
                        "el codigo de la SP arriba manualmente."
                    ) + "_")
                    lines.append("")
                lines.append("**" + _("Codigo de `%(name)s`") % {"name": full_name} + ":**")
                lines.append("")
                lines.append("```sql")
                lines.append(p["definition"].strip())
                lines.append("```")
                lines.append("")
            else:
                lines.append(f"#### `{full_name}` — " + _("no disponible (%(status)s)") % {"status": p["status"]})
                lines.append("")

    if db_tables:
        lines.append("### " + _("Esquema de tablas"))
        lines.append("")
        for t in db_tables:
            full_name = f"{t['schema_name']}.{t['table_name']}"
            lines.append(f"#### `{full_name}`")
            lines.append("")
            lines.append(f"| {_('Columna')} | {_('Tipo')} | {_('Nulo')} | {_('Longitud')} | Default |")
            lines.append("|---|---|---|---|---|")
            for c in t["columns"]:
                lines.append(
                    f"| {c['name']} | {c['type']} | {c['nullable']} | {c.get('max_length') or ''} "
                    f"| {_escape(c.get('default') or '')} |"
                )
            if t.get("foreign_keys"):
                lines.append("")
                lines.append(_("Claves foraneas") + ":")
                for fk in t["foreign_keys"]:
                    lines.append(f"- `{fk['column']}` -> `{fk['ref_table']}.{fk['ref_column']}` (`{fk['fk_name']}`)")
            lines.append("")

    # Fase 4 (KNOWN_LIMITATIONS.md L16/L17): separado de la tabla de I/O de
    # abajo a proposito -- invocacion indirecta/tardia (Reflection/COM) es un
    # riesgo de naturaleza distinta a un acceso a archivo/impresora/red (el
    # comportamiento real depende de resolucion en tiempo de ejecucion, ver
    # analyzer/confidence.py: REFLECTION), no tendria sentido enterrarlo entre
    # esos hallazgos con un nombre de "Operacion" que no deja claro por que
    # esta ahi.
    plain_io_findings = [f for f in io_findings if f.category != "reflection"]
    reflection_findings = [f for f in io_findings if f.category == "reflection"]

    lines.append("## " + _("Archivos, impresoras, procesos y red en codigo"))
    lines.append("")
    if not plain_io_findings:
        lines.append("_" + _(
            "No se detecto acceso directo a archivos/carpetas, impresoras (BarTender/PrintDocument), "
            "puertos seriales, otros ejecutables (Process.Start) o llamadas HTTP/SMTP en el codigo."
        ) + "_")
    else:
        lines.append(f"| {_('Clase')} | {_('Funcion')} | {_('Operacion')} | {_('Detalle')} |")
        lines.append("|---|---|---|---|")
        seen = set()
        for f in plain_io_findings:
            key = (f.class_name, f.method, f.raw)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"| `{f.class_name}` | `{f.method}` | {f.operation} | `{_escape(f.raw)}` |")
    lines.append("")

    lines.append("## " + _("Invocacion indirecta / tardia (Reflection, COM)"))
    lines.append("")
    if not reflection_findings:
        lines.append("_" + _(
            "No se detecto invocacion via Reflection (MethodInfo.Invoke, Activator.CreateInstance) "
            "ni COM/CLSID (Marshal.GetTypeFromCLSID) en el codigo."
        ) + "_")
    else:
        lines.append(
            "> ⚠️ " + _(
                "El comportamiento real de estas llamadas depende de resolucion en tiempo de ejecucion "
                "-- ningun analisis estatico puede saber con certeza que se invoca (KNOWN_LIMITATIONS.md L16/L17)."
            )
        )
        lines.append("")
        lines.append(f"| {_('Clase')} | {_('Funcion')} | API | {_('Detalle')} |")
        lines.append("|---|---|---|---|")
        seen = set()
        for f in reflection_findings:
            key = (f.class_name, f.method, f.raw)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"| `{f.class_name}` | `{f.method}` | {f.operation} | `{_escape(f.raw)}` |")
    lines.append("")

    return "\n".join(lines)


def reconstruct_from_db(data: dict):
    """Rebuilds the dataclasses from a db.get_app() dict. Shared by the Markdown
    renderer and the Excel/Word exporters so screen display and every export
    format stay identical without duplicating the rebuild logic."""
    app_row = data["app"]
    tech = TechStack(
        language="C#",
        dotnet_target=app_row["dotnet_target"] or "(no detectado)",
        ui_framework=(app_row["ui_framework"] or "(no detectado)").split(", "),
        db_drivers=(app_row["db_drivers"] or "(ninguno detectado)").split(", "),
    )
    settings = [
        SettingEntry(
            name=s["name"], default_value=s["default_value"],
            is_connection_string=bool(s["is_connection_string"]),
            category=s["category"], source_file=s["source_file"],
            # Filas guardadas antes de Fase 2 tienen estas columnas en NULL --
            # Evidence(extractor="UNKNOWN"...) ya documenta eso via sus propios
            # defaults, así que solo se pasa `extractor`/`confidence` cuando la
            # fila SI los tiene (evita pisar el default honesto con None).
            evidence=(
                Evidence(
                    source_file=s["source_file"], line_number=s["line_number"],
                    snippet=s["snippet"], extractor=s["extractor"], pattern=s["pattern"],
                    confidence=s["confidence"], analyzer_version=s["analyzer_version"],
                    created_at=s["created_at"],
                )
                if s["extractor"]
                else Evidence()
            ),
        )
        for s in data["settings"]
    ]
    sql_findings = [
        SqlFinding(
            file=f["file"], class_name=f["class_name"], method=f["method"], kind=f["kind"],
            raw=f["raw"], resolved=f["resolved"], category=f["category"], target=f["target"],
            is_stored_procedure=bool(f["is_stored_procedure"]),
            parameters=json.loads(f["parameters"]) if f["parameters"] else [],
            result_columns=json.loads(f["result_columns"]) if f["result_columns"] else [],
            # Mismo patron que SettingEntry arriba: filas de antes del
            # Incremento 3A no tienen estas columnas pobladas -- Evidence()
            # por defecto documenta eso ("UNKNOWN"/20%), no se inventa nada.
            evidence=(
                Evidence(
                    source_file=f["file"], line_number=f["line_number"],
                    snippet=f["snippet"], extractor=f["extractor"], pattern=f["pattern"],
                    confidence=f["confidence"], analyzer_version=f["analyzer_version"],
                    created_at=f["created_at"],
                )
                if f["extractor"]
                else Evidence()
            ),
        )
        for f in data["sql_findings"]
    ]
    io_findings = [
        LocalIOFinding(
            file=f["file"], class_name=f["class_name"], method=f["method"],
            operation=f["operation"], raw=f["raw"],
            # Filas guardadas antes de Fase 4 no tienen esta columna en el
            # dict reconstruido desde una BD vieja sin migrar todavia --
            # ".get" con el mismo default 'io' de la columna en si, nunca
            # None (LocalIOFinding.category no acepta None).
            category=f.get("category") or "io",
        )
        for f in data["io_findings"]
    ]
    flags = [
        SecurityFlag(severity=f["severity"], description=f["description"], location=f["location"])
        for f in data["security_flags"]
    ]
    companion_assemblies = [c for c in (app_row["companion_assemblies"] or "").split(", ") if c]

    db_procedures = [
        {
            "schema_name": p["schema_name"],
            "object_name": p["object_name"],
            "status": p["status"],
            "definition": p["definition"],
            "parameters": json.loads(p["parameters_json"]) if p.get("parameters_json") else [],
            "result_columns": json.loads(p["result_columns_json"]) if p.get("result_columns_json") else None,
        }
        for p in data.get("db_procedures", [])
    ]
    db_tables = [
        {
            "schema_name": t["schema_name"],
            "table_name": t["table_name"],
            "columns": json.loads(t["columns_json"]) if t["columns_json"] else [],
            "foreign_keys": json.loads(t["foreign_keys_json"]) if t["foreign_keys_json"] else [],
        }
        for t in data.get("db_tables", [])
    ]

    # Incremento Lifecycle: filas guardadas antes de esta fase no tienen estas
    # columnas pobladas (NULL, no ausentes -- la migracion ya las agrego) --
    # ActivityEvidence() por defecto documenta eso honestamente
    # (source="no_evidence", confidence=UNKNOWN), nunca se inventa una fecha.
    activity = ActivityEvidence(
        date=app_row.get("last_activity_date"),
        source=app_row.get("last_activity_source") or "no_evidence",
        confidence=(
            app_row["last_activity_confidence"]
            if app_row.get("last_activity_confidence") is not None
            else confidence.UNKNOWN
        ),
    )

    return (
        app_row["name"], tech, settings, sql_findings, io_findings, flags, companion_assemblies,
        db_procedures, db_tables, app_row.get("db_intro_notes"),
        app_row.get("build_date"), activity,
    )


def render_from_db(data: dict) -> str:
    (
        app_name, tech, settings, sql_findings, io_findings, flags, companions,
        db_procedures, db_tables, db_intro_notes, build_date, activity,
    ) = reconstruct_from_db(data)
    return render(
        app_name, tech, settings, sql_findings, io_findings, flags, companions,
        db_procedures, db_tables, db_intro_notes,
        build_date=build_date, activity=activity,
    )
