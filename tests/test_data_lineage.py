"""Incremento Data Lineage (2026-09): tests de analyzer/data_lineage.py --
deteccion de origen Oracle, traversal recursivo Oracle->app->tabla->app->...,
clasificacion por nivel, impact analysis, critical objects/apps, y
persistencia/refresh de corridas. Nunca prueba una segunda clasificacion --
analyzer.data_flow.resolve_data_flow()/resolve_app_relations() siguen siendo
la unica fuente de verdad para productor/consumidor; aqui solo se prueba el
grafo Oracle-aware construido ENCIMA de esa clasificacion. Mismo patron de BD
temporal que tests/test_app_map.py (db.DB_PATH monkeypatcheado, nunca
qapv_analyzer.db real)."""

import pytest

from analyzer import data_lineage, db, server_resolution
from analyzer.extract import SettingEntry, SqlFinding
from analyzer.techstack import TechStack


def _tech():
    return TechStack(dotnet_target="net472", ui_framework=["WinForms"], db_drivers=["System.Data.SqlClient"])


def _sql_finding(target, raw="", resolved=None, kind="CommandText", category="query"):
    return SqlFinding(
        file="Foo.cs", class_name="Foo", method="Bar", kind=kind, category=category,
        raw=raw, resolved=resolved, target=target,
    )


def _setting(name, value, category="sql_or_oracle"):
    return SettingEntry(name=name, default_value=value, is_connection_string=True, category=category, source_file="app.config")


def _save(app_name, findings, settings=None):
    db.save_analysis(app_name, f"\\\\server\\{app_name.replace('/', '_')}.exe", _tech(), settings or [], findings, [], [])


def _app_id(app_name):
    return db.get_app_id_by_name(app_name)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_data_lineage.db")
    db.init_db()


class TestDirectOracleDetection:
    """3 senales independientes -- cualquiera basta para DIRECT_ORACLE."""

    def test_oracle_connection_kind_is_direct_origin(self, temp_db):
        _save("OracleApp", [_sql_finding("SomeTable", kind="OracleConnection", raw="new OracleConnection(cx)")])
        with db.get_conn() as conn:
            origins = data_lineage.detect_oracle_origin_apps(conn)
        assert _app_id("OracleApp") in origins
        assert "ORACLE_CONNECTION_KIND" in origins[_app_id("OracleApp")].detection_method

    def test_oracle_package_call_is_direct_origin(self, temp_db):
        _save("PackageApp", [_sql_finding(
            "XXAFL_QAPV_RL1_pkg.process", category="oracle_package_call",
            raw="XXAFL_QAPV_RL1_pkg.process(:p1)",
        )])
        with db.get_conn() as conn:
            origins = data_lineage.detect_oracle_origin_apps(conn)
        assert _app_id("PackageApp") in origins
        assert "ORACLE_PACKAGE_CALL" in origins[_app_id("PackageApp")].detection_method

    def test_tns_connection_string_is_direct_origin(self, temp_db):
        _save("TnsApp", [_sql_finding("SomeTable", resolved="SELECT 1")], settings=[
            _setting("CX", "Data Source=(DESCRIPTION=(ADDRESS=(HOST=oraserver)(PORT=1521)))(CONNECT_DATA=(SID=QAPV))"),
        ])
        with db.get_conn() as conn:
            origins = data_lineage.detect_oracle_origin_apps(conn)
        assert _app_id("TnsApp") in origins
        assert "ORACLE_CONNECTION_STRING_TNS" in origins[_app_id("TnsApp")].detection_method

    def test_sql_server_only_app_is_not_a_direct_origin(self, temp_db):
        _save("SqlServerApp", [_sql_finding("SomeTable", kind="SqlConnection", raw="new SqlConnection(cx)")], settings=[
            _setting("CX", "Server=NAAMRT-QCS11;Database=QAPVMLN;"),
        ])
        with db.get_conn() as conn:
            origins = data_lineage.detect_oracle_origin_apps(conn)
        assert _app_id("SqlServerApp") not in origins


