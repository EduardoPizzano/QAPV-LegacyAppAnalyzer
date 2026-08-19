"""Incremento Mapa de Flujo de Datos (2026-08-19): tests de
analyzer/data_flow.py -- clasificacion de Application+Table en Productor
numerico / Productor PASS-FAIL / Consumidor de medicion / Consumidor de
resultado / Mixto / Indeterminado, a partir de evidencia real de
operacion+columnas. Ninguna prueba requiere archivos en disco (el modulo
solo parsea texto SQL ya extraido + result_columns, no rastrea codigo
fuente) -- a diferencia de test_server_resolution.py."""

import json

from analyzer import confidence, data_flow
from analyzer.data_flow import resolve_data_flow


def _finding(target, raw="", resolved=None, result_columns=None, is_stored_procedure=False):
    return {
        "target": target,
        "raw": raw,
        "resolved": resolved,
        "result_columns": json.dumps(result_columns) if result_columns is not None else None,
        "is_stored_procedure": is_stored_procedure,
    }


class TestInsertWithMeasurementColumnIsProductorNumerico:
    """OTDR/OTDR real: INSERT into TESTS_OTDR_RES con columnas IL/ReflA/ReflB
    entre otras -- debe clasificar como productor numerico."""

    def test_insert_with_il_and_refl_columns(self):
        findings = [_finding(
            "TESTS_OTDR_RES",
            resolved=(
                "Insert into TESTS_OTDR_RES (SERIAL_NUMBER, ENDS, CONNECTOR, STATUS, TEST_DATE, "
                "WAVE, Fiber, IL, ReflA, ReflB, EMPLOYEE_NUMBER) values ({num},'{ends}','{text}',"
                "{text10},'{hoy}',{wLen},{fibra},{text2},{text8},{text9},'{text6}')"
            ),
        )]

        edges = resolve_data_flow("OTDR/OTDR", findings)

        assert len(edges) == 1
        edge = edges[0]
        assert edge.target == "TESTS_OTDR_RES"
        assert edge.role == "productor_numerico"
        assert edge.resolution_status == "resolved"
        assert edge.writes[0].operation == "insert"
        assert "IL" in edge.writes[0].columns


class TestInsertOnlyStatusAndMetadataIsProductorPassfail:
    """InterAFL/InterConfig real: UPDATE ... SET FILE_IMAGE=..., EnOracle=0,
    LAST_UPDATE_DATE=GETDATE() -- ninguna columna de medicion, solo
    metadata -- debe ser productor pass/fail, no numerico."""

    def test_update_with_only_metadata_columns(self):
        findings = [_finding(
            "XXAFL_QAPV_TESTS_SMART_CHECK",
            resolved="Update XXAFL_QAPV_TESTS_SMART_CHECK SET FILE_IMAGE='{text2}', EnOracle=0, LAST_UPDATE_DATE=GETDATE() WHERE TEST_SMARTCHECK_ID={id}",
        )]

        edges = resolve_data_flow("InterAFL/InterAFL", findings)

        assert edges[0].role == "productor_passfail"
        assert set(edges[0].writes[0].columns) == {"FILE_IMAGE", "EnOracle", "LAST_UPDATE_DATE"}


class TestMeasurementColumnDominatesOverStatus:
    """Regla explicita del usuario: un INSERT que trae IL/ReflA/ReflB Y
    STATUS sigue siendo productor numerico -- la presencia de UNA columna
    de medicion domina la clasificacion, aunque tambien escriba status."""

    def test_insert_with_measurement_and_status_is_still_numerico(self):
        findings = [_finding(
            "TESTS_OTDR_RES",
            resolved="Insert into TESTS_OTDR_RES (SERIAL_NUMBER, STATUS, IL, ReflA, ReflB) values ('{sn}',{st},{il},{ra},{rb})",
        )]

        edges = resolve_data_flow("OTDR/OTDR", findings)

        assert edges[0].role == "productor_numerico"
        assert "STATUS" in edges[0].writes[0].columns
        assert "IL" in edges[0].writes[0].columns


