"""Incremento "exposicion en UI" (2026-08-19): tests de la capa de
COMPOSICION/PRESENTACION de la clasificacion Data Flow -- nunca de la
clasificacion en si (eso ya esta 100% cubierto por tests/test_data_flow.py,
que sigue siendo puro/sin BD). Aqui SI se toca una BD temporal (mismo patron
de tests/test_lifecycle_persistence.py: db.DB_PATH monkeypatcheado, nunca
qapv_analyzer.db real) porque resolve_data_flow_portfolio() y las rutas
Flask nuevas necesariamente leen filas ya persistidas."""

import pytest

from analyzer import data_flow, db
from analyzer.extract import SqlFinding
from analyzer.techstack import TechStack


def _tech():
    return TechStack(dotnet_target="net472", ui_framework=["WinForms"], db_drivers=["System.Data.SqlClient"])


def _sql_finding(target, raw="", resolved=None, result_columns=None):
    return SqlFinding(
        file="Foo.cs", class_name="Foo", method="Bar", kind="CommandText",
        raw=raw, resolved=resolved, target=target, result_columns=result_columns or [],
    )


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_data_flow_ui.db")
    db.init_db()


@pytest.fixture
def client(temp_db):
    """Cliente Flask de prueba sobre la MISMA BD temporal -- app.py comparte
    el modulo analyzer.db (singleton de Python), asi que el monkeypatch de
    db.DB_PATH aplica igual a las rutas."""
    import app as flask_app_module
    flask_app_module.app.testing = True
    return flask_app_module.app.test_client()


def _seed_producer_and_consumer(app_name_producer="OTDR/OTDR", app_name_consumer="DataTransfer"):
    """Escenario real ya validado en Fase 8: OTDR/OTDR escribe IL (medicion
    real) en TESTS_OTDR_RES; DataTransfer solo lee STATUS (columna ambigua)
    de la MISMA tabla -- debe confirmarse consumidor_resultado via
    test_context, no reimplementando nada, solo por pasar por el portafolio."""
    db.save_analysis(
        app_name_producer, r"\\server\OTDR.exe", _tech(), [],
        [_sql_finding("TESTS_OTDR_RES", resolved="Insert into TESTS_OTDR_RES (IL) values ({a})")],
        [], [],
    )
    db.save_analysis(
        app_name_consumer, r"\\server\DataTransfer.exe", _tech(), [],
        [_sql_finding("TESTS_OTDR_RES", resolved="SELECT STATUS FROM TESTS_OTDR_RES WHERE SERIAL_NUMBER='{sn}'")],
        [], [],
    )


class TestResolveDataFlowPortfolioComposition:
    """resolve_data_flow_portfolio() es una capa de COMPOSICION -- debe
    ensamblar evidencia de la BD y delegar toda decision de clasificacion en
    resolve_data_flow() (ya testeada en test_data_flow.py), nunca reimplementar
    ningun criterio."""

    def test_both_apps_reach_the_portfolio_result(self, temp_db):
        _seed_producer_and_consumer()

        edges = data_flow.resolve_data_flow_portfolio()
        by_app = {(e.app_name, e.target): e.role for e in edges}

        assert by_app[("OTDR/OTDR", "TESTS_OTDR_RES")] == "productor_numerico"

    def test_cross_app_test_context_is_wired_through_the_portfolio_function(self, temp_db):
        """La unica razon por la que DataTransfer puede llegar a
        consumidor_resultado leyendo solo STATUS es que
        resolve_data_flow_portfolio() construyo test_context_index sobre
        TODO el portafolio (incluyendo la escritura de OTDR/OTDR) antes de
        clasificar -- si esto reimplementara la regla en vez de reutilizar
        build_test_context_index()/resolve_data_flow(), este test fallaria."""
        _seed_producer_and_consumer()

        edges = data_flow.resolve_data_flow_portfolio()
        by_app = {(e.app_name, e.target): e.role for e in edges}

        assert by_app[("DataTransfer", "TESTS_OTDR_RES")] == "consumidor_resultado"

    def test_an_app_with_no_sql_findings_produces_no_edges_not_indeterminado(self, temp_db):
        """Ausencia de evidencia != indeterminado -- una app sin ningun
        sql_finding sencillamente no aparece en el resultado del portafolio,
        no genera una fila indeterminado inventada."""
        db.save_analysis("SinBD/SinBD", r"\\server\SinBD.exe", _tech(), [], [], [], [])

        edges = data_flow.resolve_data_flow_portfolio()

        assert not any(e.app_name == "SinBD/SinBD" for e in edges)

    def test_an_app_with_unreconstructable_columns_is_indeterminado(self, temp_db):
        """Contraste directo con el caso anterior: aqui SI hay una operacion
        detectada (INSERT) pero sin columnas reconstruibles -- eso SI debe
        ser indeterminado, con resolution_status explicito."""
        db.save_analysis(
            "DinamicoSQL/DinamicoSQL", r"\\server\Dinamico.exe", _tech(), [],
            [_sql_finding(
                "AlgunaTabla",
                raw='sqlCommand.CommandText = "insert into AlgunaTabla " + cols.ToString() + " values (" + vals.ToString() + ")";',
            )],
            [], [],
        )

        edges = data_flow.resolve_data_flow_portfolio()
        match = [e for e in edges if e.app_name == "DinamicoSQL/DinamicoSQL"]

        assert len(match) == 1
        assert match[0].role == "indeterminado"
        assert match[0].resolution_status == "unresolved_no_columns"


