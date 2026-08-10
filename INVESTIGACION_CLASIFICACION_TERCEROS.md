# Investigación — Clasificación de código propio vs. terceros en assemblies decompilados

**Fecha**: 2026-08-07
**Alcance**: investigación pura, sin cambios de producción, sin borrar archivos ni carpetas. Caso real usado: `EtiquetasRH.exe` (`bin\Debug\EtiquetasRH.exe`, app_id 363 en `qapv_analyzer.db`).

---

## 0. Resumen ejecutivo

El bloqueo por nombre de ensamblado (`THIRD_PARTY_ASSEMBLY_PATTERN` en `analyzer/decompile.py`) que agregamos ayer **no es, por sí solo, una estrategia confiable** para evitar decompilar/analizar código de terceros — la evidencia de esta investigación demuestra un caso real donde `DocumentFormat.OpenXml` volvió a decompilarse pese a estar ya bloqueado. En cambio, encontramos que **ilspycmd ya genera, sin que se lo pidamos, la evidencia exacta que necesitamos**: cada assembly decompilado (la app + cada companion) recibe su propia carpeta de nivel superior con su propio `.csproj`, y ese `.csproj` incluso lista sus propias referencias externas vía `<Reference><HintPath>`. Esa estructura, ya presente físicamente en disco, es suficiente para clasificar con confianza APPLICATION vs. THIRD_PARTY/FRAMEWORK vs. UNKNOWN **sin tocar decompile.py ni borrar nada** — el punto correcto de intervención es `extract.py` (que decide qué carpetas recorre), no el momento de la decompilación.

En el caso real medido: **98.8% del código decompilado es de terceros, 0.3% es la app real, 0.9% quedó en una categoría ambigua** que con una regla mejor (ver sección 5) se resuelve.

---

## 1. Diagnóstico — qué se investigó y cómo

Se analizó el directorio ya decompilado `decompiled/EtiquetasRH/EtiquetasRH/` (producto de dos corridas reales contra la red: la original de ayer, antes del fix, y la de hoy, después del fix) sin volver a decompilar nada ni borrar nada. Se cruzó esa evidencia física contra:

- El campo `apps.companion_assemblies` guardado en `qapv_analyzer.db` para `app_id=363`.
- El código real de `analyzer/decompile.py: find_companion_assemblies()`.
- Los `.csproj` de nivel superior generados por `ilspycmd -p`, incluyendo sus secciones `<Reference>`.
- Timestamps de archivos (`stat`) para reconstruir qué se generó en qué corrida.
- `ilspycmd --help`/`--version` para conocer las capacidades reales de la herramienta.

**Limitación explícita de esta investigación**: no se pudo ejecutar `ilspycmd` de forma aislada contra el `.exe` original (sin red desde esta sesión), así que todo el análisis parte de la salida YA producida, no de una consulta en vivo a la metadata del assembly. Donde esto importa, se marca abajo como "no confirmado, requiere prueba controlada".

---

## 2. Evidencia concreta

### 2.1 Librerías de terceros identificadas dentro del assembly

| Ensamblado decompilado (= `.csproj` de nivel superior) | ¿Ya estaba en el bloqueo de `decompile.py`? |
|---|---|
| `EtiquetasRH` | N/A — es la app misma |
| `ClosedXML.Parser` | **No** (el bloqueo solo cubre `ClosedXML` exacto, no `ClosedXML.Parser`) |
| `DocumentFormat.OpenXml` | Sí, desde el fix de ayer — **pero ver 2.3, la evidencia muestra que igual se decompiló** |
| `ExcelNumberFormat` | No |
| `RBush` | No |
| `SixLabors.Fonts` | No |

