"""Incremento Flujo de Aplicacion - E: Screen Surface Discovery (2026-08-21):
tests de analyzer/app_controls.py. PRECISION > COBERTURA -- estos tests
verifican tanto los controles que SI deben resolverse (declaracion/
instanciacion de tipo catalogado, texto explicito) como (igual de
importante) los casos que NUNCA deben inventar tipo/texto (nombre enganoso,
tipo fuera de catalogo, terceros, variable-alias con inicializador inline).
Reutiliza el patron de fixtures de tests/test_app_interactions.py
(decompiled/ sintetico + monkeypatch de DECOMPILED_DIR/db.DB_PATH); solo
TestRealPortfolioValidation usa la BD y decompiled/ REALES del portafolio."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from analyzer import app_controls, app_structure, db  # noqa: E402
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


class TestScreenSurfaceDiscovery:
    def test_case1_declaration_and_type_is_resolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private Button btnBorrar;

    private void InitializeComponent()
    {
        this.btnBorrar = new System.Windows.Forms.Button();
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseE1", {"Form1.cs": content}, "e1.db")
        result = app_controls.discover_screen_surface(app_id)

        controls = [c for c in result.controls if c.class_name == "Form1"]
        assert len(controls) == 1
        c = controls[0]
        assert c.control_name == "btnBorrar"
        assert c.control_type == "Button"
        assert c.resolution_status == "resolved"
        assert c.evidence.extractor == "APP_CONTROL_DECLARATION_AND_TYPE"

    def test_case2_control_with_text_is_captured(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private Button btnBorrar;

    private void InitializeComponent()
    {
        this.btnBorrar = new System.Windows.Forms.Button();
        this.btnBorrar.TabIndex = 16;
        this.btnBorrar.Text = "Delete";
        this.btnBorrar.UseVisualStyleBackColor = true;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseE2", {"Form1.cs": content}, "e2.db")
        result = app_controls.discover_screen_surface(app_id)

        c = [c for c in result.controls if c.control_name == "btnBorrar"][0]
        assert c.control_type == "Button"
        assert c.label_text == "Delete"
        assert c.evidence.extractor == "APP_CONTROL_LABEL_TEXT"

    def test_case3_control_without_text_is_not_unknown(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private Button btnCanc;

    private void InitializeComponent()
    {
        this.btnCanc = new System.Windows.Forms.Button();
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseE3", {"Form1.cs": content}, "e3.db")
        result = app_controls.discover_screen_surface(app_id)

        c = [c for c in result.controls if c.control_name == "btnCanc"][0]
        assert c.control_type == "Button"
        assert c.label_text is None
        assert c.resolution_status == "resolved"  # NUNCA se convierte en unknown por falta de texto

    def test_case4_type_outside_catalog_is_unresolved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private void InitializeComponent()
    {
        this.timer1 = new System.Windows.Forms.Timer(this.components);
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseE4", {"Form1.cs": content}, "e4.db")
        result = app_controls.discover_screen_surface(app_id)

        c = [c for c in result.controls if c.control_name == "timer1"][0]
        assert c.control_type is None
        assert c.resolution_status == "unresolved_control_type_unknown"
        assert c.evidence.extractor == "APP_CONTROL_TYPE_UNKNOWN"

    def test_case5_misleading_name_uses_type_evidence_not_name(self, tmp_path, monkeypatch):
        """btnWhatever no es un Button -- el Analyzer debe usar la
        instanciacion real (TextBox), nunca el prefijo del nombre."""
        content = """\