class TestDataFlowRoute:
    """/data_flow -- vista global, agrupada por rol/tabla/aplicacion segun
    ?group=, sobre la MISMA clasificacion que resolve_data_flow_portfolio()."""

    def test_returns_200(self, client):
        _seed_producer_and_consumer()
        resp = client.get("/data_flow")
        assert resp.status_code == 200

    def test_page_shows_the_classified_applications_and_roles(self, client):
        _seed_producer_and_consumer()
        resp = client.get("/data_flow")
        body = resp.get_data(as_text=True)

        assert "OTDR/OTDR" in body
        assert "DataTransfer" in body
        assert "productor_numerico" in body
        assert "consumidor_resultado" in body

    def test_group_by_table_renders_the_table_name_as_a_section(self, client):
        _seed_producer_and_consumer()
        resp = client.get("/data_flow?group=table")
        assert resp.status_code == 200
        assert "TESTS_OTDR_RES" in resp.get_data(as_text=True)

    def test_group_by_app_renders_successfully(self, client):
        _seed_producer_and_consumer()
        resp = client.get("/data_flow?group=app")
        assert resp.status_code == 200

    def test_invalid_group_falls_back_to_role_without_erroring(self, client):
        _seed_producer_and_consumer()
        resp = client.get("/data_flow?group=nonsense")
        assert resp.status_code == 200

    def test_empty_portfolio_shows_the_no_evidence_message_not_an_error(self, client):
        resp = client.get("/data_flow")
        assert resp.status_code == 200
        assert "Aún no hay evidencia" in resp.get_data(as_text=True)


class TestAppDetailDataFlowCard:
    """result.html debe mostrar la MISMA clasificacion que /data_flow para
    la misma app -- unica fuente de verdad compartida."""

    def test_app_with_classifiable_evidence_shows_its_role(self, client):
        _seed_producer_and_consumer()
        with db.get_conn() as conn:
            app_id = conn.execute("SELECT id FROM apps WHERE name = 'OTDR/OTDR'").fetchone()["id"]

        resp = client.get(f"/apps/{app_id}")
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert "Rol en el flujo de datos" in body
        assert "productor_numerico" in body
        assert "TESTS_OTDR_RES" in body

    def test_indeterminado_is_shown_explicitly(self, client):
        db.save_analysis(
            "DinamicoSQL/DinamicoSQL", r"\\server\Dinamico.exe", _tech(), [],
            [_sql_finding(
                "AlgunaTabla",
                raw='sqlCommand.CommandText = "insert into AlgunaTabla " + cols.ToString() + " values (" + vals.ToString() + ")";',
            )],
            [], [],
        )
        with db.get_conn() as conn:
            app_id = conn.execute("SELECT id FROM apps WHERE name = 'DinamicoSQL/DinamicoSQL'").fetchone()["id"]

        resp = client.get(f"/apps/{app_id}")
        body = resp.get_data(as_text=True)

        assert "indeterminado" in body

    def test_app_with_no_sql_findings_shows_absence_message_not_indeterminado(self, client):
        db.save_analysis("SinBD/SinBD", r"\\server\SinBD.exe", _tech(), [], [], [], [])
        with db.get_conn() as conn:
            app_id = conn.execute("SELECT id FROM apps WHERE name = 'SinBD/SinBD'").fetchone()["id"]

        resp = client.get(f"/apps/{app_id}")
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert "Sin clasificación de flujo de datos disponible" in body
        assert "indeterminado" not in body

    def test_classification_is_identical_between_data_flow_view_and_app_detail(self, client):
        """Misma fuente de verdad: lo que /data_flow reporta para OTDR/OTDR
        sobre TESTS_OTDR_RES debe ser exactamente lo que result.html muestra
        para esa misma app."""
        _seed_producer_and_consumer()
        with db.get_conn() as conn:
            app_id = conn.execute("SELECT id FROM apps WHERE name = 'OTDR/OTDR'").fetchone()["id"]

        portfolio_edges = data_flow.resolve_data_flow_portfolio()
        expected_role = next(e.role for e in portfolio_edges if e.app_name == "OTDR/OTDR" and e.target == "TESTS_OTDR_RES")

        resp = client.get(f"/apps/{app_id}")
        body = resp.get_data(as_text=True)

        assert expected_role in body


