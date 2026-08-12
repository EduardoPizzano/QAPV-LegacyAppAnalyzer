# RECONSTRUCTION_EVIDENCE_STUDY.md

**Fecha:** 12 de agosto de 2026
**Fase:** Segunda fase de investigación (fase 1 = Gap Analysis, ya aceptado como base de discusión, sin incrementos de implementación aprobados)
**Estado:** Para revisión y aprobación. Cero código, cero migraciones, cero modificaciones de esquema. Solo lectura/categorización.
**Regla seguida en todo momento:** ninguna aplicación legacy fue ejecutada, modificada ni escrita; ninguna base de datos legacy recibió ninguna operación de escritura; no se implementó ningún extractor nuevo; no se modificó código de producción del Analyzer.

---

## 0. Selección de las 3 aplicaciones

Las tres categorías solicitadas **sí existen** en el portafolio real (confirmado por consulta de solo lectura contra `qapv_analyzer.db`, columna `ui_framework`: 37 WinForms, 34 WPF, 18 mixtas WPF+WinForms, 14 Console/Service, 2 sin detectar). No fue necesario sustituir ninguna categoría.

| Categoría | App elegida | Por qué esta y no otra |
|---|---|---|
| **WinForms** | `AFL_DataCenter` | Deliberadamente **distinta** de `DataTransfer` (usada como evidencia exploratoria en la fase de Gap Analysis) — el objetivo es probar representatividad, no repetir la misma app. Es la WinForms con más evidencia real del portafolio (459 hallazgos SQL, 62 banderas de seguridad, 9 settings) y ya tiene historial de auditoría propio (`AUDIT-ARB-2026-08-04.md` la usa como ejemplo). |
| **WPF** | `InterConfig` | La app WPF con más evidencia real de introspección de base de datos (7 stored procedures y 31 tablas reales confirmadas, según los datos operativos disponibles) — la mejor candidata para evaluar si el modelo de datos es reconstruible de punta a punta en un caso WPF favorable. |
| **Console/Service** | `RL1Interface` | Evidencia real sustancial (46 hallazgos SQL, 14 banderas de seguridad, tablas reales confirmadas por introspección) y, por el nombre, candidata a tener integración con equipo físico de planta — relevante para el área F (integraciones externas). |

---

## A. Modelo común de evidencia de reconstrucción

Patrones que aparecieron en **las tres** apps, con independencia de su arquitectura de UI — estos son candidatos sólidos a una arquitectura de extracción común, porque no dependen de si la app es WinForms, WPF o consola:

**A.1 — Patrón de entry point uniforme y trivial de extraer.** Los tres `Program.cs`/`App.cs` siguen un patrón reconocible: WinForms envuelve `Application.Run(new DataCenter())`; WPF envuelve `app.Run()` con `StartupUri`; consola tiene un `Main` con un `while` propio. En los tres casos el entry point real es una única línea o un bloque pequeño, 100% legible, sin ambigüedad. **Esto ya es análisis estático trivial y uniforme entre arquitecturas.**

**A.2 — Lógica de negocio no-SQL invisible en las tres, de forma consistente.** Es el hallazgo más repetido de todo el estudio. En AFL_DataCenter: selección de connection string por `Planta`, ventana deslizante de importación con solape de 90 minutos, máquina de decisión `type == "D"` vs `"R"`, guardas de reentrancia `lProcesandoXXX`. En InterConfig: máquina de estados `Operation.None/Create/Update`, deduplicación por nombre normalizado, filtro LINQ "buscar mientras escribes". En RL1Interface: `RLAlta` (exclusión de conectores tipo "MTP"), validación de IL en cero/negativo, certificación de operador con ventana de gracia de 1 día, timeout de espera de resultado de equipo. **En los tres casos, esta lógica no toca SQL directamente en la misma línea que la decisión, así que hoy es completamente invisible para el Analyzer** — no aparece en ninguna tabla, no genera ningún hallazgo, se pierde en silencio. Esto es evidencia fuerte y ya no teórica de que "señalar candidatos de lógica de negocio" (recomendado en el Gap Analysis) es una necesidad transversal, no un capricho de una app en particular.