class TestRecursiveTraversalAndClassification:
    def _build_two_hop_chain(self):
        # AppOracle (DIRECT_ORACLE, escribe TableX) -> AppMiddle (lee TableX, escribe TableY) -> AppEnd (lee TableY)
        _save("AppOracle", [
            _sql_finding("SomeOracleTable", kind="OracleConnection", raw="new OracleConnection(cx)"),
            _sql_finding("TableX", resolved="Insert into TableX (IL) values ({a})"),
        ])
        _save("AppMiddle", [
            _sql_finding("TableX", resolved="SELECT IL FROM TableX WHERE ID={id}"),
            _sql_finding("TableY", resolved="Insert into TableY (IL) values ({a})"),
        ])
        _save("AppEnd", [_sql_finding("TableY", resolved="SELECT IL FROM TableY WHERE ID={id}")])

    def test_two_hop_traversal_classifies_by_level(self, temp_db):
        self._build_two_hop_chain()
        snapshot = data_lineage.compute_full_lineage_snapshot()
        by_name = {c.app_name: c for c in snapshot.classifications}

        assert by_name["AppOracle"].classification == "DIRECT_ORACLE"
        assert by_name["AppOracle"].lineage_level == 0
        assert by_name["AppMiddle"].classification == "ORACLE_DERIVED"
        assert by_name["AppMiddle"].lineage_level == 1
        assert by_name["AppMiddle"].origin_app_name == "AppOracle"
        assert by_name["AppEnd"].classification == "DOWNSTREAM_CONSUMER"
        assert by_name["AppEnd"].lineage_level == 2

    def test_three_plus_hop_traversal_is_indirect_downstream(self, temp_db):
        self._build_two_hop_chain()
        _save("AppFinal", [_sql_finding("TableZ", resolved="SELECT IL FROM TableZ WHERE ID={id}")])
        _save("AppEnd", [
            _sql_finding("TableY", resolved="SELECT IL FROM TableY WHERE ID={id}"),
            _sql_finding("TableZ", resolved="Insert into TableZ (IL) values ({a})"),
        ])

        snapshot = data_lineage.compute_full_lineage_snapshot()
        by_name = {c.app_name: c for c in snapshot.classifications}

        assert by_name["AppFinal"].classification == "INDIRECT_DOWNSTREAM"
        assert by_name["AppFinal"].lineage_level == 3
        assert by_name["AppFinal"].origin_app_name == "AppOracle"

    def test_oracle_derived_without_any_direct_oracle_connection(self, temp_db):
        """Regresion explicita: AppMiddle NUNCA se conecta a Oracle -- solo
        SQL Server -- y aun asi debe ser ORACLE_DERIVED (nunca UNRELATED)
        porque alcanza a AppOracle en 1 salto de Data Flow."""
        self._build_two_hop_chain()
        snapshot = data_lineage.compute_full_lineage_snapshot()
        by_name = {c.app_name: c for c in snapshot.classifications}

        with db.get_conn() as conn:
            origins = data_lineage.detect_oracle_origin_apps(conn)
        assert _app_id("AppMiddle") not in origins  # sin conexion Oracle propia
        assert by_name["AppMiddle"].classification == "ORACLE_DERIVED"

    def test_app_with_no_relation_to_oracle_is_unrelated(self, temp_db):
        self._build_two_hop_chain()
        _save("Isolated", [_sql_finding("UnrelatedTable", resolved="SELECT X FROM UnrelatedTable WHERE ID={id}")])

        snapshot = data_lineage.compute_full_lineage_snapshot()
        by_name = {c.app_name: c for c in snapshot.classifications}

        assert by_name["Isolated"].classification == "UNRELATED"
        assert by_name["Isolated"].lineage_level is None
        assert by_name["Isolated"].origin_app_name is None

    def test_cycle_between_two_apps_terminates_and_both_get_classified(self, temp_db):
        _save("AppOracle", [
            _sql_finding("SomeOracleTable", kind="OracleConnection", raw="new OracleConnection(cx)"),
            _sql_finding("TableA", resolved="Insert into TableA (IL) values ({a})"),
        ])
        # AppB y AppC se producen/consumen mutuamente (ciclo A<->B), ambas
        # alcanzables desde AppOracle -- debe terminar (no colgarse) y ambas
        # deben recibir una clasificacion real.
        _save("AppB", [
            _sql_finding("TableA", resolved="SELECT IL FROM TableA WHERE ID={id}"),
            _sql_finding("TableC", resolved="Insert into TableC (IL) values ({a})"),
            _sql_finding("TableD", resolved="SELECT IL FROM TableD WHERE ID={id}"),
        ])
        _save("AppC", [
            _sql_finding("TableC", resolved="SELECT IL FROM TableC WHERE ID={id}"),
            _sql_finding("TableD", resolved="Insert into TableD (IL) values ({a})"),
        ])

        snapshot = data_lineage.compute_full_lineage_snapshot()
        by_name = {c.app_name: c for c in snapshot.classifications}

        assert by_name["AppB"].classification in ("ORACLE_DERIVED",)
        assert by_name["AppC"].classification in db.LINEAGE_CLASSIFICATIONS
        assert by_name["AppC"].classification != "UNRELATED"