Los primeros 3 puntos del pedido (`SixLabors.*`, `DocumentFormat.OpenXml.*`, `ClosedXML.*`, "otras que encuentres") quedan respondidos: **las 5 librerías de arriba son exactamente el árbol de dependencias real de NuGet de `ClosedXML`** (la librería que usa `EtiquetasRH` para generar/leer Excel) — todas aparecen SIEMPRE juntas porque `ClosedXML` las trae consigo. Cualquier otra app del portafolio que use `ClosedXML` para Excel (hay varias, ver `EtiquetasRH - Copy`, `EtiquetasRHVS2022`, posiblemente `SafeRH` u otras) muy probablemente sufre el mismo problema.

### 2.2 ¿Alcanza la metadata de ilspycmd?

**Sí, y ya la estamos generando sin usarla.** Cada assembly decompilado (`ilspycmd -p -o <dir> <assembly>`, tanto para la app principal como para cada companion) produce:

1. **Una carpeta de nivel superior con el nombre exacto del assembly** (`EtiquetasRH`, `ClosedXML.Parser`, `DocumentFormat.OpenXml`, etc.) — esto es 1:1 confiable porque así organiza ilspycmd cualquier proyecto que decompila, no es una convención nuestra.
2. **Un `.csproj` con el mismo nombre**, que además de compilar, **lista las referencias externas del assembly** vía `<Reference Include="Nombre"><HintPath>ruta\al\.dll</HintPath></Reference>` — evidencia real, no inferida. Ejemplo real capturado en esta investigación:

```xml
<!-- ClosedXML.Parser.csproj -->
<Reference Include="System.Memory">
  <HintPath>\\naamrt-qcs25\...\EtiquetasRH\bin\Debug\System.Memory.dll</HintPath>
</Reference>
```

```xml
<!-- DocumentFormat.OpenXml.csproj -->
<Reference Include="DocumentFormat.OpenXml.Framework">
  <HintPath>\\naamrt-qcs25\...\EtiquetasRH\bin\Debug\DocumentFormat.OpenXml.Framework.dll</HintPath>
</Reference>
```

`ilspycmd --help` confirma además opciones que no estamos usando hoy y que podrían afinar esto más (no probadas en esta investigación por falta de red, quedan como trabajo futuro si se necesita más precisión):
- `-l|--list c` — listar clases de un assembly directamente (conteo exacto de tipos, sin aproximar por archivo `.cs`).
- `--generate-diagrammer-report-excluded` — pensado para otro propósito (diagramas), pero demuestra que ICSharpCode.Decompiler internamente sí sabe filtrar por `Type.FullName` con regex, lo que confirma que la metadata por tipo existe a ese nivel si algún día se necesita más granularidad que "por carpeta/assembly".

### 2.3 Hallazgo inesperado: el bloqueo de ayer no se aplicó de forma confiable

Este es el hallazgo más importante de la investigación, y se reporta explícitamente porque contradice lo que yo mismo había confirmado ayer con una prueba unitaria aislada.

- `analyzer/decompile.py: THIRD_PARTY_ASSEMBLY_PATTERN` **sí** bloquea `"DocumentFormat.OpenXml"` cuando se prueba el regex de forma aislada (confirmado, no cambió).
- La BD (`apps.companion_assemblies` para `app_id=363`) registra solo 4 companions: `ClosedXML.Parser.dll, ExcelNumberFormat.dll, RBush.dll, SixLabors.Fonts.dll` — **no** incluye `DocumentFormat.OpenXml.dll`, es decir, según ese registro, el bloqueo funcionó.
- **Pero** `DocumentFormat.OpenXml.csproj` existe físicamente, con una referencia real a `DocumentFormat.OpenXml.Framework.dll` (ver 2.2) — lo cual solo puede existir si `DocumentFormat.OpenXml.dll` fue decompilado de verdad.
- Los timestamps de los 6 `.csproj` de nivel superior son **todos consecutivos**, dentro de la ventana de 224 segundos que duró la corrida exitosa de hoy (11:18:30 → 11:21:21) — no hay forma de atribuir el de `DocumentFormat.OpenXml` (11:19:09) a una corrida distinta por simple cronología.