**A.3 — Código huérfano/inalcanzable en las tres.** `frmColores` (AFL_DataCenter) existe completo pero **nunca se instancia** desde ningún punto del código decompilado. `Once`, `YaExiste`, `CableBienCapturado` (RL1Interface) no tienen ningún call-site dentro del archivo. El botón `btnExcel` (InterConfig) no tiene ningún `ICommand` correspondiente en el ViewModel que lo respalde — evidencia indirecta de lo mismo. **Este patrón no estaba anticipado en el Gap Analysis original y es un hallazgo nuevo de este estudio**: el portafolio parece acumular consistentemente código/pantallas/controles que ya no están conectados a ningún flujo activo. Para el objetivo de reconstrucción, esto es crítico: reconstruir ciegamente "todo lo que hay en el código" arriesga resucitar funcionalidad que el negocio ya abandonó.

**A.4 — Credenciales en texto plano en configuración, en las tres.** `app.config`/`Settings.cs` de las tres apps contiene contraseñas de base de datos sin cifrar (enmascaradas en este documento). Ya está bien cubierto por `security_flags` en las tres — no es un gap, es una confirmación de que ese extractor generaliza bien.

**A.5 — SQL 100% por concatenación de texto, sin parámetros, en las tres.** Ninguna de las tres apps usa `SqlParameter`/`CommandType.StoredProcedure` de forma consistente; incluso donde el Analyzer clasifica un hallazgo como "stored_procedure", el mecanismo real es texto concatenado tipo `"NombreSP 'val1','val2'"` (confirmado en AFL_DataCenter y, por extensión ya documentada, en el resto del portafolio vía `security_flags`). Esto es un patrón de codificación de toda una generación de apps del portafolio, no un caso aislado.

**A.6 — Fidelidad del reporte generado (`report.py`): observación histórica, investigación posterior y conclusión actual.**

*(a) Observación histórica (lo que este estudio observó originalmente, el 12 de agosto de 2026, al leer los archivos `.md` estáticos en `reports/`):* en InterConfig, el archivo de reporte afirmaba *"No se encontraron connection strings"* pese a que `app.config` sí tiene una y el código la usa explícitamente. En RL1Interface, `CargaConnRLAlta` aparecía marcada como "revisar manualmente" pese a que el código tiene la consulta completamente resuelta en texto legible. Estas dos observaciones, leídas en ese momento como fallas del extractor, motivaron directamente el incremento **FIDELITY FIXES**.

*(b) Investigación posterior (incremento FIDELITY FIXES, cerrado el 12 de agosto de 2026 — ver `FIDELITY_FIXES_2026-08-12.md`):* se trazó cada caso de punta a punta (Input → Parser/extractor → Clasificación → Persistencia → Reporte) contra los archivos decompilados reales y contra `qapv_analyzer.db`. Para InterConfig: `_find_appconfig_connection_strings()` ya captura la connection string correctamente, y la fila ya existe en la base (`settings` id=1606, `created_at='2026-08-06T23:38:32Z'`). Para RL1Interface: el patrón de constructor de dos argumentos (`VAR_AS_COMMAND_CTOR_ARG`) ya resuelve `cmdText` a su literal completo, y esa fila también ya existe en la base (`sql_findings` id=21349, `confidence=90`, `created_at='2026-08-06T23:39:05Z'`). Al regenerar el Markdown desde esos datos ya persistidos (usando `render_from_db`, el mismo mecanismo que la vista Flask `/apps/<id>` ejecuta en cada carga de página), ninguno de los dos mensajes erróneos reaparece.

*(c) Conclusión actual:* **no hay ningún bug vivo reproducible en el extractor, en la persistencia ni en el renderizado dinámico** para ninguno de los dos casos. Los dos archivos `.md` estáticos que este estudio leyó tenían fecha de modificación anterior (30 de julio de 2026) a la corrida de análisis correcta (6 de agosto de 2026) — nunca se regeneraron después de esa corrida. Lo que este estudio documentó como "fidelidad imperfecta del extractor" era, en realidad, **una deuda de sincronización/gestión de artefactos derivados**: los `.md` en `reports/` pueden quedar desactualizados respecto a la base de datos y a la vista viva, sin que exista hoy ningún mecanismo que detecte o corrija esa divergencia automáticamente. La evidencia histórica de la observación original (a) se preserva intencionalmente en este documento — la interpretación que cambia es (c), no el hecho de que (a) ocurrió.