class TestImpactAnalysis:
    def test_direct_vs_indirect_consumers(self, temp_db):
        _save("AppOracle", [
            _sql_finding("SomeOracleTable", kind="OracleConnection", raw="new OracleConnection(cx)"),
            _sql_finding("TableX", resolved="Insert into TableX (IL) values ({a})"),
        ])
        _save("AppMiddle", [
            _sql_finding("TableX", resolved="SELECT IL FROM TableX WHERE ID={id}"),
            _sql_finding("TableY", resolved="Insert into TableY (IL) values ({a})"),
        ])
        _save("AppEnd", [_sql_finding("TableY", resolved="SELECT IL FROM TableY WHERE ID={id}")])

        summary = data_lineage.run_lineage_build()
        result = data_lineage.compute_impact_analysis(summary.run_id, app_id=_app_id("AppOracle"))

        assert result.direct_consumers == ("AppMiddle",)
        assert result.indirect_consumers == ("AppEnd",)
        assert "AppEnd" not in result.direct_consumers

    def test_impact_analysis_for_unknown_app_returns_none(self, temp_db):
        summary = data_lineage.run_lineage_build()
        assert data_lineage.compute_impact_analysis(summary.run_id, app_id=999999) is None


class TestCriticalObjectsAndApps:
    def test_hub_table_is_flagged_critical(self, temp_db):
        # Terciles se calculan sobre el portafolio ACTUAL (db._bucket_by_percentile)
        # -- con solo 2 objetos distintos ninguno alcanza "Alta" (no hay
        # suficiente spread), asi que se agregan varias tablas "solitarias"
        # (fan_in+fan_out=2) para que HubTable (fan_in+fan_out=6) quede
        # claramente en el tercil superior, igual que test_hub_app_is_flagged_critical.
        _save("Producer", [_sql_finding("HubTable", resolved="Insert into HubTable (IL) values ({a})")])
        for i in range(5):
            _save(f"Consumer{i}", [_sql_finding("HubTable", resolved="SELECT IL FROM HubTable WHERE ID={id}")])
        for i in range(5):
            _save(f"LonelyProducer{i}", [_sql_finding(f"LonelyTable{i}", resolved=f"Insert into LonelyTable{i} (IL) values ({{a}})")])
            _save(f"LonelyConsumer{i}", [_sql_finding(f"LonelyTable{i}", resolved=f"SELECT IL FROM LonelyTable{i} WHERE ID={{id}}")])

        snapshot = data_lineage.compute_full_lineage_snapshot()
        by_name = {o.object_name: o for o in snapshot.object_stats}

        assert by_name["HubTable"].is_critical_object
        assert by_name["HubTable"].fan_in == 5
        assert not by_name["LonelyTable0"].is_critical_object

    def test_hub_app_is_flagged_critical(self, temp_db):
        _save("HubApp", [
            _sql_finding(f"Table{i}", resolved=f"Insert into Table{i} (IL) values ({{a}})") for i in range(5)
        ])
        for i in range(5):
            _save(f"Consumer{i}", [_sql_finding(f"Table{i}", resolved=f"SELECT IL FROM Table{i} WHERE ID={{id}}")])

        snapshot = data_lineage.compute_full_lineage_snapshot()
        by_name = {c.app_name: c for c in snapshot.classifications}

        assert by_name["HubApp"].is_critical_app
        assert by_name["HubApp"].fan_out == 5


