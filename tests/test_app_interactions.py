"""Incremento Flujo de Aplicacion - C: Event Wiring + Intra-Class Call Flow
(2026-08-20): tests de analyzer/app_interactions.py. PRECISION > COBERTURA
-- estos tests verifican tanto los 18 casos obligatorios del incremento
como (igual de importante) los casos que NUNCA deben inventar una relacion
(control.Evento += aritmetica ordinaria, llamadas cross-class, llamadas a
BCL/DB APIs, duplicacion fisica de codigo). Reutiliza el patron de
fixtures de tests/test_app_navigation.py (decompiled/ sintetico +
monkeypatch de DECOMPILED_DIR/db.DB_PATH); solo TestRealPortfolioValidation
usa la BD y decompiled/ REALES del portafolio."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from analyzer import app_interactions, app_structure, db  # noqa: E402
from analyzer.techstack import TechStack  # noqa: E402


def _setup_app(tmp_path, monkeypatch, app_name, files, db_name, ui_framework=None):
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
        [], [], [], [],
    )
    return app_id


class TestEventWiring:
    """Casos obligatorios 1-6 del incremento."""

    def test_case1_wrapped_eventhandler_is_resolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void InitializeComponent()
    {
        this.btnBorrar.Click += new System.EventHandler(this.btnBorrar_Click);
    }

    private void btnBorrar_Click(object sender, EventArgs e)
    {
        int x = 1;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseW1", {"Form1.cs": content}, "w1.db")
        result = app_interactions.discover_interactions(app_id)

        assert len(result.event_wirings) == 1
        w = result.event_wirings[0]
        assert w.control_name == "btnBorrar"
        assert w.event_name == "Click"
        assert w.handler_method == "btnBorrar_Click"
        assert w.resolution_status == "resolved"
        assert w.evidence.extractor == "APP_EVENT_WIRING_EXPLICIT"

    def test_case2_direct_method_group_is_resolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void InitializeComponent()
    {
        this.btnGuardar.Click += this.btnGuardar_Click;
    }

    private void btnGuardar_Click(object sender, EventArgs e)
    {
        int x = 1;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseW2", {"Form1.cs": content}, "w2.db")
        result = app_interactions.discover_interactions(app_id)

        assert len(result.event_wirings) == 1
        w = result.event_wirings[0]
        assert w.control_name == "btnGuardar"
        assert w.handler_method == "btnGuardar_Click"
        assert w.resolution_status == "resolved"

    def test_case3_control_without_explicit_wiring_produces_no_edge(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private Button btnBorrar;

    private void btnBorrar_Click(object sender, EventArgs e)
    {
        int x = 1;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseW3", {"Form1.cs": content}, "w3.db")
        result = app_interactions.discover_interactions(app_id)

        assert result.event_wirings == ()

    def test_case4_matching_handler_name_without_wiring_produces_no_edge(self, tmp_path, monkeypatch):
        """No basta con que exista un control Y un metodo con nombre de
        handler -- sin el operador += observado, NO hay wiring."""
        content = """\
public class Form1 : Form
{
    private void Setup()
    {
        var texto = this.btnBorrar.Name;
    }

