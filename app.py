"""
QAPV Legacy App Analyzer — Interfaz web
------------------------------------------
Pide la carpeta (o el .exe/.dll directo) de una app legacy, la decompila y
analiza, muestra el resultado en pantalla y permite exportarlo como .md.
Todo lo analizado se acumula en qapv_analyzer.db para busqueda cruzada.

Uso:
    .venv/Scripts/python.exe app.py
    (abre http://127.0.0.1:5000)
"""

import markdown as md
from collections import defaultdict
from pathlib import Path

from flask import Flask, Response, flash, jsonify, redirect, render_template, request, url_for

from analyzer import db, diagram, enrich, export_office, priority_report
from analyzer.decompile import DecompileError, discover_assemblies, project_label
from analyzer.pipeline import run_analysis
from analyzer.report import reconstruct_from_db, render, render_from_db

EXPORT_MIMETYPES = {
    "md": "text/markdown",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

BASE_DIR = Path(__file__).parent
REPORTS_DIR = BASE_DIR / "reports"

app = Flask(__name__)
app.secret_key = "qapv-legacy-analyzer"  # solo para flash messages, no hay login/datos sensibles

db.init_db()


def _analyze_and_save(assembly: Path, custom_name: str | None = None) -> dict:
    """Runs the full pipeline for one assembly, writes its .md report, saves it
    to the accumulative DB, and then ALWAYS attempts the read-only DB
    enrichment pass (SP/table definitions) as part of the same analysis — no
    longer a separate manual step. Enrichment is strictly SELECT-only (see
    analyzer/db_introspect.py's module docstring) and any connection failure
    is caught and reported, never allowed to fail the overall analysis.
    Returns a dict with app_id + an enrichment summary. Raises DecompileError
    on failure — shared by the single-app and batch flows.

    Ordering note (fix 2026-08-13): save_analysis() FIRST, then read back
    the name DB actually kept, THEN render/write the .md — never the other
    way around. result.app_name is only the name PROPOSED for this run;
    save_analysis() may silently keep an older name when source_path already
    matches an existing row (see analyzer/db.py). Writing the report from
    result.app_name before that resolution produced an orphaned .md at the
    proposed (wrong) path while the real row's report was never refreshed —
    confirmed with GeoStatsInter 2026-08-11 and reproduced in a controlled
    diagnostic 2026-08-13 (RL1PolInterfaceLocalMultiple)."""
    result = run_analysis(assembly, custom_name)

    app_id = db.save_analysis(
        result.app_name, result.source_path, result.tech, result.settings,
        result.sql_findings, result.io_findings, result.security_flags,
        result.companion_assemblies, result.build_date, result.activity,
    )
    final_name = db.get_app(app_id)["app"]["name"]

    report_text = render(
        final_name, result.tech, result.settings, result.sql_findings,
        result.io_findings, result.security_flags, result.companion_assemblies,
        build_date=result.build_date, activity=result.activity,
    )
    # app_name can contain "/" for batch-discovered apps (e.g. "AFL.Dashboard/
    # AFL.Scrap"), which implies a report subfolder that may not exist yet.
    report_path = REPORTS_DIR / f"{final_name}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")

    enrich_summary = {"sp_ok": 0, "sp_total": 0, "tables": 0, "connection_errors": [], "attempted": False}
    try:
        enrich_result = enrich.enrich_app(app_id)
    except Exception as e:
        enrich_summary["connection_errors"] = [str(e)]
    else:
        enrich_summary["attempted"] = True
        enrich_summary["sp_total"] = len(enrich_result["procedures"])
        enrich_summary["sp_ok"] = sum(1 for p in enrich_result["procedures"] if p["status"] == "ok")
        enrich_summary["tables"] = len(enrich_result["tables"])
        enrich_summary["connection_errors"] = enrich_result["connection_errors"]
        db.save_db_objects(app_id, enrich_result["procedures"], enrich_result["tables"], enrich_result["connection_errors"])

    return {"app_id": app_id, "app_name": final_name, "enrich": enrich_summary}


def _flash_enrich_summary(enrich_summary: dict) -> None:
    """Reports the automatic read-only DB-enrichment outcome via flash — kept
    separate so single/batch/JSON flows can each decide how to surface it.
    Stays quiet for apps with no SQL Server connection at all (nothing to
    report), so this doesn't spam a flash on every single-file app."""
    if enrich_summary["sp_total"] or enrich_summary["tables"]:
        flash(
            f"Extraccion de BD (solo lectura): {enrich_summary['sp_ok']}/{enrich_summary['sp_total']} "
            f"SPs encontrados, {enrich_summary['tables']} tablas con esquema."
        )
    for err in enrich_summary["connection_errors"]:
        flash(f"No se pudo conectar a la BD (solo lectura): {err}")