class TestConfidenceNeverSilentlyUpgraded:
    def test_data_flow_hop_confidence_is_confirmed_not_invented(self, temp_db):
        _save("AppA", [_sql_finding("TableX", resolved="Insert into TableX (IL) values ({a})")])
        _save("AppB", [_sql_finding("TableX", resolved="SELECT IL FROM TableX WHERE ID={id}")])

        snapshot = data_lineage.compute_full_lineage_snapshot()
        assert len(snapshot.edges) == 1
        edge = snapshot.edges[0]
        assert edge.confidence_label in db.LINEAGE_CONFIDENCE_LABELS
        assert edge.confidence == edge.confidence  # score siempre viene de confidence.py, nunca a mano


class TestSnapshotPersistenceAndRefresh:
    def test_two_builds_create_two_separate_runs(self, temp_db):
        _save("AppA", [_sql_finding("TableX", resolved="Insert into TableX (IL) values ({a})")])
        _save("AppB", [_sql_finding("TableX", resolved="SELECT IL FROM TableX WHERE ID={id}")])

        first = data_lineage.run_lineage_build()
        second = data_lineage.run_lineage_build()

        assert first.run_id != second.run_id
        runs = db.list_lineage_runs()
        assert len(runs) == 2

        latest = db.get_latest_completed_lineage_run()
        assert latest["id"] == second.run_id

        first_edges = db.get_lineage_edges(first.run_id)
        second_edges = db.get_lineage_edges(second.run_id)
        assert all(e["lineage_run_id"] == first.run_id for e in first_edges)
        assert all(e["lineage_run_id"] == second.run_id for e in second_edges)

    def test_failed_build_never_looks_completed(self, temp_db, monkeypatch):
        def _boom():
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(data_lineage, "compute_full_lineage_snapshot", _boom)
        with pytest.raises(RuntimeError):
            data_lineage.run_lineage_build()

        runs = db.list_lineage_runs()
        assert len(runs) == 1
        assert runs[0]["status"] == "failed"
        assert db.get_latest_completed_lineage_run() is None


class TestBoundedExplorerMatchesFullTraversal:
    def test_resolve_lineage_for_app_downstream_matches_snapshot(self, temp_db):
        _save("AppOracle", [
            _sql_finding("SomeOracleTable", kind="OracleConnection", raw="new OracleConnection(cx)"),
            _sql_finding("TableX", resolved="Insert into TableX (IL) values ({a})"),
        ])
        _save("AppMiddle", [
            _sql_finding("TableX", resolved="SELECT IL FROM TableX WHERE ID={id}"),
            _sql_finding("TableY", resolved="Insert into TableY (IL) values ({a})"),
        ])
        _save("AppEnd", [_sql_finding("TableY", resolved="SELECT IL FROM TableY WHERE ID={id}")])

        tree = data_lineage.resolve_lineage_for_app(_app_id("AppOracle"), max_depth=5, direction="downstream")
        reached = {hop.to_app_name for path in tree.paths for hop in path.hops}

        assert reached == {"AppMiddle", "AppEnd"}

    def test_nonexistent_app_returns_none(self, temp_db):
        assert data_lineage.resolve_lineage_for_app(999999) is None


