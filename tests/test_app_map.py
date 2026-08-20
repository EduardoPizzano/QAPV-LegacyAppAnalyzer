"""Incremento "Mapa de Aplicaciones" (2026-08-20): tests de
resolve_app_relations() (analyzer/data_flow.py), build_app_relations_diagram()
(analyzer/diagram.py) y la ruta Flask /app_map. Nunca prueba una segunda
clasificacion -- resolve_data_flow() sigue siendo la unica fuente de verdad;
aqui solo se prueba la PROYECCION de sus DataFlowEdge en relaciones entre
aplicaciones. Mismo patron de BD temporal que tests/test_data_flow_ui.py
(db.DB_PATH monkeypatcheado, nunca qapv_analyzer.db real)."""

import pytest

from analyzer import data_flow, db, diagram
from analyzer.extract import SqlFinding
from analyzer.techstack import TechStack


def _tech():
    return TechStack(dotnet_target="net472", ui_framework=["WinForms"], db_drivers=["System.Data.SqlClient"])


def _sql_finding(target, raw="", resolved=None, result_columns=None):
    return SqlFinding(
        file="Foo.cs", class_name="Foo", method="Bar", kind="CommandText",
        raw=raw, resolved=resolved, target=target, result_columns=result_columns or [],
    )


def _save(app_name, findings):
    db.save_analysis(app_name, f"\\\\server\\{app_name.replace('/', '_')}.exe", _tech(), [], findings, [], [])


def _app_id(app_name):
    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM apps WHERE name = ?", (app_name,)).fetchone()
        return row["id"]


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_app_map.db")
    db.init_db()


@pytest.fixture
def client(temp_db):
    import app as flask_app_module
    flask_app_module.app.testing = True
    return flask_app_module.app.test_client()


class TestStrongRelationDirections:
    """Los 4 casos obligatorios de relacion FUERTE (productor->consumidor)."""

    def test_productor_to_consumidor(self, temp_db):
        _save("OTDR/OTDR", [_sql_finding("TESTS_OTDR_RES", resolved="Insert into TESTS_OTDR_RES (IL) values ({a})")])
        _save("DataTransfer", [_sql_finding("TESTS_OTDR_RES", resolved="SELECT STATUS FROM TESTS_OTDR_RES WHERE SERIAL_NUMBER='{sn}'")])

        producer_rel = data_flow.resolve_app_relations(_app_id("OTDR/OTDR"))
        consumer_rel = data_flow.resolve_app_relations(_app_id("DataTransfer"))

        assert [r.other_app for r in producer_rel.produces_to] == ["DataTransfer"]
        assert producer_rel.produces_to[0].evidence[0].table == "TESTS_OTDR_RES"
        assert [r.other_app for r in consumer_rel.consumes_from] == ["OTDR/OTDR"]

    def test_productor_to_mixto(self, temp_db):
        """A escribe TableY; C escribe Y (otra columna) Y ADEMAS la lee --
        C es mixto sobre Y, pero para la relacion con A actua como
        consumidor real (SI lee Y)."""
        _save("AppA/AppA", [_sql_finding("TableY", resolved="Insert into TableY (IL) values ({a})")])
        _save("AppC/AppC", [
            _sql_finding("TableY", resolved="Insert into TableY (STATUS) values ({s})"),
            _sql_finding("TableY", resolved="SELECT STATUS FROM TableY WHERE ID={id}"),
        ])

        rel_a = data_flow.resolve_app_relations(_app_id("AppA/AppA"))

        assert "AppC/AppC" in [r.other_app for r in rel_a.produces_to]

    def test_mixto_to_consumidor(self, temp_db):
        """C es mixto sobre TableZ (escribe medicion real Y la lee); B solo
        lee TableZ -- C debe aparecer como productor hacia B."""
        _save("AppC/AppC", [
            _sql_finding("TableZ", resolved="Insert into TableZ (IL) values ({a})"),
            _sql_finding("TableZ", resolved="SELECT IL FROM TableZ WHERE ID={id}"),
        ])
        _save("AppB/AppB", [_sql_finding("TableZ", resolved="SELECT IL FROM TableZ WHERE ID={id}")])

        rel_c = data_flow.resolve_app_relations(_app_id("AppC/AppC"))
        rel_b = data_flow.resolve_app_relations(_app_id("AppB/AppB"))

        assert "AppB/AppB" in [r.other_app for r in rel_c.produces_to]
        assert "AppC/AppC" in [r.other_app for r in rel_b.consumes_from]

    def test_mixto_to_mixto(self, temp_db):
        """A y B son AMBOS mixto sobre la MISMA tabla (cada uno escribe y
        lee) -- deben aparecer relaciones en AMBAS direcciones."""
        _save("AppA/AppA", [
            _sql_finding("TableW", resolved="Insert into TableW (IL) values ({a})"),
            _sql_finding("TableW", resolved="SELECT IL FROM TableW WHERE ID={id}"),
        ])
        _save("AppB/AppB", [
            _sql_finding("TableW", resolved="Insert into TableW (IL) values ({a})"),
            _sql_finding("TableW", resolved="SELECT IL FROM TableW WHERE ID={id}"),
        ])

        rel_a = data_flow.resolve_app_relations(_app_id("AppA/AppA"))

        assert "AppB/AppB" in [r.other_app for r in rel_a.produces_to]
        assert "AppB/AppB" in [r.other_app for r in rel_a.consumes_from]
        # Cada app ademas se auto-produce/consume la misma tabla (self-loop).
        assert any(s.table == "TableW" for s in rel_a.self_loops)


