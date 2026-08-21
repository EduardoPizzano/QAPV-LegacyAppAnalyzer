"""Incremento Flujo de Aplicacion - B: Navigation Discovery (2026-08-20):
tests de analyzer/app_navigation.py. PRECISION > COBERTURA -- estos tests
verifican tanto los casos que SI deben producir una navegacion confirmada
como (igual de importante) los casos que NUNCA deben inventar una relacion
sin respaldo real de codigo (codigo muerto, reflection, Close/Hide/Dispose/
Activate/Focus, target dinamico). Reutiliza el patron de fixtures de
tests/test_app_structure.py (decompiled/ sintetico + monkeypatch de
DECOMPILED_DIR/db.DB_PATH); solo TestRealPortfolioValidation usa la BD y
decompiled/ REALES del portafolio."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from analyzer import app_navigation, app_structure, db  # noqa: E402
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


class TestSyntheticNavigationCases:
    """Los 9 casos sinteticos obligatorios del incremento, mas variantes
    explicitas de las reglas de exclusion."""

    def test_case1_instantiation_and_showdialog_same_method_is_resolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void btnBorrar_Click(object sender, EventArgs e)
    {
        var form = new FormDeleteReference();
        form.ShowDialog();
    }
}

public class FormDeleteReference : Form
{
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "Case1App", {"Form1.cs": content}, "case1.db")
        result = app_navigation.discover_navigation(app_id)

        edges = [e for e in result.edges if e.source_class == "Form1"]
        assert len(edges) == 1
        edge = edges[0]
        assert edge.source_method == "btnBorrar_Click"
        assert edge.target_class == "FormDeleteReference"
        assert edge.operation == "show_dialog"
        assert edge.resolution_status == "resolved"
        assert edge.evidence.extractor == "APP_NAVIGATION_INSTANTIATION_AND_SHOW"
        assert "ShowDialog" in edge.evidence.snippet

    def test_case2_instantiation_and_show_same_method_is_resolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void btnSearch_Click(object sender, EventArgs e)
    {
        var form = new FormSearch();
        form.Show();
    }
}

public class FormSearch : Form
{
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "Case2App", {"Form1.cs": content}, "case2.db")
        result = app_navigation.discover_navigation(app_id)

        edges = [e for e in result.edges if e.source_class == "Form1"]
        assert len(edges) == 1
        assert edges[0].operation == "show"
        assert edges[0].target_class == "FormSearch"
        assert edges[0].resolution_status == "resolved"

    def test_case3_inline_instantiation_and_showdialog_is_resolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void btnSearch_Click(object sender, EventArgs e)
    {
        new FormSearch().ShowDialog();
    }
}

public class FormSearch : Form
{
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "Case3App", {"Form1.cs": content}, "case3.db")
        result = app_navigation.discover_navigation(app_id)

        edges = [e for e in result.edges if e.source_class == "Form1"]
        assert len(edges) == 1
        assert edges[0].target_class == "FormSearch"
        assert edges[0].operation == "show_dialog"
        assert edges[0].resolution_status == "resolved"

    def test_case4_dead_code_instantiation_without_show_produces_no_navigation(self, tmp_path, monkeypatch):
        """Caso critico del incremento: instanciar sin jamas mostrar NO es
        navegacion -- grep de "new FormX" + grep de "ShowDialog" en el
        archivo NUNCA debe combinarse sin evidencia de que ocurren en el
        MISMO metodo."""
        content = """\
public class Form1 : Form
{
    private void Prepare()
    {
        var form = new FormDeleteReference();
    }
}