# ---------------------------------------------------------------------------
# Auditoria iteracion 2 (2026-09): confidence de un hop ahora se deriva del
# resolution_status REAL de server_resolution (ver analyzer/data_lineage.py:
# _attribute_server/_confidence_for_hop), no de un valor fijo. Estos tests
# reconstruyen, con archivos .cs sinteticos reales (mismo patron que
# tests/test_server_resolution.py: DECOMPILED_DIR monkeypatcheado + fixture
# de codigo), el bug real encontrado en produccion (DataCenter2ORA ->
# XXAFL_QAPV_TESTS_JGR -> DataTransfer: dos conexiones, Oracle y SQL Server,
# escribiendo al mismo target literal) para probar la correccion end-to-end
# a traves del pipeline real, no solo la funcion pura en aislamiento.
# ---------------------------------------------------------------------------

ORACLE_TNS = (
    "Data Source=(DESCRIPTION=(ADDRESS=(HOST=oraserver)(PORT=1521)))"
    "(CONNECT_DATA=(SID=QAPV));User Id=q;Password=x;"
)
SQLSERVER_CX = "Server=NAAMRT-QCS25;Database=QAPVMLN;User Id=QUALITY;Password=x;"


def _write_source_file(tmp_path, monkeypatch, app_root, file_rel, content):
    """Mismo patron que tests/test_server_resolution.py: DECOMPILED_DIR
    apunta a una carpeta temporal, nunca a decompiled/ real."""
    decompiled_dir = tmp_path / "decompiled"
    monkeypatch.setattr(server_resolution, "DECOMPILED_DIR", decompiled_dir)
    path = decompiled_dir / app_root / file_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


AMBIGUOUS_BRIDGE_CS = """
public class Form1
{
    private string CXOra;
    private string CXSql;

    public void ExportaORACLE()
    {
        OracleConnection oracleConnection2 = new OracleConnection(CXOra);
        OracleCommand oracleCommand2 = new OracleCommand();
        oracleCommand2.CommandText = commandText;

        SqlConnection sqlConnection2 = new SqlConnection(CXSql);
        SqlCommand sqlCommand3 = new SqlCommand(cmdText, sqlConnection2);
    }
}
"""

RESOLVED_PRODUCER_CS = """
public class Form1
{
    private string CX;

    public void Exporta()
    {
        SqlConnection sqlConnection = new SqlConnection(CX);
        SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection);
    }
}
"""


