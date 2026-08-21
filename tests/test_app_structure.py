"""Incremento Flujo de Aplicacion - A: Application Structure Discovery
(2026-08-20): tests de analyzer/app_structure.py. Concepto DISTINTO de
Data Flow -- este modulo nunca toca resolve_data_flow()/
resolve_data_flow_for_app()/resolve_app_relations(), y estos tests nunca
verifican clasificacion de datos, solo estructura (entry point / clases /
metodos). La mayoria de los tests son puros (via _scan_file_structure
sobre archivos .cs sinteticos en tmp_path); solo la validacion contra las
5 apps reales del diagnostico usa la BD/decompiled/ real del portafolio."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from analyzer import app_structure, db  # noqa: E402
from analyzer.techstack import TechStack  # noqa: E402


def _write_cs(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _scan(tmp_path, name, content, app_name="TestApp"):
    path = _write_cs(tmp_path, name, content)
    classes, methods, entry_points, _method_intervals, _lines = app_structure._scan_file_structure(path, app_name, name)
    return classes, methods, entry_points


class TestEntryPointDetection:
    """1. Entry point WinForms. 2. Entry point alternativo (WPF)."""

    def test_winforms_application_run_is_detected(self, tmp_path):
        """Patron real confirmado: decompiled/Andon/Andon/Andon/Program.cs."""
        content = """\
public class Program
{
    [STAThread]
    private static void Main()
    {
        Application.EnableVisualStyles();
        Application.Run(new Form2());
    }
}
"""
        classes, methods, entry_points = _scan(tmp_path, "Program.cs", content)

        assert len(entry_points) == 1
        ep = entry_points[0]
        assert ep.pattern == "application_run"
        assert ep.class_name == "Program"
        assert ep.method_name == "Main"
        assert ep.evidence.extractor == "APP_STRUCTURE_ENTRY_POINT_APPLICATION_RUN"
        assert ep.evidence.confidence == 95

    def test_winforms_application_run_wrapped_in_instance_guard(self, tmp_path):
        """Patron real confirmado: decompiled/CopyJDSU/CopyJDSU/Program.cs
        -- Application.Run() envuelto en un if de instancia unica. Debe
        seguir detectandose (no se exige que sea la unica instruccion)."""
        content = """\
public class Program
{
    [STAThread]
    private static void Main()
    {
        string thisprocessname = Process.GetCurrentProcess().ProcessName;
        if (Process.GetProcesses().Count((Process p) => p.ProcessName == thisprocessname) <= 1)
        {
            Application.Run(new CopyJDSU3());
        }
    }
}
"""
        _, _, entry_points = _scan(tmp_path, "Program.cs", content)

        assert len(entry_points) == 1
        assert entry_points[0].pattern == "application_run"

    def test_wpf_app_run_pattern_is_detected_as_a_separate_pattern(self, tmp_path):
        """Patron real confirmado: decompiled/AFL.Entrega/AFL.Entrega/App.cs
        -- distinto mecanismo de arranque, detector separado (no la misma
        regex que application_run)."""
        content = """\
public class App : Application
{
    [STAThread]
    public static void Main()
    {
        App app = new App();
        app.InitializeComponent();
        app.Run();
    }
}
"""
        _, _, entry_points = _scan(tmp_path, "App.cs", content)

        assert len(entry_points) == 1
        ep = entry_points[0]
        assert ep.pattern == "wpf_app_run"
        assert ep.class_name == "App"
        assert ep.evidence.extractor == "APP_STRUCTURE_ENTRY_POINT_WPF_APP_RUN"

    def test_bare_main_without_run_pattern_is_lower_confidence(self, tmp_path):
        content = """\
public class Program
{
    private static void Main()
    {
        Console.WriteLine("hola");
    }
}
"""
        _, _, entry_points = _scan(tmp_path, "Program.cs", content)

        assert len(entry_points) == 1
        ep = entry_points[0]
        assert ep.pattern == "bare_main"
        assert ep.evidence.confidence == 70
        assert ep.evidence.confidence < 95  # nunca tan seguro como application_run


class TestFormAndWindowDetection:
    """3. Deteccion de Form. 4. Deteccion de Window/WPF."""

    def test_winforms_form_class_is_detected(self, tmp_path):
        content = """\