class TestListSqlFindingsForTargets:
    """db.list_sql_findings_for_targets() -- consulta dirigida (incremento de
    rendimiento 2026-08-19): reemplaza list_apps()+get_app() por app para
    construir test_context_index, sin volver a implementar ningun criterio
    de normalizacion (la fuente de verdad sigue siendo
    analyzer.data_flow.normalize_table_key())."""

    def test_finds_findings_of_the_target_app(self, temp_db):
        _seed_producer_and_consumer()
        rows = db.list_sql_findings_for_targets({"TESTS_OTDR_RES"})

        assert any(r["app_name"] == "OTDR/OTDR" and r["target"] == "TESTS_OTDR_RES" for r in rows)

    def test_finds_findings_of_other_apps_sharing_the_same_table(self, temp_db):
        """Es exactamente el requisito cross-app: DataTransfer NO es la app
        que se esta consultando, pero SI comparte la tabla -- debe aparecer."""
        _seed_producer_and_consumer()
        rows = db.list_sql_findings_for_targets({"TESTS_OTDR_RES"})

        assert any(r["app_name"] == "DataTransfer" and r["target"] == "TESTS_OTDR_RES" for r in rows)

    def test_does_not_include_unrelated_tables(self, temp_db):
        db.save_analysis(
            "OTDR/OTDR", r"\\server\OTDR.exe", _tech(), [],
            [_sql_finding("TESTS_OTDR_RES", resolved="Insert into TESTS_OTDR_RES (IL) values ({a})")],
            [], [],
        )
        db.save_analysis(
            "OtraApp/OtraApp", r"\\server\Otra.exe", _tech(), [],
            [_sql_finding("TablaCompletamenteAjena", resolved="SELECT STATUS FROM TablaCompletamenteAjena")],
            [], [],
        )
        rows = db.list_sql_findings_for_targets({"TESTS_OTDR_RES"})

        assert not any(r["target"] == "TablaCompletamenteAjena" for r in rows)

    def test_respects_dbo_prefix_and_case_normalization(self, temp_db):
        """Misma tabla, distinta forma real en BD (case + prefijo dbo.) --
        debe encontrarla igual, ya que build_test_context_index() la
        normalizara de todos modos a la misma clave."""
        db.save_analysis(
            "AppConPrefijo/AppConPrefijo", r"\\server\App.exe", _tech(), [],
            [_sql_finding("dbo.TWaveLength", resolved="SELECT TEST_DATE FROM dbo.TWaveLength")],
            [], [],
        )
        rows = db.list_sql_findings_for_targets({"TWAVELENGTH"})

        assert any(r["app_name"] == "AppConPrefijo/AppConPrefijo" for r in rows)

    def test_excludes_stored_procedures(self, temp_db):
        """Mismo criterio que build_test_context_index(): un procedimiento
        no tiene 'columnas' de tabla en el mismo sentido -- no debe
        alimentar el contexto acotado."""
        db.save_analysis(
            "AppConSP/AppConSP", r"\\server\App.exe", _tech(), [],
            [SqlFinding(
                file="Foo.cs", class_name="Foo", method="Bar", kind="CommandText",
                raw="", resolved="EXEC dbo.TESTS_OTDR_RES @a=1",
                target="TESTS_OTDR_RES", is_stored_procedure=True,
            )],
            [], [],
        )
        rows = db.list_sql_findings_for_targets({"TESTS_OTDR_RES"})

        assert not any(r["app_name"] == "AppConSP/AppConSP" for r in rows)

    def test_empty_target_keys_returns_empty(self, temp_db):
        assert db.list_sql_findings_for_targets(set()) == []


