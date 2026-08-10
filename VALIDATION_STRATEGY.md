# Estrategia de Validación de Cobertura — QAPV Legacy App Analyzer

**Rol asumido para este documento**: Auditor técnico / pentester de la propia herramienta — la hipótesis de partida es que existen puntos ciegos, y cada afirmación de este documento está respaldada por evidencia real (archivo:línea) extraída del portafolio de ~60 apps ya decompiladas en `decompiled/`, no por conocimiento teórico de .NET Framework.

**Disparador de este documento**: la corrección del defecto de `find_settings()` (2026-08-06) — el extractor solo buscaba connection strings en `Settings.cs`, y 6 apps con cientos de hallazgos SQL (`ReportViewer`, `InterConfig`, `InterAFL`, `SGI`, `ReferenceControlWpf`, parcialmente `DataTransfer`/`VINS1`) tenían su conexión real completamente invisible. Ese hallazgo puntual demostró que el proceso de "¿qué sabemos de esta app?" puede estar sistemáticamente incompleto sin que nada en la UI lo indique — la app simplemente se veía como "sin SQL" o "sin tablas", indistinguible de una app que genuinamente no usa base de datos.

**Alcance de este documento**: NO se modifica código. Es una auditoría + plan. La implementación empieza solo después de aprobar esta estrategia, como se pidió explícitamente.

**Metodología**: lectura directa de los 9 módulos de `analyzer/` (extract.py, security.py, db_introspect.py, enrich.py, techstack.py, decompile.py, pipeline.py, report.py, db.py) para inventariar qué existe hoy, más 3 auditorías paralelas de evidencia (agentes de exploración de solo lectura) que grepearon los ~60 apps decompilados reales buscando cada patrón de las áreas A-G solicitadas. Cuando un patrón no aparece en el portafolio, este documento lo dice explícitamente como "no encontrado en este portafolio" — distinto de "no soportado por el extractor" — porque son dos afirmaciones distintas y mezclarlas sería exactamente el tipo de zona gris que este documento existe para eliminar.

---

## 1. Capacidades actuales

Inventario verificado por lectura directa del código (no por lo que la documentación dice que hace, sino por lo que el código realmente ejecuta):

### 1.1 Descubrimiento de conexiones (`analyzer/extract.py: find_settings()`)

| Mecanismo | Soporte |
|---|---|
| `Settings.cs` con `[DefaultSettingValue("...")]` | ✅ Completo (regex `SETTING_BLOCK`/`DEFAULT_VALUE`) |
| `app.config` `<connectionStrings><add name=... connectionString=.../>` | ✅ Completo (agregado 2026-08-06, `_find_appconfig_connection_strings()`, XML real vía `ElementTree`, ignora entradas comentadas automáticamente) |
| Clasificación de "¿es una conexión de BD?" | Heurística de contenido (`LOOKS_LIKE_DB_CONN`: busca `Server=`/`Data Source=`/`Database=`/`User Id=`/`UID=`) — no depende de que el atributo `[SpecialSetting(ConnectionString)]` esté presente, ya documentado como necesario desde el caso `CopyJDSU` |

**Todo lo demás está fuera de este alcance hoy** (ver sección 4).

### 1.2 SQL / Oracle (`analyzer/extract.py: scan_file()`, `SQL_TRIGGER`)

Dispara únicamente sobre: `new SqlConnection(`, `new OracleConnection(`, `CommandText =`, `new SqlCommand(`, `new OracleCommand(`. Para cada disparo:
- Resuelve variables de un solo nivel (`_resolve_variable`: busca `var = "literal";` entre el inicio del método actual y el punto del disparo — **no busca fuera del método**, ver 3.2).
- Clasifica en `query` / `stored_procedure` / `oracle_package_call` vía `_classify_sql()` (heurística: si hay `CommandType.StoredProcedure` cerca, o el primer literal parece `schema.Nombre`/`Nombre 'arg'` sin palabras clave SQL → SP; si no, busca `FROM`/`INTO`/`UPDATE` para adivinar la tabla).
- Extrae parámetros (`.Parameters.Add`/`.AddWithValue`) y columnas de resultado (`reader["Col"]`) con ventana acotada al cierre del método (`_find_method_end`), evitando el bug histórico de atribuir columnas al método equivocado.

### 1.3 Stored Procedures — introspección real (`analyzer/db_introspect.py` + `analyzer/enrich.py`)

Única parte de la herramienta que consulta la BD real (solo lectura, invariante arquitectónico estricto — nunca `EXEC`, nunca DML/DDL):
- `get_procedure_definition`: `OBJECT_DEFINITION()` → texto fuente completo del SP.
- `get_procedure_parameters`: `sys.parameters`/`sys.types` → firma formal (nombre, tipo, longitud, output, default).
- `get_procedure_result_columns`: `sys.dm_exec_describe_first_result_set_for_object` → forma del primer result set (análisis estático del plan, nunca ejecuta el SP; `None` si SQL Server no puede determinarlo — SQL dinámico, tablas temp, múltiples result sets).
- `get_table_columns`/`list_foreign_keys`: `INFORMATION_SCHEMA.COLUMNS`/`sys.foreign_keys`.

`enrich.py` decide **qué** SPs/tablas buscar a partir de `sql_findings` (no de la BD directamente) y **con qué** conexión (`settings` con `category='sql_or_oracle'`, filtrando por `_looks_like_sqlserver()` que excluye lo que parece Oracle vía `ORACLE_HINT`).

### 1.4 Estado de conexiones — lo que existe hoy