public class FormDeleteReference : Form
{
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "Case4App", {"Form1.cs": content}, "case4.db")
        result = app_navigation.discover_navigation(app_id)

        assert [e for e in result.edges if e.source_class == "Form1"] == []

    def test_case4b_instantiation_and_show_in_different_methods_produces_no_navigation(self, tmp_path, monkeypatch):
        """Variante explicita del caso 4: instanciacion en un metodo, Show
        en OTRO metodo distinto -- no se asume que pertenecen al mismo
        flujo salvo que la relacion pueda demostrarse con seguridad."""
        content = """\
public class Form1 : Form
{
    private FormSearch _pending;

    private void Prepare()
    {
        var localForm = new FormSearch();
    }

    private void Other()
    {
        _pending.Show();
    }
}

public class FormSearch : Form
{
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "Case4bApp", {"Form1.cs": content}, "case4b.db")
        result = app_navigation.discover_navigation(app_id)

        edges = [e for e in result.edges if e.source_class == "Form1"]
        # El unico Show() observado esta en Other(), sobre una variable
        # (_pending) nunca instanciada dentro de ESE metodo -- target
        # desconocido, nunca "resolved" por proximidad entre metodos.
        assert len(edges) == 1
        assert edges[0].source_method == "Other"
        assert edges[0].resolution_status == "unresolved_navigation_target_unknown"
        assert edges[0].target_class is None

    def test_case5_dynamic_target_remains_indeterminate(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void OpenDynamic()
    {
        var form = CreateFormDynamically();
        form.ShowDialog();
    }

    private Form CreateFormDynamically()
    {
        return new Form1();
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "Case5App", {"Form1.cs": content}, "case5.db")
        result = app_navigation.discover_navigation(app_id)

        edges = [e for e in result.edges if e.source_method == "OpenDynamic"]
        assert len(edges) == 1
        assert edges[0].target_class is None
        assert edges[0].resolution_status == "unresolved_navigation_target_unknown"
        assert edges[0].evidence.extractor == "APP_NAVIGATION_TARGET_TYPE_UNKNOWN"

    def test_case6_close_is_never_navigation(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void btnCerrar_Click(object sender, EventArgs e)
    {
        var form = new FormDeleteReference();
        form.Close();
    }
}

public class FormDeleteReference : Form
{
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "Case6App", {"Form1.cs": content}, "case6.db")
        result = app_navigation.discover_navigation(app_id)

        assert [e for e in result.edges if e.source_class == "Form1"] == []

    def test_case6b_hide_activate_focus_dispose_are_never_navigation(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void btnVarios_Click(object sender, EventArgs e)
    {
        var form = new FormDeleteReference();
        form.Hide();
        form.Activate();
        form.Focus();
        form.Dispose();
    }
}

public class FormDeleteReference : Form
{
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "Case6bApp", {"Form1.cs": content}, "case6b.db")
        result = app_navigation.discover_navigation(app_id)

        assert [e for e in result.edges if e.source_class == "Form1"] == []

    def test_case7_reflection_dispatch_produces_no_confirmed_navigation(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void ExecuteDynamic(string functionName)
    {
        var method = this.GetType().GetMethods().Where(m => m.Name == functionName).First();
        method.Invoke(this, null);
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "Case7App", {"Form1.cs": content}, "case7.db")
        result = app_navigation.discover_navigation(app_id)

        assert [e for e in result.edges if e.source_class == "Form1"] == []

    def test_case8_homonymous_methods_in_different_classes_are_attributed_correctly(self, tmp_path, monkeypatch):
        content = """\
public class ClaseA : Form
{
    private void Abrir()
    {
        var form = new FormX();
        form.ShowDialog();
    }
}

public class ClaseB : Form
{
    private void Abrir()
    {
        var form = new FormY();
        form.Show();
    }
}

public class FormX : Form
{
}

public class FormY : Form
{
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "Case8App", {"Ambas.cs": content}, "case8.db")
        result = app_navigation.discover_navigation(app_id)

        by_class = {e.source_class: e for e in result.edges}
        assert by_class["ClaseA"].target_class == "FormX"
        assert by_class["ClaseA"].operation == "show_dialog"
        assert by_class["ClaseB"].target_class == "FormY"
        assert by_class["ClaseB"].operation == "show"

    def test_case9_wpf_window_preserves_inherited_unknown_never_declares_no_navigation(self, tmp_path, monkeypatch):
        content = """\
public class MainWindow : Window
{
    public MainWindow() { }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "Case9App", {"MainWindow.cs": content}, "case9.db",
                             ui_framework=["WPF"])
        result = app_navigation.discover_navigation(app_id)

        # Sin wiring de eventos en .cs, no hay Show()/ShowDialog() que
        # observar en MainWindow -- CERO edges es correcto aqui, PERO la
        # limitacion WPF (heredada de Application Structure Discovery, sin
        # duplicarla) debe seguir presente para que no se lea como "esta
        # ventana no tiene ninguna navegacion".
        assert any(u.reason_code == "wpf_event_wiring_not_observable_in_cs" for u in result.unknowns)

    def test_field_reassignment_in_same_method_is_still_detected(self, tmp_path, monkeypatch):
        """Seccion 2.5 del incremento: variable declarada como campo de
        clase, pero cuya asignacion "= new Type()" ocurre dentro del cuerpo
        de un metodo -- se reduce al mismo patron intra-metodo, sin logica
        adicional."""
        content = """\
public class Form1 : Form
{
    private FormSearch _searchForm;

    private void btnSearch_Click(object sender, EventArgs e)
    {
        _searchForm = new FormSearch();
        _searchForm.Show();
    }
}

public class FormSearch : Form
{
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseFieldApp", {"Form1.cs": content}, "casefield.db")
        result = app_navigation.discover_navigation(app_id)

        edges = [e for e in result.edges if e.source_class == "Form1"]
        assert len(edges) == 1
        assert edges[0].target_class == "FormSearch"
        assert edges[0].resolution_status == "resolved"

