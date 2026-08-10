# Estrategia de Pruebas Automatizadas

**Punto de partida**: el proyecto no tiene ningún test hoy (`KNOWN_LIMITATIONS.md` L26). Esta estrategia no es "agregar cobertura de tests en general" — es específicamente la red de seguridad que `ARCHITECTURE_REVIEW.md` exige antes de tocar `extract.py`/`enrich.py`, construida sobre **evidencia real ya verificada**, no sobre C# sintético inventado para la ocasión.

---

## 1. Infraestructura

```
tests/
  conftest.py                 # fixtures compartidos (paths, mocks de pyodbc)
  fixtures/
    reportviewer/
      app.config
      ReportViewer.ViewModel/MainVM.cs
    interconfig/
      app.config
    interafl/
      app.config
      InterAFL.ViewModel/ProcVM.cs
    sgi/
      app.config
      SGI.ViewModel.Transacciones/SurtirVM.cs
    datatransfer/
      DataTransfer.cs                    # solo las lineas relevantes + contexto suficiente para que el parser de metodo/clase funcione
      PrintReportViewer.cs
    almacendiagnostico/
      Program.cs
    vins1_modbus/
      Modbus/Form1.cs
    golden/
      reportviewer.json
      interconfig.json
      interafl.json
      sgi.json
      datatransfer_stringbuilder.json
      datatransfer_reflection.json
      almacendiagnostico.json
      vins1_modbus.json
  test_extract_connections.py       # Fase 1-2: find_settings, app.config, campo de clase
  test_extract_sql.py                # Fase 2-3: classify_sql, resolution_status, StringBuilder
  test_extract_reflection.py         # Fase 4: reflection/COM/Modbus
  test_confidence.py                 # Fase 1: CONFIDENCE_TABLE
  test_failure_catalog.py            # Fase 1-2: mapeo SQLSTATE -> reason_code
  test_enrich_mocked.py              # Fase 2: enrich.py con pyodbc mockeado
  test_coverage.py                   # Fase 5: Discovery/Resolution Coverage
  test_characterization.py           # Fase 0: comportamiento actual, ancla contra regresiones no relacionadas
  test_sentinels.py                  # patrones P3 de KNOWN_LIMITATIONS.md — alertan si aparecen
```

**Por qué fixtures congelados y no `decompiled/` en vivo**: `decompiled/` puede borrarse/regenerarse (no está en git, ver `.gitignore`), y re-decompilar requiere red + `ilspycmd` + acceso al share de planta — nada de eso debe ser un prerequisito para correr la suite de tests en cualquier máquina. Los fixtures son copias mínimas y estáticas de los fragmentos de código reales ya verificados, versionadas junto al proyecto.

---

## 2. Fixtures dorados (los 6 casos reales + 2 adicionales confirmados en la misma auditoría)

Cada fixture documenta: el bug/gap real que representa, el archivo de origen exacto, y la aserción concreta que el test debe verificar — no una comparación exhaustiva de todo el output, solo los campos relevantes para ese caso (evita que el test se rompa por cambios no relacionados, ver `VALIDATION_FRAMEWORK.md` sección 8.3).

### 2.1 `reportviewer` — connection string solo en `app.config`, ninguna en `Settings.cs`
- **Origen real**: `decompiled/ReportViewer/ReportViewer/app.config` (bug ya corregido en esta misma sesión).
- **Aserción**: `find_settings(fixture_root)` retorna una entrada con `category='sql_or_oracle'`, `default_value` conteniendo `NAAMRT-QCS25`, `source_file='app.config'`.
- **Por qué es un test de regresión real, no solo de la fase nueva**: este es exactamente el bug que originó todo el Validation Framework — si algo en las fases siguientes rompe el parseo de `app.config`, este test lo detecta inmediatamente.

### 2.2 `interconfig` / `interafl` — mismo bug, con la variante de deduplicación por valor
- **Origen real**: `decompiled/InterConfig/InterConfig/app.config` (7 conexiones nuevas, una de ellas — `CX` de `InterAFL.ViewModel` — referenciando `DataTransfer.Properties.Settings.CX` bajo un nombre distinto al de `Settings.cs`).
- **Aserción**: el conteo total de `settings` con `category='sql_or_oracle'` no duplica la misma connection string bajo dos nombres (verifica la lógica de `seen_values` de `find_settings`); las entradas comentadas (`<!-- <add name="CX" connectionString="...sa7..." /> -->`) NUNCA aparecen en el resultado.