- Una lista curada de servidores conocidos como decomisionados (`KNOWN_UNREACHABLE_SERVERS = {"naamrt-qcs11"}`) con un mensaje específico y correcto (server dado de baja, reemplazo conocido, fecha de confirmación) — **este es el único caso hoy que cumple el estándar de "nunca ambiguo" que pide la sección E**.
- Para cualquier OTRO fallo de conexión: `_short_error()` colapsa la excepción de pyodbc en exactamente dos variantes: `"no se pudo conectar (SQLSTATE {code})..."` si el mensaje trae un SQLSTATE entre comillas, o el genérico `"no se pudo conectar — revisar con infraestructura/DBA"` en cualquier otro caso — **esto es exactamente la ambigüedad que la sección E pide eliminar** (ver 3.4/4.5).

### 1.5 Archivos de configuración

Solo `Settings.cs` (vía Settings designer) y, desde el fix reciente, `app.config`'s `<connectionStrings>`. `web.config`, `user.config`, `<appSettings>` (sección distinta de `<connectionStrings>`), variables de entorno, Registro de Windows, INI, XML propio, JSON: **cero soporte** (ver sección 4 para veredicto por patrón, con evidencia).

### 1.6 Comunicación con sistemas externos (`LOCAL_IO_TRIGGER`)

Cubre: `File.*`, `Directory.*`, `StreamReader/StreamWriter/FileStream`, `DirectoryInfo`, `Process.Start`, `PrintDocument/PrintDialog/PrinterSettings`, `SerialPort`, `HttpClient/WebClient/HttpWebRequest/SmtpClient`, `WebRequest.Create`, `BarTender.Application`, `.PrintOut(`. Ver sección 4 para lo que falta (COM/ActiveX, Reflection, Modbus como segunda ocurrencia, etc.).

### 1.7 Carga dinámica de código

**No existe ningún mecanismo hoy.** Cero menciones de Reflection en todo `analyzer/*.py` (confirmado por grep — la única coincidencia es la palabra "dynamic" dentro de un comentario en inglés de `db_introspect.py` sobre SQL dinámico, no una detección real).

### 1.8 Motor de prioridad, diccionario, dependencias, patrones (v0.5)

Ya construidos y fuera del alcance de esta auditoría (que es sobre el **Technical Analysis Engine**, la capa de extracción de la que todo lo demás depende) — pero vale la pena señalar que **cualquier gap cerrado aquí mejora automáticamente esas capacidades** sin tocarlas, porque son agregaciones de solo lectura sobre `sql_findings`/`settings`/`security_flags` (Principio de Read Models ya establecido).

---

## 2. Cobertura actual (Gap Analysis con evidencia)

Formato por área solicitada: ✅ Completo / 🟡 Parcial / ❌ No soportado / cada fila cita evidencia real (archivo:línea) cuando existe, o dice explícitamente "no encontrado en el portafolio" cuando la búsqueda no encontró nada que perder.

### A. Descubrimiento de conexiones