**Hipótesis mejor sustentada (no confirmada al 100%)**: el servidor Flask corre en modo debug con recarga automática; cuando edité `decompile.py` ayer, es probable que el proceso que ya estaba decompilando esta misma carpeta (la solicitud original de "1381s trabajando") **no se haya reiniciado limpiamente** — Werkzeug en Windows no siempre mata de forma confiable un worker bloqueado en una llamada `subprocess.run()` sincrónica — y haya seguido corriendo con el código viejo (sin el bloqueo) hasta terminar de decompilar `DocumentFormat.OpenXml`, mientras que la llamada nueva que yo disparé después sí corrió con el código corregido para el resto de companions.

**Conclusión operativa, independientemente de la causa exacta**: confiar únicamente en "evitar que se decompile" es frágil — depende del estado del proceso, de que el blocklist esté 100% completo, y de que no haya rutas indirectas. **No se puede garantizar con un blocklist en `decompile.py` que el código de terceros nunca llegue a existir en disco** — lo cual, de hecho, refuerza directamente la estrategia recomendada en la sección 4 (clasificar después, no solo prevenir antes).

---

## 3. Métricas de volumen (caso real, `EtiquetasRH`)

Conteo por carpeta de nivel superior (proxy de "tipos" — cada `.cs` es normalmente un tipo, con la salvedad de `partial class`/tipos anidados, que no se separaron en esta pasada):

| Categoría | Archivos `.cs` | % |
|---|---:|---:|
| **APPLICATION** (`EtiquetasRH`, `EtiquetasRH.Properties`, `EtiquetasRH.View`, `EtiquetasRH.ViewModel`) | 15 | 0.3% |
| **THIRD_PARTY** (`ClosedXML.Parser*`, `DocumentFormat.OpenXml*`, `ExcelNumberFormat`, `RBush`, `SixLabors.Fonts*`) | 5,435 | 98.8% |
| **UNKNOWN** (ver 3.1) | 52 | 0.9% |
| **TOTAL** | 5,502 | 100% |

### 3.1 Qué cae en UNKNOWN y por qué

Clasificando por "el nombre de la carpeta empieza con un nombre conocido de terceros", 52 archivos no calzan con ningún patrón:

`UnicodeTrieGenerator.StateAutomation` (17), `System.Diagnostics.CodeAnalysis` (15), `System.Runtime.CompilerServices` (10), `System` (5), `SixLabors` (3, el namespace raíz sin `.Fonts`), `Properties` (1), `System.Runtime.Versioning` (1).

**Estos NO son código propio** — son polyfills/shims de compatibilidad de C# moderno (`System.*`) embebidos por el compilador al apuntar a `netstandard2.0`, y utilidades internas de `SixLabors.Fonts` (su generador de tablas Unicode). El motivo de que caigan en UNKNOWN con una regla "empieza-con" es que **no comparten el mismo prefijo textual que el `.csproj` que los generó** — pero sí pertenecen, físicamente, a uno de los 6 assemblies ya identificados (no hay un séptimo `.csproj`). Esto es exactamente el argumento para clasificar **por membresía de carpeta dentro del set de `.csproj` generados**, no por coincidencia de texto de namespace (ver sección 4).

Con esa corrección, el UNKNOWN real cae a 0% en este caso — todo pertenece a uno de los 6 assemblies conocidos.

---

## 4. ¿Se puede evitar analizar terceros sin borrar evidencia física? — Sí

**Sí, de forma directa.** La evidencia (2.2) ya demuestra que la organización en disco (una carpeta de nivel superior = un assembly = un `.csproj`) es 1:1 confiable. Eso significa que `extract.py` puede decidir, ANTES de abrir un archivo, a qué assembly pertenece, sin necesitar leer el contenido del archivo ni tocar `decompile.py`:

