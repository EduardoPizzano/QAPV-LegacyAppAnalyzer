"""Incremento 4 (DISENO_INCREMENTO_4_SP_CLASSIFICACION.md): la evidencia de
`stored_procedure` debe atarse a la variable de comando concreta que tiene
`CommandType.StoredProcedure`, nunca a una ventana ciega de lineas.

El fixture sp_classification_case/Repository.cs reproduce, en un unico
metodo por escenario, cada patron real ya confirmado en el portafolio
(AFL_DataCenter, AFL.Dashboard, INVENTA2-2TEST/SGI): conexion abierta cerca
de un SP real, dos SqlCommand en el mismo metodo (uno SELECT, otro SP), SPs
cruzando SqlCommand/OracleCommand, un SP cuyo CommandType.StoredProcedure
queda mas lejos que la vieja ventana de 8 lineas, y un comando sin ninguna
evidencia de SP aunque otro comando del mismo metodo si sea un SP real."""

from analyzer.extract import scan_project


def _findings(fixture_root, method, kind=None):
    sql_findings, _ = scan_project(fixture_root("sp_classification_case"))
    rows = [f for f in sql_findings if f.method == method]
    if kind is not None:
        rows = [f for f in rows if f.kind == kind]
    return rows


def _only(rows, **kwargs):
    """Filtra por atributos exactos y exige exactamente 1 resultado -- para
    que un assert sobre datos inexistentes falle con un mensaje claro en vez
    de un IndexError opaco."""
    matches = [
        r for r in rows if all(getattr(r, k) == v for k, v in kwargs.items())
    ]
    assert len(matches) == 1, f"esperaba 1 finding con {kwargs}, encontre {len(matches)} en {rows}"
    return matches[0]


class TestConnectionNeverStoredProcedure:
    """Escenarios 1/C: SqlConnection/OracleConnection nunca son SP, aunque
    haya un SP real a pocas lineas."""

    def test_sql_connection_near_real_sp_is_not_stored_procedure(self, fixture_root):
        rows = _findings(fixture_root, "ConnectionNearRealSp", kind="SqlConnection")
        assert len(rows) == 1
        assert rows[0].category != "stored_procedure"
        assert rows[0].is_stored_procedure is False

    def test_real_sp_in_same_method_still_detected(self, fixture_root):
        row = _only(_findings(fixture_root, "ConnectionNearRealSp", kind="CommandText"), target="UpdateAlgo")
        assert row.category == "stored_procedure"
        assert row.is_stored_procedure is True

    def test_oracle_connection_near_real_sp_is_not_stored_procedure(self, fixture_root):
        rows = _findings(fixture_root, "CrossTechnologyNoContamination", kind="OracleConnection")
        assert len(rows) == 1
        assert rows[0].category != "stored_procedure"

    def test_no_finding_has_truncated_raw_text_as_target(self, fixture_root):
        """Regresion directa del fallback eliminado (`text.strip()[:60]`) --
        ningun target debe verse como codigo C# crudo."""
        sql_findings, _ = scan_project(fixture_root("sp_classification_case"))
        for f in sql_findings:
            if f.target:
                assert "SqlConnection" not in f.target
                assert "using " not in f.target
                assert ";" not in f.target


class TestTwoCommandsNoCrossContamination:
    """Escenario A (obligatorio): SqlCommand A = SELECT, SqlCommand B = SP,
    en el mismo metodo. Es la causa raiz exacta del bug original."""

    def test_select_command_is_not_stored_procedure(self, fixture_root):
        row = _only(_findings(fixture_root, "TwoCommandsOneIsSp", kind="CommandText"), target="Tabla")
        assert row.category == "query"
        assert row.is_stored_procedure is False

    def test_real_sp_command_is_stored_procedure(self, fixture_root):
        row = _only(_findings(fixture_root, "TwoCommandsOneIsSp", kind="CommandText"), target="UpdateOtraCosa")
        assert row.category == "stored_procedure"
        assert row.is_stored_procedure is True


class TestTwoDistinctStoredProceduresDoNotMix:
    """Escenario 5: dos SPs distintos cercanos -- cada uno resuelve su
    propio nombre, ninguno hereda el del otro."""

    def test_each_sp_resolves_its_own_name(self, fixture_root):
        rows = _findings(fixture_root, "TwoDistinctStoredProcedures", kind="CommandText")
        targets = {r.target for r in rows if r.category == "stored_procedure"}
        assert targets == {"SpAlpha", "SpBeta"}


