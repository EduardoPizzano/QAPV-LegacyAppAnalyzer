"""Tests de caracterizacion (Fase 0 de IMPLEMENTATION_PLAN.md).

Estos tests NO afirman que el comportamiento actual sea correcto -- afirman
que es el que hoy existe, verificado a mano contra cada app real. Sirven de
ancla: si una fase futura (1-4) cambia uno de estos resultados SIN que sea
el objetivo explicito de esa fase, esto se rompe y avisa de una regresion.

Cuando una fase futura SI cierra uno de los gaps aqui documentados (ver
comentarios "GAP CONOCIDO"), la asercion correspondiente se actualiza a
proposito en el mismo commit que cierra el gap -- nunca se actualiza sola.
"""

from analyzer.enrich import _short_error
from analyzer.extract import SqlFinding, find_settings, scan_project
from analyzer.report import _rows_for_method


def _settings_by_name(settings):
    return {s.name: s for s in settings}


class TestReportViewerConnectionDiscovery:
    """Bug real ya corregido (2026-08): la conexion de ReportViewer solo vive
    en app.config's <connectionStrings>, nunca en Settings.cs. Este test es
    la regresion permanente de ESE fix especifico."""

    def test_connection_found_in_appconfig(self, fixture_root, load_golden):
        settings = find_settings(fixture_root("reportviewer"))
        golden = load_golden("reportviewer")
        by_name = _settings_by_name(settings)

        assert len(settings) == len(golden["settings"]) == 1
        conn = by_name["QAPVMLN"]
        assert conn.category == "sql_or_oracle"
        assert conn.is_connection_string is True
        assert "NAAMRT-QCS25" in conn.default_value
        assert conn.source_file == "app.config"


class TestInterConfigConnectionDiscovery:
    def test_connection_found_in_appconfig(self, fixture_root, load_golden):
        settings = find_settings(fixture_root("interconfig"))
        assert len(settings) == 1
        assert settings[0].name == "CX"
        assert settings[0].category == "sql_or_oracle"


class TestInterAflConnectionDiscovery:
    """7 conexiones reales (revelaron el servidor NAAMRT-QCS10, nunca antes
    documentado) + confirma que las entradas COMENTADAS en app.config (con
    credenciales viejas 'sa7') se ignoran correctamente."""

    def test_seven_real_connections_found(self, fixture_root):
        settings = find_settings(fixture_root("interafl"))
        assert len(settings) == 7
        names = {s.name for s in settings}
        assert names == {"connectionString", "CX", "CXEXFO", "CXAFLPrueba", "CXAFL", "CXNORLAND", "CXEXFO2"}

    def test_commented_out_dev_server_entries_never_appear(self, fixture_root):
        """El fixture tiene un bloque comentado de 6 entradas viejas (CX,
        CXEXFO, CXAFLPrueba, CXAFL, CXNORLAND, CXEXFO2) apuntando al servidor
        de desarrollo AST-PB1\\MSSQLSERVER01 -- ninguna debe aparecer.
        Nota: la entrada 'connectionString' SI es real/activa (no comentada)
        y legitimamente apunta a AST-PB1 con la misma credencial 'sa7' -- no
        se puede usar 'sa7' como señal por si sola, hay que verificar contra
        el servidor de desarrollo comentado especificamente."""
        settings = find_settings(fixture_root("interafl"))
        by_name = _settings_by_name(settings)
        commented_only_names = {"CX", "CXEXFO", "CXAFLPrueba", "CXAFL", "CXNORLAND", "CXEXFO2"}
        for name in commented_only_names:
            assert "AST-PB1" not in by_name[name].default_value, (
                f"'{name}' resolvio al valor comentado de AST-PB1 en vez del valor activo -- "
                "el parser de app.config dejo de ignorar comentarios XML."
            )

    def test_active_dev_entry_is_not_confused_with_commented_ones(self, fixture_root):
        """La unica entrada real que legitimamente usa 'sa7' es 'connectionString'
        (no comentada) -- confirma que no se está filtrando de mas."""
        settings = find_settings(fixture_root("interafl"))
        by_name = _settings_by_name(settings)
        assert "AST-PB1" in by_name["connectionString"].default_value