public class Form1 : Form
{
    private void btnBorrar_Click(object sender, EventArgs e) { }
}
"""
        classes, _, _ = _scan(tmp_path, "Form1.cs", content)

        assert len(classes) == 1
        assert classes[0].class_type == "form"
        assert classes[0].base_type == "Form"
        assert classes[0].evidence.confidence == 90

    def test_wpf_window_class_is_detected(self, tmp_path):
        content = """\
public class MainContainer : Window
{
    private void Button_Click(object sender, RoutedEventArgs e) { }
}
"""
        classes, _, _ = _scan(tmp_path, "MainContainer.cs", content)

        assert len(classes) == 1
        assert classes[0].class_type == "window"
        assert classes[0].base_type == "Window"

    def test_usercontrol_is_not_classified_as_form_or_window(self, tmp_path):
        """Diseno explicito: solo Form/Window exactos cuentan -- UserControl
        u otras clases base NO se tratan como tal en este incremento."""
        content = """\
public class MyPanel : UserControl
{
}
"""
        classes, _, _ = _scan(tmp_path, "MyPanel.cs", content)

        assert classes[0].class_type == "class"


class TestNormalClassDetection:
    """5. Clase normal."""

    def test_class_without_ui_base_type_is_generic(self, tmp_path):
        content = """\
public class Helpers
{
    public static void DoSomething() { }
}
"""
        classes, _, _ = _scan(tmp_path, "Helpers.cs", content)

        assert len(classes) == 1
        assert classes[0].class_type == "class"
        assert classes[0].base_type is None


class TestMethodAttribution:
    """6. Metodos pertenecientes a su clase. 7. Dos clases con metodos
    homonimos (caso critico -- no deben mezclarse)."""

    def test_methods_are_attributed_to_their_class(self, tmp_path):
        content = """\
public class Form1 : Form
{
    private void Baja(int datosIndex)
    {
        int x = 1;
    }

    private void GuardaLog(string modo)
    {
        int y = 2;
    }
}
"""
        classes, methods, _ = _scan(tmp_path, "Form1.cs", content)

        assert len(methods) == 2
        assert {m.method_name for m in methods} == {"Baja", "GuardaLog"}
        assert all(m.class_name == "Form1" for m in methods)

    def test_homonymous_methods_in_different_classes_are_not_mixed(self, tmp_path):
        """Caso critico explicito del incremento: _find_method_body (usada
        en server_resolution.py) NO tiene scope de clase y confundiria
        estos dos metodos -- _scan_file_structure SI debe distinguirlos
        porque acota la busqueda al intervalo de llaves de cada clase."""
        content = """\
public class ClaseA : Form
{
    private void Validate()
    {
        int marcaA = 1;
    }
}

