"""Incremento Flujo de Aplicacion - D: Application Flow + Data Flow
Integration (2026-08-20): tests de analyzer/app_data_flow.py. Este modulo
es una CAPA DE COMPOSICION -- estos tests verifican que conecta
correctamente MethodInfo (Incremento A) + CallEdge (Incremento C) +
DataFlowEdge (analyzer.data_flow, ya existente) SIN reimplementar ninguna
de las tres, y que nunca inventa una relacion Method->SQL ni propaga mas de
UN salto de Call Flow. Reutiliza el patron de fixtures de
tests/test_app_interactions.py (decompiled/ sintetico + monkeypatch de
DECOMPILED_DIR/db.DB_PATH); solo TestRealPortfolioValidation usa la BD y
decompiled/ REALES del portafolio."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from analyzer import app_data_flow, app_structure, data_flow, db  # noqa: E402
from analyzer.evidence import Evidence  # noqa: E402
from analyzer.extract import SqlFinding  # noqa: E402
from analyzer.techstack import TechStack  # noqa: E402


def _setup_app(tmp_path, monkeypatch, app_name, files, sql_findings, db_name, ui_framework=None):
    root = tmp_path / "decompiled"
    app_dir = root / app_name / app_name
    app_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        (app_dir / filename).write_text(content, encoding="utf-8")
    monkeypatch.setattr(app_structure, "DECOMPILED_DIR", root)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / db_name)
    db.init_db()
    app_id = db.save_analysis(
        app_name, rf"\\server\{app_name}.exe",
        TechStack(dotnet_target="net472", ui_framework=ui_framework or [], db_drivers=[]),
        [], sql_findings, [], [],
    )
    return app_id


def _finding(class_name, method, target, raw, resolved=None, category="query",
             is_stored_procedure=False, result_columns=None, file="Form1.cs",
             line_number=1, snippet=None):
    return SqlFinding(
        file=file, class_name=class_name, method=method, kind="CommandText",
        raw=raw, resolved=resolved, category=category, target=target,
        is_stored_procedure=is_stored_procedure, result_columns=result_columns or [],
        evidence=Evidence(line_number=line_number, snippet=snippet or raw),
    )


# Todos los tests usan un unico archivo "Form1.cs" -- extract.py (SqlFinding)
# y app_structure.py (MethodInfo) resuelven "file" relativo a raices
# DISTINTAS (ver investigacion previa: output_dir=DECOMPILED_DIR/app_name
# completo vs resolve_decompiled_root=DECOMPILED_DIR/primer segmento). Para
# que _paths_match() los reconcilie sin ambiguedad en estos fixtures
# sinteticos (que no usan un app_name con "/"), basta con que ambos usen el
# MISMO nombre de archivo relativo simple.


class TestMethodSqlMapping:
    def test_case1_method_with_select_produces_consumer_role(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void GetEmployee()
    {
        int x = 1;
    }
}
"""
        finding = _finding("Form1", "GetEmployee", "Employees",
                            raw='string sql = "SELECT Name, Age FROM Employees";',
                            resolved="SELECT Name, Age FROM Employees",
                            result_columns=["Name", "Age"], line_number=42)
        app_id = _setup_app(tmp_path, monkeypatch, "CaseD1", {"Form1.cs": content}, [finding], "d1.db")
        result = app_data_flow.discover_application_data_flow(app_id)

        ops = [o for o in result.operations if o.method_name == "GetEmployee"]
        assert len(ops) == 1
        op = ops[0]
        assert op.class_name == "Form1"
        assert op.operation == "select"
        assert op.table == "Employees"
        assert op.data_flow_role.startswith("consumidor")
        assert op.access_kind == "direct"
        assert op.resolution_status == "resolved"
        assert op.evidence.line_number == 42

    def test_case2_method_with_insert_produces_producer_role(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void SaveEmployee()
    {
        int x = 1;
    }
}
"""
        finding = _finding("Form1", "SaveEmployee", "Employees",
                            raw="INSERT INTO Employees (Name, Age) VALUES (@Name, @Age)",
                            resolved="INSERT INTO Employees (Name, Age) VALUES (@Name, @Age)")
        app_id = _setup_app(tmp_path, monkeypatch, "CaseD2", {"Form1.cs": content}, [finding], "d2.db")
        result = app_data_flow.discover_application_data_flow(app_id)

        ops = [o for o in result.operations if o.method_name == "SaveEmployee"]
        assert len(ops) == 1
        assert ops[0].operation == "insert"
        assert ops[0].data_flow_role.startswith("productor")

    def test_case3_method_with_multiple_operations_conserves_all(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void Process()
    {
        int x = 1;
    }
}
"""
        findings = [
            _finding("Form1", "Process", "Employees", raw="SELECT Name FROM Employees", resolved="SELECT Name FROM Employees", result_columns=["Name"]),
            _finding("Form1", "Process", "Orders", raw="UPDATE Orders SET Status = @Status", resolved="UPDATE Orders SET Status = @Status"),
            _finding("Form1", "Process", "AuditLog", raw="INSERT INTO AuditLog (Msg) VALUES (@Msg)", resolved="INSERT INTO AuditLog (Msg) VALUES (@Msg)"),
        ]
        app_id = _setup_app(tmp_path, monkeypatch, "CaseD3", {"Form1.cs": content}, findings, "d3.db")
        result = app_data_flow.discover_application_data_flow(app_id)

        ops = [o for o in result.operations if o.method_name == "Process"]
        assert len(ops) == 3
        by_table = {o.table: o.operation for o in ops}
        assert by_table == {"Employees": "select", "Orders": "update", "AuditLog": "insert"}

    def test_case4_method_without_sql_produces_zero_edges(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void BtnClick(object sender, EventArgs e)
    {
        int x = 1;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseD4", {"Form1.cs": content}, [], "d4.db")
        result = app_data_flow.discover_application_data_flow(app_id)

        assert [o for o in result.operations if o.method_name == "BtnClick"] == []

    def test_case5_sql_finding_without_matching_method_is_unresolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void RealMethod()
    {
        int x = 1;
    }
}
"""
        finding = _finding("Form1", "NonExistentMethod", "Employees",
                            raw="SELECT Name FROM Employees", resolved="SELECT Name FROM Employees",
                            result_columns=["Name"])
        app_id = _setup_app(tmp_path, monkeypatch, "CaseD5", {"Form1.cs": content}, [finding], "d5.db")
        result = app_data_flow.discover_application_data_flow(app_id)

        ops = [o for o in result.operations if o.table == "Employees"]
        assert len(ops) == 1
        assert ops[0].resolution_status == "unresolved_method_sql_mapping"
        assert ops[0].method_name == "NonExistentMethod"  # etiqueta cruda conservada, nunca inventada

    def test_case9_mixed_role_is_conserved_not_split(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void SyncEmployee()
    {
        int x = 1;
    }
}
"""
        findings = [
            _finding("Form1", "SyncEmployee", "Employees", raw="SELECT Name FROM Employees", resolved="SELECT Name FROM Employees", result_columns=["Name"]),
            _finding("Form1", "SyncEmployee", "Employees", raw="UPDATE Employees SET Name = @Name", resolved="UPDATE Employees SET Name = @Name"),
        ]
        app_id = _setup_app(tmp_path, monkeypatch, "CaseD9", {"Form1.cs": content}, findings, "d9.db")
        result = app_data_flow.discover_application_data_flow(app_id)

        ops = [o for o in result.operations if o.method_name == "SyncEmployee"]
        assert len(ops) == 2
        assert all(o.data_flow_role == "mixto" for o in ops)

    def test_case10_multiple_tables_are_all_conserved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void SyncAll()
    {
        int x = 1;
    }
}
"""
        findings = [
            _finding("Form1", "SyncAll", "Employees", raw="SELECT Name FROM Employees", resolved="SELECT Name FROM Employees", result_columns=["Name"]),
            _finding("Form1", "SyncAll", "Orders", raw="SELECT Total FROM Orders", resolved="SELECT Total FROM Orders", result_columns=["Total"]),
            _finding("Form1", "SyncAll", "AuditLog", raw="SELECT Msg FROM AuditLog", resolved="SELECT Msg FROM AuditLog", result_columns=["Msg"]),
        ]
        app_id = _setup_app(tmp_path, monkeypatch, "CaseD10", {"Form1.cs": content}, findings, "d10.db")
        result = app_data_flow.discover_application_data_flow(app_id)

        tables = {o.table for o in result.operations if o.method_name == "SyncAll"}
        assert tables == {"Employees", "Orders", "AuditLog"}

    def test_case11_ambiguous_overload_never_crosses_evidence(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void Process()
    {
        int x = 1;
    }

    private void Process(string mode)
    {
        int y = 2;
    }
}
"""
        finding = _finding("Form1", "Process", "Employees",
                            raw="SELECT Name FROM Employees", resolved="SELECT Name FROM Employees",
                            result_columns=["Name"])
        app_id = _setup_app(tmp_path, monkeypatch, "CaseD11", {"Form1.cs": content}, [finding], "d11.db")
        result = app_data_flow.discover_application_data_flow(app_id)

        ops = [o for o in result.operations if o.table == "Employees"]
        assert len(ops) == 1
        assert ops[0].resolution_status == "unresolved_method_sql_mapping"

    def test_case12_table_casing_uses_normalize_table_key(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void ReadBoth()
    {
        int x = 1;
    }
}
"""
        findings = [
            _finding("Form1", "ReadBoth", "Employees", raw="SELECT Name FROM Employees", resolved="SELECT Name FROM Employees", result_columns=["Name"]),
            _finding("Form1", "ReadBoth", "EMPLOYEES", raw="SELECT Name FROM EMPLOYEES", resolved="SELECT Name FROM EMPLOYEES", result_columns=["Name"]),
        ]
        app_id = _setup_app(tmp_path, monkeypatch, "CaseD12", {"Form1.cs": content}, findings, "d12.db")
        result = app_data_flow.discover_application_data_flow(app_id)

        ops = [o for o in result.operations if o.method_name == "ReadBoth"]
        assert len(ops) == 2
        # normalize_table_key() colapsa "Employees"/"EMPLOYEES" a la MISMA
        # clave -- ambos deben resolver al mismo rol (nunca dos criterios
        # de normalizacion distintos).
        assert {data_flow.normalize_table_key(o.table) for o in ops} == {"EMPLOYEES"}
        assert len({o.data_flow_role for o in ops}) == 1


class TestCallFlowPropagation:
    def test_case6_direct_call_produces_direct_and_indirect_labeled_correctly(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void btn_Click(object sender, EventArgs e)
    {
        Save();
    }

    private void Save()
    {
        int x = 1;
    }
}
"""
        finding = _finding("Form1", "Save", "Employees",
                            raw="INSERT INTO Employees (Name) VALUES (@Name)",
                            resolved="INSERT INTO Employees (Name) VALUES (@Name)")
        app_id = _setup_app(tmp_path, monkeypatch, "CaseD6", {"Form1.cs": content}, [finding], "d6.db")
        result = app_data_flow.discover_application_data_flow(app_id)

        direct = [o for o in result.operations if o.method_name == "Save" and o.access_kind == "direct"]
        indirect = [o for o in result.operations if o.method_name == "btn_Click" and o.access_kind == "indirect"]
        assert len(direct) == 1
        assert direct[0].operation == "insert"
        assert direct[0].via_method is None
        assert len(indirect) == 1
        assert indirect[0].operation == "insert"
        assert indirect[0].table == "Employees"
        assert indirect[0].via_method == "Save"
        assert indirect[0].class_name == "Form1"

    def test_case7_maximum_one_hop_does_not_propagate_transitively(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void A()
    {
        B();
    }

    private void B()
    {
        C();
    }

    private void C()
    {
        int x = 1;
    }
}
"""
        finding = _finding("Form1", "C", "Employees",
                            raw="INSERT INTO Employees (Name) VALUES (@Name)",
                            resolved="INSERT INTO Employees (Name) VALUES (@Name)")
        app_id = _setup_app(tmp_path, monkeypatch, "CaseD7", {"Form1.cs": content}, [finding], "d7.db")
        result = app_data_flow.discover_application_data_flow(app_id)

        method_names = {o.method_name for o in result.operations}
        assert "C" in method_names   # directo
        assert "B" in method_names   # indirecto, 1 salto (B -> C)
        assert "A" not in method_names  # NUNCA transitivo (A -> B -> C -> SQL)

        b_ops = [o for o in result.operations if o.method_name == "B"]
        assert all(o.access_kind == "indirect" and o.via_method == "C" for o in b_ops)

    def test_case8_cycle_does_not_loop(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void A()
    {
        B();
        int x = 1;
    }

    private void B()
    {
        A();
    }
}
"""
        finding = _finding("Form1", "A", "Employees",
                            raw="INSERT INTO Employees (Name) VALUES (@Name)",
                            resolved="INSERT INTO Employees (Name) VALUES (@Name)")
        app_id = _setup_app(tmp_path, monkeypatch, "CaseD8", {"Form1.cs": content}, [finding], "d8.db")

        result = app_data_flow.discover_application_data_flow(app_id)  # no debe colgarse ni lanzar RecursionError

        by_method = {(o.method_name, o.access_kind) for o in result.operations}
        assert ("A", "direct") in by_method
        assert ("B", "indirect") in by_method
        # Nunca aparece un tercer salto de vuelta a A via B (eso implicaria
        # una travesia recursiva del ciclo, no una composicion de 1 salto).
        assert len(result.operations) == 2