| Mecanismo | Estado | Evidencia |
|---|---|---|
| `Settings.cs` | ✅ Completo | Base de ~50 apps ya analizadas |
| `app.config` `<connectionStrings>` | ✅ Completo (recién agregado) | `ReportViewer`, `InterConfig`, `InterAFL` (7 conexiones), `SGI`, `ReferenceControlWpf` |
| `web.config` / `user.config` | ❌ No soportado — **no aplica hoy**: `Glob **/web.config` y `**/user.config` devuelven 0 archivos en las 60 apps (son ejecutables de escritorio, no ASP.NET) | N/A en este portafolio, pero el código no tiene ninguna lógica para ellos si apareciera uno |
| `ConfigurationManager.AppSettings` (`<appSettings>`) | ❌ No soportado — sección XML distinta de `<connectionStrings>`, nunca parseada | No auditado exhaustivamente en esta ronda (los `<appSettings>` vistos de paso, ej. `InterAFL`'s `SmartCheckImageDesPath`, no son connection strings, pero el mecanismo en sí no está cubierto) |
| Variables de entorno | ❌ No soportado, y **no aplica**: 33 coincidencias de `Environment.GetEnvironmentVariable` en todo el portafolio, el 100% en librerías vendorizadas (iText, BouncyCastle, OpenCvSharp, BenchmarkDotNet, Roslyn) — cero uso en código propio de ninguna app | Confirmado por auditoría de evidencia |
| Registro de Windows | ❌ No soportado, y **no aplica**: único uso real es `LabelPrint/LabelPrint/RawInput_dll/RegistryAccess.cs` para identificar dispositivos HID (escáneres), no para leer config de BD | Confirmado |
| Archivos INI | ❌ No soportado, y **no aplica para conexiones**: `config.ini` sí se usa (CompareImages, VINS1) pero exclusivamente para calibración de color, nunca para credenciales de BD | Confirmado |
| XML personalizado | ❌ No soportado, y **no aplica para conexiones**: usado en `Verificador.cs`/`CopyJDSU` para datos de inspección, nunca connection strings | Confirmado |
| JSON | ❌ No soportado, y **no aplica**: no existe un solo archivo `.json` de configuración de app en las 60 apps (`OTDR` usa `JsonConvert` pero no para config de conexión) | Confirmado |
| ODBC DSN / `OdbcConnection` | ❌ No soportado, y **no aplica**: 0 ocurrencias en 60 apps | Confirmado |
| OleDb | 🟡 **Parcial y con hallazgo real**: la forma "connection string estática en `app.config`" ya se captura (ej. `CXNORLAND`/`CXNORLANDMTP` de InterAFL, `Provider=Microsoft.ACE.OLEDB.12.0`), pero la forma **concatenada en tiempo de ejecución** es invisible | `InterAFL/InterAFL.ViewModel/ProcVM.cs:7911-7912`: `"provider=Microsoft.ACE.OLEDB.12.0;Data Source='" + text4 + "';Extended Properties=Excel 12.0;"` → `new OleDbConnection(connectionString)` — importación de Excel, nunca aparece en ningún finding |
| Oracle TNS | 🟡 Parcial: el `ORACLE_HINT` de `enrich.py` exige `Data Source\s*=\s*\(` (formato completo) o la palabra literal "Oracle"/"TNS" — un alias TNS simple tipo `Data Source=MiAlias;User Id=x;Password=y` (sin paréntesis) pasaría el filtro `_looks_like_sqlserver()` y el enriquecedor intentaría conectarse con el driver de SQL Server, fallando de forma engañosa | No confirmado en este portafolio (todas las Oracle vistas usan el formato `DESCRIPTION=(...)` completo), pero es un gap de diseño real, no solo teórico |
| Cadenas hardcodeadas (nivel método) | ✅ Completo — capturadas vía `_resolve_variable` si están dentro del método que las usa | — |
| **Cadenas hardcodeadas (nivel de campo de clase)** | ❌ **No soportado — hallazgo de severidad alta** | `INVENTA2-2TEST/AlmacenDiagnostico/Program.cs:230`: `private static string connStr = "Data Source=NAAMRT-QCS11;Initial Catalog=Inventa2;User ID=quality;Password=apodaca";` — credencial en texto plano real, **invisible hoy** porque `_resolve_variable` solo escanea desde el inicio del método actual, nunca los campos declarados a nivel de clase |
| Cadenas construidas dinámicamente (SQL Server) | ✅ Cubierto para el patrón `"..." + var + "..."` visto en `new SqlConnection(...)` | — |

### B. SQL

| Mecanismo | Estado | Evidencia |
|---|---|---|
| `SqlConnection`/`OracleConnection` | ✅ Completo | — |
| `SqlCommand`/`OracleCommand` | ✅ Completo | — |
| `SqlDataAdapter`/`OracleDataAdapter` | ✅ Completo **en la práctica** (no por diseño): auditoría confirmó 81 archivos con `SqlDataAdapter`, y en el 100% de los casos revisados el adapter envuelve un `SqlCommand`/`OracleCommand` ya creado en el mismo archivo, que el extractor ya captura por su cuenta | `ItemTrack.Model/Repository.cs:541`, `AFL.Dashboard/.../Class1.cs:220` |
| `TableAdapter` / DataSet Designer (.xsd) | N/A — **no existe en este portafolio**: 0 archivos `.xsd`, 0 menciones de `TableAdapterManager`, 0 archivos `*Designer.cs` de datos. Todo el acceso a datos es ADO.NET escrito a mano | Confirmado exhaustivamente |
| `ExecuteReader`/`ExecuteScalar`/`ExecuteNonQuery` | ✅ Completo | — |
| `CommandType.StoredProcedure` | ✅ Completo (ventana de búsqueda bidireccional, ya corregido en una sesión anterior) | — |
| Consultas dinámicas por concatenación simple | ✅ Completo | — |
| **`StringBuilder` para armar SQL** | ❌ **No soportado — confirmado en las 2 apps más grandes del portafolio** | `INVENTA2-2TEST/SGI/SGI.ViewModel.Transacciones/SurtirVM.cs:429-439` (INSERT/DELETE real sobre `ValeRH`/`ValePartes`/`ValesHistorico`, finding actual: `target=None, resolved=None`); `DataTransfer/DataTransfer/DataTransfer.cs:14479` (`INSERT INTO XXAFL_QAPV_REWORKS_PRUEBA`/`Reworks_PRUEBA`, mismo resultado vacío); también en `ItemTrack.Model/Repository.cs` y `AFLProdMon.Helpers/Repository.cs` |
| Interpolación de strings (`$"..."`) | N/A — **no existe en este portafolio**: todo el código usa concatenación clásica `"..." + var`, cero uso de interpolación C# 6+ para SQL | Confirmado exhaustivamente |
| Reflection generando SQL | N/A — **no existe**: los únicos usos de `GetType().GetProperties()` son de librerías vendorizadas (QuestPDF, BenchmarkDotNet, Emgu.CV) | Confirmado |
| Entity Framework / LINQ-to-SQL / Dapper | N/A — **no existe en ningún app propio**: 0 `DbContext`, 0 `ObjectContext`, 0 `DbSet<`, 0 uso de Dapper. `EntityFramework.dll` está referenciado como companion en `DataTransfer` pero el código propio no lo usa para acceso a datos (confirmar si se usa en algo más al revisar esa app a fondo) | Confirmado por búsqueda exhaustiva |
| Comando creado en un archivo/método distinto de donde se ejecuta | N/A — **no existe**: el estilo de este codebase siempre crea, llena y ejecuta el `SqlCommand` en el mismo método | Confirmado |

### C. Stored Procedures — clasificación de resolución

Hoy, cada SP detectado en `sql_findings` termina en uno de estos estados reales (no documentados como tales en ningún lado — este es exactamente el gap de la sección C/D):

| Estado real posible hoy | ¿Se distingue explícitamente? |
|---|---|
| Definición + parámetros + columnas de resultado obtenidos | Sí (status='ok' en `db_procedures`) |
| SP no existe en la BD real (nombre mal resuelto, o realmente no existe — ej. `GetJobs` en `AFL.Dashboard`, confirmado como stub vacío) | Parcial — `status='not_found'`, pero no distingue "el nombre que extrajimos del C# está mal" de "el SP genuinamente no existe en el servidor" |
| Conexión nunca intentada (servidor conocido como caído) | Sí, con mensaje específico (`KNOWN_UNREACHABLE_SERVERS`) |
| Conexión intentada y falló (timeout, DNS, auth, permisos) | **No** — todo colapsa en el mismo mensaje genérico de `_short_error()` |
| SP encontrado pero sin permiso `VIEW DEFINITION` | **No distinguido de "no existe"** — `get_procedure_definition` regresa `None` en ambos casos por diseño de `OBJECT_DEFINITION()` (SQL Server mismo no distingue "no existe" de "sin permiso" para un usuario sin ese permiso — sería necesario un chequeo adicional contra `sys.objects` para diferenciarlos) |
| Result set no determinable estáticamente (SQL dinámico dentro del SP, tablas temp) | Sí, pero se traduce como `None` silencioso — no aparece en el reporte como "no determinable", simplemente no aparece la sección |

### D. Queries — diagnóstico específico vs. mensaje genérico

**Confirmado**: `analyzer/report.py:50` es el único punto de origen del mensaje genérico `"(conexion detectada, query no resuelta automaticamente — revisar manualmente)"`. Se dispara cuando un grupo de `SqlFinding` para un método no tiene ningún literal con comillas — es decir, exactamente el caso de SQL armado con `StringBuilder` (sección B) o con una variable resuelta desde fuera del método (sección A, campo de clase). **La causa raíz ya está identificada por el gap analysis de arriba — no hay que adivinar el diagnóstico, hay que instrumentar cada gap para que declare su propia causa** (ver Fase 1 del plan).

### E. Estado de conexiones

Ya cubierto en 1.4/2.A. Resumen del gap: existe UN mensaje específico bien hecho (`naamrt-qcs11` decomisionado) que sirve de plantilla para todos los demás — hoy es la excepción, debería ser la norma.

### F. Integraciones

| Patrón | Estado | Evidencia |
|---|---|---|
| REST/HTTP | ✅ Completo (`HttpClient`/`WebClient`/`HttpWebRequest`) | — |
| SOAP/WCF | N/A — no existe en el portafolio (0 `System.ServiceModel`, 0 `.wsdl`) | Confirmado |
| FTP | N/A — no existe (0 `FtpWebRequest`) | Confirmado |
| SMTP | ✅ Completo (`SmtpClient`) | — |
| MSMQ | N/A — no existe (único hit es un falso positivo de `SnackbarMessageQueue`, UI toast de MaterialDesign) | Confirmado |
| RabbitMQ | N/A — no existe | Confirmado |
| Named Pipes | N/A a nivel de app — el único hit real es infraestructura de compilación de Roslyn (`VBCSCompiler`), no lógica de negocio | Confirmado |
| Sockets crudos | N/A a nivel de app — el único uso de `TcpClient`/`TcpListener` es interno a la librería vendorizada `EasyModbus` | Confirmado |
| SerialPort | ✅ Completo | Ya capturado en `LightValidation`, `Reflect`, etc. |
| **PLC/Modbus** | 🟡 **Parcial, con una segunda instancia real no detectada** | `VINS1/Modbus/Modbus/Form1.cs:41-42`: `new ModbusClient("192.168.1.5", 502)` — segunda app standalone con conexión Modbus-TCP en vivo, invisible porque `LOCAL_IO_TRIGGER` no incluye ningún patrón de `EasyModbus`/`ModbusClient` (la única razón por la que `MonTemp2` se documentó fue lectura manual del companion assembly, no detección automática) |
| OPC | N/A — no existe | Confirmado |
| **DLL externas / COM / ActiveX** | ❌ **No soportado — 5 apps con dependencia real de Excel COM Interop vía CLSID, invisible a cualquier chequeo ingenuo de `Microsoft.Office.Interop`** | `ReportViewer/.../MainVM.cs:1178`, `OTDR/.../Form1.cs:3562` (+60 líneas de `dynamic` operando el objeto COM), `AFLProdMon/.../ExportToExcel.cs:61,120`, `VINS1/VINS1/DataTransfer/DataTransfer.cs:7638`, `DataTransfer/DataTransfer/DataTransfer.cs:9823` — todas usan `Activator.CreateInstance(Marshal.GetTypeFromCLSID(new Guid("00024500-...")))`, el CLSID literal de Excel.Application, evadiendo cualquier búsqueda de la referencia estática `Microsoft.Office.Interop.Excel` |
| Crystal Reports | N/A — no existe | Confirmado |
| **Microsoft ReportViewer (control)** | 🟡 **Parcial — la app llamada "ReportViewer" NO lo usa; dos apps distintas sí, en vivo** | `DataTransfer/DataTransfer/ReportRVwr.cs:111` y `VINS1/VINS1/DataTransfer/ReportRVwr.cs:111`: `new Microsoft.Reporting.WinForms.ReportViewer()` — uso real confirmado, no solo un companion sin usar. **Lección explícita: el nombre del ejecutable no predice su funcionalidad real** — la app literalmente llamada `ReportViewer.exe` genera sus reportes con iText/QuestPDF/Excel COM, no con el control ReportViewer |
| Servicios de Windows | N/A — no existe (0 `ServiceBase`) | Confirmado |

### G. Reflection

| Patrón | Estado | Evidencia |
|---|---|---|
| `Assembly.Load`/`Assembly.LoadFrom` | ❌ No soportado (sin evidencia de uso real en este portafolio más allá de vendored) | — |
| `Activator.CreateInstance` | ❌ **No soportado — confirmado en uso real, no solo teórico** | `DataTransfer/PrintReportViewer.cs:79`, mismo en `VINS1` |
| `Type.GetType` | ❌ No soportado | — |
| `MethodInfo.Invoke` | ❌ **No soportado — confirmado en uso real y repetido (7 llamadas)** | `DataTransfer/PrintReportViewer.cs:12-25,53,74,82,85,88,101,123` (duplicado en `VINS1`): invoca métodos **no públicos** del control ReportViewer (`"OnPrint"`, `"DoesStateAllowPrinting"`, `"CreateEMFDeviceInfo"`, etc.) vía `type.GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)` + `.Invoke(obj, parms)` |
| `dynamic` para late-binding | ❌ No soportado — visto en el mismo patrón COM (sección F) | `OTDR/Form1.cs:3564-3744` |

**Impacto en confiabilidad del análisis (lo que pide explícitamente la sección G)**: cuando una app invoca miembros **no públicos** de un ensamblado de terceros vía reflection (como en `PrintReportViewer.ExecuteFunction`), el comportamiento real de la app queda atado a la versión exacta de ese ensamblado — no hay contrato público que garantice que `"OnPrintingBegin"` siga existiendo con esa firma en otra versión. Un desarrollador reconstruyendo esto en Ignition MES **no puede** simplemente "llamar al método publico equivalente", porque no existe uno — tendría que decidir conscientemente si replica el hack de reflection o busca una vía soportada. Sin detectar este patrón, el migration package generado no advertiría de esto en absoluto: aparecería como "usa Microsoft.Reporting.WinForms" sin ninguna señal de que la app depende de APIs internas no documentadas de esa librería.

---

## 3. Riesgos detectados

Ordenados por severidad real (impacto de negocio/migración), no por facilidad de arreglo:

1. **Credencial de producción en texto plano, completamente invisible hoy** — `AlmacenDiagnostico/Program.cs:230`, `Data Source=NAAMRT-QCS11;...;Password=apodaca` declarado como campo de clase. Ni aparece en `settings`, ni dispara ningún `security_flag`, ni fue mencionado en ninguna revisión de negocio anterior de esa app — el escaneo de seguridad de este mismo proyecto tiene un hueco exactamente del tamaño de este gap de extracción.
2. **Dos de las apps más grandes del portafolio (`DataTransfer`, 366 hallazgos; `SGI`, 264 hallazgos) tienen queries reales de escritura (INSERT/DELETE multi-tabla) que se reportan como `target=None` sin ninguna pista** — cualquier análisis de dependencias/diccionario de datos que dependa de `target` (el Data Dictionary y el Dependency Graph del portafolio, v0.5) **subestima silenciosamente** las tablas reales que estas 2 apps tocan.
3. **Reflection invocando miembros no públicos de un control de terceros** (`DataTransfer`/`VINS1`) — riesgo de migración real y no documentado en ningún lado hasta esta auditoría: no hay una vía pública equivalente a replicar.
4. **5 apps dependen de Excel instalado en el equipo vía COM Interop tardío** (CLSID, no referencia estática) — si Excel no está instalado en el servidor donde se migre/ejecute, o si se busca esta dependencia de la forma ingenua (`grep "Microsoft.Office.Interop"`), se pasa por alto en las 5.
5. **Mensajes de error de conexión ambiguos** — ya identificado por el propio usuario como motivo de esta auditoría; confirmado en código (`_short_error()`), afecta a **toda app cuyo servidor no esté en la lista curada de servidores muertos**.
6. **Segunda instancia de integración PLC/Modbus no detectada** (`VINS1/Modbus`) — un desarrollador que solo lea el reporte de `MonTemp2` creería que Modbus es un caso aislado.

---

## 4. Gaps funcionales (resumen consolidado)

| # | Gap | Severidad | Apps afectadas confirmadas |
|---|---|---|---|
| 1 | Connection strings hardcodeadas a nivel de campo de clase (fuera del método) | Alta | AlmacenDiagnostico (1 confirmada, puede haber más sin auditar el 100% de las 60 apps) |
| 2 | SQL armado con `StringBuilder` | Alta | DataTransfer, SGI, ItemTrack, AFLProdMon (4 apps, 6 ocurrencias) |
| 3 | Mensajes de error de conexión genéricos (no distinguen DNS/timeout/auth/permisos) | Alta (afecta confiabilidad percibida de TODO el portafolio) | Todas menos las que apuntan a `naamrt-qcs11` |
| 4 | Mensaje genérico "query no resuelta automáticamente" sin diagnóstico | Alta (mismo grupo que #1/#2) | Cualquier app con gap #1 o #2 |
| 5 | Reflection / `dynamic` / `Activator.CreateInstance` — cero detección | Media-Alta | DataTransfer, VINS1 (invocación de miembros no públicos de terceros) |
| 6 | COM/ActiveX vía CLSID (Excel) — cero detección | Media-Alta | ReportViewer, OTDR, AFLProdMon, VINS1, DataTransfer (5 apps) |
| 7 | Segunda instancia de Modbus/PLC no capturada como integración | Media | VINS1/Modbus |
| 8 | OleDb con connection string concatenada en runtime | Media | InterAFL |
| 9 | Oracle TNS en formato "alias simple" (sin paréntesis) mal clasificado como SQL Server | Baja (no confirmado en este portafolio, pero es un defecto de diseño real en `enrich.py`'s `ORACLE_HINT`) | Ninguna confirmada hoy |
| 10 | `<appSettings>` (distinto de `<connectionStrings>`) nunca parseado | Baja | No se confirmó que oculte credenciales, pero el mecanismo no existe |
| — | ODBC, Registry, INI, XML propio, JSON, EF, Dapper, TableAdapter/.xsd, LINQ-to-SQL, interpolación de SQL, FTP, MSMQ, RabbitMQ, Named Pipes/Sockets a nivel app, OPC, Crystal Reports, Windows Services, SOAP/WCF | **N/A confirmado** | Ninguna — no construir soporte para esto ahora; ver nota abajo |

**Nota importante sobre los "N/A"**: no se recomienda invertir esfuerzo en soportar mecanismos que hoy **no existen en ninguna de las 60 apps ya analizadas**. Pero como el inventario de apps de la planta tiene ~30 apps aún sin analizar (ver memoria del proyecto), estos mecanismos deben quedar **documentados como "conocido no soportado, no confirmado necesario"** (sección 8, casos de prueba centinela) para que, si una futura app sí los usa, el gap se note inmediatamente en vez de descubrirse por accidente meses después — exactamente el problema que originó esta auditoría.

---

## 5. Priorización

Criterio: (a) evidencia real de que el gap ya afecta apps ya analizadas > gap teórico; (b) apps grandes/críticas > apps pequeñas; (c) arreglo barato y aislado > arreglo que requiere rediseño.

| Prioridad | Ítem | Por qué |
|---|---|---|
| **P0** | #3 Mensajes de conexión específicos + #4 Diagnóstico específico en vez de "no resuelta" | Mismo trabajo raíz (clasificar la CAUSA en vez de colapsar a un genérico), afecta la confiabilidad percibida de TODO el portafolio, no requiere nueva capacidad de parsing — es reorganizar información que ya se tiene o es barata de obtener |
| **P0** | #1 Hardcoded a nivel de campo de clase | Severidad de seguridad real ya confirmada (credencial de producción), arreglo acotado (extender `_resolve_variable`/agregar un escaneo de campos de clase) |
| **P1** | #2 StringBuilder SQL | Afecta a las 2 apps más grandes del portafolio; el fix es más grande (requiere trackear un objeto mutable a través de líneas, no solo un `var = "literal"`) pero el retorno es alto |
| **P1** | #6 COM/CLSID + #5 Reflection | Comparten la misma primitiva de detección (`Activator.CreateInstance`, `MethodInfo.Invoke`, `GetTypeFromCLSID`, `dynamic`) — conviene implementarlos juntos como una sola categoría nueva de finding ("integración de bajo nivel / reflection"), 7 apps afectadas en total |
| **P2** | #7 Modbus como integración detectada (no solo documentada a mano) | Aporta valor real pero acotado a 2 apps conocidas |
| **P2** | #8 OleDb runtime-concatenado | 1 app confirmada, patrón de bajo riesgo (Excel import, no credencial de producción) |
| **P3** | #9 Oracle TNS alias simple, #10 `<appSettings>` | Sin evidencia de que oculten algo hoy — documentar como gap conocido, no implementar todavía (Principio 4: la complejidad solo crece con evidencia objetiva de que el valor supera el costo) |
| **P3 (transversal)** | H. Coverage %, I. Confidence Score, J. Unknowns | Estas tres dependen de que los gaps P0/P1 ya estén resueltos o al menos catalogados — calcular un "% de cobertura" ANTES de cerrar los gaps conocidos produciría un número artificialmente alto sobre una base incompleta. Se implementan en la fase final, consumiendo el catálogo de causas ya construido en P0. |

---

## 6. Plan de implementación por fases

**Fase 1 — Diagnóstico específico (P0, sin nueva capacidad de extracción)**
- Reemplazar `_short_error()` en `analyzer/db_introspect.py`/`enrich.py` por un mapeo de causas reales: DNS no resuelto, timeout de red, autenticación fallida, permisos insuficientes (login válido pero sin acceso a la BD), nombre de servidor no encontrado — usando el SQLSTATE/mensaje real de pyodbc, que ya trae esta información (hoy se descarta).
- Reemplazar el mensaje genérico de `report.py:50` por una causa explícita, escrita en el momento en que `_classify_sql`/`scan_file` fallan en resolver algo: "SQL armado con StringBuilder, no capturado" / "variable resuelta fuera del método actual" / "SQL dinámico, no se pudo determinar el texto" — cada causa mapeada 1:1 a un gap ya catalogado en la sección 4, no un mensaje inventado por caso.
- Esto es reorganización de información + un mapeo de errores más granular — no requiere nuevos regex de extracción, es la fase de menor riesgo y mayor retorno inmediato (coherente con la regla 0.6 del roadmap general del proyecto).

**Fase 2 — Cerrar el gap de seguridad (P0)**
- Extender `find_settings()`/agregar una función hermana que escanee declaraciones de campo a nivel de clase (`(private|public|internal)\s+(static\s+)?(readonly\s+)?string\s+\w+\s*=\s*"..."`) con el mismo filtro de "parece connection string" ya existente (`LOOKS_LIKE_DB_CONN`), para que el caso `AlmacenDiagnostico` (y cualquier otro igual, no confirmado aún) se capture y dispare `security.check_settings` como cualquier otro.
- Re-ejecutar el escaneo (no la decompilación) sobre las 60 apps ya analizadas, igual que se hizo para el fix de `app.config`.

**Fase 3 — StringBuilder SQL (P1)**
- Nueva función en `extract.py` que, al encontrar `new StringBuilder()` seguido de `.Append(...)`/`.AppendLine(...)` con contenido que matchea `SQL_KEYWORDS`, acumula el texto igual que hoy se hace con `CommandText =` — el punto de enganche es el mismo (`sqlCommand.CommandText = stringBuilder.ToString();`), solo que ahora la resolución de esa asignación necesita mirar hacia atrás el objeto `StringBuilder`, no una variable de string literal.
- Validar contra los 2 casos ya identificados como fixtures (`SGI/SurtirVM.cs:439`, `DataTransfer.cs:14479`) antes de darlo por bueno.

**Fase 4 — Reflection / COM / integraciones nuevas (P1-P2)**
- Nueva categoría de finding (no reutilizar `LocalIOFinding` tal cual, ya que esto es conceptualmente distinto — ver sección 7) para: `Activator.CreateInstance`, `MethodInfo.Invoke`/`GetMethod().Invoke`, `Marshal.GetTypeFromCLSID`, `Type.GetTypeFromProgID`, uso de `dynamic` fuera de los casos ya cubiertos.
- Extender `LOCAL_IO_TRIGGER` (o crear un registro paralelo) con `ModbusClient`/`EasyModbus` como patrón de integración PLC explícito, y `OleDbConnection`/`Provider=` para el caso de OleDb runtime-concatenado.

**Fase 5 — Coverage %, Confidence Score, Unknowns (H/I/J)**
- Solo después de que las fases 1-4 estén implementadas y validadas — construir el motor de cobertura sobre un catálogo de causas YA correcto, no sobre los mensajes genéricos actuales (ver sección 7 para el diseño propuesto).

---

## 7. Recomendaciones arquitectónicas

1. **Un solo catálogo de "causas de no-resolución", no mensajes ad hoc dispersos.** Hoy cada módulo (`report.py`, `enrich.py`, `db_introspect.py`) decide su propio texto cuando algo no se puede resolver. Se recomienda centralizar esto en un módulo nuevo, p. ej. `analyzer/diagnostics.py`, con un enum/registro cerrado de causas (`DYNAMIC_SQL_STRINGBUILDER`, `VARIABLE_OUT_OF_METHOD_SCOPE`, `SERVER_UNREACHABLE_DNS`, `SERVER_UNREACHABLE_TIMEOUT`, `AUTH_FAILED`, `INSUFFICIENT_PERMISSIONS`, `OBJECT_NOT_FOUND`, `REFLECTION_DETECTED`, etc.), del mismo modo que `FINDING_STATUSES`/`REVIEW_STATUSES` ya son enums cerrados y centralizados en `db.py`. Este catálogo es el que alimenta tanto los mensajes específicos (D/E) como la sección `UNKNOWNS` (J) — una sola fuente de verdad, no dos implementaciones que puedan divergir.
2. **Confidence Score como propiedad del método de extracción, no un número inventado por finding.** La tabla de ejemplo de la sección I (100% BD real, 95% app.config, 80% inferido, 60% regex, 30% reconstrucción parcial) debe derivarse mecánicamente de **qué función encontró el dato**, no asignarse a mano por finding — p. ej., todo lo que sale de `db_introspect.py` es 100% por construcción (viene de metadata real de SQL Server), todo lo que sale de `_find_appconfig_connection_strings` es 95% (texto declarado explícitamente, sin ambigüedad de parsing), todo lo que sale de heurísticas de regex sobre C# (`_classify_sql`, `_resolve_variable`) es 60%, y lo reconstruido vía inferencia posterior (ej. adivinar una tabla del `FROM` de un SQL parcialmente resuelto) es 30%. Esto se puede implementar como un campo `confidence` calculado en el momento en que cada función retorna su resultado, no como una capa separada que intente adivinar la confianza después del hecho.
3. **Coverage % como Read Model, coherente con el patrón ya establecido en v0.5.** Igual que `get_priority_and_complexity()` es una agregación de solo lectura sobre datos ya extraídos, el % de cobertura por app (Conexiones/SPs/Queries/Tablas/Reglas de negocio/Reportes/Integraciones externas) debe ser una función nueva en `db.py` que cuenta cuántos hallazgos de cada categoría tienen `confidence` alto vs. cuántos quedaron en el catálogo de `UNKNOWNS` — no un cálculo nuevo y aislado. Mantiene la filosofía "cero heurísticas opacas, un solo lugar para los pesos" ya aplicada al Priority & Complexity Engine.
4. **Nueva categoría de finding para Reflection/COM, no forzarlo dentro de `LocalIOFinding`.** `LocalIOFinding` hoy representa "esta línea toca un archivo/proceso/impresora/red" — Reflection y COM son conceptualmente "esta línea invoca código de forma tardía/indirecta", una preocupación distinta (impacta la CONFIABILIDAD del análisis estático mismo, no es solo "otra integración más"). Se recomienda una tabla nueva `reflection_findings` (o extender `sql_findings`/`io_findings` con una tercera lista de igual peso) para poder decir explícitamente en el reporte "esta app tiene N puntos donde el análisis estático puede estar incompleto porque el código decide en tiempo de ejecución qué invocar".
5. **La lección "el nombre del ejecutable no predice su funcionalidad" (confirmada con `ReportViewer.exe`) debe ser una regla operativa, no solo una anécdota.** Se recomienda que cualquier agente/persona que audite una app nueva verifique SIEMPRE contra el código, nunca asuma por el nombre — esto ya se sigue en la práctica en las revisiones de negocio recientes, pero vale la pena dejarlo explícito como principio en `ARCHITECTURAL_PRINCIPLES.md` si se detectan más casos.
6. **Ninguna de estas fases requiere tocar `db_introspect.py`'s invariante de solo lectura.** Todo lo propuesto es extracción estática adicional (regex/AST-lite sobre C# decompilado) o mejor clasificación de errores ya recibidos — no se abre ninguna vía de escritura nueva ni se relaja el invariante ya documentado.

---

## 8. Casos de prueba para evitar regresiones futuras

**Estado actual: no existe ningún test automatizado en el proyecto** (`requirements.txt` no incluye `pytest`, no hay carpeta `tests/`, no hay `conftest.py`). Esto es una deuda técnica real dado que ya se han corregido ~15 bugs reales del extractor a lo largo de las sesiones anteriores (ver historial del proyecto) sin ningún mecanismo que impida que un cambio futuro los reintroduzca. Se recomienda crear `tests/` con `pytest`, usando como fixtures los **casos reales ya confirmados en esta auditoría** — no hay que inventar código de prueba sintético, ya existe evidencia real y con veredicto conocido:

| Caso de prueba | Fixture (archivo real ya decompilado) | Aserción |
|---|---|---|
| Detecta connection string en `app.config` | `decompiled/ReportViewer/ReportViewer/app.config` | `find_settings()` debe regresar una entrada `category='sql_or_oracle'` con `default_value` conteniendo `NAAMRT-QCS25` |
| No duplica connection string cuando `Settings.cs` y `app.config` mirrorean el mismo valor | `decompiled/InterConfig/InterConfig` (companion `InterAFL.ViewModel/ConfVM.cs` referencia `DataTransfer.Properties.Settings.CX`) | El conteo total de `settings` con `category='sql_or_oracle'` no debe duplicar el mismo valor bajo dos nombres |
| Ignora entradas comentadas en `<connectionStrings>` | `decompiled/InterAFL/InterAFL/app.config` (tiene un bloque `<!-- ... -->` completo con credenciales viejas `sa/sa7`) | Ninguna entrada con `sa7` debe aparecer en el resultado |
| **(nuevo, Fase 2)** Detecta connection string hardcodeada como campo de clase | `decompiled/INVENTA2-2TEST/AlmacenDiagnostico/Program.cs:230` | Debe aparecer un `SettingEntry` con `NAAMRT-QCS11`/`Inventa2`, y `security.check_settings()` debe generar un flag de severidad `alta` |
| **(nuevo, Fase 3)** Resuelve SQL armado con StringBuilder | `decompiled/INVENTA2-2TEST/SGI/SGI.ViewModel.Transacciones/SurtirVM.cs:429-439` | El `SqlFinding` correspondiente debe tener `category='query'`, `target` conteniendo `ValeRH` o `ValePartes` o `ValesHistorico` (no `None`) |
| **(nuevo, Fase 3)** Ídem en la app más grande | `decompiled/DataTransfer/DataTransfer/DataTransfer.cs:14479` | `target` debe contener `XXAFL_QAPV_REWORKS_PRUEBA` o `Reworks_PRUEBA`, no `None` |
| **(nuevo, Fase 4)** Detecta Reflection invocando miembros no públicos | `decompiled/DataTransfer/DataTransfer/PrintReportViewer.cs:12-25` | Debe generar un finding de la nueva categoría reflection con al menos 7 invocaciones (`OnPrint`, `DoesStateAllowPrinting`, etc.) |
| **(nuevo, Fase 4)** Detecta COM/CLSID de Excel | `decompiled/ReportViewer/ReportViewer/ReportViewer.ViewModel/MainVM.cs:1178` | Debe generar un finding de integración COM, distinto de un `Microsoft.Office.Interop` estático |
| **(nuevo, Fase 4)** Detecta segunda instancia de Modbus | `decompiled/VINS1/Modbus/Modbus/Form1.cs:41-42` | Debe generar un `LocalIOFinding` (o la categoría nueva de integración) con operación tipo Modbus/PLC, IP `192.168.1.5` visible en el `raw` |
| **(nuevo, Fase 1)** Mensaje de error de conexión específico | Simular (mock de `pyodbc.connect`) un timeout, un DNS fallido, y un login fallido por separado | Cada uno debe producir un texto de causa DISTINTO, ninguno debe caer en el genérico `"revisar con infraestructura/DBA"` |
| **(nuevo, Fase 1)** Mensaje de query no resuelta con causa específica | Un `SqlFinding` sintético con `raw` conteniendo `stringBuilder.ToString()` | El reporte debe decir "SQL armado con StringBuilder", no el genérico de `report.py:50` |
| **Centinela para gaps N/A (sección 4)** | N/A — prueba de "no regresión silenciosa": un test que falle a propósito si alguna vez aparece `TableAdapter`, `DbContext`, `.wsdl`, `RabbitMQ`, `System.ServiceModel` en cualquier app nueva analizada, para que la primera vez que ocurra se note en CI en vez de descubrirse meses después en una revisión manual | Grep sobre `decompiled/` completo, alerta (no falla el build, pero sí loguea) si aparece cualquiera de estos patrones en una app que no los tenía antes |

**Nota sobre infraestructura de test**: dado que `db_introspect.py`/`enrich.py` requieren una conexión SQL Server real, los tests de Fase 1 (mensajes de error) deben mockear `pyodbc.connect` para simular cada tipo de excepción sin depender de la red de planta — los tests de extracción (Fases 2-4) no necesitan mock alguno, son 100% sobre archivos ya decompilados en disco, current y determinísticos.

---

## Resumen ejecutivo de una línea

El analizador cubre bien el patrón dominante de este portafolio (ADO.NET escrito a mano, `Settings.cs`/`app.config`), pero tiene puntos ciegos reales y ya confirmados con evidencia — no teóricos — en: connection strings a nivel de campo de clase (riesgo de seguridad concreto), SQL armado con `StringBuilder` (afecta a las 2 apps más grandes), Reflection/COM/ActiveX (cero detección, 7 apps afectadas), y mensajes de error/diagnóstico genéricos que ocultan exactamente estas causas en vez de señalarlas. Los mecanismos "de manual de .NET" que el portafolio NO usa (EF, Dapper, TableAdapter, ODBC, JSON/INI/Registry para config, FTP/MSMQ/RabbitMQ/SOAP/Named Pipes/Sockets/OPC/Crystal Reports/Windows Services) están correctamente fuera de alcance hoy y no ameritan inversión inmediata — pero deben quedar como casos centinela para no repetir el mismo tipo de sorpresa si aparecen en una de las ~30 apps del inventario aún sin analizar.