class TestSelectOfMeasurementColumnIsConsumidorMedicion:
    """IRLStatsInter real: SELECT ... ILEndA, ILEndB ... FROM TESTS_RL1_RES
    (via result_columns, ya poblado por extract.py) -- consumidor de
    medicion, no solo de resultado."""

    def test_select_ilenda_ilendb_via_result_columns(self):
        findings = [_finding(
            "TESTS_RL1_RES",
            raw='sqlCommand.CommandText = "SELECT Device, TEST_RL1_ID, SERIAL_NUMBER FROM TESTS_RL1_RES where ID=@id";',
            result_columns=["Device", "TEST_RL1_ID", "SERIAL_NUMBER", "TEST_DATE", "STATUS", "ILEndA", "ILEndB"],
        )]

        edges = resolve_data_flow("IRLStatsInter/IRLStatsInter", findings)

        assert edges[0].role == "consumidor_medicion"
        assert edges[0].reads[0].operation == "select"
        assert "ILEndA" in edges[0].reads[0].columns


class TestSelectOfStatusOnlyIsConsumidorResultado:
    """DataTransfer real: SELECT TOP 1 STATUS AS Result, TEST_DATE AS fecha,
    EMPLOYEE_NUMBER AS Operador FROM XXAFL_QAPV_TESTS_VIAVI -- STATUS esta
    en el catalogo de status/metadata -- consumidor de resultado."""

    def test_select_status_and_metadata_only(self):
        findings = [_finding(
            "XXAFL_QAPV_TESTS_VIAVI",
            resolved="SELECT TOP 1 STATUS AS Result, TEST_DATE AS fecha, EMPLOYEE_NUMBER AS Operador FROM XXAFL_QAPV_TESTS_VIAVI WITH(NOLOCK) where SERIAL_NUMBER='{sn}'",
        )]

        edges = resolve_data_flow("DataTransfer", findings)

        assert edges[0].role == "consumidor_resultado"
        assert set(edges[0].reads[0].columns) == {"STATUS", "TEST_DATE", "EMPLOYEE_NUMBER"}

    def test_select_passfail_only_is_also_consumidor_resultado(self):
        """PruebaBINNA real: SELECT ... PassFail ... -- mismo concepto que
        STATUS, nombre alterno, tambien en el catalogo."""
        findings = [_finding(
            "PruebaBINNA",
            resolved="SELECT TOP 1 Device, Fecha, Operator, PassFail FROM PruebaBINNA WHERE Serial='{sn}'",
        )]

        edges = resolve_data_flow("Polaridad/Release", findings)

        assert edges[0].role == "consumidor_resultado"

    def test_select_only_test_metadata_no_status_is_still_consumidor_resultado(self):
        """Metadata de prueba respaldada por catalogo (TEST_DATE) sin
        STATUS -- sigue siendo consumidor de resultado, no general."""
        findings = [_finding(
            "TESTS_OTDR_RES",
            resolved="SELECT TOP 1 TEST_DATE, EMPLOYEE_NUMBER FROM TESTS_OTDR_RES WHERE SERIAL_NUMBER='{sn}'",
        )]

        edges = resolve_data_flow("SomeApp", findings)

        assert edges[0].role == "consumidor_resultado"


class TestSelectOfUnrelatedColumnsIsConsumidorGeneral:
    """Regla corregida (2026-08-19): "SELECT + no medicion = consumidor de
    resultado" quedo ELIMINADA -- producia falsos positivos reales (78% de
    los 938 originales). Polaridad/Release real: SELECT PartNo/TYPES/digits
    FROM CustomItems (un catalogo de articulos, sin relacion con resultado
    de prueba) -- debe ser consumidor_general, nunca consumidor_resultado."""

    def test_select_partno_types_digits_from_customitems(self):
        findings = [_finding(
            "CustomItems",
            resolved="SELECT PartNo, TYPES, digits FROM CustomItems WHERE PartNo='{p}'",
        )]

        edges = resolve_data_flow("Polaridad/Release", findings)

        assert edges[0].role == "consumidor_general"

    def test_select_general_business_columns_from_djitem(self):
        """DJItem real: catalogo de articulos de cliente, sin relacion con
        resultado de prueba."""
        findings = [_finding(
            "DJItem",
            resolved="SELECT CUSTOMER_NAME, CUSTOMER_NUMBER, ITEM_NUMBER, JOB_START_QUANTITY FROM DJItem WHERE ITEM_NUMBER='{i}'",
        )]

        edges = resolve_data_flow("Polaridad/Release", findings)

        assert edges[0].role == "consumidor_general"
        assert set(edges[0].reads[0].columns) == {"CUSTOMER_NAME", "CUSTOMER_NUMBER", "ITEM_NUMBER", "JOB_START_QUANTITY"}