def _batch_name(root_path: str, exe_path: str) -> str:
    """Names a batch-discovered app as 'RootFolder/ModuleFolder' (e.g.
    'AFL.Dashboard/AFL.Scrap') so sibling modules of the same solution are
    recognizable as a family instead of colliding on bare exe stems.

    When the root folder holds a single project whose module label is
    identical to the root name (e.g. discovering root
    "...\\QAPV_DATACENTER\\GeoStatsInter" finds only
    "GeoStatsInter\\bin\\Debug\\GeoStatsInter.exe"), collapses to the bare
    name instead of "GeoStatsInter/GeoStatsInter". Beyond being a redundant
    label (group_apps_for_sidebar() already collapses this same case for
    display), NOT collapsing it created a real bug: run_analysis() derives
    its decompile output_dir from this name (DECOMPILED_DIR / app_name), so
    "GeoStatsInter/GeoStatsInter" decompiles into a directory *nested inside*
    the standalone "GeoStatsInter" app's own output_dir. The extractor then
    picked up that standalone app's already-decompiled Program.cs as if it
    were part of the new run, doubling every SQL/IO finding -- and
    save_analysis's upsert-by-name meant the two analyses landed in separate
    DB rows instead of one being recognized as a re-analysis of the other."""
    root_name = Path(root_path).name
    module = project_label(Path(exe_path))
    if module == root_name:
        return root_name
    return f"{root_name}/{module}"


@app.route("/")
def index():
    return render_template("index.html", apps=db.group_apps_for_sidebar(), selected_id=None)


@app.route("/analyze", methods=["POST"])
def analyze():
    raw_path = request.form.get("path", "").strip().strip('"')
    custom_name = request.form.get("name", "").strip() or None

    if not raw_path:
        flash("Indica una ruta de carpeta o de archivo .exe/.dll.")
        return redirect(url_for("index"))

    p = Path(raw_path)
    if not p.exists():
        flash(f"No se encontro la ruta: {raw_path}")
        return redirect(url_for("index"))

    if p.is_file():
        assembly = p
    else:
        candidates = sorted(p.glob("*.exe")) or sorted(p.glob("*.dll"))
        if not candidates:
            flash(f"No se encontro ningun .exe/.dll directamente dentro de: {raw_path}")
            return redirect(url_for("index"))
        if len(candidates) > 1:
            return render_template("choose_assembly.html", folder=raw_path, candidates=candidates)
        assembly = candidates[0]

    try:
        outcome = _analyze_and_save(assembly, custom_name)
    except DecompileError as e:
        flash(f"Error al decompilar '{assembly.name}': {e}")
        return redirect(url_for("index"))

    flash(f"'{assembly.stem}' analizado correctamente.")
    _flash_enrich_summary(outcome["enrich"])
    return redirect(url_for("app_detail", app_id=outcome["app_id"]))


@app.route("/discover", methods=["POST"])
def discover():
    raw_path = request.form.get("root_path", "").strip().strip('"')
    if not raw_path:
        flash("Indica una carpeta raiz para escanear.")
        return redirect(url_for("index"))

    root = Path(raw_path)
    if not root.exists() or not root.is_dir():
        flash(f"No se encontro la carpeta: {raw_path}")
        return redirect(url_for("index"))

    candidates = discover_assemblies(root)
    groups = defaultdict(list)
    for exe in candidates:
        # Default-check only the Debug build so a solution with many modules
        # doesn't hand back everything pre-selected (Debug+Release doubles the
        # list) — Release stays visible, just unchecked, in case Debug is stale.
        default_checked = "release" not in {p.lower() for p in exe.parts}
        groups[project_label(exe)].append((str(exe), default_checked))

    return render_template(
        "discover_results.html", root_path=raw_path, candidates=candidates, groups=dict(groups),
        apps=db.group_apps_for_sidebar(), selected_id=None,
    )


@app.route("/analyze_batch", methods=["POST"])
def analyze_batch():
    """Non-JS fallback for the batch flow — the discover_results.html page
    normally drives this one app at a time via /analyze_one (for the progress
    bar) and only falls back to this bulk route if JavaScript is disabled."""
    selected = request.form.getlist("selected")
    root_path = request.form.get("root_path", "").strip()
    if not selected:
        flash("No seleccionaste ninguna app para analizar.")
        return redirect(url_for("index"))

    last_app_id = None
    for raw_path in selected:
        assembly = Path(raw_path)
        name = _batch_name(root_path, raw_path) if root_path else None
        try:
            outcome = _analyze_and_save(assembly, name)
            last_app_id = outcome["app_id"]
            flash(f"'{name or assembly.stem}' analizado correctamente.")
            _flash_enrich_summary(outcome["enrich"])
        except DecompileError as e:
            flash(f"Error al decompilar '{assembly.name}': {e}")

    if last_app_id is not None:
        return redirect(url_for("app_detail", app_id=last_app_id))
    return redirect(url_for("index"))