**A.7 — Nota sobre fuente de verdad (source of truth), agregada tras el cierre de FIDELITY FIXES.**

Este estudio, y cualquier estudio futuro sobre el Analyzer, debe distinguir explícitamente tres capas con autoridad decreciente:

- **Fuente primaria de evidencia:** `qapv_analyzer.db` / el modelo persistido actual (tablas `settings`, `sql_findings`, `io_findings`, `security_flags`, `db_procedures`, `db_tables`). Esto es lo que el extractor produjo realmente, en la corrida más reciente.
- **Vista derivada:** el renderizado web dinámico (`app.py::app_detail`, que llama `render_from_db()` sobre la fuente primaria en cada request). Al ser generada en el momento de la consulta, esta vista siempre refleja el estado actual de la fuente primaria.
- **Artefactos secundarios:** los archivos `reports/*.md` estáticos, escritos una sola vez al momento de cada análisis (`_analyze_and_save()` en `app.py`/`main.py`) y nunca regenerados automáticamente después. Pueden quedar desactualizados respecto a la fuente primaria si la app se reanaliza o si el extractor mejora, sin que ese archivo se toque de nuevo.

Un `.md` desactualizado en `reports/` **no constituye por sí solo evidencia suficiente de un defecto actual del Analyzer** si no coincide con la evidencia persistida en la fuente primaria y con el comportamiento del pipeline/extractor actual. Confirmarlo requiere el mismo tipo de trazabilidad aplicada en FIDELITY FIXES: comparar el artefacto contra la base y contra el código vivo, no leer el artefacto de forma aislada.

**A.8 — Observación documental sobre RL1Interface2 (no investigada).**

Existe una segunda entrada del portafolio `RL1Interface2/RL1Interface2` (app_id 369), analizada posteriormente (2026-08-10) a la app `RL1Interface` estudiada aquí (app_id 344, analizada 2026-08-06), que podría representar una versión distinta, un reanálisis o una aplicación relacionada. Su relación con `RL1Interface` no ha sido determinada y queda fuera del alcance de este estudio y de FIDELITY FIXES — no se extrae ninguna conclusión sobre ella en este documento.

---

## B. Evidencia específica de cada aplicación (no generalizable)

**B.1 — La recuperabilidad de la UI depende enteramente de la tecnología, no es un problema único.**
- **WinForms (AFL_DataCenter):** la UI es **100% recuperable de forma estática hoy mismo**, sin ninguna herramienta nueva. Se verificó con evidencia aritmética exacta: 41 `Button` + 98 `TextBox` + 8 `Label` = 147, y hay exactamente 147 llamadas a `Controls.Add(...)` — la cuenta cierra. Los manejadores de evento están todos cableados de forma estándar y legible (`this.btnX.Click += new EventHandler(btnX_Click)`). Esto **confirma y refuerza** (no contradice) el hallazgo de la fase anterior con `DataTransfer`.
- **WPF (InterConfig):** la UI es **prácticamente invisible hoy**, y por una razón estructural distinta: el layout, los estilos, y sobre todo los `Command`/`ItemsSource` bindings viven en archivos `.baml` (XAML compilado a binario) que `ilspycmd` extrae como recurso pero no decompila a texto. El code-behind (`MainWindow.cs`, `UCConf.cs`) revela únicamente el **tipo** y el **nombre de variable** de 7 controles, vía los campos generados por `IComponentConnector.Connect` — cero información de apariencia, layout o de qué dispara qué. Este hallazgo **contradice** la generalización implícita de la fase anterior (basada solo en `DataTransfer`, que es WinForms) de que "la UI ya está en lo decompilado" — **no aplica a WPF con la técnica de decompilación actual**.
- **Console/Service (RL1Interface):** no aplica el concepto de "UI" en absoluto — confirmado por ausencia total de `Form`/`Window` y de cualquier framework de UI referenciado. El concepto equivalente relevante es el **disparador de ejecución** (loop de polling con `Thread.Sleep`, no un evento de usuario).