    private void btnBorrar_Click(object sender, EventArgs e)
    {
        int x = 1;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseW4", {"Form1.cs": content}, "w4.db")
        result = app_interactions.discover_interactions(app_id)

        assert result.event_wirings == ()

    def test_case5_wpf_window_preserves_inherited_unknown(self, tmp_path, monkeypatch):
        content = """\
public class MainWindow : Window
{
    public MainWindow() { }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseW5", {"MainWindow.cs": content}, "w5.db",
                             ui_framework=["WPF"])
        result = app_interactions.discover_interactions(app_id)

        assert result.event_wirings == ()
        assert any(u.reason_code == "wpf_event_wiring_not_observable_in_cs" for u in result.unknowns)

    def test_case6_messagebox_show_is_never_event_wiring(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void btnGuardar_Click(object sender, EventArgs e)
    {
        MessageBox.Show("Operacion completada");
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseW6", {"Form1.cs": content}, "w6.db")
        result = app_interactions.discover_interactions(app_id)

        assert result.event_wirings == ()

    def test_property_style_increment_is_never_treated_as_wiring(self, tmp_path, monkeypatch):
        """Guarda de precision critica: "control.Algo += identificador;" es
        SINTACTICAMENTE IDENTICO a wiring directo -- solo se acepta como
        wiring si el identificador es un metodo REAL de la clase. Un
        incremento de propiedad/numerico ordinario NUNCA debe producir una
        relacion inventada."""
        content = """\
public class Form1 : Form
{
    private void Acumular()
    {
        int cantidad = 5;
        this.total.Monto += cantidad;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseWArith", {"Form1.cs": content}, "warith.db")
        result = app_interactions.discover_interactions(app_id)

        assert result.event_wirings == ()

    def test_lambda_rhs_produces_unresolved_handler_unknown(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void InitializeComponent()
    {
        this.btnBorrar.Click += (sender, e) => DoSomething();
    }

    private void DoSomething()
    {
        int x = 1;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseWLambda", {"Form1.cs": content}, "wlambda.db")
        result = app_interactions.discover_interactions(app_id)

        assert len(result.event_wirings) == 1
        w = result.event_wirings[0]
        assert w.control_name == "btnBorrar"
        assert w.event_name == "Click"
        assert w.handler_method is None
        assert w.resolution_status == "unresolved_event_handler_unknown"


class TestIntraClassCallFlow:
    """Casos obligatorios 7-18 del incremento."""

    def test_case7_simple_call_is_resolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void Process()
    {
        Save();
    }

    private void Save()
    {
        int x = 1;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseC7", {"Form1.cs": content}, "c7.db")
        result = app_interactions.discover_interactions(app_id)

        edges = [e for e in result.call_edges if e.source_method == "Process"]
        assert len(edges) == 1
        assert edges[0].target_method == "Save"
        assert edges[0].resolution_status == "resolved"
        assert edges[0].evidence.extractor == "APP_CALL_INTRA_CLASS"

    def test_case8_this_prefixed_call_is_resolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void Process()
    {
        this.Save();
    }

    private void Save()
    {
        int x = 1;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseC8", {"Form1.cs": content}, "c8.db")
        result = app_interactions.discover_interactions(app_id)

        edges = [e for e in result.call_edges if e.source_method == "Process"]
        assert len(edges) == 1
        assert edges[0].target_method == "Save"
        assert edges[0].resolution_status == "resolved"

    def test_case9_call_with_arguments_is_resolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void btnBorrar_Click(object sender, EventArgs e)
    {
        int id = 5;
        Baja(id);
    }

    private void Baja(int datosIndex)
    {
        int x = 1;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseC9", {"Form1.cs": content}, "c9.db")
        result = app_interactions.discover_interactions(app_id)

        edges = [e for e in result.call_edges if e.source_method == "btnBorrar_Click"]
        assert len(edges) == 1
        assert edges[0].target_method == "Baja"
        assert edges[0].resolution_status == "resolved"

    def test_case10_multiple_calls_from_one_method_produce_multiple_edges(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void Process()
    {
        Validate();
        Save();
        Log();
    }

    private void Validate() { int a = 1; }
    private void Save() { int b = 2; }
    private void Log() { int c = 3; }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseC10", {"Form1.cs": content}, "c10.db")
        result = app_interactions.discover_interactions(app_id)

        edges = [e for e in result.call_edges if e.source_method == "Process"]
        targets = {e.target_method for e in edges}
        assert targets == {"Validate", "Save", "Log"}
        assert all(e.resolution_status == "resolved" for e in edges)

    def test_case11_recursion_produces_a_valid_self_loop_edge(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void Process()
    {
        Process();
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseC11", {"Form1.cs": content}, "c11.db")
        result = app_interactions.discover_interactions(app_id)

        edges = [e for e in result.call_edges if e.source_method == "Process"]
        assert len(edges) == 1
        assert edges[0].target_method == "Process"
        assert edges[0].resolution_status == "resolved"

    def test_case12_nonexistent_method_is_unresolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void Process()
    {
        DoesNotExist();
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseC12", {"Form1.cs": content}, "c12.db")
        result = app_interactions.discover_interactions(app_id)

        edges = [e for e in result.call_edges if e.source_method == "Process"]
        assert len(edges) == 1
        assert edges[0].target_method is None
        assert edges[0].resolution_status == "unresolved_call_target_unknown"

    def test_case13_qualified_call_to_another_class_is_never_intra_class(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void Process()
    {
        Helper.Save();
    }
}

public class Helper
{
    public static void Save() { int x = 1; }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseC13", {"Form1.cs": content}, "c13.db")
        result = app_interactions.discover_interactions(app_id)

        edges = [e for e in result.call_edges if e.source_method == "Process"]
        assert edges == []

    def test_case14_inherited_method_is_marked_inherited_not_resolved(self, tmp_path, monkeypatch):
        content = """\
public class BaseForm : Form
{
    protected void Save()
    {
        int x = 1;
    }
}

public class ChildForm : BaseForm
{
    private void Process()
    {
        Save();
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseC14", {"Forms.cs": content}, "c14.db")
        result = app_interactions.discover_interactions(app_id)

        edges = [e for e in result.call_edges if e.source_class == "ChildForm" and e.source_method == "Process"]
        assert len(edges) == 1
        assert edges[0].target_method == "Save"
        assert edges[0].resolution_status == "unresolved_call_target_inherited"

    def test_case15_ambiguous_overload_is_never_resolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void Process()
    {
        int id = 1;
        Save(id);
    }

    private void Save()
    {
        int x = 1;
    }

    private void Save(int id)
    {
        int y = 2;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseC15", {"Form1.cs": content}, "c15.db")
        result = app_interactions.discover_interactions(app_id)

        edges = [e for e in result.call_edges if e.source_method == "Process"]
        assert len(edges) == 1
        assert edges[0].target_method == "Save"
        assert edges[0].resolution_status == "unresolved_call_target_ambiguous"

    def test_case16_homonymous_methods_in_two_classes_resolve_against_the_correct_class(self, tmp_path, monkeypatch):
        content = """\
public class ClaseA : Form
{
    private void Process()
    {
        Save();
    }

    private void Save()
    {
        int a = 1;
    }
}

public class ClaseB : Form
{
    private void Process()
    {
        Save();
    }

    private void Save()
    {
        int b = 2;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseC16", {"Ambas.cs": content}, "c16.db")
        result = app_interactions.discover_interactions(app_id)

        by_class = {e.source_class: e for e in result.call_edges if e.source_method == "Process"}
        assert by_class["ClaseA"].resolution_status == "resolved"
        assert by_class["ClaseA"].target_method == "Save"
        assert by_class["ClaseB"].resolution_status == "resolved"
        assert by_class["ClaseB"].target_method == "Save"

    def test_case17_console_messagebox_and_db_api_calls_produce_no_edge(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void Process()
    {
        Console.WriteLine("hola");
        MessageBox.Show("hola");
        connection.Open();
        reader.Read();
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseC17", {"Form1.cs": content}, "c17.db")
        result = app_interactions.discover_interactions(app_id)

        edges = [e for e in result.call_edges if e.source_method == "Process"]
        assert edges == []

    def test_case18_physically_duplicated_files_do_not_trigger_false_ambiguity(self, tmp_path, monkeypatch):
        """Incremento B descubrio que el mismo codigo puede existir
        fisicamente duplicado en dos carpetas (ej. Release/ y
        app.publish/) -- el conteo de metodos para desambiguar sobrecargas
        se calcula POR ARCHIVO precisamente para que esta duplicacion NUNCA
        se confunda con sobrecarga real."""
        root = tmp_path / "decompiled"
        dir1 = root / "DupApp" / "Release"
        dir2 = root / "DupApp" / "app.publish"
        dir1.mkdir(parents=True)
        dir2.mkdir(parents=True)
        content = """\
public class Form1 : Form
{
    private void Process()
    {
        Save();
    }

    private void Save()
    {
        int x = 1;
    }
}
"""
        (dir1 / "Form1.cs").write_text(content, encoding="utf-8")
        (dir2 / "Form1.cs").write_text(content, encoding="utf-8")
        monkeypatch.setattr(app_structure, "DECOMPILED_DIR", root)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "c18.db")
        db.init_db()
        app_id = db.save_analysis(
            "DupApp", r"\\server\DupApp.exe",
            TechStack(dotnet_target="net472", ui_framework=["WinForms"], db_drivers=[]),
            [], [], [], [],
        )

        result = app_interactions.discover_interactions(app_id)

        edges = [e for e in result.call_edges if e.source_method == "Process"]
        assert len(edges) == 2  # uno por archivo fisicamente duplicado
        assert all(e.resolution_status == "resolved" for e in edges)
        assert all(e.target_method == "Save" for e in edges)


class TestThirdPartyAndMissingApp:
    def test_third_party_interactions_are_excluded(self, tmp_path, monkeypatch):
        root = tmp_path / "decompiled"
        app_dir = root / "MyApp" / "MyApp"
        app_dir.mkdir(parents=True)
        (app_dir / "Form1.cs").write_text(
            "public class Form1 : Form\n{\n    private void Process()\n    {\n"
            "        Save();\n    }\n\n    private void Save() { int x = 1; }\n}\n",
            encoding="utf-8",
        )
        third_party_dir = root / "MyApp" / "Newtonsoft.Json"
        third_party_dir.mkdir(parents=True)
        (third_party_dir / "JsonConvert.cs").write_text(
            "public class JsonConvert : Form\n{\n    private void Process()\n    {\n"
            "        Save();\n    }\n\n    private void Save() { int x = 1; }\n}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(app_structure, "DECOMPILED_DIR", root)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "thirdparty.db")
        db.init_db()
        app_id = db.save_analysis(
            "MyApp", r"\\server\MyApp.exe",
            TechStack(dotnet_target="net472", ui_framework=["WinForms"], db_drivers=[]),
            [], [], [], [],
        )

        result = app_interactions.discover_interactions(app_id)

        source_classes = {e.source_class for e in result.call_edges}
        assert "Form1" in source_classes
        assert "JsonConvert" not in source_classes

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

        result = app_interactions.discover_interactions(app_id)

        assert result.event_wirings == ()
        assert result.call_edges == ()
        assert len(result.unknowns) == 1
        assert result.unknowns[0].reason_code == "unresolved_no_source_file"

    def test_nonexistent_app_id_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "none.db")
        db.init_db()

        assert app_interactions.discover_interactions(999999) is None


class TestRealPortfolioValidation:
    """Validacion contra las 5 apps reales del diagnostico -- contra la BD
    y decompiled/ REALES del portafolio. Atencion especial a RefControl,
    donde ya existe evidencia real confirmada de
    btnBorrar_Click -> Baja() -> GuardaLog()."""

    def _app_id(self, exact_name):
        for row in db.list_apps():
            if row["name"] == exact_name:
                return row["id"]
        pytest.skip(f"App real '{exact_name}' no encontrada en la BD del portafolio")

    def test_refcontrol_shows_btnborrar_click_calling_baja_and_guardalog(self):
        app_id = self._app_id("RefControl/RefControl")
        result = app_interactions.discover_interactions(app_id)

        assert result is not None
        click_edges = [e for e in result.call_edges if e.source_method == "btnBorrar_Click"]
        targets = {e.target_method for e in click_edges if e.resolution_status == "resolved"}
        assert {"Baja", "GuardaLog"} <= targets

    def test_epoxylabel_simple_app_produces_result_without_crashing(self):
        app_id = self._app_id("EpoxyLabel/EpoxyLabel")
        result = app_interactions.discover_interactions(app_id)

        assert result is not None

    def test_testvalidation_wpf_app_preserves_wpf_unknown(self):
        app_id = self._app_id("TestValidation/TestValidation")
        result = app_interactions.discover_interactions(app_id)

        assert result is not None
        assert any(u.reason_code == "wpf_event_wiring_not_observable_in_cs" for u in result.unknowns)

    def test_afl_datacenter_sql_heavy_app_produces_result_without_crashing(self):
        app_id = self._app_id("AFL_DataCenter")
        result = app_interactions.discover_interactions(app_id)

        assert result is not None

    def test_geometria_datatransfer_app_produces_result_without_crashing(self):
        app_id = self._app_id("Geometria/Release")
        result = app_interactions.discover_interactions(app_id)

        assert result is not None