class TestThirdPartyAndMissingApp:
    def test_missing_decompiled_folder_produces_empty_result_and_inherits_unknown(self, tmp_path, monkeypatch):
        root = tmp_path / "decompiled_vacio"
        monkeypatch.setattr(app_structure, "DECOMPILED_DIR", root)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "missing.db")
        db.init_db()
        app_id = db.save_analysis(
            "AppSinCarpeta", r"\\server\AppSinCarpeta.exe",
            TechStack(dotnet_target="net472", ui_framework=[], db_drivers=[]),
            [], [], [], [],
        )

        result = app_data_flow.discover_application_data_flow(app_id)

        assert result.operations == ()
        assert len(result.unknowns) == 1
        assert result.unknowns[0].reason_code == "unresolved_no_source_file"

    def test_nonexistent_app_id_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "none.db")
        db.init_db()

        assert app_data_flow.discover_application_data_flow(999999) is None


class TestRealPortfolioValidation:
    """Validacion contra las 5 apps reales del diagnostico -- contra la BD
    y decompiled/ REALES del portafolio. Atencion especial a RefControl,
    donde ya existe evidencia real confirmada de
    btnBorrar_Click -> Baja() -> DELETE."""

    def _app_id(self, exact_name):
        for row in db.list_apps():
            if row["name"] == exact_name:
                return row["id"]
        pytest.skip(f"App real '{exact_name}' no encontrada en la BD del portafolio")

    def test_refcontrol_shows_btnborrar_click_indirect_delete_via_baja(self):
        app_id = self._app_id("RefControl/RefControl")
        result = app_data_flow.discover_application_data_flow(app_id)

        assert result is not None
        baja_direct = [o for o in result.operations if o.method_name == "Baja" and o.access_kind == "direct"]
        assert any(o.operation == "delete" and o.table == "Referencias" for o in baja_direct)

        click_indirect = [o for o in result.operations if o.method_name == "btnBorrar_Click" and o.access_kind == "indirect"]
        assert any(o.operation == "delete" and o.table == "Referencias" and o.via_method == "Baja" for o in click_indirect)

    def test_afl_datacenter_still_benefits_from_incremento_a_truncation_fix(self):
        app_id = self._app_id("AFL_DataCenter")
        result = app_data_flow.discover_application_data_flow(app_id)

        assert result is not None
        # InitializeComponent (linea 8379, mas alla del limite truncado de
        # 6000 lineas corregido en Incremento C) no deberia producir SQL,
        # pero la correccion debe seguir permitiendo que OTROS metodos mas
        # alla de esa linea (si tienen SQL real) se mapeen correctamente --
        # se confirma indirectamente por la ausencia de crash y por que
        # existan operaciones resueltas en la clase DataCenter.
        assert any(o.class_name == "DataCenter" and o.resolution_status == "resolved" for o in result.operations)

    def test_geometria_datatransfer_does_not_duplicate_relations_via_app_publish(self):
        """extract.py (SqlFinding) solo escanea Release/, NUNCA app.publish/
        (confirmado: output_dir=DECOMPILED_DIR/app_name completo, mientras
        que la duplicacion fisica de Incremento B vive en app_structure.py,
        que escanea un nivel de raiz mas arriba). La duplicacion de
        MethodInfo/method_intervals de Incremento B NUNCA debe multiplicar
        las operaciones de datos: debe existir correspondencia EXACTA 1:1
        entre sql_findings crudos con evidencia informativa (operacion o
        target) y operaciones directas producidas -- nunca 2 operaciones
        directas por el mismo SqlFinding real."""
        app_id = self._app_id("Geometria/Release")
        data = db.get_app(app_id)
        expected_direct_count = sum(
            1 for row in data["sql_findings"]
            if not (app_data_flow._resolve_operation(row) is None and not row.get("target"))
        )

        result = app_data_flow.discover_application_data_flow(app_id)

        assert result is not None
        direct_ops = [o for o in result.operations if o.access_kind == "direct"]
        assert len(direct_ops) == expected_direct_count

    def test_testvalidation_wpf_app_produces_result_without_crashing(self):
        app_id = self._app_id("TestValidation/TestValidation")
        result = app_data_flow.discover_application_data_flow(app_id)

        assert result is not None

    def test_epoxylabel_simple_app_produces_result_without_crashing(self):
        app_id = self._app_id("EpoxyLabel/EpoxyLabel")
        result = app_data_flow.discover_application_data_flow(app_id)

        assert result is not None