class TestEscapedWhitespaceNormalization:
    """Bug real encontrado en la auditoria (2026-08-19): algunos
    SqlFinding.raw/resolved traen \\r\\n LITERALMENTE escapado (2
    caracteres: backslash+r, no un salto de linea real) -- confirmado en
    INVENTA2-2TEST/InventaVales - rebuild -> ValeAutorizacionesDetalle, un
    UPDATE real que \\s+ no detectaba. La normalizacion debe ser general
    (aplicada en detect_operation y en las 3 funciones de extraccion), no
    un parche puntual."""

    def test_update_with_escaped_crlf_is_detected(self):
        text = (
            "UPDATE ValeAutorizacionesDetalle \\r\\n"
            "                                     SET Estado = @Estado, Autorizador = @Op\\r\\n"
            "                                     WHERE IDValeAutorizacion = @IdSolicitud"
        )

        assert data_flow.detect_operation(text) == "update"
        assert set(data_flow.extract_update_columns(text)) == {"Estado", "Autorizador"}

    def test_insert_with_escaped_crlf_is_detected(self):
        text = (
            "INSERT INTO BoxJobSO_RES (\\r\\n"
            "    BJSOId, Job, Line\\r\\n"
            ") values (1, '{job}', '{line}')"
        )

        assert data_flow.detect_operation(text) == "insert"
        assert data_flow.extract_insert_columns(text) == ["BJSOId", "Job", "Line"]

    def test_select_with_escaped_crlf_is_detected(self):
        text = (
            "SELECT \\r\\n"
            "    SERIAL_NUMBER,\\r\\n"
            "    STATUS\\r\\n"
            "FROM TESTS_OTDR_RES WHERE SERIAL_NUMBER='{sn}'"
        )

        assert data_flow.detect_operation(text) == "select"
        assert data_flow.extract_select_columns(text) == ["SERIAL_NUMBER", "STATUS"]


class TestReadColumnsDoNotLeakIntoWriteClassification:
    """Una columna de medicion (o de resultado) presente UNICAMENTE en un
    SELECT no debe convertir un INSERT/UPDATE de la misma app+tabla en
    productor -- writes y reads se clasifican sobre columnas de SU PROPIO
    lado, nunca mezcladas (salvo la etiqueta compuesta "mixto")."""

    def test_measurement_column_only_in_select_does_not_make_insert_numerico(self):
        findings = [
            _finding("SomeTable", resolved="SELECT IL FROM SomeTable WHERE ID={id}"),
            _finding("SomeTable", resolved="Insert into SomeTable (STATUS) values ({st})"),
        ]

        edges = resolve_data_flow("App", findings)

        assert edges[0].role == "mixto"
        assert edges[0].writes[0].columns == ("STATUS",)
        assert "IL" not in edges[0].writes[0].columns

    def test_status_column_only_in_select_does_not_make_write_passfail_falsely(self):
        """Aqui el escritor SI escribe algo (FILE_IMAGE, no catalogado) --
        confirma que su clasificacion como productor_passfail viene de SU
        PROPIA columna, no de que el SELECT (en otra operacion) trajera
        STATUS."""
        findings = [
            _finding("SomeTable", resolved="SELECT STATUS FROM SomeTable WHERE ID={id}"),
            _finding("SomeTable", resolved="Insert into SomeTable (FILE_IMAGE) values ({f})"),
        ]

        edges = resolve_data_flow("App", findings)

        assert edges[0].role == "mixto"
        assert edges[0].writes[0].columns == ("FILE_IMAGE",)
        assert "STATUS" not in edges[0].writes[0].columns