**B.2 — Las integraciones externas son completamente específicas de cada app, y aparecieron patrones no previstos.**
- AFL_DataCenter: únicamente filesystem (logs locales + share UNC), sin ninguna integración externa adicional.
- InterConfig: posible integración con Excel (`ExcelDataReader`) pero **no confirmada directamente** en el código propio de InterConfig (solo en el companion `InterAFL/ProcVM`, fuera del alcance de lectura de este estudio) — marcada explícitamente como Confidence, no Evidence.
- RL1Interface: integración **HTTP/REST real con equipo físico de planta** (`GET http://{ip}:8083/integration/results/combined-measurements`, con deserialización JSON tipada, timeout de 5s, manejo específico de errores HTTP). **Este es un hallazgo nuevo importante**: ni Modbus ni COM ni ninguna de las integraciones "sentinela" catalogadas en `KNOWN_LIMITATIONS.md` cubren "HTTP/REST hacia un endpoint local de equipo de planta" — es un patrón de integración real, confirmado con evidencia de código, que no estaba en el catálogo de límites conocidos del proyecto.

**B.3 — Los outputs/reportes de negocio son específicos, no comunes.** Ninguna de las tres apps de este estudio genera reportes de negocio (Excel/PDF/RDLC) — contrasta con `DataTransfer` (estudiada en la fase anterior), que sí tenía definiciones `.rdlc` reales embebidas y un motor de reportes propio. Conclusión: el área de "reportes/exports del legado" es de alto valor cuando aparece, pero su presencia es específica de cada app, no un patrón transversal — un extractor de reportes debe activarse condicionalmente (solo cuando se detecten las librerías/recursos asociados), no asumirse como necesario para toda app.

**B.4 — La estructura del código difiere radicalmente por arquitectura.** AFL_DataCenter es un monolito de un solo formulario de 9,687 líneas con 19 "pipelines" repetidos por copy-paste. InterConfig sigue MVVM limpio (View/ViewModel separados, lógica concentrada en `ConfVM`). RL1Interface es un único archivo de 46KB sin ninguna separación de responsabilidades. Esto tiene una consecuencia práctica real: **un extractor de "candidatos de lógica de negocio" tendría que buscar en lugares distintos según la arquitectura** (el formulario mismo en WinForms legado, el ViewModel en WPF/MVVM, el archivo único en consola) — no hay una única convención de "dónde vive la lógica" aplicable a las tres.

---

## C. Brechas del esquema actual

Contrastando los tres informes de "ajuste al esquema" (tablas: `apps, settings, sql_findings, io_findings, security_flags, db_procedures, db_tables, findings, unknowns`):

1. **No existe ninguna entidad "pantalla/ventana/control"** — confirmado como gap total en las tres apps. Ni siquiera para AFL_DataCenter, donde la evidencia de UI es perfectamente extraíble hoy, hay dónde guardarla.
2. **No existe ninguna entidad "evento→método"** — mismo gap total, independientemente de si el evento es un `Click` de WinForms o un `Command` de WPF (aunque en WPF además falta la fuente del dato, como se documentó en B.1).
3. **No existe ninguna entidad "arista de navegación"** — gap total; y con el matiz nuevo de este estudio: en las tres apps estudiadas, la navegación real observada es mínima o nula (un solo formulario/vista activa cada una) — a diferencia de `DataTransfer` (múltiples formularios reales: `FrmRework`, `frmValida`, `FrmReworkGeo`, etc.), que sí tendría aristas reales que perderse. **Esto sugiere que la necesidad de un "grafo de navegación" varía mucho entre apps del portafolio, y no se puede dimensionar bien con solo 3 muestras** — recomendamos no asumir su prioridad sin una muestra más amplia (ver Sección H).
4. **No existe ninguna entidad estructurada para "candidato de regla de negocio".** Es la brecha de esquema más importante confirmada por este estudio, precisamente porque A.2 demostró que es un patrón común a las tres apps. Hoy, cuando una regla de negocio no genera SQL/IO, no tiene ningún lugar — ni siquiera `findings`/`unknowns` tienen columnas semánticas (`rule_type`, `precondition`, `effect`) para ese propósito; solo podrían usarse como texto libre sin estructura.
5. **No existe ninguna entidad "alcanzabilidad/código muerto".** Gap totalmente nuevo, descubierto en este estudio (A.3) — no estaba identificado en el Gap Analysis original. No hay ninguna forma de marcar "este método/formulario no tiene ningún call-site conocido" de forma estructurada y consultable.
6. **No existe ninguna categoría semántica de integración** (HTTP a equipo vs. lectura de Excel vs. archivo de log genérico) — todo colapsa en `io_findings` genérico sin distinguir tipo de integración ni su contrato (puerto, ruta, formato esperado).
7. **No existe ninguna relación causal lógica→efecto.** El esquema modela nodos (una sentencia SQL, una operación de archivo) pero no la relación "esta condición de negocio dispara este efecto en BD/hardware" — esa cadena, documentada en los tres mapas de reconstrucción de este estudio, solo existe hoy en la lectura manual del código.
8. **Confirmación independiente de un gap ya señalado en el Gap Analysis**: en InterConfig, el reporte `.md` no incluye ninguna sección de introspección de esquema (SPs/tablas reales) pese a que se esperaba esa evidencia — consistente con la preocupación ya documentada sobre la persistencia de `db_procedures`/`db_tables` frente al ciclo de vida de `apps.id`.