class TestBoundedContextEquivalence:
    """LA PRUEBA MAS IMPORTANTE de este incremento: el camino ACOTADO
    (resolve_data_flow_for_app) debe producir, para cada edge, EXACTAMENTE
    los mismos valores (app, target, role, writes, reads,
    resolution_status) que filtrar el resultado del camino PORTFOLIO-WIDE
    (resolve_data_flow_portfolio) para esa misma app -- no solo los mismos
    conteos agregados."""

    def _edges_as_tuples(self, edges):
        """Representacion comparable edge por edge -- incluye writes/reads
        completos (operation + columns), no solo el rol."""
        return sorted(
            (
                e.app_name, e.target, e.role, e.resolution_status,
                tuple((w.operation, w.columns) for w in e.writes),
                tuple((r.operation, r.columns) for r in e.reads),
            )
            for e in edges
        )

    def test_bounded_matches_portfolio_wide_including_cross_app_confirmation(self, temp_db):
        """Escenario obligatorio: OTDR/OTDR escribe IL (señal fuerte) en
        TESTS_OTDR_RES; DataTransfer solo lee STATUS (columna ambigua) de la
        MISMA tabla -- el camino acotado para DataTransfer debe seguir
        confirmando consumidor_resultado via la evidencia de OTDR/OTDR,
        exactamente igual que el camino portfolio-wide."""
        _seed_producer_and_consumer()
        with db.get_conn() as conn:
            producer_id = conn.execute("SELECT id FROM apps WHERE name = 'OTDR/OTDR'").fetchone()["id"]
            consumer_id = conn.execute("SELECT id FROM apps WHERE name = 'DataTransfer'").fetchone()["id"]

        portfolio_edges = data_flow.resolve_data_flow_portfolio()
        portfolio_producer = [e for e in portfolio_edges if e.app_name == "OTDR/OTDR"]
        portfolio_consumer = [e for e in portfolio_edges if e.app_name == "DataTransfer"]

        bounded_producer = data_flow.resolve_data_flow_for_app(producer_id)
        bounded_consumer = data_flow.resolve_data_flow_for_app(consumer_id)

        assert self._edges_as_tuples(bounded_producer) == self._edges_as_tuples(portfolio_producer)
        assert self._edges_as_tuples(bounded_consumer) == self._edges_as_tuples(portfolio_consumer)
        # Confirmacion explicita de que la clasificacion cruzada SI llego:
        assert any(e.role == "consumidor_resultado" for e in bounded_consumer)

    def test_bounded_matches_portfolio_wide_for_an_app_with_no_shared_tables(self, temp_db):
        """Caso de control: una app cuyas tablas nadie mas toca -- el
        contexto acotado no deberia diferir del portfolio-wide tampoco."""
        _seed_producer_and_consumer()
        db.save_analysis(
            "Aislada/Aislada", r"\\server\Aislada.exe", _tech(), [],
            [_sql_finding("TablaExclusivaDeAislada", resolved="SELECT STATUS FROM TablaExclusivaDeAislada")],
            [], [],
        )
        with db.get_conn() as conn:
            app_id = conn.execute("SELECT id FROM apps WHERE name = 'Aislada/Aislada'").fetchone()["id"]

        portfolio_edges = [e for e in data_flow.resolve_data_flow_portfolio() if e.app_name == "Aislada/Aislada"]
        bounded_edges = data_flow.resolve_data_flow_for_app(app_id)

        assert self._edges_as_tuples(bounded_edges) == self._edges_as_tuples(portfolio_edges)

    def test_bounded_matches_portfolio_wide_for_an_app_with_no_sql_findings(self, temp_db):
        db.save_analysis("SinBD/SinBD", r"\\server\SinBD.exe", _tech(), [], [], [], [])
        with db.get_conn() as conn:
            app_id = conn.execute("SELECT id FROM apps WHERE name = 'SinBD/SinBD'").fetchone()["id"]

        portfolio_edges = [e for e in data_flow.resolve_data_flow_portfolio() if e.app_name == "SinBD/SinBD"]
        bounded_edges = data_flow.resolve_data_flow_for_app(app_id)

        assert bounded_edges == portfolio_edges == []