1. `find_settings()`/`scan_project()` hoy hacen `root.rglob("*.cs")` sin distinguir de dónde viene cada archivo.
2. Un nuevo paso, ejecutado una vez por app antes de iterar archivos, listaría los `.csproj` de nivel superior de `root` y clasificaría cada uno:
   - Si el nombre coincide con el nombre de la app (`app_name`, ya lo sabe `pipeline.py`) → **APPLICATION**.
   - Si coincide con `THIRD_PARTY_ASSEMBLY_PATTERN` (el mismo patrón que ya existe en `decompile.py`, reutilizado, no reinventado) → **THIRD_PARTY/FRAMEWORK**.
   - Si no coincide con ninguno de los dos → **UNKNOWN_COMPANION** (candidato a ClassLib propio — como el caso real ya documentado de `AFL.Dashboard`/`ClassLib.dll` — **debe seguir escaneándose**, nunca se descarta solo por no reconocerlo).
3. El recorrido de archivos (`rglob`) se restringe a las carpetas clasificadas como APPLICATION o UNKNOWN_COMPANION — las THIRD_PARTY/FRAMEWORK se saltan.

**Nada se borra.** Los archivos de `DocumentFormat.OpenXml`, `SixLabors.Fonts`, etc. seguirían exactamente donde están — simplemente `extract.py` nunca los abre. Si alguna vez se necesita auditar qué se excluyó (para depurar un falso negativo), la evidencia física completa sigue ahí para revisarla a mano.

---

## 5. Generalización: EXE+DLLs vs. single-file/self-contained

**Confirmado para el caso EXE+DLLs** (`bin\Debug\EtiquetasRH.exe`, el caso analizado en esta investigación a fondo): el mecanismo es exactamente el ya conocido — `find_companion_assemblies()` encuentra `.dll` sueltos junto al `.exe` y los decompila cada uno en su propia carpeta/`.csproj`. La estrategia de la sección 4 aplica sin cambios.

**No confirmado directamente para single-file/self-contained** (`bin\Debug\app.publish\EtiquetasRH.exe`, la otra mitad de este mismo caso real, analizada ayer). Esa corrida original SÍ decompiló `DocumentFormat.OpenXml` en grande (3,528 de 3,567 archivos), pero **no puedo separar con certeza, a partir de la evidencia que quedó en disco, si eso vino de `find_companion_assemblies()` encontrando un `.dll` suelto (poco probable en un publish single-file real, que normalmente empaqueta las dependencias DENTRO del único ejecutable) o de que `ilspycmd` mismo sepa desempaquetar un bundle single-file y decompile sus assemblies internos automáticamente** — la salida de `app.publish` y la de `bin\Debug` terminaron mezcladas en la misma carpeta de destino (mismo `app_name` calculado, ver hallazgo lateral en la sección 7) antes de que pudiera aislarlas.

**Lo que sí se puede afirmar con la información disponible**: si `ilspycmd` desempaqueta un single-file bundle, lo hace generando la MISMA forma de evidencia (carpetas + `.csproj` por assembly) — es el comportamiento documentado y esperado de ICSharpCode.Decompiler al leer un bundle de un solo archivo (no es una función nueva, es soporte nativo de la librería). Si eso se confirma con una prueba aislada, **la misma estrategia de la sección 4 cubre ambos casos sin necesitar dos rutas de código distintas.**

**Prueba controlada recomendada antes de dar esto por buena** (no ejecutada en esta investigación, por instrucción explícita de no tocar producción): analizar `app.publish\EtiquetasRH.exe` de nuevo, pero con un `app_name` distinto al de `bin\Debug\EtiquetasRH.exe` (para que caigan en carpetas de salida separadas y no se mezclen), y comparar la lista de `.csproj` generados contra la de esta investigación.

---

## 6. Estrategia recomendada (resumen)