    def test_messagebox_show_is_never_navigation(self, tmp_path, monkeypatch):
        """Gap descubierto validando contra las 5 apps reales: MessageBox
        es una clase estatica del BCL, jamas una instancia de Form/Window
        -- debe excluirse igual que Close/Hide/Dispose/Activate/Focus."""
        content = """\
public class Form1 : Form
{
    private void btnGuardar_Click(object sender, EventArgs e)
    {
        MessageBox.Show("Operacion completada");
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseMsgBoxApp", {"Form1.cs": content}, "casemsgbox.db")
        result = app_navigation.discover_navigation(app_id)

        assert [e for e in result.edges if e.source_class == "Form1"] == []

    def test_target_type_known_but_not_a_confirmed_screen(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void OpenHelper()
    {
        var helper = new HelperDialogWrapper();
        helper.ShowDialog();
    }
}

public class HelperDialogWrapper
{
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseNotScreenApp", {"Form1.cs": content}, "casenotscreen.db")
        result = app_navigation.discover_navigation(app_id)

        edges = [e for e in result.edges if e.source_class == "Form1"]
        assert len(edges) == 1
        assert edges[0].target_class == "HelperDialogWrapper"
        assert edges[0].resolution_status == "unresolved_target_not_a_known_screen"
        assert edges[0].evidence.extractor == "APP_NAVIGATION_TARGET_NOT_CONFIRMED_SCREEN"


class TestNavigationThirdPartyAndMissingApp:
    def test_third_party_navigation_is_excluded(self, tmp_path, monkeypatch):
        root = tmp_path / "decompiled"
        app_dir = root / "MyApp" / "MyApp"
        app_dir.mkdir(parents=True)
        (app_dir / "Form1.cs").write_text(
            "public class Form1 : Form\n{\n    private void Real_Click()\n    {\n"
            "        var form = new FormReal();\n        form.Show();\n    }\n}\n\n"
            "public class FormReal : Form\n{\n}\n",
            encoding="utf-8",
        )
        third_party_dir = root / "MyApp" / "Newtonsoft.Json"
        third_party_dir.mkdir(parents=True)
        (third_party_dir / "JsonConvert.cs").write_text(
            "public class JsonConvert : Form\n{\n    private void Vendor_Click()\n    {\n"
            "        var form = new JsonFormVendor();\n        form.ShowDialog();\n    }\n}\n\n"
            "public class JsonFormVendor : Form\n{\n}\n",
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

        result = app_navigation.discover_navigation(app_id)

        target_classes = {e.target_class for e in result.edges}
        assert "FormReal" in target_classes
        assert "JsonFormVendor" not in target_classes

    def test_missing_decompiled_folder_produces_no_edges_and_inherits_unknown(self, tmp_path, monkeypatch):
        root = tmp_path / "decompiled_vacio"
        monkeypatch.setattr(app_structure, "DECOMPILED_DIR", root)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "missing.db")
        db.init_db()
        app_id = db.save_analysis(
            "AppSinCarpeta", r"\\server\AppSinCarpeta.exe",
            TechStack(dotnet_target="net472", ui_framework=[], db_drivers=[]),
            [], [], [], [],
        )

        result = app_navigation.discover_navigation(app_id)

        assert result.edges == ()
        assert len(result.unknowns) == 1
        assert result.unknowns[0].reason_code == "unresolved_no_source_file"

    def test_nonexistent_app_id_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "none.db")
        db.init_db()

        assert app_navigation.discover_navigation(999999) is None


class TestRealPortfolioNavigationValidation:
    """Validacion contra las 5 apps reales del diagnostico -- contra la BD
    y decompiled/ REALES del portafolio, no fixtures sinteticas. No se
    aceptan conteos exactos adivinados: solo se afirma lo que la
    validacion manual (ver reporte final) confirmo contra el codigo
    decompilado real."""

    def _app_id(self, exact_name):
        for row in db.list_apps():
            if row["name"] == exact_name:
                return row["id"]
        pytest.skip(f"App real '{exact_name}' no encontrada en la BD del portafolio")

    def test_refcontrol_has_at_least_one_resolved_navigation(self):
        app_id = self._app_id("RefControl/RefControl")
        result = app_navigation.discover_navigation(app_id)

        assert result is not None
        resolved = [e for e in result.edges if e.resolution_status == "resolved"]
        assert len(resolved) >= 1

    def test_epoxylabel_simple_app_produces_navigation_result_without_crashing(self):
        app_id = self._app_id("EpoxyLabel/EpoxyLabel")
        result = app_navigation.discover_navigation(app_id)

        assert result is not None  # app de un solo Form -- pocas o cero navegaciones es un resultado valido

    def test_testvalidation_wpf_app_preserves_wpf_unknown(self):
        app_id = self._app_id("TestValidation/TestValidation")
        result = app_navigation.discover_navigation(app_id)

        assert result is not None
        assert any(u.reason_code == "wpf_event_wiring_not_observable_in_cs" for u in result.unknowns)

    def test_afl_datacenter_sql_heavy_app_produces_navigation_result_without_crashing(self):
        app_id = self._app_id("AFL_DataCenter")
        result = app_navigation.discover_navigation(app_id)

        assert result is not None

    def test_geometria_datatransfer_app_produces_navigation_result_without_crashing(self):
        app_id = self._app_id("Geometria/Release")
        result = app_navigation.discover_navigation(app_id)

        assert result is not None