class TestHopConfidenceReflectsServerResolution:
    """TEST A y TEST B (autorizacion Iteracion 2)."""

    def test_a_ambiguous_oracle_vs_sqlserver_bridge_degrades_to_possible(self, temp_db, tmp_path, monkeypatch):
        """Reconstruye el bug real DataCenter2ORA -> XXAFL_QAPV_TESTS_JGR ->
        DataTransfer: la MISMA tabla se escribe via una conexion Oracle Y una
        conexion SQL Server distintas dentro del mismo metodo -- server_resolution
        debe (y ya) marca esto unresolved_ambiguous_conditional con candidatos
        oracle+sqlserver. El hop NO debe quedar en 85/CONFIRMED, pero SIGUE
        existiendo en el grafo y DataTransfer (aqui: BridgeConsumer) sigue
        siendo alcanzable por BFS -- confidence describe la evidencia, no
        decide si el edge existe."""
        _write_source_file(tmp_path, monkeypatch, "BridgeApp", "Form1.cs", AMBIGUOUS_BRIDGE_CS)

        _save("BridgeApp", [
            SqlFinding(
                file="Form1.cs", class_name="Form1", method="ExportaORACLE", kind="CommandText",
                category="query", raw="oracleCommand2.CommandText = commandText;",
                resolved="UPDATE BridgeTable SET STATUS=1 WHERE ID={id}", target="BridgeTable",
            ),
            SqlFinding(
                file="Form1.cs", class_name="Form1", method="ExportaORACLE", kind="SqlCommand",
                category="query", raw="SqlCommand sqlCommand3 = new SqlCommand(cmdText, sqlConnection2);",
                resolved="UPDATE BridgeTable SET FLAG=0 WHERE ID={id}", target="BridgeTable",
            ),
        ], settings=[_setting("CXOra", ORACLE_TNS), _setting("CXSql", SQLSERVER_CX)])
        _save("BridgeConsumer", [_sql_finding("BridgeTable", resolved="SELECT STATUS FROM BridgeTable WHERE ID={id}")])

        snapshot = data_lineage.compute_full_lineage_snapshot()
        bridge_edges = [e for e in snapshot.edges if e.object_name == "BridgeTable"]
        assert len(bridge_edges) == 1
        edge = bridge_edges[0]

        assert edge.confidence == 45
        assert edge.confidence_label == "POSSIBLE"
        assert edge.resolution_status == "unresolved_ambiguous_conditional"
        assert set(edge.candidate_kinds) == {"oracle", "sqlserver"}
        # El edge sigue existiendo y BridgeConsumer sigue siendo alcanzable --
        # confidence nunca filtra la adyacencia del BFS.
        assert edge.from_app_name == "BridgeApp"
        assert edge.to_app_name == "BridgeConsumer"

        tree = data_lineage.resolve_lineage_for_app(_app_id("BridgeApp"), max_depth=3, direction="downstream")
        reached = {hop.to_app_name for path in tree.paths for hop in path.hops}
        assert "BridgeConsumer" in reached

    def test_b_resolved_single_candidate_keeps_confirmed(self, temp_db, tmp_path, monkeypatch):
        """Caso VINS: una sola conexion candidata (SQL Server), sin
        ambiguedad -- debe CONSERVAR 85/CONFIRMED, nunca degradarse."""
        _write_source_file(tmp_path, monkeypatch, "ResolvedProducer", "Form1.cs", RESOLVED_PRODUCER_CS)

        _save("ResolvedProducer", [
            SqlFinding(
                file="Form1.cs", class_name="Form1", method="Exporta", kind="SqlCommand",
                category="query", raw="SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection);",
                resolved="UPDATE ResolvedTable SET IL=1 WHERE ID={id}", target="ResolvedTable",
            ),
        ], settings=[_setting("CX", SQLSERVER_CX)])
        _save("ResolvedConsumer", [_sql_finding("ResolvedTable", resolved="SELECT IL FROM ResolvedTable WHERE ID={id}")])

        snapshot = data_lineage.compute_full_lineage_snapshot()
        edges = [e for e in snapshot.edges if e.object_name == "ResolvedTable"]
        assert len(edges) == 1
        edge = edges[0]

        assert edge.resolution_status == "resolved"
        assert edge.candidate_kinds == ("sqlserver",)
        assert edge.confidence == 85
        assert edge.confidence_label == "CONFIRMED"

    def test_ambiguous_between_two_non_oracle_candidates_is_probable_not_possible(self, temp_db, tmp_path, monkeypatch):
        """Ambiguedad que NO involucra Oracle (ej. dos conexiones SQL Server
        distintas) no amenaza la narrativa de linaje Oracle -- debe caer en
        PROBABLE (Caso C), no en POSSIBLE (Caso B, reservado exclusivamente
        para ambiguedad Oracle-vs-otro)."""
        cs = """
public class Form1
{
    private string CX1;
    private string CX2;

    public void Exporta()
    {
        SqlConnection sqlConnection1 = new SqlConnection(CX1);
        SqlCommand sqlCommand1 = new SqlCommand(cmdText, sqlConnection1);

        SqlConnection sqlConnection2 = new SqlConnection(CX2);
        SqlCommand sqlCommand2 = new SqlCommand(cmdText, sqlConnection2);
    }
}
"""
        _write_source_file(tmp_path, monkeypatch, "TwoSqlServerApp", "Form1.cs", cs)

        _save("TwoSqlServerApp", [
            SqlFinding(
                file="Form1.cs", class_name="Form1", method="Exporta", kind="SqlCommand",
                category="query", raw="SqlCommand sqlCommand1 = new SqlCommand(cmdText, sqlConnection1);",
                resolved="UPDATE SharedTable SET A=1 WHERE ID={id}", target="SharedTable",
            ),
            SqlFinding(
                file="Form1.cs", class_name="Form1", method="Exporta", kind="SqlCommand",
                category="query", raw="SqlCommand sqlCommand2 = new SqlCommand(cmdText, sqlConnection2);",
                resolved="UPDATE SharedTable SET B=0 WHERE ID={id}", target="SharedTable",
            ),
        ], settings=[_setting("CX1", "Server=SRV1;Database=DB1;"), _setting("CX2", "Server=SRV2;Database=DB2;")])
        _save("SharedConsumer", [_sql_finding("SharedTable", resolved="SELECT A FROM SharedTable WHERE ID={id}")])

        snapshot = data_lineage.compute_full_lineage_snapshot()
        edges = [e for e in snapshot.edges if e.object_name == "SharedTable"]
        assert len(edges) == 1
        edge = edges[0]

        assert edge.resolution_status == "unresolved_ambiguous_conditional"
        assert set(edge.candidate_kinds) == {"sqlserver"}  # ambos candidatos son sqlserver -- ninguno es 'oracle'
        assert edge.confidence == 70
        assert edge.confidence_label == "PROBABLE"