class TestSgiStringBuilderGap:
    """GAP CONOCIDO (KNOWN_LIMITATIONS.md L8): el INSERT/DELETE real sobre
    ValeRH/ValePartes/ValesHistorico se arma con StringBuilder y hoy queda
    con target=None. Cuando la Fase 3 cierre este gap, esta asercion se
    invierte a proposito (ver test marcado mas abajo)."""

    def test_stringbuilder_query_today_has_no_target(self, fixture_root):
        sql_findings, _ = scan_project(fixture_root("sgi"))
        sb_findings = [f for f in sql_findings if "stringBuilder" in f.raw or "StringBuilder" in f.raw]
        assert sb_findings, "El fixture dejo de contener el patron StringBuilder esperado"
        for f in sb_findings:
            assert f.target is None, (
                "Un finding armado con StringBuilder ya tiene 'target' resuelto -- "
                "si esto es intencional (Fase 3 completada), actualizar este test "
                "a proposito, no dejar que pase por accidente."
            )

    def test_non_stringbuilder_query_in_same_method_resolves_fine(self, fixture_root):
        """El primer INSERT (literal directo, sin StringBuilder) de la MISMA
        app/metodo SI se resuelve hoy -- confirma que el gap es especifico de
        StringBuilder, no un problema general de esa app."""
        sql_findings, _ = scan_project(fixture_root("sgi"))
        literal_findings = [f for f in sql_findings if f.target == "ValesHistorico"]
        assert literal_findings, "El INSERT literal directo (sin StringBuilder) dejo de resolverse"


class TestDataTransferStringBuilderGap:
    """GAP CERRADO por el Incremento Funcional 3A (VALIDATION_STRATEGY.md):
    este caso especifico es un StringBuilder LINEAL (una sola llamada
    .Append(), sin if/else/for/while de por medio entre la declaracion y el
    .ToString()) -- se reconstruye completo. Ver KNOWN_LIMITATIONS.md L8
    para el caso que SIGUE sin resolver (sgi/SurtirVM.cs: StringBuilder con
    ramificacion, ver TestSgiStringBuilderGap abajo)."""

    def test_stringbuilder_query_now_resolves_with_target(self, fixture_root):
        sql_findings, _ = scan_project(fixture_root("datatransfer"))
        sb_findings = [f for f in sql_findings if "stringBuilder" in f.raw or "StringBuilder" in f.raw]
        assert sb_findings
        for f in sb_findings:
            assert f.target == "XXAFL_QAPV_REWORKS_PRUEBA"
            assert f.resolved is not None
            assert "{JOB_IDAnt}" in f.resolved and "{text2}" in f.resolved, (
                "Los segmentos dinamicos (variables C# reales, no literales) deben quedar "
                "marcados entre llaves -- nunca se inventa un valor para ellos."
            )
            assert f.evidence.extractor == "PARTIAL_RECONSTRUCTION"
            assert f.evidence.pattern == "STRINGBUILDER_APPEND"


class TestDataTransferReflectionGap:
    """GAP CONOCIDO (KNOWN_LIMITATIONS.md L16): PrintReportViewer.cs invoca
    miembros NO PUBLICOS de Microsoft.Reporting.WinForms.ReportViewer via
    MethodInfo.Invoke/Activator.CreateInstance -- el archivo entero no
    dispara ni SQL_TRIGGER ni LOCAL_IO_TRIGGER, asi que scan_project() lo
    salta por completo (ni siquiera lo abre a fondo). Cero rastro de que la
    app depende de una API no documentada de un ensamblado de terceros."""

    def test_reflection_file_produces_zero_findings_today(self, fixture_root):
        sql_findings, io_findings = scan_project(fixture_root("datatransfer"))
        reflection_file_findings = [
            f for f in (sql_findings + io_findings) if "PrintReportViewer.cs" in f.file
        ]
        assert reflection_file_findings == [], (
            "PrintReportViewer.cs ya genera algun finding -- si esto es intencional "
            "(Fase 4 completada), actualizar este test a proposito con la aserccion "
            "real esperada (categoria 'reflection', 7 invocaciones vistas: OnPrint, "
            "DoesStateAllowPrinting, CreateEMFDeviceInfo, etc.)."
        )