### 2.3 `sgi` — SQL armado con `StringBuilder`, la app con más hallazgos SQL del portafolio
- **Origen real**: `decompiled/INVENTA2-2TEST/SGI/SGI.ViewModel.Transacciones/SurtirVM.cs:429-439` — INSERT/DELETE real sobre `ValeRH`/`ValePartes`/`ValesHistorico`.
- **Aserción (Fase 3)**: el `SqlFinding` correspondiente tiene `category='query'`, `target` conteniendo una de las 3 tablas (no `None`), `resolution_status='resolved'`.
- **Aserción previa (Fase 2, antes de que exista la Fase 3)**: `resolution_status='unresolved_dynamic_sql'`, `reason_code='DYNAMIC_SQL'` — es decir, este fixture tiene DOS snapshots dorados válidos en momentos distintos del plan (uno por fase), documentados explícitamente para no confundir "todavía no implementado" con "se rompió".

### 2.4 `datatransfer` (dos fixtures independientes del mismo archivo fuente)
- **`datatransfer_stringbuilder.json`** — origen: `DataTransfer.cs:14479`, dos `INSERT INTO XXAFL_QAPV_REWORKS_PRUEBA`/`Reworks_PRUEBA` ensamblados con `StringBuilder` dentro de un loop. Misma aserción de dos-fases que 2.3.
- **`datatransfer_reflection.json`** — origen: `PrintReportViewer.cs:12-25,53,74,82,85,88,101,123`. **Aserción (Fase 4)**: se genera al menos 1 finding de la nueva categoría reflection, con 7 invocaciones distintas detectadas (`OnPrint`, `DoesStateAllowPrinting`, `CreateEMFDeviceInfo`, etc.), `reason_code='REFLECTION'`.

### 2.5 `almacendiagnostico` — connection string hardcodeada a nivel de campo de clase
- **Origen real**: `decompiled/INVENTA2-2TEST/AlmacenDiagnostico/Program.cs:230` — `private static string connStr = "Data Source=NAAMRT-QCS11;...;Password=apodaca";`.
- **Aserción (Fase 2)**: `find_settings()` (extendido) retorna esta entrada; `security.check_settings()` genera un `SecurityFlag` de severidad `alta` sobre ella. **Este es el test más importante de la Fase 2** — verifica el cierre de un gap de seguridad real, no solo un gap de cobertura.

### 2.6 `vins1_modbus` — segunda integración PLC no documentada
- **Origen real**: `decompiled/VINS1/Modbus/Modbus/Form1.cs:41-42` — `new ModbusClient("192.168.1.5", 502)`.
- **Aserción (Fase 4)**: genera un finding de integración con la IP `192.168.1.5` visible en `raw`, categorizado como integración PLC/Modbus, no como I/O genérico sin clasificar.

### 2.7 Fixture adicional confirmado en la auditoría: COM/Excel vía CLSID (no listado explícitamente por el usuario, pero con evidencia igual de fuerte)
- **Origen real**: `decompiled/ReportViewer/ReportViewer/ReportViewer.ViewModel/MainVM.cs:1178` (o alternativamente `OTDR/Form1.cs:3562`, ambos válidos — se elige ReportViewer por ya estar en el set de fixtures).
- **Aserción (Fase 4)**: `Activator.CreateInstance(Marshal.GetTypeFromCLSID(...))` se detecta como integración COM, sin requerir una referencia estática a `Microsoft.Office.Interop`.

---

## 3. Mocking de `pyodbc` (Fase 1-2, `test_enrich_mocked.py`)

`db_introspect.py`/`enrich.py` requieren una conexión SQL Server real por diseño — los tests de mapeo de causas NO deben requerir red. Se mockea `pyodbc.connect` para lanzar excepciones sintéticas con los SQLSTATE reales de cada escenario:

```python
import pytest
from unittest.mock import patch

SQLSTATE_CASES = [
    ("08001", "SERVER_OFFLINE_OR_UNREACHABLE"),   # no se pudo establecer la conexion (red/servidor caido)
    ("28000", "LOGIN_FAILED"),                     # autenticacion invalida
    ("HYT00", "TIMEOUT"),                          # tiempo de espera agotado
    ("42000", "SP_NOT_FOUND_OR_NO_PERMISSION"),    # objeto no encontrado o sin permiso
    ("08S01", "SERVER_OFFLINE_OR_UNREACHABLE"),    # link de comunicacion caido durante la conexion
]

@pytest.mark.parametrize("sqlstate,expected_code", SQLSTATE_CASES)
def test_failure_catalog_maps_sqlstate(sqlstate, expected_code):
    assert failure_catalog.code_from_sqlstate(sqlstate) == expected_code

def test_enrich_never_falls_back_to_raw_exception_text():
    with patch("pyodbc.connect", side_effect=make_pyodbc_error("28000", "Login failed for user 'quality'")):
        result = enrich.enrich_app(app_id=<fixture con connection string valida>)
    assert result["connection_errors"][0]["reason_code"] == "LOGIN_FAILED"
    assert "revisar con infraestructura/DBA" not in result["connection_errors"][0]["message"]  # el generico viejo ya no debe aparecer
```