@app.route("/analyze_one", methods=["POST"])
def analyze_one():
    """JSON endpoint used by discover_results.html's progress bar: analyzes
    exactly one assembly and reports back success/failure for that one app,
    including a summary of the automatic read-only DB-enrichment pass."""
    payload = request.get_json(silent=True) or {}
    raw_path = (payload.get("path") or "").strip()
    root_path = (payload.get("root") or "").strip()
    if not raw_path:
        return jsonify(ok=False, name="?", error="Ruta vacia"), 400

    assembly = Path(raw_path)
    name = _batch_name(root_path, raw_path) if root_path else assembly.stem
    try:
        outcome = _analyze_and_save(assembly, name)
    except DecompileError as e:
        return jsonify(ok=False, name=name, error=str(e))

    return jsonify(ok=True, name=name, app_id=outcome["app_id"], enrich=outcome["enrich"])


@app.route("/apps/<int:app_id>")
def app_detail(app_id):
    data = db.get_app(app_id)
    if not data:
        flash("Esa app no existe en la base de datos.")
        return redirect(url_for("index"))
    report_html = md.markdown(
        render_from_db(data),
        extensions=["tables", "fenced_code", "codehilite", "md_in_html"],
        extension_configs={"codehilite": {"guess_lang": False, "pygments_style": "vs", "noclasses": False}},
    )
    _, _, _, sql_findings, io_findings, *_rest = reconstruct_from_db(data)
    dataflow_diagram = diagram.build_dataflow_diagram(sql_findings, io_findings)
    return render_template(
        "result.html", data=data, report_html=report_html, dataflow_diagram=dataflow_diagram,
        apps=db.group_apps_for_sidebar(), selected_id=app_id,
    )


@app.route("/apps/<int:app_id>/enrich", methods=["POST"])
def enrich_route(app_id):
    """Connects (read-only) to the app's own connection strings and pulls the
    real definitions of the SPs/tables it references — see analyzer/enrich.py
    and analyzer/db_introspect.py for the strict SELECT-only guarantee."""
    data = db.get_app(app_id)
    if not data:
        flash("Esa app no existe en la base de datos.")
        return redirect(url_for("index"))

    try:
        result = enrich.enrich_app(app_id)
    except Exception as e:
        flash(f"Error al conectar a la base de datos: {e}")
        return redirect(url_for("app_detail", app_id=app_id))

    db.save_db_objects(app_id, result["procedures"], result["tables"], result["connection_errors"])

    ok_procs = sum(1 for p in result["procedures"] if p["status"] == "ok")
    flash(
        f"Extraccion de BD (solo lectura): {ok_procs}/{len(result['procedures'])} SPs encontrados, "
        f"{len(result['tables'])} tablas con esquema."
    )
    if result["connection_errors"]:
        for err in result["connection_errors"]:
            flash(f"No se pudo conectar: {err}")

    return redirect(url_for("app_detail", app_id=app_id))


@app.route("/apps/<int:app_id>/review", methods=["POST"])
def review_route(app_id):
    """Records the manual business-logic review pass — pure bookkeeping in
    our own tracking DB, never touches the legacy app itself."""
    data = db.get_app(app_id)
    if not data:
        flash("Esa app no existe en la base de datos.")
        return redirect(url_for("index"))

    status = request.form.get("review_status", "borrador")
    notes = request.form.get("review_notes", "").strip()
    try:
        db.set_review(app_id, status, notes)
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("app_detail", app_id=app_id))

    flash("Estado de revision actualizado.")
    return redirect(url_for("app_detail", app_id=app_id))