class TestAlmacenDiagnosticoClassFieldGap:
    """GAP CONOCIDO (KNOWN_LIMITATIONS.md L1): connection string con
    credencial de produccion en texto plano, declarada como campo de clase
    (fuera de cualquier metodo). Hoy find_settings() no la ve en absoluto."""

    def test_class_field_connection_string_not_found_today(self, fixture_root):
        settings = find_settings(fixture_root("almacendiagnostico"))
        assert settings == [], (
            "find_settings() ya encuentra la connection string de campo de clase -- "
            "si esto es intencional (Fase 2 completada), actualizar este test a "
            "proposito y agregar la aserccion de seguridad correspondiente "
            "(security.check_settings debe marcar severidad 'alta')."
        )

    def test_sql_findings_still_reference_the_unresolved_connection(self, fixture_root):
        """Aunque la connection string en si no se vea, el uso de SqlConnection
        SI se detecta -- confirma que el gap es especificamente de settings,
        no de sql_findings."""
        sql_findings, _ = scan_project(fixture_root("almacendiagnostico"))
        assert any(f.kind == "SqlConnection" for f in sql_findings)


class TestVins1ModbusGap:
    """GAP CONOCIDO (KNOWN_LIMITATIONS.md L18): segunda integracion PLC/Modbus
    real del portafolio (la primera, MonTemp2, solo se documento a mano).
    LOCAL_IO_TRIGGER no reconoce ModbusClient/EasyModbus -- hoy esto no
    genera NINGUN finding, ni de SQL ni de I/O."""

    def test_modbus_integration_produces_zero_findings_today(self, fixture_root):
        sql_findings, io_findings = scan_project(fixture_root("vins1_modbus"))
        assert sql_findings == []
        assert io_findings == [], (
            "El patron ModbusClient/EasyModbus ya genera un io_finding -- "
            "si esto es intencional (Fase 4 completada), actualizar este test "
            "a proposito con la aserccion real esperada (operation='ModbusClient', "
            "raw conteniendo '192.168.1.5')."
        )


# ---------------------------------------------------------------------------
# Deuda de Fase 0 resuelta en Fase 1 (ver revision de arquitectura): fixture
# camino-feliz, deduplicacion por valor, y caracterizacion de los 2 puntos
# que originaron toda la auditoria (mensajes ambiguos de enrich.py/report.py).
# ---------------------------------------------------------------------------

class TestHappyPathBaseline:
    """Fixture de control POSITIVO -- app simple, Settings.cs con una
    conexion valida, un query literal completamente resoluble, sin ningun
    gap conocido. Objetivo: demostrar que las fases futuras (que solo tocan
    los casos-gap) no rompen el ~90% del portafolio que ya funciona bien.
    No se espera CERO ruido (el propio trigger de SqlConnection/declaracion
    de SqlCommand no resuelve a un literal, igual que en cualquier app real
    del portafolio) -- se espera que la query real SI resuelva completa."""

    def test_connection_resolves_cleanly(self, fixture_root):
        settings = find_settings(fixture_root("happy_path"))
        assert len(settings) == 1
        assert settings[0].name == "CX"
        assert settings[0].category == "sql_or_oracle"
        assert "NAAMRT-QCS25" in settings[0].default_value

    def test_query_resolves_completely_with_target(self, fixture_root):
        sql_findings, _ = scan_project(fixture_root("happy_path"))
        resolved = [f for f in sql_findings if f.target == "DJItem"]
        assert len(resolved) == 1
        finding = resolved[0]
        assert finding.category == "query"
        assert finding.resolved is not None
        assert "SELECT JobId, PartNo FROM DJItem" in finding.resolved

    def test_report_renders_the_real_query_not_the_generic_message(self, fixture_root):
        """Extremo a extremo: el reporte final debe mostrar la query real,
        nunca el mensaje generico de report.py -- confirma que el camino
        feliz llega intacto hasta la capa de presentacion."""
        sql_findings, _ = scan_project(fixture_root("happy_path"))
        by_method = {}
        for f in sql_findings:
            by_method.setdefault((f.class_name, f.method), []).append(f)
        rows = []
        for group in by_method.values():
            rows.extend(_rows_for_method(group))
        rendered_texts = [row[0] for row in rows]
        assert any("SELECT JobId, PartNo FROM DJItem" in t for t in rendered_texts)
        assert not any("no resuelta automaticamente" in t for t in rendered_texts)