class TestWeakRelationsNeverBecomeArrows:
    def test_consumidor_consumidor_does_not_produce_an_arrow(self, temp_db):
        """Dos apps que SOLO leen la misma tabla -- relacion debil, nunca
        produces_to/consumes_from."""
        _save("AppA/AppA", [_sql_finding("TablaX", resolved="SELECT IL FROM TablaX WHERE ID={id}")])
        _save("AppB/AppB", [_sql_finding("TablaX", resolved="SELECT IL FROM TablaX WHERE ID={id}")])

        rel_a = data_flow.resolve_app_relations(_app_id("AppA/AppA"))

        assert rel_a.produces_to == ()
        assert rel_a.consumes_from == ()
        assert any(s.other_app == "AppB/AppB" and s.kind == "consumidor_compartido" for s in rel_a.shared)

    def test_productor_productor_does_not_produce_an_arrow(self, temp_db):
        """Dos apps que SOLO escriben la misma tabla -- relacion debil,
        nunca produces_to/consumes_from."""
        _save("AppA/AppA", [_sql_finding("TablaX", resolved="Insert into TablaX (IL) values ({a})")])
        _save("AppB/AppB", [_sql_finding("TablaX", resolved="Insert into TablaX (IL) values ({a})")])

        rel_a = data_flow.resolve_app_relations(_app_id("AppA/AppA"))

        assert rel_a.produces_to == ()
        assert rel_a.consumes_from == ()
        assert any(s.other_app == "AppB/AppB" and s.kind == "productor_compartido" for s in rel_a.shared)


class TestIndeterminadoExcluded:
    def test_indeterminado_table_generates_no_relation(self, temp_db):
        """SELECT * sin result_columns -- indeterminado, no debe generar
        NINGUNA relacion aunque otra app SI tenga evidencia real ahi."""
        _save("AppA/AppA", [_sql_finding(
            "TablaDinamica",
            raw='sqlCommand.CommandText = "SELECT * FROM TablaDinamica WHERE ID=@id";',
        )])
        _save("AppB/AppB", [_sql_finding("TablaDinamica", resolved="Insert into TablaDinamica (IL) values ({a})")])

        rel_a = data_flow.resolve_app_relations(_app_id("AppA/AppA"))

        assert rel_a.produces_to == ()
        assert rel_a.consumes_from == ()
        assert rel_a.shared == ()