---

## D. Brechas de análisis estático (recuperable en principio, no implementado hoy)

Estas son cosas que **si se leyera el código correctamente**, ya se podrían extraer sin necesidad de ejecutar nada ni de un humano:

- **Extracción de UI/controles/eventos para WinForms** — probado factible con evidencia aritmética exacta en AFL_DataCenter. Gap de implementación, no de técnica.
- **Señalización de candidatos de lógica de negocio** (condicionales/cálculos/validaciones/máquinas de estado fuera de contexto SQL) — el patrón es reconocible (métodos con ramificación no trivial, comparaciones contra listas cacheadas, guardas booleanas de reentrancia) aunque no 100% automatizable en su significado (eso pasa a la Sección E).
- **Análisis de alcanzabilidad/código muerto** — construir el grafo de llamadas desde el entry point y marcar métodos/formularios sin ningún call-site es una técnica estática estándar y bien acotada (no requiere entender significado, solo estructura de llamadas).
- **Gestión de artefactos derivados desactualizados (A.6/A.7)** — no es una corrección de extractor (el incremento FIDELITY FIXES cerró esta investigación confirmando que el extractor, la persistencia y el renderizado dinámico ya son correctos para los dos casos observados en A.6). Lo que sigue abierto, como tema documental/arquitectónico y sin solución implementada todavía, es decidir qué hacer con los `.md` estáticos de `reports/` para que no vuelvan a divergir silenciosamente de la fuente primaria (ver H.1).
- **Clasificación de HTTP/REST como categoría de integración distinta**, en vez de mezclarse con "archivos/procesos/red" genérico — mismo nivel de esfuerzo que ya se hizo para Reflection/COM/Modbus en la Fase 4 del roadmap.
- **Ampliar `THIRD_PARTY_ASSEMBLY_PATTERN`** para reconocer `ExcelDataReader` (segunda confirmación real del mismo gap ya señalado en `DISENO_INCREMENTO_3_CLASIFICACION.md` a partir de `DataTransfer`) — evidencia acumulada en 2 de las 4 apps inspeccionadas hasta ahora en todo el proceso de investigación.

## E. Brechas que requieren revisión humana

Cosas que el análisis estático puede *señalar* pero no puede *decidir* por sí solo:

- **Significado e vigencia de negocio de cada regla señalada** (¿la ventana de solape de 90 minutos en AFL_DataCenter sigue siendo el valor correcto? ¿por qué se excluyen conectores "MTP" en `RLAlta`? ¿qué implica una ventana de gracia de 1 día en la certificación de operadores?).
- **Si el código huérfano (A.3) es código muerto real o funcionalidad reservada/deshabilitada intencionalmente** — un desarrollador con contexto de negocio debe decidir si `frmColores`, `Once`/`YaExiste`/`CableBienCapturado`, o el destino de `btnExcel` se reconstruyen, se descartan explícitamente, o se investigan más.
- **Significado físico de la integración de hardware** (qué mide exactamente el endpoint `:8083` del equipo RL1/EXFO, qué representan los umbrales IL/RL para control de calidad de fibra óptica) — el código confirma *que* se mide y *qué estructura* tiene el resultado, pero no *por qué* esos valores importan para el negocio.
- **Confirmar bindings reales de WPF una vez decodificado el BAML** (si se resuelve D en el futuro) — aun con el XAML en texto, confirmar que el `Command="{Binding CmdGuardar}"` inferido por convención de nombres es efectivamente el que está enlazado requiere una revisión, no solo lectura automática.
- **Decidir el estatus real de las 2 tablas de las 10 referenciadas en RL1Interface que no fueron confirmadas por introspección real** (¿ya no existen, están mal escritas en el código, o la introspección simplemente no alcanzó a verificarlas?) — esto es Unknown hoy y requiere que alguien con acceso de negocio a esos sistemas lo aclare.

