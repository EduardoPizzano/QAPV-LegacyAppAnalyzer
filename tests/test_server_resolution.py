"""Incremento Huella de Datos (2026-08-18): tests de analyzer/server_resolution.py,
el resolver formal que reemplaza la investigacion ad hoc (resolve_table_servers.py,
mantenido como evidencia historica, nunca importado desde produccion).

Cubre, como minimo, los 6 fixes de extraccion descubiertos durante esa
investigacion (cada uno demostrado contra el bug que corrige, no solo contra
el caso feliz), mas los casos de servidor no resoluble, conexion ambigua,
clasificacion ERP positiva/negativa, y la regla arquitectonica de no usar
ningun inventario externo para inventar una resolucion faltante."""

from datetime import datetime

import pytest

from analyzer import confidence, db, server_resolution
from analyzer.server_resolution import (
    build_setting_lookup,
    is_oracle_erp_table,
    resolve_write_targets,
)
from analyzer.techstack import TechStack


def _setting(name, default_value):
    return {"name": name, "default_value": default_value}


def _finding(file="Class1.cs", method="Graba", target="LCJob", raw="", resolved=None, is_stored_procedure=False):
    return {
        "file": file, "class_name": "Class1", "method": method, "target": target,
        "raw": raw, "resolved": resolved, "is_stored_procedure": is_stored_procedure,
    }


def _write(tmp_path, filename, content):
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return tmp_path


QCS25 = "Server=NAAMRT-QCS25;Database=QAPVMLN;User Id=QUALITY;Password=x;"
QCS12 = "Server=NAAMRT-QCS12;Database=QAPVMLN2;User Id=QUALITY;Password=x;"


class TestFix1WriteVerbOnlyInResolvedNotRaw:
    """AFL.Dashboard real: `raw` es boilerplate C# ("using (SqlCommand
    sqlCommand = new SqlCommand(cmdText, sqlConnection))") que casi nunca
    contiene el verbo INSERT/UPDATE/DELETE -- el verbo real vive en
    `resolved`. Un filtro que solo mirara `raw` habria descartado esta fila
    en silencio (bug real confirmado con AFL.Dashboard: 0 de 5 escrituras
    detectadas antes de este fix)."""

    CS = """
public class Class1
{
    private string CX;

    public bool Graba(string idJob)
    {
        using (SqlConnection sqlConnection = new SqlConnection(CX))
        {
            using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))
            {
                sqlCommand.ExecuteNonQuery();
            }
        }
        return true;
    }
}
"""

    def test_raw_alone_would_never_match_the_write_keywords(self):
        """Confirma la premisa del bug: `raw` por si solo no contiene el
        verbo -- si el filtro solo mirara `raw`, esta fila se perderia."""
        raw = "using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))"
        assert server_resolution.WRITE_KEYWORDS.search(raw) is None

    def test_resolved_field_carries_the_real_verb_and_gets_detected(self, tmp_path):
        root = _write(tmp_path, "Class1.cs", self.CS)
        settings = [_setting("CX", QCS25)]
        findings = [_finding(
            raw="using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))",
            resolved="UPDATE LCJob SET Linea='1' WHERE ID={idJob}",
        )]

        results = resolve_write_targets("App", settings, findings, root)

        assert len(results) == 1
        assert results[0].target == "LCJob"
        assert results[0].resolution_status == "resolved"
        assert results[0].candidates[0].server == "NAAMRT-QCS25"