1. **No depender solo de prevenir la decompilación** (sección 2.3 demuestra que es frágil). Mantener el blocklist de `decompile.py` como optimización de "ahorrar trabajo cuando se puede" — sigue siendo útil, no se propone quitarlo — pero no como única defensa.
2. **Agregar una clasificación post-decompilación en `extract.py`**, basada en los `.csproj` de nivel superior ya generados (evidencia gratis, ya existente). Tres categorías: `APPLICATION`, `THIRD_PARTY_OR_FRAMEWORK`, `UNKNOWN_COMPANION` (esta última SIEMPRE se escanea, nunca se descarta por default).
3. **Reutilizar el mismo `THIRD_PARTY_ASSEMBLY_PATTERN`** para ambas capas (prevención en `decompile.py`, clasificación en `extract.py`) — una sola fuente de verdad, ya existe, no se duplica.
4. **Nunca borrar ni mover archivos.** La exclusión vive enteramente en qué carpetas recorre `extract.py`, no en qué existe en disco.

---

## 7. Hallazgo lateral (no es el foco de esta investigación, pero se documenta)

Al revisar `app_id` para `EtiquetasRH`, se confirmó que la corrida de hoy (`bin\Debug\EtiquetasRH.exe`) generó un `app_name` idéntico al de ayer (`bin\Debug\app.publish\EtiquetasRH.exe`) — **`EtiquetasRH/EtiquetasRH`** en ambos casos, porque `project_label()` agrupa por la carpeta encima de `bin`, que es la misma para ambos ejecutables. El upsert-por-nombre de `save_analysis()` **borró silenciosamente la fila de ayer (app_id 362)** al guardar la de hoy (app_id 363) — es el mismo "bug de colisión de nombres" ya documentado como conocido-y-no-corregido en `project_ilspycmd_toolchain` (memoria de sesiones anteriores). No se toca en esta investigación (fuera de alcance de lo pedido), solo se deja constancia porque explica por qué solo hay una fila en la BD para dos análisis reales distintos.

---

## 8. Archivos que habría que modificar (si se decide implementar)

| Archivo | Cambio propuesto |
|---|---|
| `analyzer/decompile.py` | Ninguno obligatorio. Opcional: exportar `THIRD_PARTY_ASSEMBLY_PATTERN` ya es público (no requiere cambio) para que `extract.py` lo importe y reutilice. |
| `analyzer/extract.py` | Nueva función (ej. `_classify_top_level_assemblies(root, app_name) -> dict[str, str]`) que lea los `.csproj` de nivel superior y devuelva la clasificación por carpeta. `find_settings()`/`scan_project()` la usan para filtrar qué carpetas de `root.rglob(...)` visitan. |
| `analyzer/pipeline.py` | Posiblemente pasar `app_name`/el nombre real del assembly principal a `extract.py` si no ya lo tiene disponible ahí (hoy `find_settings`/`scan_project` solo reciben `root`). |
| `analyzer/db.py` | Ninguno — no se propone ningún campo nuevo en esta fase, solo un cambio de comportamiento en qué se escanea. |
| `tests/` | Ver sección 9. |

**Nada de esto se implementó** — es la lista para cuando se decida seguir adelante.

---

## 9. Tests necesarios (si se decide implementar)