public class Form1 : Form
{
    private void InitializeComponent()
    {
        this.btnWhatever = new System.Windows.Forms.TextBox();
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseE5", {"Form1.cs": content}, "e5.db")
        result = app_controls.discover_screen_surface(app_id)

        c = [c for c in result.controls if c.control_name == "btnWhatever"][0]
        assert c.control_type == "TextBox"

    def test_case6_wpf_preserves_explicit_control_surface_gap(self, tmp_path, monkeypatch):
        content = """\
public class MainWindow : Window
{
    public MainWindow() { }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseE6", {"MainWindow.cs": content}, "e6.db",
                             ui_framework=["WPF"])
        result = app_controls.discover_screen_surface(app_id)

        assert result.controls == ()
        assert any(u.reason_code == "wpf_control_surface_not_observable_in_cs" for u in result.unknowns)

    def test_wpf_code_behind_field_declaration_is_never_treated_as_winforms_control(self, tmp_path, monkeypatch):
        """Gap real descubierto validando contra TestValidation/TestValidation:
        el code-behind generado por el compilador XAML declara campos bare
        de tipo catalogado ("internal TextBox txtX;") para cada elemento con
        x:Name -- eso es System.Windows.Controls.TextBox (WPF), NUNCA
        System.Windows.Forms.TextBox, y la declaracion no distingue el
        namespace. Sin una instanciacion System.Windows.Forms confirmante,
        una clase Window jamas debe producir un control resuelto."""
        content = """\
public class MainWindow : Window
{
    internal TextBox txtQtyPerBox;
    internal Button btnSave;

    public MainWindow() { }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseEWpfField", {"MainWindow.cs": content}, "ewpffield.db",
                             ui_framework=["WPF"])
        result = app_controls.discover_screen_surface(app_id)

        assert result.controls == ()

    def test_case7_third_party_control_is_never_captured(self, tmp_path, monkeypatch):
        """Microsoft.Reporting.WinForms.ReportViewer -- namespace distinto
        de System.Windows.Forms, excluido por construccion, sin necesitar
        lista negra."""
        content = """\
public class Form1 : Form
{
    private void InitializeComponent()
    {
        this.viewer = new Microsoft.Reporting.WinForms.ReportViewer();
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseE7", {"Form1.cs": content}, "e7.db")
        result = app_controls.discover_screen_surface(app_id)

        assert result.controls == ()

    def test_case8_physically_duplicated_files_preserve_context_per_class(self, tmp_path, monkeypatch):
        root = tmp_path / "decompiled"
        dir1 = root / "DupApp" / "Release"
        dir2 = root / "DupApp" / "app.publish"
        dir1.mkdir(parents=True)
        dir2.mkdir(parents=True)
        content = """\
public class Form1 : Form
{
    private Button btnBorrar;

    private void InitializeComponent()
    {
        this.btnBorrar = new System.Windows.Forms.Button();
        this.btnBorrar.Text = "Delete";
    }
}
"""
        (dir1 / "Form1.cs").write_text(content, encoding="utf-8")
        (dir2 / "Form1.cs").write_text(content, encoding="utf-8")
        monkeypatch.setattr(app_structure, "DECOMPILED_DIR", root)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "e8.db")
        db.init_db()
        app_id = db.save_analysis(
            "DupApp", r"\\server\DupApp.exe",
            TechStack(dotnet_target="net472", ui_framework=["WinForms"], db_drivers=[]),
            [], [], [], [],
        )

        result = app_controls.discover_screen_surface(app_id)

        matching = [c for c in result.controls if c.control_name == "btnBorrar"]
        assert len(matching) == 2  # uno por archivo fisicamente duplicado, consistente con A-D
        assert all(c.control_type == "Button" and c.label_text == "Delete" for c in matching)

    def test_case9_multiple_controls_are_all_conserved(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private Button btnBorrar;
    private TextBox txtRef;
    private Label label1;

    private void InitializeComponent()
    {
        this.btnBorrar = new System.Windows.Forms.Button();
        this.txtRef = new System.Windows.Forms.TextBox();
        this.label1 = new System.Windows.Forms.Label();
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseE9", {"Form1.cs": content}, "e9.db")
        result = app_controls.discover_screen_surface(app_id)

        names = {c.control_name for c in result.controls}
        assert names == {"btnBorrar", "txtRef", "label1"}

    def test_case10_declaration_instantiation_and_text_on_separate_lines(self, tmp_path, monkeypatch):
        content = """\
public class Form1 : Form
{
    private Button btnBorrar;

    private void InitializeComponent()
    {
        this.btnCanc = new System.Windows.Forms.Button();
        this.btnBorrar = new System.Windows.Forms.Button();
        this.btnBorrar.Margin = new System.Windows.Forms.Padding(3, 4, 3, 4);
        this.btnBorrar.Name = "btnBorrar";
        this.btnBorrar.Size = new System.Drawing.Size(96, 32);
        this.btnBorrar.TabIndex = 16;
        this.btnBorrar.Text = "Delete";
        this.btnBorrar.UseVisualStyleBackColor = true;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseE10", {"Form1.cs": content}, "e10.db")
        result = app_controls.discover_screen_surface(app_id)

        c = [c for c in result.controls if c.control_name == "btnBorrar"][0]
        assert c.control_type == "Button"
        assert c.label_text == "Delete"

    def test_alias_variable_with_inline_initializer_is_never_a_control(self, tmp_path, monkeypatch):
        """Caso real encontrado en Geometria/Release/DataTransfer.cs:753 --
        una variable-alias reasignada despues a controles reales, NUNCA un
        control genuino de la pantalla."""
        content = """\
public class Form1 : Form
{
    private TextBox LeSerialCharola = new TextBox();
    private TextBox txtSerial1;

    private void InitializeComponent()
    {
        this.txtSerial1 = new System.Windows.Forms.TextBox();
    }

    private void Validar()
    {
        LeSerialCharola = txtSerial1;
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseEAlias", {"Form1.cs": content}, "ealias.db")
        result = app_controls.discover_screen_surface(app_id)

        names = {c.control_name for c in result.controls}
        assert "LeSerialCharola" not in names
        assert "txtSerial1" in names

    def test_field_declared_without_matching_instantiation_still_resolves_type(self, tmp_path, monkeypatch):
        """Robustez: declaracion e instanciacion pueden faltar una sin la
        otra -- la declaracion sola, con tipo del catalogo, ya es evidencia
        suficiente de tipo (aunque no aparezca texto sin metodo que buscar)."""
        content = """\
public class Form1 : Form
{
    private Label lblOnlyDeclared;
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseEDeclOnly", {"Form1.cs": content}, "edeclonly.db")
        result = app_controls.discover_screen_surface(app_id)

        c = [c for c in result.controls if c.control_name == "lblOnlyDeclared"][0]
        assert c.control_type == "Label"
        assert c.label_text is None
        assert c.resolution_status == "resolved"

    def test_scope_never_mixes_controls_between_classes(self, tmp_path, monkeypatch):
        content = """\
public class FormA : Form
{
    private Button btnAceptar;

    private void InitializeComponent()
    {
        this.btnAceptar = new System.Windows.Forms.Button();
        this.btnAceptar.Text = "OK A";
    }
}

public class FormB : Form
{
    private TextBox btnAceptar;

    private void InitializeComponent()
    {
        this.btnAceptar = new System.Windows.Forms.TextBox();
        this.btnAceptar.Text = "OK B";
    }
}
"""
        app_id = _setup_app(tmp_path, monkeypatch, "CaseEScope", {"Forms.cs": content}, "escope.db")
        result = app_controls.discover_screen_surface(app_id)

        by_class = {c.class_name: c for c in result.controls if c.control_name == "btnAceptar"}
        assert by_class["FormA"].control_type == "Button"
        assert by_class["FormA"].label_text == "OK A"
        assert by_class["FormB"].control_type == "TextBox"
        assert by_class["FormB"].label_text == "OK B"


class TestThirdPartyAndMissingApp:
    def test_third_party_folder_is_excluded(self, tmp_path, monkeypatch):
        root = tmp_path / "decompiled"
        app_dir = root / "MyApp" / "MyApp"
        app_dir.mkdir(parents=True)
        (app_dir / "Form1.cs").write_text(
            "public class Form1 : Form\n{\n    private void InitializeComponent()\n    {\n"
            "        this.btnReal = new System.Windows.Forms.Button();\n    }\n}\n",
            encoding="utf-8",
        )
        third_party_dir = root / "MyApp" / "Newtonsoft.Json"
        third_party_dir.mkdir(parents=True)
        (third_party_dir / "JsonConvert.cs").write_text(
            "public class JsonConvert : Form\n{\n    private void InitializeComponent()\n    {\n"
            "        this.btnVendor = new System.Windows.Forms.Button();\n    }\n}\n",
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

        result = app_controls.discover_screen_surface(app_id)

        names = {c.control_name for c in result.controls}
        assert "btnReal" in names
        assert "btnVendor" not in names

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

        result = app_controls.discover_screen_surface(app_id)

        assert result.controls == ()
        assert len(result.unknowns) == 1
        assert result.unknowns[0].reason_code == "unresolved_no_source_file"

    def test_nonexistent_app_id_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "none.db")
        db.init_db()

        assert app_controls.discover_screen_surface(999999) is None


class TestRealPortfolioValidation:
    """Validacion contra las 5 apps reales del diagnostico -- contra la BD
    y decompiled/ REALES del portafolio, no fixtures sinteticas."""

    def _app_id(self, exact_name):
        for row in db.list_apps():
            if row["name"] == exact_name:
                return row["id"]
        pytest.skip(f"App real '{exact_name}' no encontrada en la BD del portafolio")

    def test_refcontrol_has_resolved_controls_with_and_without_text(self):
        app_id = self._app_id("RefControl/RefControl")
        result = app_controls.discover_screen_surface(app_id)

        assert result is not None
        resolved = [c for c in result.controls if c.resolution_status == "resolved"]
        assert len(resolved) > 0
        assert any(c.label_text == "Delete" for c in resolved)
        assert any(c.label_text is None for c in resolved)
        assert "LeSerialCharola" not in {c.control_name for c in result.controls}

    def test_epoxylabel_simple_app_produces_result_without_crashing(self):
        app_id = self._app_id("EpoxyLabel/EpoxyLabel")
        result = app_controls.discover_screen_surface(app_id)

        assert result is not None
        assert len(result.controls) > 0

    def test_testvalidation_wpf_app_preserves_control_surface_gap(self):
        app_id = self._app_id("TestValidation/TestValidation")
        result = app_controls.discover_screen_surface(app_id)

        assert result is not None
        assert any(u.reason_code == "wpf_control_surface_not_observable_in_cs" for u in result.unknowns)
        # Gap real corregido: los campos bare del code-behind XAML
        # (internal TextBox/Button para elementos con x:Name) NUNCA deben
        # producir un control WinForms resuelto -- son System.Windows.
        # Controls.*, no System.Windows.Forms.*, y la declaracion sola no
        # distingue el namespace.
        assert result.controls == ()

    def test_afl_datacenter_sql_heavy_app_produces_result_without_crashing(self):
        app_id = self._app_id("AFL_DataCenter")
        result = app_controls.discover_screen_surface(app_id)

        assert result is not None
        assert len(result.controls) > 0

    def test_geometria_datatransfer_excludes_alias_and_third_party(self):
        app_id = self._app_id("Geometria/Release")
        result = app_controls.discover_screen_surface(app_id)

        assert result is not None
        names = {c.control_name for c in result.controls}
        assert "LeSerialCharola" not in names
        assert "viewer" not in names  # Microsoft.Reporting.WinForms.ReportViewer, tercero