class TestFix2TernaryWithNestedParens:
    """Monitor1/Form1.cs:BuscaConfig real (linea 391-392): el ternario que
    selecciona la conexion trae parentesis propios alrededor de su CONDICION
    (`((Planta == "2") ? CX2 : CX)`), asignado primero a una variable local
    y luego pasado a `new SqlConnection(...)`. Un regex que exigiera un
    unico par de parentesis envolviendo todo el ternario no lo detectaba en
    absoluto (0 candidatos -> "unresolved" incorrecto); despues del fix, se
    detectan AMBOS candidatos posibles -> "ambiguo", que es la verdad
    honesta (cual aplica depende de una condicion no evaluada)."""

    CS = """
public class Class1
{
    private string CX;
    private string CX2;

    public int BuscaCantidad(int idJob)
    {
        string connectionString = ((Planta == "2") ? CX2 : CX);
        using SqlConnection connection = new SqlConnection(connectionString);
        string cmdText = "select 1";
        using (SqlCommand sqlCommand = new SqlCommand(cmdText, connection))
        {
            sqlCommand.ExecuteNonQuery();
        }
        return 0;
    }
}
"""

    def test_nested_parens_ternary_yields_both_candidates_ambiguous(self, tmp_path):
        root = _write(tmp_path, "Class1.cs", self.CS)
        settings = [_setting("CX", QCS25), _setting("CX2", QCS12)]
        findings = [_finding(
            method="BuscaCantidad",
            raw="using (SqlCommand sqlCommand = new SqlCommand(cmdText, connection))",
            resolved="UPDATE LCJob SET Cantidad=1 WHERE ID={idJob}",
        )]

        results = resolve_write_targets("App", settings, findings, root)

        assert len(results) == 1
        result = results[0]
        assert result.resolution_status == "unresolved_ambiguous_conditional"
        servers = {c.server for c in result.candidates}
        assert servers == {"NAAMRT-QCS25", "NAAMRT-QCS12"}
        assert result.evidence.extractor == "CONNECTION_AMBIGUOUS_CONDITIONAL"
        assert result.evidence.confidence == confidence.CONFIDENCE_TABLE["CONNECTION_AMBIGUOUS_CONDITIONAL"] == 55


class TestFix3NeverTrustIsConnectionString:
    """Monitor1 real: el setting "CX2" (Server=NAAMRT-QCS12;Database=QAPVMLN2;...)
    tenia is_connection_string=0 en la BD -- ese flag lo pone
    _classify_setting() en extract.py y puede fallar. build_setting_lookup()
    nunca lo consulta: valida el VALOR de forma independiente."""

    def test_setting_with_real_connection_value_is_included_even_if_marked_false(self):
        settings = [
            {"name": "CX2", "default_value": QCS12, "is_connection_string": False},
        ]

        lookup = build_setting_lookup(settings)

        assert "CX2" in lookup
        assert lookup["CX2"]["server"] == "NAAMRT-QCS12"

    def test_a_value_that_does_not_look_like_a_connection_string_is_excluded(self):
        settings = [{"name": "Idioma", "default_value": "es-MX", "is_connection_string": True}]

        lookup = build_setting_lookup(settings)

        assert "Idioma" not in lookup


class TestFix4CtorWithoutArgsThenConnectionStringProperty:
    """LabelPrint2/Form1.cs:UpdateAppSettings real: `new SqlConnection()`
    sin argumentos, seguido en otra linea de `sqlConnection.ConnectionString
    = CX;` -- un regex que solo mirara el argumento del constructor nunca
    veria esta conexion."""

    CS = """
public class Class1
{
    private string CX;

    public bool UpdateAppSettings(string idJob)
    {
        SqlConnection sqlConnection = new SqlConnection();
        sqlConnection.ConnectionString = CX;
        using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))
        {
            sqlCommand.ExecuteNonQuery();
        }
        return true;
    }
}
"""

    def test_property_assignment_after_no_arg_ctor_resolves(self, tmp_path):
        root = _write(tmp_path, "Class1.cs", self.CS)
        settings = [_setting("CX", QCS25)]
        findings = [_finding(
            method="UpdateAppSettings",
            raw="using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))",
            resolved="UPDATE Settings SET X=1 WHERE ID={idJob}",
        )]

        results = resolve_write_targets("App", settings, findings, root)

        assert results[0].resolution_status == "resolved"
        assert results[0].candidates[0].server == "NAAMRT-QCS25"
        assert results[0].evidence.extractor == "CONNECTION_CTOR_DIRECT_SETTING"
        assert results[0].evidence.confidence == confidence.CONFIDENCE_TABLE["CONNECTION_CTOR_DIRECT_SETTING"] == 95