class TestAmbiguityVocabularyNeverProducesConfirmed:
    """TEST C (autorizacion Iteracion 2): unresolved_lineage_origin_ambiguous /
    unresolved_lineage_direction_ambiguous siguen sin ser producidos por
    ningun extractor real hoy (confirmado en la auditoria de Iteracion 2 via
    grep -- ver analyzer/unknown.py) -- no se fabrica un fixture artificial
    para un estado que el codigo no genera. Esta clase protege la REGLA
    (cualquier resolution_status de ambiguedad -> confidence <= 45 y label !=
    CONFIRMED) probando la funcion que la implementa contra los 4 valores
    reales que server_resolution SI puede producir, incluyendo el caso limite
    exacto de la ambiguedad Oracle-vs-otro."""

    @pytest.mark.parametrize("resolution_status,candidate_kinds", [
        ("unresolved_ambiguous_conditional", ("oracle", "sqlserver")),
        ("unresolved_ambiguous_conditional", ("sqlserver", "oracle")),
        ("unresolved_ambiguous_conditional", ("oracle", "localdb")),
    ])
    def test_oracle_vs_other_ambiguity_never_reaches_confirmed(self, resolution_status, candidate_kinds):
        score, label = data_lineage._confidence_for_hop(resolution_status, candidate_kinds)
        assert score <= 45
        assert label != "CONFIRMED"
        assert label == "POSSIBLE"

    @pytest.mark.parametrize("resolution_status", [
        "unresolved_ambiguous_conditional",  # sin candidato oracle
        "unresolved_no_literal",
        "unresolved_no_source_file",
    ])
    def test_non_oracle_uncertainty_never_reaches_confirmed_either(self, resolution_status):
        score, label = data_lineage._confidence_for_hop(resolution_status, ("sqlserver", "sqlserver"))
        assert label != "CONFIRMED"
        assert score < 85

    def test_only_resolved_status_reaches_confirmed(self):
        score, label = data_lineage._confidence_for_hop("resolved", ("sqlserver",))
        assert score == 85
        assert label == "CONFIRMED"


class TestDirectOracleConfidenceUnaffected:
    """TEST D (autorizacion Iteracion 2): la correccion de confidence de
    hops (LineageHop/_attribute_server) es una capa distinta de
    detect_oracle_origin_apps() (que nunca se toco) -- Direct Oracle debe
    seguir siendo 95/CONFIRMED sin excepcion."""

    def test_direct_oracle_origin_stays_95_confirmed(self, temp_db):
        _save("OracleApp", [_sql_finding("SomeTable", kind="OracleConnection", raw="new OracleConnection(cx)")])
        with db.get_conn() as conn:
            origins = data_lineage.detect_oracle_origin_apps(conn)
        ev = origins[_app_id("OracleApp")]
        assert ev.confidence == 95
        assert ev.confidence_label == "CONFIRMED"

        snapshot = data_lineage.compute_full_lineage_snapshot()
        classification = next(c for c in snapshot.classifications if c.app_name == "OracleApp")
        assert classification.classification == "DIRECT_ORACLE"