class TestSelfLoop:
    def test_same_app_producing_and_consuming_is_a_self_loop_not_a_relation(self, temp_db):
        _save("AppSola/AppSola", [
            _sql_finding("TESTS_OTDR_RES", resolved="Insert into TESTS_OTDR_RES (IL) values ({a})"),
            _sql_finding("TESTS_OTDR_RES", resolved="SELECT IL FROM TESTS_OTDR_RES WHERE ID={id}"),
        ])

        rel = data_flow.resolve_app_relations(_app_id("AppSola/AppSola"))

        assert rel.produces_to == ()
        assert rel.consumes_from == ()
        assert len(rel.self_loops) == 1
        assert rel.self_loops[0].table == "TESTS_OTDR_RES"


class TestDeduplicationAndEvidencePreservation:
    def test_multiple_shared_tables_collapse_into_one_relation_with_all_tables_as_evidence(self, temp_db):
        """A y B comparten DOS tablas (relacion fuerte en ambas) -- debe
        ser UNA sola AppRelation con 2 entradas de evidencia, no 2
        relaciones separadas hacia la misma app."""
        _save("AppA/AppA", [
            _sql_finding("TablaUno", resolved="Insert into TablaUno (IL) values ({a})"),
            _sql_finding("TablaDos", resolved="Insert into TablaDos (IL) values ({a})"),
        ])
        _save("AppB/AppB", [
            _sql_finding("TablaUno", resolved="SELECT IL FROM TablaUno WHERE ID={id}"),
            _sql_finding("TablaDos", resolved="SELECT IL FROM TablaDos WHERE ID={id}"),
        ])

        rel_a = data_flow.resolve_app_relations(_app_id("AppA/AppA"))

        assert len(rel_a.produces_to) == 1
        assert rel_a.produces_to[0].other_app == "AppB/AppB"
        tables = {ev.table for ev in rel_a.produces_to[0].evidence}
        assert tables == {"TablaUno", "TablaDos"}

    def test_evidence_preserves_operations_and_columns(self, temp_db):
        _save("AppA/AppA", [_sql_finding("TESTS_OTDR_RES", resolved="Insert into TESTS_OTDR_RES (IL, ReflA) values ({a},{b})")])
        _save("AppB/AppB", [_sql_finding("TESTS_OTDR_RES", resolved="SELECT IL FROM TESTS_OTDR_RES WHERE ID={id}")])

        rel_a = data_flow.resolve_app_relations(_app_id("AppA/AppA"))
        ev = rel_a.produces_to[0].evidence[0]

        assert set(ev.write_ops[0].columns) == {"IL", "ReflA"}
        assert ev.read_ops[0].columns == ("IL",)


class TestNoRelationsAndMultipleNeighbors:
    def test_app_with_no_relations(self, temp_db):
        _save("Aislada/Aislada", [_sql_finding("TablaSolo", resolved="SELECT COMENTARIO FROM TablaSolo WHERE ID={id}")])
        rel = data_flow.resolve_app_relations(_app_id("Aislada/Aislada"))

        assert rel.produces_to == ()
        assert rel.consumes_from == ()
        assert rel.self_loops == ()
        # comentario/generic -> no known columns matched? Aqui se reconstruye
        # una columna real (COMENTARIO), asi que shared podria tener 0 pares
        # (no hay otra app en la tabla) -- confirma "sin relaciones" real.
        assert rel.shared == ()

    def test_app_with_no_sql_findings_has_no_relations(self, temp_db):
        _save("SinBD/SinBD", [])
        rel = data_flow.resolve_app_relations(_app_id("SinBD/SinBD"))

        assert rel.produces_to == () and rel.consumes_from == () and rel.shared == () and rel.self_loops == ()

    def test_app_with_multiple_neighbors(self, temp_db):
        _save("Productora/Productora", [_sql_finding("TESTS_OTDR_RES", resolved="Insert into TESTS_OTDR_RES (IL) values ({a})")])
        _save("Consumidor1/Consumidor1", [_sql_finding("TESTS_OTDR_RES", resolved="SELECT STATUS FROM TESTS_OTDR_RES WHERE ID={id}")])
        _save("Consumidor2/Consumidor2", [_sql_finding("TESTS_OTDR_RES", resolved="SELECT TEST_DATE FROM TESTS_OTDR_RES WHERE ID={id}")])

        rel = data_flow.resolve_app_relations(_app_id("Productora/Productora"))

        assert {r.other_app for r in rel.produces_to} == {"Consumidor1/Consumidor1", "Consumidor2/Consumidor2"}

    def test_nonexistent_app_returns_none(self, temp_db):
        assert data_flow.resolve_app_relations(999999) is None