class TestDeduplicationByValue:
    """Deuda #3 de la revision de Fase 0: el mismo valor de connection string
    declarado en Settings.cs Y en app.config (bajo un nombre distinto, patron
    real ya visto en InterAFL -> DataTransfer.Properties.Settings.CX) debe
    deduplicarse por VALOR -- find_settings() no debe reportar la misma
    conexion real dos veces."""

    def test_same_value_under_different_names_yields_one_entry(self, fixture_root):
        settings = find_settings(fixture_root("dedup_case"))
        assert len(settings) == 1, (
            f"Se esperaba 1 entrada (deduplicada por valor), se encontraron "
            f"{len(settings)}: {[s.name for s in settings]}"
        )

    def test_the_surviving_entry_comes_from_settings_cs(self, fixture_root):
        """Cuando el mismo valor existe en ambos mecanismos, hoy sobrevive la
        entrada de Settings.cs (se procesa primero en find_settings()) -- se
        documenta el orden actual explicitamente, no se asume."""
        settings = find_settings(fixture_root("dedup_case"))
        assert settings[0].source_file.endswith("Settings.cs")


class TestEnrichGenericConnectionErrorMessage:
    """GAP CONOCIDO (KNOWN_LIMITATIONS.md L21): _short_error() de enrich.py
    colapsa CUALQUIER excepcion de pyodbc (salvo el servidor curado
    naamrt-qcs11) en uno de exactamente 2 mensajes -- nunca distingue
    DNS/timeout/autenticacion/permisos. Esta es la foto de "antes" que el
    Failure Cause Catalog reemplazara -- el objetivo NO es mantener este
    comportamiento, es tener evidencia exacta de que existe hoy."""

    def test_sqlstate_error_produces_the_current_sqlstate_message(self):
        # Forma tipica de un error real de pyodbc: ('SQLSTATE', '[mensaje...]')
        fake_pyodbc_error = Exception("('08001', '[08001] [Microsoft][ODBC Driver 17 for SQL Server]TCP Provider: No se pudo abrir una conexion')")
        message = _short_error(fake_pyodbc_error)
        assert message == "no se pudo conectar (SQLSTATE 08001) — revisar con infraestructura/DBA", (
            "El mensaje cambio -- si esto es intencional (Failure Cause Catalog "
            "conectado), actualizar este test a proposito con la aserccion real "
            "esperada (ej. reason_code='SERVER_UNAVAILABLE' o 'DNS_NOT_RESOLVED')."
        )

    def test_generic_error_produces_the_current_generic_message(self):
        # Cualquier excepcion que no tenga la forma "('SQLSTATE', ...)" de pyodbc
        fake_generic_error = Exception("Connection timed out after 10 seconds")
        message = _short_error(fake_generic_error)
        assert message == "no se pudo conectar — revisar con infraestructura/DBA", (
            "El mensaje generico cambio -- si esto es intencional (Failure Cause "
            "Catalog conectado), actualizar este test a proposito. Este mensaje "
            "NUNCA distingue timeout/DNS/auth/permisos hoy -- es exactamente el "
            "gap que origino toda esta auditoria."
        )

    def test_two_different_real_causes_produce_indistinguishable_messages(self):
        """La prueba mas directa del gap: un timeout de red y un login
        invalido (dos causas completamente distintas) producen HOY el
        mismo texto -- no hay forma de diferenciarlos leyendo el reporte."""
        timeout_error = Exception("Timeout expired while connecting to the server")
        login_error = Exception("Login failed for user 'quality'")
        assert _short_error(timeout_error) == _short_error(login_error), (
            "Timeout y login-fallido ya producen mensajes distintos -- si esto "
            "es intencional (Failure Cause Catalog conectado), esta aserccion "
            "debe invertirse a proposito (assert !=), no queda como esta."
        )


class TestReportGenericQueryMessage:
    """GAP CONOCIDO (KNOWN_LIMITATIONS.md L22): cuando ningun SqlFinding de un
    metodo tiene un literal resoluble (ej. SQL armado con StringBuilder),
    report.py._rows_for_method produce el mismo mensaje generico sin importar
    la causa real. Foto de "antes" del Failure Cause Catalog."""

    def test_unresolved_group_produces_the_current_generic_message(self):
        unresolved_finding = SqlFinding(
            file="Example.cs", class_name="Example", method="Deshacer",
            kind="SqlCommand", raw="sqlCommand2.CommandText = stringBuilder.ToString();",
            resolved=None, category="query", target=None,
        )
        rows = list(_rows_for_method([unresolved_finding]))
        assert len(rows) == 1
        text = rows[0][0]
        assert text == "(conexion detectada, query no resuelta automaticamente — revisar manualmente)", (
            "El mensaje genereico cambio -- si esto es intencional (causa "
            "especifica conectada, KNOWN_LIMITATIONS.md L22 cerrada), "
            "actualizar este test a proposito con el mensaje real esperado "
            "(ej. 'SQL armado con StringBuilder, no capturado')."
        )