class TestDeterministicOriginAttribution:
    """TEST E (autorizacion Iteracion 2): cuando 2+ origenes DIRECT_ORACLE
    alcanzan la misma app en el mismo nivel minimo, origin_app_name debe ser
    estable y basado en menor app_id -- nunca en el orden fisico en que
    SQLite devuelve filas de sql_findings/settings sin ORDER BY.

    Para que el test realmente ejercite la correccion (y no solo coincida
    por casualidad, como pasaba en el portafolio real donde el app_id mas
    chico ya ganaba por el orden de insercion natural), el app con MENOR
    app_id se guarda con evidencia Oracle detectada por la TERCERA consulta
    de detect_oracle_origin_apps (TNS via settings), mientras que el app con
    MAYOR app_id usa evidencia detectada por la PRIMERA consulta
    (OracleConnection kind) -- sin el sort por app_id, el de mayor id
    aparece antes en el dict y gana la carrera del BFS; con el sort,
    el de menor id gana, como exige la regla."""

    def test_lower_app_id_wins_the_tie_regardless_of_detection_query_order(self, temp_db):
        # Se guarda PRIMERO -> menor app_id -- pero su UNICA senal Oracle es
        # TNS (3ra consulta de detect_oracle_origin_apps).
        _save("LowIdOracleOrigin", [
            _sql_finding("BridgeTable", resolved="Insert into BridgeTable (IL) values ({a})"),
        ], settings=[_setting("CXOra", ORACLE_TNS)])

        # Se guarda DESPUES -> mayor app_id -- pero su senal Oracle es
        # OracleConnection kind (1ra consulta) -- sin el fix, esta app
        # apareceria PRIMERO en oracle_origins.items() y ganaria la carrera.
        _save("HighIdOracleOrigin", [
            _sql_finding("SomeOracleTable", kind="OracleConnection", raw="new OracleConnection(cx)"),
            _sql_finding("BridgeTable", resolved="Insert into BridgeTable (IL) values ({a})"),
        ])

        _save("SharedDownstream", [_sql_finding("BridgeTable", resolved="SELECT IL FROM BridgeTable WHERE ID={id}")])

        low_id = _app_id("LowIdOracleOrigin")
        high_id = _app_id("HighIdOracleOrigin")
        assert low_id < high_id, "precondicion del test: LowId debe tener menor app_id que HighId"

        with db.get_conn() as conn:
            origins = data_lineage.detect_oracle_origin_apps(conn)
        origin_ids_in_dict_order = list(origins.keys())
        assert origin_ids_in_dict_order.index(high_id) < origin_ids_in_dict_order.index(low_id), (
            "precondicion del test: sin ordenar, HighId debe aparecer ANTES que LowId "
            "en el dict (asi el test realmente prueba el sort, no coincide con el sin ordenar)"
        )

        snapshot = data_lineage.compute_full_lineage_snapshot()
        by_name = {c.app_name: c for c in snapshot.classifications}

        assert by_name["SharedDownstream"].classification == "ORACLE_DERIVED"
        assert by_name["SharedDownstream"].lineage_level == 1
        assert by_name["SharedDownstream"].origin_app_name == "LowIdOracleOrigin"

        # Repetible: correr el snapshot de nuevo produce el mismo resultado.
        snapshot2 = data_lineage.compute_full_lineage_snapshot()
        by_name2 = {c.app_name: c for c in snapshot2.classifications}
        assert by_name2["SharedDownstream"].origin_app_name == "LowIdOracleOrigin"