class TestFix5IdentifierCaseAndUnderscoreNormalization:
    """QAPV2/VaLabel/PullTest/SGI reales: el codigo usa "_cx"/"_connectionString"/
    "connectionString" para un campo asignado en otro lado desde el setting
    real ("CX"), nunca literalmente el mismo nombre con las mismas
    mayusculas."""

    CS = """
public class Class1
{
    private string _cx;

    public bool Graba(string idJob)
    {
        using (SqlConnection sqlConnection = new SqlConnection(_cx))
        {
            using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))
            {
                sqlCommand.ExecuteNonQuery();
            }
        }
        return true;
    }
}
"""

    def test_leading_underscore_and_case_difference_still_matches_the_setting(self, tmp_path):
        root = _write(tmp_path, "Class1.cs", self.CS)
        settings = [_setting("CX", QCS25)]  # nombre real del setting: "CX", sin guion bajo
        findings = [_finding(
            raw="using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))",
            resolved="UPDATE LCJob SET X=1 WHERE ID={idJob}",
        )]

        results = resolve_write_targets("App", settings, findings, root)

        assert results[0].resolution_status == "resolved"
        assert results[0].candidates[0].setting_name == "CX"


class TestFix6ConfigurationManagerConnectionStringsFullIndexer:
    """SGI/ValesRHVM.cs y SurtirVM.cs reales: `algo.ConnectionString =
    ConfigurationManager.ConnectionStrings["connectionString"].ConnectionString;`
    -- el lado derecho no es un identificador simple (no lo captura el regex
    de asignacion de propiedad simple), es el mismo indexer usado para campos
    de clase, solo que dentro de una asignacion de propiedad."""

    CS = """
public class Class1
{
    private SqlConnection sqlConnection;

    public bool Graba(string idJob)
    {
        sqlConnection = new SqlConnection();
        sqlConnection.ConnectionString = ConfigurationManager.ConnectionStrings["connectionString"].ConnectionString;
        using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))
        {
            sqlCommand.ExecuteNonQuery();
        }
        return true;
    }
}
"""

    def test_full_indexer_inside_property_assignment_resolves(self, tmp_path):
        root = _write(tmp_path, "Class1.cs", self.CS)
        settings = [_setting("connectionString", QCS25)]
        findings = [_finding(
            raw="using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))",
            resolved="UPDATE LCJob SET X=1 WHERE ID={idJob}",
        )]

        results = resolve_write_targets("App", settings, findings, root)

        assert results[0].resolution_status == "resolved"
        assert results[0].candidates[0].setting_name == "connectionString"


class TestUnresolvableConnection:
    """La conexion se abre via un helper opaco (`Helper.GetConnection()`) --
    ningun patron conocido la reconoce. Debe quedar sin resolver a proposito,
    nunca inventar un servidor."""

    CS = """
public class Class1
{
    public bool Graba(string idJob)
    {
        using (var conn = Helper.GetConnection())
        {
            using (SqlCommand sqlCommand = new SqlCommand(cmdText, conn))
            {
                sqlCommand.ExecuteNonQuery();
            }
        }
        return true;
    }
}
"""

    def test_opaque_connection_helper_stays_unresolved(self, tmp_path):
        root = _write(tmp_path, "Class1.cs", self.CS)
        settings = [_setting("CX", QCS25)]
        findings = [_finding(
            raw="using (SqlCommand sqlCommand = new SqlCommand(cmdText, conn))",
            resolved="UPDATE LCJob SET X=1 WHERE ID={idJob}",
        )]

        results = resolve_write_targets("App", settings, findings, root)

        assert results[0].resolution_status == "unresolved_no_literal"
        assert results[0].candidates == ()

    def test_missing_source_file_is_a_distinct_status_from_a_parsing_gap(self, tmp_path):
        """El archivo decompilado ya no existe en disco (ej. carpeta
        limpiada/regenerada) -- esto es un problema de entorno, no lo mismo
        que "se leyo el archivo pero no se encontro el patron"."""
        settings = [_setting("CX", QCS25)]
        findings = [_finding(
            file="ArchivoQueNoExiste.cs",
            raw="using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))",
            resolved="UPDATE LCJob SET X=1 WHERE ID={idJob}",
        )]

        results = resolve_write_targets("App", settings, findings, tmp_path)

        assert results[0].resolution_status == "unresolved_no_source_file"
        assert results[0].candidates == ()