## F. Información que solo se obtiene en runtime (y bajo qué condiciones, si alguna vez se persigue)

- **Comportamiento real de los bindings/triggers de WPF** una vez decodificado el XAML: saber qué controla cada `Trigger`/`Converter` con datos reales requeriría, en el límite, observar la app corriendo — pero esto es un caso de borde; la mayor parte de la estructura (qué control existe, a qué se enlaza) sí es estática una vez resuelto el problema de BAML (Sección D), no requiere runtime.
- **Comportamiento exacto del endpoint HTTP del equipo RL1/EXFO** ante condiciones no vistas en el código (qué responde en casos límite, latencia real, comportamiento ante desconexión) — esto sí requeriría observación en vivo del equipo, y **cae directamente bajo la restricción #3 del contrato de seguridad** (no ejecutar/interactuar con equipos de planta sin aislamiento demostrado). No se recomienda perseguir esto salvo en un entorno de laboratorio explícitamente aprobado.
- **Confirmación definitiva de que el código huérfano nunca se ejecuta** — técnicamente, la ausencia de call-site dentro de un único ensamblado no descarta invocación desde otro ensamblado no incluido en este árbol (reflection, un job programado externo, etc.). Ampliar el análisis a ensamblados relacionados (companion assemblies) es todavía estático y debería intentarse antes de concluir que algo es runtime-only.

## G. Restricciones de seguridad aplicadas en este estudio

Se cumplieron literalmente las cinco restricciones del encargo: no se modificó ningún archivo legacy (todo el trabajo fue `Read`/`Grep` sobre copias ya decompiladas, staged en el contenedor de análisis); no se ejecutó ninguna aplicación legacy; no se realizó ninguna operación de escritura contra ninguna base de datos legacy (no se abrió ninguna conexión a SQL Server/Oracle en este estudio; la única consulta de base de datos que se hizo fue de solo lectura contra `qapv_analyzer.db`, que es la base propia del Analyzer, no una base legacy, y solo para obtener los conteos que sirvieron de contexto de selección — nunca ejecutada por los tres subagentes de estudio, quienes trabajaron exclusivamente sobre archivos ya decompilados); no se implementó ningún extractor nuevo; no se modificó código de producción del Analyzer ni su esquema. Las credenciales reales encontradas en `app.config` de las tres apps se enmascararon en todos los informes (incluido este documento).

## H. Incremento de implementación recomendado (no aprobado, para discusión)

Con la evidencia de este estudio, la recomendación se ajusta respecto a la de la fase anterior:

1. **Primero (ya cerrado): investigación de fidelidad completada por el incremento FIDELITY FIXES** — confirmó que no hay ningún bug vivo en el extractor para los dos casos de A.6; la causa real era artefactos `.md` desactualizados (A.6/A.7). Pendiente, como decisión documental/arquitectónica todavía **sin implementación aprobada**: tratar los `.md` estáticos de `reports/` explícitamente como artefactos derivados (no como fuente primaria de verdad, ver A.7), y decidir en una futura fase si deben (a) regenerarse automáticamente cada vez que la fuente primaria cambia, (b) validarse contra la base de datos antes de confiar en ellos, o (c) dejar de formar parte del flujo normal de consulta (dado que la vista web ya cubre ese mismo propósito leyendo siempre de la fuente primaria). No se diseña ni se implementa ninguna de estas tres opciones en este documento.
2. **Segundo, un extractor de UI/controles/eventos limitado a WinForms** — es el único de los tres frameworks donde se demostró que la información ya está 100% disponible en el código decompilado actual. No extender a WPF todavía.
3. **En paralelo, no como extractor sino como investigación**: determinar si existe una vía de decompilación de BAML→XAML (dentro del ecosistema ILSpy/`ilspycmd` u otra herramienta de solo lectura) antes de decidir qué hacer con WPF — hoy no sabemos si el problema de BAML es "no lo intentamos" o "no es posible con las herramientas actuales sin pasos adicionales". Esto es investigación, no implementación.
4. **Un análisis de alcanzabilidad/código muerto** (Sección D) — es barato, es común a las tres arquitecturas, y previene el riesgo real (confirmado en las tres apps) de reconstruir funcionalidad abandonada.
5. **Ampliar la muestra de apps con navegación real antes de priorizar un "grafo de navegación"** — la muestra de 3 apps de este estudio resultó tener navegación mínima/nula, lo cual podría llevar a subestimar esa necesidad si no se compara contra apps que sí navegan mucho (como `DataTransfer`). Recomendamos una ronda adicional de muestreo dirigida específicamente a apps con múltiples formularios antes de comprometer esfuerzo aquí.