El caso `naamrt-qcs11` (servidor conocido) sigue probándose por separado — ya tiene su propio comportamiento correcto documentado, este test solo confirma que NO se rompe al introducir el catálogo general.

---

## 4. Tests de caracterización (Fase 0) — anclar el comportamiento actual antes de tocar nada

Antes de escribir un solo fixture nuevo, se corre el pipeline actual (sin ningún cambio) contra 2-3 apps ya conocidas y se congela el output como snapshot — no para decir que ese output es "correcto" en un sentido absoluto, sino para que cualquier fase futura que lo cambie SIN QUERER (una regresión, no una mejora deliberada) se note inmediatamente. Candidatos: `AFL.Dashboard` (caso simple, bien entendido, ya tiene 9 módulos con revisión de negocio completa) y `DataTransfer` (caso grande, complejo, ya conocido a fondo).

---

## 5. Tests centinela (P3 de `KNOWN_LIMITATIONS.md`) — detectar necesidad futura, no implementar hoy

```python
SENTINEL_PATTERNS = {
    "TableAdapter":              "L13 — DataSet Designer, confirmado ausente en el portafolio hoy",
    "DbContext":                 "L12 — Entity Framework, confirmado ausente",
    ".wsdl":                     "L20 — SOAP/WCF, confirmado ausente",
    "RabbitMQ":                  "L20 — confirmado ausente",
    "System.ServiceModel":       "L20 — confirmado ausente",
    "System.ServiceProcess":     "L20 — Windows Services, confirmado ausente",
    "CrystalDecisions":          "L20 — Crystal Reports, confirmado ausente",
    "OdbcConnection":            "L7 — ODBC DSN, confirmado ausente",
}

def test_sentinel_patterns_still_absent_from_portfolio():
    """No falla el build -- solo advierte (log/warning) si un patron que hoy
    NO existe en ninguna app aparece en una nueva. La primera vez que ocurra,
    hay que promover ese patron de KNOWN_LIMITATIONS.md P3 a una fase real del
    Implementation Plan, no descubrirlo por accidente meses despues."""
    for pattern, note in SENTINEL_PATTERNS.items():
        hits = grep_portfolio(pattern)
        if hits:
            warnings.warn(f"Patron centinela '{pattern}' encontrado ({note}) en: {hits}")
```

Este test se corre contra el portafolio completo (`decompiled/`), no contra los fixtures congelados — es el único test de esta suite que necesita el árbol completo de apps ya decompiladas, y deliberadamente nunca falla el build (solo advierte), porque su función es informativa, no de regresión.

---

## 6. Proceso obligatorio: de bug real a fixture permanente (regla de aceptación, no sugerencia)

De aquí en adelante, **ningún fix a `extract.py`/`enrich.py`/`db_introspect.py` se da por cerrado sin este checklist**:

1. Identificar el archivo/línea real donde el bug se manifiesta (no un caso sintético) — siempre que exista una app real en el portafolio que lo demuestre, como en los 8 casos de arriba.
2. Copiar el fragmento mínimo necesario a `tests/fixtures/<nombre>/` (suficiente contexto de clase/método para que el parser de `extract.py` funcione, no el archivo completo si es enorme — ej. `DataTransfer.cs` real tiene 20,427 líneas, el fixture solo necesita las ~30 líneas alrededor de cada caso, con la firma de clase/método intacta).
3. Escribir el snapshot dorado (`tests/fixtures/golden/<nombre>.json`) con el resultado ESPERADO después del fix, no antes — verificado a mano contra el código fuente real (igual que se hizo en las sesiones de revisión de negocio de este proyecto).
4. Escribir el test que falla ANTES del fix (confirma que el fixture realmente reproduce el bug) y pasa DESPUÉS.
5. El PR/commit que corrige el bug incluye el fixture + el test en el mismo cambio — nunca por separado, nunca "lo agrego después".

**Esto convierte cada sesión futura de "encontramos un bug real en la app X" (como ya ha pasado repetidamente en el historial del proyecto) en un fixture permanente automáticamente, en vez de una corrección aislada que puede regresar silenciosamente.**
