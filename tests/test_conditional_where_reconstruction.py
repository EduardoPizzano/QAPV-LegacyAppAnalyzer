"""Gap encontrado durante la revision de logica de negocio de QAPV2 (app id
434, 2026-09): _reconstruct_dynamic_sql() abandonaba la reconstruccion
COMPLETA de una query en cuanto encontraba CUALQUIER linea if/for/while/
switch entre la primera asignacion de la variable y su uso en CommandText --
incluso cuando esa condicion solo protegia un WHERE opcional agregado
DESPUES de un SELECT base ya conocido e incondicional (`text2 = "select ...
from Employees"; if (...) { text2 = text2 + " where ..."; }`).

Alcance explicito, igual que el resto del Incremento 3A (ver
test_increment3a_sql_reconstruction.py): sigue sin haber ejecucion
simbolica. Un segmento que SOLO se agrega bajo una condicion se marca como
dinamico/incierto (mismo mecanismo `{...}` ya usado para una variable C#
sin resolver) en vez de fabricarse como si siempre aplicara -- nunca se
inventa si el WHERE realmente se agrego o no. Lo que cambia es que el
SELECT base, que SI corre siempre, deja de perderse por completo."""

from analyzer.confidence import CONFIDENCE_TABLE
from analyzer.extract import scan_project
from analyzer.report import _group_by_method, _rows_for_method


class TestConditionallyAppendedWhereClauseResolvesItsUnconditionalBase:
    def test_unconditional_base_select_is_recovered(self, fixture_root):
        sql_findings, _ = scan_project(fixture_root("conditional_where_case"))
        command_text_findings = [f for f in sql_findings if f.kind == "CommandText"]
        assert command_text_findings, "el trigger CommandText = text2; debe seguir disparando un SqlFinding"
        f = command_text_findings[0]
        assert f.resolved is not None, "el SELECT base incondicional ya no debe quedar sin resolver"
        assert f.resolved.startswith(
            "select ID, EMPLOYEE_NUMBER as Clave, FULL_NAME as Nombre from Employees"
        )

    def test_conditionally_appended_where_is_marked_dynamic_never_fabricated(self, fixture_root):
        """El WHERE solo se agrega si employeeNumber no esta vacio -- nunca se
        sabe en tiempo de analisis si esa rama corrio, asi que ese segmento
        debe quedar marcado como incierto ({...}), nunca concatenado como si
        siempre aplicara."""
        sql_findings, _ = scan_project(fixture_root("conditional_where_case"))
        f = next(x for x in sql_findings if x.kind == "CommandText")
        assert "{" in f.resolved and "}" in f.resolved, "el fragmento condicional debe quedar entre llaves, no fabricado"
        assert "EMPLOYEE_NUMBER=" not in f.resolved.split("{")[0], (
            "el WHERE no debe aparecer concatenado directo al SELECT base fuera de las llaves"
        )

    def test_evidence_is_partial_reconstruction_not_hardcoded(self, fixture_root):
        """Queda un segmento dinamico real (el WHERE condicional) -- la
        confidence correcta es PARTIAL_RECONSTRUCTION (80), nunca
        HARDCODED_METHOD_LITERAL (90, reservado para 100% literal conocido)."""
        sql_findings, _ = scan_project(fixture_root("conditional_where_case"))
        f = next(x for x in sql_findings if x.kind == "CommandText")
        assert f.evidence.extractor == "PARTIAL_RECONSTRUCTION"
        assert f.evidence.confidence == CONFIDENCE_TABLE["PARTIAL_RECONSTRUCTION"] == 80
        assert f.evidence.pattern == "STRING_VAR_ASSIGN"

    def test_report_row_shows_real_sql_not_generic_placeholder(self, fixture_root):
        """Regresion end-to-end del sintoma real visto en el reporte de
        QAPV2: la fila de txtOperador_Validating mostraba el mensaje
        generico de "no resuelta" en vez del SELECT real."""
        sql_findings, _ = scan_project(fixture_root("conditional_where_case"))
        groups = _group_by_method(sql_findings)
        group = groups[("Form1", "txtOperador_Validating")]
        rows = list(_rows_for_method(group))
        resolved_rows = [r for r in rows if "select ID" in r[0]]
        assert resolved_rows, f"ninguna fila muestra el SELECT real, rows={rows}"


class TestUnrelatedBranchingStillNeverResolves:
    """No debe aflojarse la proteccion contra ejecucion simbolica para los
    casos que YA estaban correctamente sin resolver antes de este cambio --
    corre el mismo fixture de test_increment3a_sql_reconstruction.py como
    guardia de regresion directa en este archivo."""

    def test_ternary_still_stays_unresolved(self, fixture_root):
        sql_findings, _ = scan_project(fixture_root("ternary_branch_case"))
        command_findings = [f for f in sql_findings if f.kind == "SqlCommand"]
        assert command_findings
        for f in command_findings:
            assert f.resolved is None
            assert f.evidence.extractor == "UNKNOWN"