1. **Fixture nuevo, con evidencia real**: copiar la forma exacta de `EtiquetasRH` (una carpeta app pequeña + un `ClosedXML.Parser.csproj`/`DocumentFormat.OpenXml.csproj` mínimos, recortados, congelados) a `tests/fixtures/` — mismo patrón ya usado en todo el proyecto (fixtures reales, no sintéticos cuando hay un caso real disponible).
2. **Clasificación correcta**: dado ese fixture, `_classify_top_level_assemblies()` debe devolver `APPLICATION` para la carpeta de la app, `THIRD_PARTY_OR_FRAMEWORK` para `ClosedXML.Parser`/`DocumentFormat.OpenXml`.
3. **UNKNOWN_COMPANION nunca se descarta**: un fixture con una carpeta que NO matchee el patrón de terceros (simulando un `ClassLib` propio) debe seguir apareciendo en los `sql_findings`/`settings` extraídos — regresión directa contra el caso ya validado de `AFL.Dashboard`.
4. **`find_settings()`/`scan_project()` ya no abren archivos de terceros**: contar cuántos `.cs` se leen (ej. con un mock/spy sobre `Path.read_text`) antes y después, confirmando que las carpetas THIRD_PARTY quedan fuera del recorrido.
5. **No hay falso negativo cuando el `.csproj` de terceros no existe todavía** (app analizada con una versión vieja del pipeline, sin estos `.csproj`): debe degradar con gracia a "escanear todo" (comportamiento actual), nunca lanzar excepción.
6. **Regresión de portafolio**: re-correr el pipeline completo contra el portafolio ya analizado y confirmar que ningún `sql_finding`/`setting` real desaparece (comparar conteos antes/después, mismo mecanismo ya usado en los Incrementos 2 y 3A).

---

## 10. Riesgos de falsos positivos / falsos negativos

| Riesgo | Probabilidad | Mitigación propuesta |
|---|---|---|
| **Falso positivo**: clasificar un `ClassLib` propio como THIRD_PARTY porque su nombre coincide por coincidencia con el patrón (ej. una empresa que tuviera un assembly interno llamado literalmente `Serilog.Utils` sin ser el paquete real) | Baja — el patrón actual ya es específico (nombres de paquetes reales, no genéricos) | Mantener el patrón como **allowlist explícita de terceros conocidos**, nunca heurísticas genéricas tipo "si tiene un punto en el nombre". Cualquier ampliación del patrón debe basarse en evidencia real (como se hizo con `DocumentFormat.OpenXml` esta semana), nunca especulativa. |
| **Falso negativo**: un companion de terceros NUEVO (no listado) se sigue escaneando como `UNKNOWN_COMPANION` — desperdicia tiempo de análisis pero NO produce datos incorrectos (`extract.py` simplemente no encontrará triggers SQL/IO reales ahí, o si los encuentra por una coincidencia rarísima, ya se filtran hoy por su propio contenido) | Media-alta — es, por diseño, el comportamiento por defecto seguro | Aceptable: es exactamente la postura ya establecida en el proyecto ("ante la incertidumbre, no inferir silenciosamente") — más lento pero nunca silenciosamente incorrecto. |
| **La clasificación depende de que el `.csproj` se haya generado correctamente** — si `ilspycmd` cambia de versión y altera su convención de nombres de proyecto, la clasificación por nombre de carpeta se rompe silenciosamente | Baja pero real | Test de regresión (sección 9, punto 5) que confirme degradación segura a "escanear todo" si no se encuentra ningún `.csproj` reconocible. |
| **El hallazgo de la sección 2.3 (bloqueo que no se aplicó de forma confiable) puede repetirse** — si el problema es realmente el reload de Werkzeug en Windows, cualquier edición de código mientras el servidor está analizando algo puede dejar corridas en un estado híbrido (mitad código viejo, mitad nuevo) | Media, específico a este entorno de desarrollo | No es un riesgo del extractor en sí — es un riesgo operativo. Recomendación práctica: evitar editar `analyzer/*.py` mientras hay un análisis largo corriendo; si se necesita, reiniciar el servidor manualmente (no confiar en el auto-reload) antes de la siguiente corrida. |

---

## 11. Siguiente paso

Ninguno todavía — por instrucción explícita, esta investigación no implementa nada. Queda a la espera de decisión: si se aprueba la estrategia de la sección 4/6, el siguiente paso sería la prueba controlada de la sección 5 (single-file aislado) antes de escribir código, para no generalizar sin confirmar el segundo caso.