class TestAmbiguousConnectionGeneral:
    """Caso general (sin parentesis anidados) de conexion ambigua -- dos
    settings validos, la condicion no se evalua. Demuestra el estado
    "ambiguo" como concepto propio, no solo como efecto secundario del fix
    de regex de la clase anterior."""

    CS = """
public class Class1
{
    private string CX;
    private string CX2;

    public bool Graba(string idJob)
    {
        string connectionString = esPlanta2 ? CX2 : CX;
        using (SqlConnection sqlConnection = new SqlConnection(connectionString))
        {
            using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))
            {
                sqlCommand.ExecuteNonQuery();
            }
        }
        return true;
    }
}
"""

    def test_two_valid_settings_yield_ambiguous_not_a_guess(self, tmp_path):
        root = _write(tmp_path, "Class1.cs", self.CS)
        settings = [_setting("CX", QCS25), _setting("CX2", QCS12)]
        findings = [_finding(
            raw="using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))",
            resolved="UPDATE LCJob SET X=1 WHERE ID={idJob}",
        )]

        results = resolve_write_targets("App", settings, findings, root)

        assert results[0].resolution_status == "unresolved_ambiguous_conditional"
        assert len(results[0].candidates) == 2


class TestClassFieldFallback:
    """El ctor SI esta en el metodo que escribe, pero su argumento (`strConn`)
    es un campo de clase asignado en OTRO lado (su inicializador, fuera del
    cuerpo de este metodo) -- no resuelve a nivel de metodo. Se resuelve por
    el respaldo de archivo completo (unico setting de ese tipo referenciado
    via ConnectionStrings[...] en cualquier parte del archivo), con
    confianza SETTINGS_CLASS_LITERAL (menor que un ctor con argumento
    directamente resoluble)."""

    CS = """
public class Class1
{
    private string strConn = ConfigurationManager.ConnectionStrings["CX"].ConnectionString;

    public bool Graba(string idJob)
    {
        using (SqlConnection sqlConnection = new SqlConnection(strConn))
        {
            using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))
            {
                sqlCommand.ExecuteNonQuery();
            }
        }
        return true;
    }
}
"""

    def test_single_file_level_candidate_resolves_with_lower_confidence(self, tmp_path):
        root = _write(tmp_path, "Class1.cs", self.CS)
        settings = [_setting("CX", QCS25)]
        findings = [_finding(
            raw="using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))",
            resolved="UPDATE LCJob SET X=1 WHERE ID={idJob}",
        )]

        results = resolve_write_targets("App", settings, findings, root)

        assert results[0].resolution_status == "resolved"
        assert results[0].evidence.extractor == "SETTINGS_CLASS_LITERAL"
        assert results[0].evidence.confidence == confidence.CONFIDENCE_TABLE["SETTINGS_CLASS_LITERAL"] == 85


class TestOracleErpClassification:
    """Catalogo explicito de 29 tablas reales (create_xxafl_qapv_tests_tables.sql),
    NO una regla de patron de nombre -- pedido explicito del usuario
    (2026-08-18)."""

    def test_real_catalog_table_is_flagged_erp(self):
        assert is_oracle_erp_table("XXAFL_QAPV_TESTS_BINNA") is True

    def test_case_insensitive_match_still_works(self):
        assert is_oracle_erp_table("xxafl_qapv_tests_jdsu") is True

    def test_lookalike_name_not_in_the_real_catalog_is_not_flagged(self):
        """XXAFL_QAPV_TESTS_BINNA_202406 EXISTE de verdad en un servidor real
        (confirmado en la investigacion Huella de Datos) pero NO es una de
        las 29 tablas del DDL del ERP -- es una copia/particion local con un
        nombre parecido. Clasificarla como ERP por patron de nombre seria
        inventar un hecho que el DDL real no respalda."""
        assert is_oracle_erp_table("XXAFL_QAPV_TESTS_BINNA_202406") is False

    def test_unrelated_table_name_is_not_flagged(self):
        assert is_oracle_erp_table("LCJob") is False