class TestMixtoPreservesBothOperations:
    """Misma Application+Table con evidencia real de lectura Y escritura --
    debe ser 'mixto', conservando AMBAS operaciones y sus columnas, nunca
    colapsado a una sola etiqueta."""

    def test_select_measurement_and_insert_measurement_is_mixto(self):
        findings = [
            _finding(
                "TESTS_OTDR_RES",
                raw='sqlCommand.CommandText = "SELECT ILEndA, ILEndB FROM TESTS_OTDR_RES where ID=@id";',
                result_columns=["ILEndA", "ILEndB"],
            ),
            _finding("TESTS_OTDR_RES", resolved="Insert into TESTS_OTDR_RES (IL, ReflA) values ({a},{b})"),
        ]

        edges = resolve_data_flow("App", findings)

        assert len(edges) == 1
        edge = edges[0]
        assert edge.role == "mixto"
        assert len(edge.reads) == 1 and len(edge.writes) == 1
        assert "ILEndA" in edge.reads[0].columns
        assert "IL" in edge.writes[0].columns

    def test_select_status_and_insert_metadata_is_also_mixto(self):
        """Mixto no requiere que ambos lados sean numericos -- basta con que
        haya evidencia real de lectura Y escritura sobre la misma tabla."""
        findings = [
            _finding("XXAFL_QAPV_TESTS_SMART_CHECK", resolved="SELECT STATUS FROM XXAFL_QAPV_TESTS_SMART_CHECK WHERE SERIAL_NUMBER='{sn}'"),
            _finding("XXAFL_QAPV_TESTS_SMART_CHECK", resolved="Update XXAFL_QAPV_TESTS_SMART_CHECK SET FILE_IMAGE='{f}' WHERE TEST_SMARTCHECK_ID={id}"),
        ]

        edges = resolve_data_flow("InterAFL/InterAFL", findings)

        assert edges[0].role == "mixto"
        assert edges[0].reads[0].columns == ("STATUS",)
        assert edges[0].writes[0].columns == ("FILE_IMAGE",)


class TestDynamicSqlUnresolvableIsIndeterminado:
    """SQL dinamico real (StringBuilder-style, sin lista de columnas
    literal reconstruible) -- debe quedar Indeterminado, nunca inventar
    columnas."""

    def test_insert_without_reconstructable_column_list_is_indeterminado(self):
        """El verbo (INSERT) y la tabla SI son visibles en el texto, pero la
        lista de columnas se arma dinamicamente (StringBuilder) y no es
        literal -- se sabe que hay una escritura, pero no se sabe que
        columnas toca. Distinto del caso "ningun verbo visible en
        absoluto", que ni siquiera produce una fila (mismo principio que
        WRITE_KEYWORDS en server_resolution.py: sin evidencia de operacion,
        no hay nada que clasificar)."""
        findings = [_finding(
            "TESTS_OTDR_RES",
            raw='sqlCommand.CommandText = "insert into TESTS_OTDR_RES " + columnsBuilder.ToString() + " values (" + valuesBuilder.ToString() + ")";',
            resolved=None,
        )]

        edges = resolve_data_flow("SomeApp", findings)

        assert edges[0].role == "indeterminado"
        assert edges[0].resolution_status == "unresolved_no_columns"

    def test_select_star_is_indeterminado_not_a_guessed_column_list(self):
        findings = [_finding(
            "TESTS_OTDR_RES",
            resolved="SELECT * FROM TESTS_OTDR_RES WHERE SERIAL_NUMBER='{sn}'",
        )]

        edges = resolve_data_flow("SomeApp", findings)

        assert edges[0].role == "indeterminado"
        assert edges[0].reads[0].columns == ()


class TestAppWritingMultipleTablesWithDifferentRoles:
    """Una misma app puede ser productor numerico en una tabla y productor
    pass/fail en otra -- cada (app, tabla) se clasifica de forma
    independiente."""

    def test_two_targets_two_different_roles(self):
        findings = [
            _finding("TESTS_OTDR_RES", resolved="Insert into TESTS_OTDR_RES (IL, ReflA) values ({a},{b})"),
            _finding("Centinela", resolved="Insert into Centinela (Descripcion, Terminado) values ('{d}',0)"),
        ]

        edges = resolve_data_flow("OTDR/OTDR", findings)
        by_target = {e.target: e.role for e in edges}

        assert by_target["TESTS_OTDR_RES"] == "productor_numerico"
        assert by_target["Centinela"] == "productor_passfail"