class TestMermaidDiagramGeneration:
    def test_no_relations_returns_none(self):
        assert diagram.build_app_relations_diagram("SomeApp", (), (), ()) is None

    def test_diagram_includes_focal_app_and_produces_to_edge(self):
        rel = data_flow.AppRelation(
            other_app="DataTransfer",
            evidence=(data_flow.RelationEvidence(table="TESTS_OTDR_RES", write_ops=(), read_ops=()),),
        )
        text = diagram.build_app_relations_diagram("OTDR/OTDR", (rel,), (), ())

        assert "flowchart LR" in text
        assert "OTDR/OTDR" in text
        assert "DataTransfer" in text
        assert "TESTS_OTDR_RES" in text

    def test_self_loop_rendered_without_extra_node(self):
        loop = data_flow.SelfLoop(table="TESTS_OTDR_RES", write_ops=(), read_ops=())
        text = diagram.build_app_relations_diagram("OTDR/OTDR", (), (), (loop,))

        assert text is not None
        assert "TESTS_OTDR_RES" in text

    def test_node_limit_truncates_diagram(self):
        many_relations = tuple(
            data_flow.AppRelation(
                other_app=f"Consumer{i}",
                evidence=(data_flow.RelationEvidence(table="SharedTable", write_ops=(), read_ops=()),),
            )
            for i in range(diagram.MAX_NODES + 10)
        )
        text = diagram.build_app_relations_diagram("Producer", many_relations, (), ())

        assert "truncado" in text
        # No debe haber mas nodos que el limite establecido.
        assert text.count("(\"Consumer") < diagram.MAX_NODES


class TestAppMapRoute:
    def test_no_app_selected_shows_the_search_form(self, client):
        resp = client.get("/app_map")
        assert resp.status_code == 200
        assert "Mapa de Aplicaciones" in resp.get_data(as_text=True)

    def test_selecting_a_producer_shows_its_relations(self, client):
        _save("OTDR/OTDR", [_sql_finding("TESTS_OTDR_RES", resolved="Insert into TESTS_OTDR_RES (IL) values ({a})")])
        _save("DataTransfer", [_sql_finding("TESTS_OTDR_RES", resolved="SELECT STATUS FROM TESTS_OTDR_RES WHERE SERIAL_NUMBER='{sn}'")])
        app_id = _app_id("OTDR/OTDR")

        resp = client.get(f"/app_map?app_id={app_id}")
        body = resp.get_data(as_text=True)

        assert resp.status_code == 200
        assert "DataTransfer" in body
        assert "TESTS_OTDR_RES" in body

    def test_nonexistent_app_id_shows_not_found_message(self, client):
        resp = client.get("/app_map?app_id=999999")
        assert resp.status_code == 200
        assert "no existe" in resp.get_data(as_text=True)

    def test_weak_relations_appear_only_in_the_secondary_panel(self, client):
        _save("AppA/AppA", [_sql_finding("TablaCatalogo", resolved="SELECT COMENTARIO FROM TablaCatalogo WHERE ID={id}")])
        _save("AppB/AppB", [_sql_finding("TablaCatalogo", resolved="SELECT COMENTARIO FROM TablaCatalogo WHERE ID={id}")])
        app_id = _app_id("AppA/AppA")

        resp = client.get(f"/app_map?app_id={app_id}")
        body = resp.get_data(as_text=True)

        assert "Sin relaciones fuertes" in body
        assert "Relaciones potenciales" in body
        assert "AppB/AppB" in body