class TestNoAttributionFromExternalInventory:
    """Regla arquitectonica explicita (2026-08-18): un servidor SOLO se
    atribuye con evidencia de CODIGO. Este modulo no tiene, en ningun lado,
    codigo que lea un inventario externo de servidores (CSV, diccionario,
    etc.) -- por construccion, no hay forma de que una tabla "exista en
    algun servidor real" filtre hacia una atribucion si el codigo de la app
    no la conecta ahi. Esta prueba demuestra el comportamiento: sin
    evidencia de codigo, el resultado es un estado sin resolver, nunca un
    servidor adivinado -- sin importar que tan seguros estemos, por otra
    via, de que la tabla existe fisicamente en algun lado."""

    CS = """
public class Class1
{
    public bool Graba(string idJob)
    {
        using (var conn = Helper.GetConnection())
        {
            using (SqlCommand sqlCommand = new SqlCommand(cmdText, conn))
            {
                sqlCommand.ExecuteNonQuery();
            }
        }
        return true;
    }
}
"""

    def test_table_with_zero_code_evidence_never_gets_a_server(self, tmp_path):
        root = _write(tmp_path, "Class1.cs", self.CS)
        # Ningun setting definido -- ninguna evidencia de codigo de conexion
        # en absoluto, exactamente el caso real de las tablas de InterConfig/
        # CopyJDSU que la investigacion encontro que existian fisicamente en
        # otro servidor segun un inventario externo, pero cuyo codigo nunca
        # las conecta ahi.
        settings = []
        findings = [_finding(
            target="XXAFL_QAPV_TESTS_JDSU",
            raw="using (SqlCommand sqlCommand = new SqlCommand(cmdText, conn))",
            resolved="UPDATE XXAFL_QAPV_TESTS_JDSU SET X=1 WHERE ID={idJob}",
        )]

        results = resolve_write_targets("App", settings, findings, root)

        # Sin settings, build_setting_lookup queda vacio y resolve_write_targets
        # corta temprano -- ni siquiera se produce una fila para este target.
        assert results == []

    def test_module_never_imports_any_external_inventory_reader(self):
        """Verificacion estructural: el modulo no importa csv/pandas ni
        ningun lector de un archivo de inventario externo -- la unica fuente
        de datos que toca es la BD (settings/sql_findings ya extraidos) y el
        codigo fuente ya decompilado en disco."""
        import inspect

        source = inspect.getsource(server_resolution)
        for forbidden in ("import csv", "import pandas", "server_inventory", "server_dictionary"):
            assert forbidden not in source


class TestResolveAppEndToEnd:
    """Integracion ligera del wrapper que si toca la BD (resolve_app), sobre
    una BD temporal -- mismo patron ya usado en test_lifecycle_persistence.py."""

    def test_resolve_app_reads_settings_and_sql_findings_from_the_real_db(self, tmp_path, monkeypatch):
        from analyzer.extract import SettingEntry, SqlFinding

        db_path = tmp_path / "test.db"
        monkeypatch.setattr(db, "DB_PATH", db_path)
        db.init_db()
        monkeypatch.setattr(server_resolution, "DECOMPILED_DIR", tmp_path / "decompiled")

        _write(tmp_path / "decompiled" / "App", "Class1.cs", TestFix1WriteVerbOnlyInResolvedNotRaw.CS)

        tech = TechStack(dotnet_target="net472", ui_framework=["WinForms"], db_drivers=["System.Data.SqlClient"])
        setting = SettingEntry(name="CX", default_value=QCS25, is_connection_string=True, category="sql_or_oracle", source_file="Settings.cs")
        finding = SqlFinding(
            file="Class1.cs", class_name="Class1", method="Graba", kind="SqlConnection",
            raw="using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))",
            resolved="UPDATE LCJob SET Linea='1' WHERE ID={idJob}",
            category="query", target="LCJob",
        )
        app_id = db.save_analysis("App", r"\\server\App.exe", tech, [setting], [finding], [], [])

        resolution = server_resolution.resolve_app(app_id)

        assert resolution is not None
        assert resolution.app_name == "App"
        assert len(resolution.targets) == 1
        assert resolution.targets[0].resolution_status == "resolved"
        assert resolution.targets[0].candidates[0].server == "NAAMRT-QCS25"


class TestEvidenceShape:
    def test_evidence_has_a_parseable_timestamp_and_analyzer_version(self, tmp_path):
        root = _write(tmp_path, "Class1.cs", TestFix1WriteVerbOnlyInResolvedNotRaw.CS)
        settings = [_setting("CX", QCS25)]
        findings = [_finding(
            raw="using (SqlCommand sqlCommand = new SqlCommand(cmdText, sqlConnection))",
            resolved="UPDATE LCJob SET Linea='1' WHERE ID={idJob}",
        )]

        results = resolve_write_targets("App", settings, findings, root)

        ev = results[0].evidence
        assert ev.source_file is None  # el "file" real vive en sql_findings.file, no duplicado aqui
        assert ev.line_number is not None
        assert "sqlConnection" in ev.snippet or "CX" in ev.snippet
        datetime.fromisoformat(ev.created_at)