class TestTableWithMultipleProducerAndConsumerApps:
    """Se llama una vez por app (mismo patron que server_resolution.py) --
    una tabla con multiples apps productoras/consumidoras se refleja
    llamando resolve_data_flow() por separado para cada una; esta prueba
    confirma que cada llamada es independiente y no se contamina entre
    apps."""

    def test_producer_and_consumer_apps_classified_independently(self):
        """Nota (2026-08-19, incremento test_context): STATUS es una columna
        AMBIGUA del catalogo (ver auditoria Fase 8) -- ya no basta sola para
        consumidor_resultado sin test_context confirmado. IL (medicion real,
        escrita por OTDR/OTDR) confirma test_context(TESTS_OTDR_RES), que se
        agrega explicitamente aqui antes de clasificar a DataTransfer -- este
        es exactamente el escenario de dos apps validado en la Fase 8."""
        producer_findings = [_finding("TESTS_OTDR_RES", resolved="Insert into TESTS_OTDR_RES (IL) values ({a})")]
        consumer_findings = [_finding("TESTS_OTDR_RES", resolved="SELECT STATUS FROM TESTS_OTDR_RES WHERE SERIAL_NUMBER='{sn}'")]

        test_context_index = data_flow.build_test_context_index([
            ("OTDR/OTDR", producer_findings),
            ("DataTransfer", consumer_findings),
        ])

        producer_edges = resolve_data_flow("OTDR/OTDR", producer_findings, test_context_index=test_context_index)
        consumer_edges = resolve_data_flow("DataTransfer", consumer_findings, test_context_index=test_context_index)

        assert producer_edges[0].role == "productor_numerico"
        assert consumer_edges[0].role == "consumidor_resultado"


class TestCatalogExclusionsAreRespected:
    """ILPassLimit/RLPassLimit/WAVE son numericas por nombre pero
    EXCLUIDAS del catalogo (limites/parametros, no medicion real) -- un
    INSERT que solo las toca a ellas (mas metadata) debe ser pass/fail, NO
    numerico, aunque 'suenen' a medicion."""

    def test_insert_with_only_excluded_numeric_like_columns_is_passfail(self):
        findings = [_finding(
            "TESTS_OTDR_RES",
            resolved="Insert into TESTS_OTDR_RES (SERIAL_NUMBER, WAVE, ILPassLimit, RLPassLimit, STATUS) values ('{sn}',{w},{p1},{p2},{st})",
        )]

        edges = resolve_data_flow("SomeApp", findings)

        assert edges[0].role == "productor_passfail"
        assert not data_flow.is_measurement_column("WAVE")
        assert not data_flow.is_measurement_column("ILPassLimit")
        assert not data_flow.is_measurement_column("RLPassLimit")

    def test_keyerror_is_not_in_the_catalog_yet(self):
        """Aprobado explicitamente: keyerror queda fuera hasta tener
        evidencia adicional de tipo de dato, aunque aparece junto a
        columnas de medicion reales en el mismo INSERT."""
        assert not data_flow.is_measurement_column("keyerror")


class TestDeleteIsStructuralNotIndeterminate:
    """DELETE no tiene lista de columnas por naturaleza (borra filas
    completas) -- eso es distinto de "no se pudo reconstruir" (SQL
    dinamico). Un DELETE puro se clasifica como productor pass/fail, no
    indeterminado -- OTDR/OTDR real: 'delete From OTDR_Proceso where
    ID={leID}'."""

    def test_delete_only_is_productor_passfail_not_indeterminado(self):
        findings = [_finding("OTDR_Proceso", resolved="delete From OTDR_Proceso where ID={leID}")]

        edges = resolve_data_flow("OTDR/OTDR", findings)

        assert edges[0].role == "productor_passfail"
        assert edges[0].writes[0].operation == "delete"
        assert edges[0].writes[0].columns == ()