@app.route("/apps/<int:app_id>/export/<fmt>")
def export(app_id, fmt):
    data = db.get_app(app_id)
    if not data:
        flash("Esa app no existe en la base de datos.")
        return redirect(url_for("index"))
    if fmt not in EXPORT_MIMETYPES:
        flash(f"Formato de exportacion no soportado: {fmt}")
        return redirect(url_for("app_detail", app_id=app_id))

    (
        app_name, tech, settings, sql_findings, io_findings, flags, companions,
        db_procedures, db_tables, db_intro_notes, build_date, activity,
    ) = reconstruct_from_db(data)

    if fmt == "md":
        content = render(
            app_name, tech, settings, sql_findings, io_findings, flags, companions,
            db_procedures, db_tables, db_intro_notes,
            build_date=build_date, activity=activity,
        )
    elif fmt == "xlsx":
        content = export_office.build_xlsx(
            app_name, tech, settings, sql_findings, io_findings, flags, companions, db_procedures, db_tables,
        )
    else:
        content = export_office.build_docx(
            app_name, tech, settings, sql_findings, io_findings, flags, companions, db_procedures, db_tables,
        )

    return Response(
        content,
        mimetype=EXPORT_MIMETYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{app_name}.{fmt}"'},
    )


@app.route("/apps/<int:app_id>/delete", methods=["POST"])
def delete(app_id):
    data = db.get_app(app_id)
    name = data["app"]["name"] if data else "?"
    db.delete_app(app_id)
    flash(f"'{name}' eliminado de la base de datos.")
    return redirect(url_for("index"))


@app.route("/search")
def search():
    term = request.args.get("term", "").strip()
    mode = request.args.get("mode", "table")
    results = []
    if term:
        results = db.search_by_table(term) if mode == "table" else db.search_by_connection(term)
    return render_template("search.html", term=term, mode=mode, results=results)


@app.route("/findings")
def findings():
    return render_template(
        "findings.html", findings=db.list_findings(), statuses=db.FINDING_STATUSES,
        apps=db.group_apps_for_sidebar(), selected_id=None,
    )


@app.route("/findings/delete/<int:finding_id>", methods=["POST"])
def delete_finding_route(finding_id):
    db.delete_finding(finding_id)
    flash("Hallazgo eliminado.")
    return redirect(url_for("findings"))


@app.route("/findings/status/<int:finding_id>", methods=["POST"])
def set_finding_status_route(finding_id):
    """Registra un cambio de estado explicito sobre un hallazgo (ver
    analyzer/db.py: set_finding_status — sin autenticacion de usuarios
    todavia, changed_by queda vacio por ahora)."""
    status = request.form.get("status", "")
    try:
        db.set_finding_status(finding_id, status)
    except ValueError as e:
        flash(str(e))
    else:
        flash(f"Hallazgo #{finding_id} marcado como {status}.")
    return redirect(url_for("findings"))


@app.route("/portfolio")
def portfolio():
    """Capacidades de portafolio (v0.5): diccionario de datos consolidado,
    grafo de dependencias, catalogo de patrones y Priority & Complexity
    Engine — pura agregacion sobre datos ya extraidos, sin analisis nuevo.
    Ver analyzer/db.py: get_table_dictionary() / get_dependency_graph() /
    get_pattern_catalog() / get_priority_and_complexity(). La tabla de
    prioridad muestra solo un resumen por app (badge + evidencia breve,
    via priority_report.short_evidence — no recalcula nada); el reporte
    completo de apoyo a la decision vive en /apps/<id>/priority_report."""
    priority_rows = db.get_priority_and_complexity()
    for r in priority_rows:
        r["short_evidence"] = {
            key: priority_report.short_evidence(key, f) for key, f in r["factors"].items()
        }
    return render_template(
        "portfolio.html",
        table_dictionary=db.get_table_dictionary(),
        dependency_graph=db.get_dependency_graph(),
        pattern_catalog=db.get_pattern_catalog(),
        priority_and_complexity=priority_rows,
        factor_weights=db.FACTOR_WEIGHTS,
        severity_weight=db.SEVERITY_WEIGHT,
        apps=db.group_apps_for_sidebar(), selected_id=None,
    )


@app.route("/apps/<int:app_id>/priority_report")
def priority_report_view(app_id):
    """Reporte de apoyo a la decision para una app (resumen ejecutivo,
    narrativa, tabla de factores con Evidencia/Interpretacion separadas,
    observaciones arquitectonicas, conclusion). Presentacion pura sobre
    get_priority_and_complexity() — ver analyzer/priority_report.py."""
    report = priority_report.build_report(app_id)
    if report is None:
        flash("App no encontrada.")
        return redirect(url_for("portfolio"))
    # Narrativa/conclusion son prosa con enfasis **markdown** simple (misma
    # libreria ya usada en app_detail() para renderizar reportes) -- nada
    # nuevo que instalar.
    report["narrativa_html"] = md.markdown(report["narrativa"])
    report["conclusion_html"] = md.markdown(report["conclusion"])
    return render_template(
        "priority_report.html", report=report,
        apps=db.group_apps_for_sidebar(), selected_id=app_id,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