Ningún punto de esta lista debe interpretarse como aprobado — quedan para tu revisión y decisión.

---

## Respuesta explícita a la pregunta final

> **¿Puede la arquitectura actual del Analyzer evolucionar hacia una plataforma de evidencia orientada a reconstrucción sin convertirse en un decompilador completo o un emulador de runtime?**

**Sí, con dos condiciones concretas que delimitan el límite arquitectónico.**

La razón por la que "sí" es defendible con la evidencia de este estudio: todo lo nuevo que este estudio demostró como necesario y factible —extracción de UI para WinForms, señalización de candidatos de lógica de negocio, análisis de alcanzabilidad, clasificación fina de integraciones— es, sin excepción, **una pasada estática más sobre código que ya está decompilado hoy**, siguiendo exactamente el mismo patrón que ya usa el Analyzer (`extract.py` reconociendo un patrón nuevo, escribiendo a una tabla nueva o extendida). Ninguno de estos requiere ejecutar la app legacy, y ninguno requiere entender C# a un nivel más profundo que buscar patrones estructurales (llamadas, herencia de `Form`, campos generados por el compilador) — no se necesita reimplementar un compilador ni un intérprete.

**Condición 1 — el límite de WPF/BAML debe resolverse como "más decompilación", no como ejecución.** El problema de InterConfig no es que falte observar la app corriendo; es que falta un paso de decompilación (BAML→XAML) que hoy no se usa. Si esa vía existe con herramientas de solo lectura (por investigar, Sección H.3), sigue siendo 100% estático y cabe perfectamente en la arquitectura actual. Si no existe, la limitación se acepta como `UNKNOWN` permanente para la estructura visual de WPF — **no** se debe compensar intentando ejecutar la app para "ver cómo se ve", porque eso sí cruzaría hacia territorio de runtime.

**Condición 2 — la "lógica de negocio" debe seguir tratándose como candidatos para revisión humana, nunca como inferencia automática de significado.** Este estudio confirma (A.2) que hay mucha lógica de negocio real fuera de SQL, pero también confirma que su *significado* (por qué 90 minutos, por qué se excluye "MTP", por qué 1 día de gracia) no es algo que un análisis de patrones pueda decidir — y no debería intentarlo. El proyecto ya tiene un flujo de revisión humana funcionando (`review_status`, 80 de 105 apps ya revisadas) — extenderlo a estas nuevas categorías de evidencia es coherente con la arquitectura actual, no un cambio de naturaleza del Analyzer.

**El límite arquitectónico real, donde SÍ tendría que convertirse en otra cosa:** si el objetivo dejara de ser "darle a un desarrollador humano suficiente evidencia para reconstruir y validar él mismo" (Level 2, según la fase anterior) y pasara a ser "demostrar automáticamente que la reconstrucción es equivalente al legado sin intervención humana" (Level 3). Eso sí exigiría, sin alternativa estática razonable: o bien un analizador semántico completo de C# (reimplementar buena parte de un frontend de compilador, algo que el proyecto ya evitó deliberadamente por diseño — ver `extract.py`), o bien observación real de comportamiento en runtime del legado y del equipo físico que controla (lo cual el propio contrato de seguridad del usuario restringe a un entorno de laboratorio aislado que hoy no existe). Ese es el mismo límite L2→L3 identificado en la fase anterior del Gap Analysis — este estudio no lo mueve, lo confirma con evidencia real de tres arquitecturas distintas.

---

**Quedo a la espera de tu revisión y aprobación antes de continuar con cualquier incremento de implementación.**