class TestResultColumnsPreferredOverRegexFallback:
    def test_result_columns_used_when_present_even_if_regex_would_differ(self):
        findings = [_finding(
            "TESTS_RL1_RES",
            resolved="SELECT STATUS, TEST_DATE FROM TESTS_RL1_RES WHERE SERIAL_NUMBER='{sn}'",
            result_columns=["STATUS", "ILEndA"],
        )]

        edges = resolve_data_flow("App", findings)

        # Si se hubiera usado el regex sobre `resolved` (STATUS, TEST_DATE)
        # el resultado seria "consumidor_resultado" -- pero result_columns
        # (la fuente preferida) trae ILEndA, asi que debe ganar medicion.
        assert edges[0].role == "consumidor_medicion"
        assert edges[0].reads[0].evidence.extractor == "DATA_ROLE_COLUMNS_FROM_RESULT_COLUMNS"
        assert confidence.CONFIDENCE_TABLE["DATA_ROLE_COLUMNS_FROM_RESULT_COLUMNS"] == 90


class TestJoinAliasNormalization:
    """TestValidation/TestValidation real: SELECT g.SERIAL_NUMBER, g.ENDS,
    g.CONNECTOR ... -- el alias de tabla debe quitarse antes de comparar
    contra el catalogo."""

    def test_table_alias_prefix_is_stripped(self):
        """Nota (2026-08-19, incremento test_context): SERIAL_NUMBER/ENDS/
        STATUS son columnas AMBIGUAS del catalogo -- se agrega aqui el
        test_context real de XXAFL_QAPV_TESTS_SMART_CHECK (TEST_DATE
        confirmado, evidencia real del portafolio) para que el foco de esta
        prueba (normalizacion de alias) siga aislado del comportamiento de
        test_context, ya cubierto por TestTestContextIndex."""
        findings = [_finding(
            "XXAFL_QAPV_TESTS_SMART_CHECK",
            resolved="SELECT g.SERIAL_NUMBER, g.ENDS, g.STATUS FROM XXAFL_QAPV_TESTS_SMART_CHECK g WHERE g.PROCESADO=0",
        )]
        test_context_index = {"XXAFL_QAPV_TESTS_SMART_CHECK": data_flow.TEST_CONTEXT_CONFIRMADO}

        edges = resolve_data_flow("TestValidation/TestValidation", findings, test_context_index=test_context_index)

        assert set(edges[0].reads[0].columns) == {"SERIAL_NUMBER", "ENDS", "STATUS"}
        assert edges[0].role == "consumidor_resultado"

    def test_alias_prefixed_measurement_column_still_matches_catalog(self):
        findings = [_finding(
            "TESTS_OTDR_RES",
            resolved="SELECT g.SERIAL_NUMBER, g.IL FROM TESTS_OTDR_RES g WHERE g.PROCESADO=0",
        )]

        edges = resolve_data_flow("App", findings)

        assert edges[0].role == "consumidor_medicion"