class TestCrossTechnologyNoContamination:
    """Escenario B (obligatorio): OracleCommand (SELECT) + SqlCommand (SP)
    cercanos -- ninguno contamina al otro."""

    def test_oracle_select_is_query_not_stored_procedure(self, fixture_root):
        row = _only(
            _findings(fixture_root, "CrossTechnologyNoContamination", kind="CommandText"),
            target="VISTA_ORACLE",
        )
        assert row.category == "query"

    def test_sql_command_sp_is_stored_procedure(self, fixture_root):
        row = _only(
            _findings(fixture_root, "CrossTechnologyNoContamination", kind="CommandText"),
            target="UpdateDesdeOracle",
        )
        assert row.category == "stored_procedure"


class TestFarStoredProcedureSameVariable:
    """Escenario D (obligatorio): CommandType.StoredProcedure mas lejos que
    la vieja ventana ciega de 8 lineas, sobre la MISMA variable -- debe
    detectarse porque la evidencia esta atada a la variable, no a la
    distancia. El literal "Update" fuerza que la deteccion dependa
    exclusivamente de esa evidencia (ver comentario en el fixture)."""

    def test_far_stored_procedure_is_detected(self, fixture_root):
        row = _only(_findings(fixture_root, "FarStoredProcedureSameVariable", kind="CommandText"), target="Update")
        assert row.category == "stored_procedure"
        assert row.is_stored_procedure is True


class TestNoEvidenceNeverForcesStoredProcedure:
    """Escenario E (obligatorio): sin CommandType.StoredProcedure atado a
    ESTA variable, no se fuerza stored_procedure aunque otro comando del
    mismo metodo si sea un SP real."""

    def test_plain_command_without_own_storedprocedure_is_not_sp(self, fixture_root):
        rows = _findings(fixture_root, "NoEvidenceNoForcedStoredProcedure", kind="CommandText")
        plain = [r for r in rows if "Config" in (r.target or "")]
        assert len(plain) == 1
        assert plain[0].category == "query"
        assert plain[0].is_stored_procedure is False

    def test_real_sp_in_same_method_unaffected(self, fixture_root):
        row = _only(
            _findings(fixture_root, "NoEvidenceNoForcedStoredProcedure", kind="CommandText"),
            target="UpdateOtroMas",
        )
        assert row.category == "stored_procedure"


class TestAnonymousConnectionMergedStatementNeverStoredProcedure:
    """Descubierto durante la validacion de Incremento 4 contra AFL_DataCenter
    real (btnSO_Click): un `using (new SqlConnection(...))` anonimo sin ';'
    propia se junta, via _capture_statement, con la siguiente linea -- que en
    este caso construye texto con forma de nombre de SP valido (Camino A).
    La regla de kind absoluto (CONNECTION_KINDS en _classify_sql) debe cubrir
    esto tambien, no solo la evidencia atada a variable."""

    def test_anonymous_connection_is_never_stored_procedure(self, fixture_root):
        rows = _findings(fixture_root, "AnonymousConnectionMergedWithSpNameText", kind="SqlConnection")
        assert len(rows) == 1
        assert rows[0].category != "stored_procedure"
        assert rows[0].is_stored_procedure is False


class TestExistingNameBasedDetectionUnchanged:
    """Escenario 8: los caminos que ya detectaban SP por nombre limpio
    (constructor de 2 argumentos, literal concatenado con comilla) siguen
    funcionando exactamente igual, sin depender de CommandType cercano."""

    def test_sp_by_constructor_literal(self, fixture_root):
        rows = _findings(fixture_root, "RealStoredProcedureByConstructorName", kind="SqlCommand")
        sp_rows = [r for r in rows if r.category == "stored_procedure"]
        assert len(sp_rows) == 1
        assert sp_rows[0].target == "UpdatePorConstructor"

    def test_sp_by_concatenated_literal(self, fixture_root):
        rows = _findings(fixture_root, "RealStoredProcedureByConcatenatedLiteral", kind="CommandText")
        sp_rows = [r for r in rows if r.category == "stored_procedure"]
        assert len(sp_rows) == 1
        assert sp_rows[0].target == "UpdatePorConcat"