public class ClaseB : Form
{
    private void Validate()
    {
        int marcaB = 2;
    }
}
"""
        classes, methods, _ = _scan(tmp_path, "DosClases.cs", content)

        assert len(classes) == 2
        validate_methods = [m for m in methods if m.method_name == "Validate"]
        assert len(validate_methods) == 2
        by_class = {m.class_name: m for m in validate_methods}
        assert "ClaseA" in by_class and "ClaseB" in by_class
        assert by_class["ClaseA"].line != by_class["ClaseB"].line


class TestWpfEventWiringUnknown:
    """8. WPF donde no existe evidencia de wiring XAML -- debe declararse
    explicitamente como Unknown, nunca como "sin eventos"."""

    def test_wpf_window_without_designer_generates_explicit_unknown(self, tmp_path, monkeypatch):
        root = tmp_path / "decompiled"
        app_dir = root / "MyWpfApp" / "MyWpfApp"
        app_dir.mkdir(parents=True)
        (app_dir / "MainWindow.cs").write_text(
            "public class MainWindow : Window\n{\n    public MainWindow() { }\n}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(app_structure, "DECOMPILED_DIR", root)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
        db.init_db()
        app_id = db.save_analysis(
            "MyWpfApp", r"\\server\MyWpfApp.exe",
            TechStack(dotnet_target="net472", ui_framework=["WPF"], db_drivers=[]),
            [], [], [], [],
        )

        result = app_structure.discover_application_structure(app_id)

        assert any(c.class_type == "window" for c in result.classes)
        assert len(result.unknowns) == 1
        assert result.unknowns[0].reason_code == "wpf_event_wiring_not_observable_in_cs"
        assert result.unknowns[0].evidence_class == "MainWindow"
        # NUNCA se declara ausencia de eventos -- solo la limitacion.
        assert "no observable" in result.unknowns[0].impact.lower() or "no se infiere" in result.unknowns[0].impact.lower()


class TestLargeClassAndMethodWindows:
    """Gap descubierto en Incremento C (2026-08-20), validando
    analyzer/app_interactions.py contra AFL_DataCenter: una ventana
    artificial de "i + 6000" lineas para clases y "i + 2000" para metodos
    truncaba estructuras reales mas largas (DataCenter: 9668 lineas),
    cortando metodos legitimos (InitializeComponent en la linea 8379) fuera
    del intervalo detectado. _brace_matched_end ya acota internamente a
    len(lines) -- no se necesita ninguna ventana adicional."""

    def test_method_declared_beyond_the_old_6000_line_class_window_is_detected(self, tmp_path):
        padding = "\n".join(f"    // relleno linea {i}" for i in range(6500))
        content = f"""\
public class DataCenter : Form
{{
{padding}
    private void InitializeComponent()
    {{
        this.btnDJ.Click += new System.EventHandler(btnDJ_Click);
    }}
}}
"""
        classes, methods, _ = _scan(tmp_path, "DataCenter.cs", content)

        assert len(classes) == 1
        assert any(m.method_name == "InitializeComponent" for m in methods)

    def test_method_body_longer_than_the_old_2000_line_window_is_fully_captured(self, tmp_path):
        padding = "\n".join(f"        // relleno linea {i}" for i in range(2500))
        content = f"""\