class TestTestContextIndex:
    """Incremento Fase 8 (2026-08-19): test_context(tabla) agrega evidencia
    de TODAS las apps del portafolio (reads+writes, solo columnas
    efectivamente reconstruidas) para decidir si una columna AMBIGUA del
    catalogo (STATUS/DEVICE/CONNECTOR/ENDS/SERIAL_NUMBER/EMPLOYEE_NUMBER/
    WIP_ENTITY_NAME) sustenta consumidor_resultado, o si coincide por
    nombre con una tabla de negocio no relacionada (DJItem/Employees, ver
    auditoria previa: ~95 edges reales que quedaron mal clasificados con
    la regla anterior)."""

    def test_cross_app_strong_signal_confirms_ambiguous_read(self):
        """Escenario obligatorio #1: App A escribe con señal fuerte
        (medicion real IL), App B lee solo una columna ambigua (STATUS) de
        la MISMA tabla -- debe confirmar consumidor_resultado en B."""
        producer_findings = [_finding("TESTS_OTDR_RES", resolved="Insert into TESTS_OTDR_RES (IL) values ({a})")]
        consumer_findings = [_finding("TESTS_OTDR_RES", resolved="SELECT STATUS FROM TESTS_OTDR_RES WHERE SERIAL_NUMBER='{sn}'")]

        index = data_flow.build_test_context_index([
            ("OTDR/OTDR", producer_findings),
            ("DataTransfer", consumer_findings),
        ])

        assert index["TESTS_OTDR_RES"] == data_flow.TEST_CONTEXT_CONFIRMADO

        producer_edges = resolve_data_flow("OTDR/OTDR", producer_findings, test_context_index=index)
        consumer_edges = resolve_data_flow("DataTransfer", consumer_findings, test_context_index=index)

        assert producer_edges[0].role == "productor_numerico"
        assert consumer_edges[0].role == "consumidor_resultado"

    def test_select_star_with_result_columns_recognizes_strong_signal(self):
        """Escenario obligatorio #2: GEOMETRIASTATS real -- SQL es
        'SELECT * FROM GeometriaStats ...' (sin lista de columnas
        reconstruible por texto), pero result_columns (reader["X"] real)
        trae TEST_DATE -- test_context debe reconocerla igual que si
        viniera de la lista literal de SELECT."""
        findings = [_finding(
            "GeometriaStats",
            raw='sqlCommand.CommandText = "SELECT * FROM GeometriaStats WHERE Fecha BETWEEN @fs AND @fe";',
            result_columns=["Device", "TEST_DATE", "PassPercent"],
        )]

        index = data_flow.build_test_context_index([("GeoStatsConsultas/GeoStatsConsultas", findings)])

        assert index["GEOMETRIASTATS"] == data_flow.TEST_CONTEXT_CONFIRMADO

    def test_table_with_single_weak_column_is_desconocido(self):
        """Escenario obligatorio #3: DEVICESBINNA real -- 'SELECT * FROM
        DevicesBinna', result_columns solo aporta 'Device' (columna
        ambigua, no fuerte) -- sin ninguna otra señal en todo el
        portafolio, debe quedar desconocido, nunca forzado."""
        findings = [_finding(
            "DevicesBinna",
            raw='sqlCommand.CommandText = "SELECT * FROM DevicesBinna";',
            result_columns=["Device"],
        )]

        index = data_flow.build_test_context_index([("GeoStats/GeoStats", findings)])

        assert index.get("DEVICESBINNA", data_flow.TEST_CONTEXT_DESCONOCIDO) == data_flow.TEST_CONTEXT_DESCONOCIDO

    def test_ambiguous_column_without_confirmed_context_is_consumidor_general(self):
        """Escenario obligatorio #4: DJItem real -- WIP_ENTITY_NAME es
        columna ambigua del catalogo, pero DJItem nunca tiene señal fuerte
        en ninguna app del portafolio -- debe ser consumidor_general,
        NUNCA indeterminado (hay columnas reales conocidas)."""
        findings = [_finding(
            "DJItem",
            resolved="SELECT CUSTOMER_NAME, CUSTOMER_NUMBER, WIP_ENTITY_NAME FROM DJItem WHERE ITEM_NUMBER='{i}'",
        )]

        edges = resolve_data_flow("Polaridad/Release", findings)  # sin test_context_index

        assert edges[0].role == "consumidor_general"
        assert edges[0].resolution_status == "resolved"

    def test_ambiguous_column_with_confirmed_context_is_consumidor_resultado(self):
        """Escenario obligatorio #5: misma columna ambigua (DEVICE), pero
        con test_context confirmado explicitamente -- debe ser
        consumidor_resultado."""
        findings = [_finding(
            "XXAFL_QAPV_CUST_SERIALS",
            resolved="SELECT DEVICE FROM XXAFL_QAPV_CUST_SERIALS WHERE SERIAL_NUMBER='{sn}'",
        )]
        test_context_index = {"XXAFL_QAPV_CUST_SERIALS": data_flow.TEST_CONTEXT_CONFIRMADO}

        edges = resolve_data_flow("NewVaLabel/NewVaLabel", findings, test_context_index=test_context_index)

        assert edges[0].role == "consumidor_resultado"

    def test_strong_signal_in_read_is_sufficient_without_confirmed_context(self):
        """Escenario obligatorio #6: TEST_DATE (señal fuerte) presente
        directamente en la lectura debe bastar por si sola, incluso sin
        ningun test_context_index disponible (tabla nunca vista antes en
        el portafolio agregado)."""
        findings = [_finding(
            "BrandNewTestTable",
            resolved="SELECT TEST_DATE FROM BrandNewTestTable WHERE ID={id}",
        )]

        edges = resolve_data_flow("SomeApp", findings)  # sin test_context_index

        assert edges[0].role == "consumidor_resultado"

    def test_stored_procedure_rows_do_not_feed_test_context_index(self):
        """Escenario obligatorio #7a: una fila is_stored_procedure=1 con
        señales fuertes en su texto NO debe aportar evidencia -- un
        procedimiento no tiene "columnas" de tabla en el mismo sentido."""
        findings = [_finding(
            "SomeTable",
            resolved="SELECT TEST_DATE, DEVICE_STATUS FROM SomeTable",
            is_stored_procedure=True,
        )]

        index = data_flow.build_test_context_index([("App", findings)])

        assert "SOMETABLE" not in index

    def test_code_like_target_does_not_feed_test_context_index(self):
        """Escenario obligatorio #7b: target que es en realidad una
        variable/declaracion de codigo C# capturada por error (bug real,
        ver auditoria Fase 7) no debe aportar evidencia de ninguna tabla."""
        findings = [_finding(
            "using SqlCommand sqlCommand = new SqlCommand();",
            resolved="SELECT TEST_DATE FROM RealTable",
        )]

        index = data_flow.build_test_context_index([("App", findings)])

        assert len(index) == 0
        assert data_flow.normalize_table_key("using SqlCommand sqlCommand = new SqlCommand();") is None

    def test_reads_and_writes_are_both_aggregated(self):
        """Escenario obligatorio #8: la union debe incluir columnas tanto de
        escrituras como de lecturas -- verificado explicitamente sobre un
        indice construido con ambas."""
        findings = [
            _finding("MixedTable", resolved="Insert into MixedTable (TEST_DATE) values ({d})"),
            _finding("MixedTable", resolved="SELECT DEVICE FROM MixedTable WHERE ID={id}"),
        ]

        index = data_flow.build_test_context_index([("App", findings)])

        assert index["MIXEDTABLE"] == data_flow.TEST_CONTEXT_CONFIRMADO

    def test_schema_prefix_normalization_shares_profile(self):
        """Escenario obligatorio #9: 'dbo.TWaveLength' y 'TWaveLength' deben
        compartir el mismo perfil agregado de test_context."""
        findings_a = [_finding("dbo.TWaveLength", resolved="Insert into dbo.TWaveLength (TEST_DATE) values ({d})")]
        findings_b = [_finding("TWaveLength", resolved="SELECT STATUS FROM TWaveLength WHERE ID={id}")]

        index = data_flow.build_test_context_index([("AppA", findings_a), ("AppB", findings_b)])

        assert index["TWAVELENGTH"] == data_flow.TEST_CONTEXT_CONFIRMADO

        edges_b = resolve_data_flow("AppB", findings_b, test_context_index=index)
        assert edges_b[0].role == "consumidor_resultado"

    def test_misleading_table_name_without_strong_signal_stays_desconocido(self):
        """Escenario obligatorio #10: DJITEM_PRUEBAS real -- el nombre
        contiene literalmente "PRUEBAS" pero su contenido es 100% catalogo
        ERP (CUSTOMER_NAME/CUSTOMER_NUMBER/ITEM_NUMBER/WIP_ENTITY_NAME),
        sin ninguna señal fuerte -- debe permanecer desconocido a pesar del
        nombre, y su lectura debe ser consumidor_general."""
        findings = [_finding(
            "DJITEM_PRUEBAS",
            resolved="SELECT CUSTOMER_NAME, CUSTOMER_NUMBER, ITEM_NUMBER, WIP_ENTITY_NAME FROM DJITEM_PRUEBAS WHERE ITEM_NUMBER='{i}'",
        )]

        index = data_flow.build_test_context_index([("FaceLabUnion/FaceLabUnion", findings)])

        assert index.get("DJITEM_PRUEBAS", data_flow.TEST_CONTEXT_DESCONOCIDO) == data_flow.TEST_CONTEXT_DESCONOCIDO

        edges = resolve_data_flow("FaceLabUnion/FaceLabUnion", findings, test_context_index=index)
        assert edges[0].role == "consumidor_general"