public class Form1 : Form
{{
    private void InitializeComponent()
    {{
{padding}
        this.btnDJ.Click += new System.EventHandler(btnDJ_Click);
    }}
}}
"""
        path = _write_cs(tmp_path, "Form1.cs", content)
        classes, methods, entry_points, method_intervals, lines = app_structure._scan_file_structure(path, "TestApp", "Form1.cs")

        assert len(method_intervals) == 1
        start, end, class_name, method_name = method_intervals[0]
        wiring_line_idx = next(i for i, l in enumerate(lines) if "btnDJ.Click" in l)
        assert start <= wiring_line_idx <= end


class TestNoStructureAndThirdParty:
    """9. Archivo sin estructura detectable. 10. No confundir codigo de
    terceros con estructura de la aplicacion."""

    def test_file_with_no_detectable_structure_returns_empty(self, tmp_path):
        content = "// solo un comentario\nint x = 1;\n"
        classes, methods, entry_points = _scan(tmp_path, "Vacio.cs", content)

        assert classes == [] and methods == [] and entry_points == []

    def test_third_party_folder_is_skipped(self, tmp_path, monkeypatch):
        root = tmp_path / "decompiled"
        app_dir = root / "MyApp" / "MyApp"
        app_dir.mkdir(parents=True)
        (app_dir / "Form1.cs").write_text(
            "public class Form1 : Form\n{\n    private void Real_Click() { }\n}\n",
            encoding="utf-8",
        )
        # "Newtonsoft.Json" coincide con THIRD_PARTY_ASSEMBLY_PATTERN
        # (Newtonsoft.* en analyzer/decompile.py) -- debe excluirse.
        third_party_dir = root / "MyApp" / "Newtonsoft.Json"
        third_party_dir.mkdir(parents=True)
        (third_party_dir / "JsonConvert.cs").write_text(
            "public class JsonConvert : Form\n{\n    private void Vendor_Click() { }\n}\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(app_structure, "DECOMPILED_DIR", root)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test2.db")
        db.init_db()
        app_id = db.save_analysis(
            "MyApp", r"\\server\MyApp.exe",
            TechStack(dotnet_target="net472", ui_framework=["WinForms"], db_drivers=[]),
            [], [], [], [],
        )

        result = app_structure.discover_application_structure(app_id)

        class_names = {c.class_name for c in result.classes}
        assert "Form1" in class_names
        assert "JsonConvert" not in class_names

    def test_missing_decompiled_folder_produces_explicit_unknown_not_empty_silence(self, tmp_path, monkeypatch):
        root = tmp_path / "decompiled_vacio"
        monkeypatch.setattr(app_structure, "DECOMPILED_DIR", root)
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test3.db")
        db.init_db()
        app_id = db.save_analysis(
            "AppSinCarpeta", r"\\server\AppSinCarpeta.exe",
            TechStack(dotnet_target="net472", ui_framework=[], db_drivers=[]),
            [], [], [], [],
        )

        result = app_structure.discover_application_structure(app_id)

        assert result.classes == () and result.methods == () and result.entry_points == ()
        assert len(result.unknowns) == 1
        assert result.unknowns[0].reason_code == "unresolved_no_source_file"

    def test_nonexistent_app_id_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "test4.db")
        db.init_db()

        assert app_structure.discover_application_structure(999999) is None


class TestRealPortfolioValidation:
    """Validacion contra las 5 apps reales perfiladas en el diagnostico --
    contra la BD y decompiled/ REALES del portafolio, no fixtures
    sinteticas. Los nombres/ids se resuelven por nombre en vez de fijar el
    id numerico, para no depender de que nunca cambie el id en la BD real."""

    def _app_id(self, exact_name):
        """Coincidencia EXACTA -- "AFL_DataCenter" es substring de
        "AFL_DataCenter_Print" (app real distinta), asi que un match por
        substring elegiria la app equivocada."""
        for row in db.list_apps():
            if row["name"] == exact_name:
                return row["id"]
        pytest.skip(f"App real '{exact_name}' no encontrada en la BD del portafolio")

    def test_epoxylabel_simple_app_has_one_form_and_entry_point(self):
        app_id = self._app_id("EpoxyLabel/EpoxyLabel")
        result = app_structure.discover_application_structure(app_id)

        assert result is not None
        assert any(ep.pattern == "application_run" for ep in result.entry_points)
        assert any(c.class_name == "Form1" and c.class_type == "form" for c in result.classes)

    def test_refcontrol_multi_form_app_has_all_three_real_forms(self):
        app_id = self._app_id("RefControl/RefControl")
        result = app_structure.discover_application_structure(app_id)

        form_names = {c.class_name for c in result.classes if c.class_type == "form"}
        assert {"Form1", "FormDeleteReference", "FrmSearchSerialReference"} <= form_names

    def test_afl_datacenter_sql_heavy_app_has_datacenter_form(self):
        app_id = self._app_id("AFL_DataCenter")
        result = app_structure.discover_application_structure(app_id)

        assert any(c.class_name == "DataCenter" and c.class_type == "form" for c in result.classes)
        assert len(result.methods) > 50  # monolito real de 123 metodos conocidos

    def test_testvalidation_wpf_navigation_app_has_three_windows_and_unknowns(self):
        app_id = self._app_id("TestValidation/TestValidation")
        result = app_structure.discover_application_structure(app_id)

        window_names = {c.class_name for c in result.classes if c.class_type == "window"}
        assert {"MainContainer", "ReportWindow", "View_Configuracion"} <= window_names
        assert any(u.reason_code == "wpf_event_wiring_not_observable_in_cs" for u in result.unknowns)
        assert any(ep.pattern == "wpf_app_run" for ep in result.entry_points)

    def test_geometria_datatransfer_ambiguous_app_still_produces_structure(self):
        app_id = self._app_id("Geometria/Release")
        result = app_structure.discover_application_structure(app_id)

        assert any(c.class_name == "DataTransfer" and c.class_type == "form" for c in result.classes)
        assert len(result.entry_points) >= 1
