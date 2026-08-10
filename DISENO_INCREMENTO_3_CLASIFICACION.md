# Diseño técnico — Incremento 3: clasificación post-decompilación

**Fecha**: 2026-08-10
**Estado**: propuesta de diseño, **sin código, sin tests, sin commit**. Implementa la estrategia ya aprobada en `INVESTIGACION_CLASIFICACION_TERCEROS.md` (sección 4/6) con las conclusiones ya confirmadas por la prueba aislada single-file.

---

## 0. Resumen de la propuesta

La clasificación se calcula en **`analyzer/pipeline.py`** (el único lugar que ya conoce tanto la *intención* — qué le pedimos a `ilspycmd` que decompilara — como la *evidencia* — qué `.csproj` existen realmente en disco después), vive en un **módulo nuevo y pequeño** (`analyzer/classification.py`, reutilizando `THIRD_PARTY_ASSEMBLY_PATTERN` de `decompile.py` sin duplicarlo), y se **pasa como parámetro** a `find_settings()`/`scan_project()`, que la usan solo para decidir qué carpetas de nivel superior saltarse en su `rglob("*.cs")` — nunca para decidir qué archivos existen en disco.

**Regla de seguridad central** (Principio 3 de `ARCHITECTURAL_PRINCIPLES.md`, "ante la incertidumbre, preservar evidencia y pedir validación humana"): una carpeta **solo** se salta si matchea positivamente `THIRD_PARTY_ASSEMBLY_PATTERN`. Todo lo demás — incluyendo cualquier caso ambiguo, inesperado, o si no hay ningún `.csproj` en absoluto — se sigue escaneando exactamente como hoy. El costo de un falso negativo (escanear de más) es tiempo; el costo de un falso positivo (dejar de escanear código real) sería perder lógica de negocio real — la asimetría de riesgo justifica el sesgo hacia "escanear de más".

---

## 1. Archivos inspeccionados y hallazgos clave

| Archivo | Qué hace hoy (relevante a este diseño) |
|---|---|
| `analyzer/decompile.py` | `THIRD_PARTY_ASSEMBLY_PATTERN` (línea 15-26, ya público, reutilizable sin duplicar). `find_companion_assemblies(assembly_path)` (línea 63-75) — glob de `.dll` sueltos junto al `.exe`, filtrados por el patrón; devuelve solo los que **no** matchean (candidatos a código propio). `decompile()` (línea 87-110) invoca `ilspycmd -p -o <output_dir> <assembly>` una vez por cada uno. |
| `analyzer/pipeline.py` | `run_analysis()` (línea 30-60) es el **único punto que conoce ambas cosas**: la intención (`assembly_path`, y la lista que devuelve `find_companion_assemblies()`) y, después de decompilar, el resultado físico en `output_dir`. Nunca toca la BD. |
| `analyzer/extract.py` | Tres `rglob` sobre `root`: `_find_appconfig_connection_strings()` (`*.config`, línea 113), `find_settings()` (`*.cs`, línea 164), `scan_project()` (`*.cs`, línea 781). Ninguno recibe hoy información sobre qué assembly originó cada archivo — solo reciben `root: Path`. |
| `analyzer/db.py` | No tiene nada relacionado a assemblies/companions salvo `apps.companion_assemblies` (texto plano, lista de nombres de `.dll` que `pipeline.py` decompiló como companion). **No se necesita ningún cambio de esquema para este incremento** — la clasificación es un dato de tiempo de análisis, no de almacenamiento (ver decisión 4 abajo). |
| `tests/test_characterization.py`, `test_sentinels.py` | Ningún test hoy ejercita `.csproj` ni clasificación. `test_sentinels.py` ya reconoce informalmente el concepto de "librerías vendorizadas" (comentario línea 55-59: iText, BouncyCastle, OpenCvSharp, BenchmarkDotNet, Roslyn) pero **sin filtrarlas de forma automática** — "sería repetir esa auditoría en cada test run". Este incremento le daría a ese test una forma de filtrar automáticamente, como beneficio colateral (no se propone hacerlo ahora, solo se señala). |
| `tests/fixtures/*` | **Ningún fixture existente tiene un `.csproj`** — todos son archivos `.cs` sueltos, sin la estructura de proyecto real que genera `ilspycmd -p`. Se necesita al menos un fixture nuevo para probar la clasificación (ver sección 11). |
| `ARCHITECTURAL_PRINCIPLES.md` | Principio 3 (ya citado arriba) y Principio 4 ("la complejidad tecnológica solo crece con evidencia objetiva") — este incremento no requiere ADR nuevo: es un cambio de comportamiento reversible y aditivo en el motor de extracción, no una decisión de identidad/almacenamiento permanente (los únicos dos dominios con ADRs hoy). |
| `analyzer/techstack.py` (no pedido explícitamente, pero relevante) | **Hallazgo lateral real**: `techstack.py` también hace `root.rglob("*.csproj")` (línea 35) y `root.rglob("*.cs")` (línea 44) sin ninguna restricción — esto **ya está causando un bug observado**: el análisis de `EtiquetasRH/EtiquetasRH` (caso `bin\Debug`) mostró `dotnet_target: netstandard2.0` en vez de `net48`, porque `techstack.detect()` recogió el `.csproj` de un companion (`ClosedXML.Parser`, `netstandard2.0`) en vez del de la app real. No se propone corregirlo en este incremento (el usuario acotó el análisis a `extract.py`/`decompile.py`/`pipeline.py`/`db.py`), pero se documenta como candidato directo para reutilizar la misma clasificación después. |

---

## 2. Respuestas a los 14 puntos

### 1. Dónde debe vivir la clasificación

**El cómputo**: en `analyzer/pipeline.py: run_analysis()`, porque es el único lugar que ya tiene ambas señales sin necesitar pasar información nueva entre módulos.

**La lógica/API reutilizable**: en un módulo nuevo, `analyzer/classification.py` — mismo patrón ya establecido en el proyecto (un módulo chico por concepto: `evidence.py`, `confidence.py`, `failure_catalog.py`, `unknown.py`, `techstack.py`, `security.py`). Ver decisión 1 para la alternativa considerada (meterlo en `decompile.py`).

### 2. Qué API debería tener

Dos señales combinadas, no solo una:

```python
# analyzer/classification.py (nuevo modulo, propuesta de firma -- no implementado)

AssemblyClassification = Literal["APPLICATION", "THIRD_PARTY_OR_FRAMEWORK", "UNKNOWN_COMPANION"]

def classify_top_level_assemblies(
    output_dir: Path,
    application_assembly_stem: str,
    intentional_companion_stems: set[str],
) -> dict[str, AssemblyClassification]:
    """Enumera los .csproj de nivel superior de output_dir (ya generados por
    ilspycmd) y clasifica cada nombre. No lee ningun .cs, no modifica nada."""
```

Y en `extract.py`, un parámetro nuevo (opcional, con default seguro) en las dos funciones que hacen `rglob("*.cs")`:

```python
def find_settings(root: Path, skip_top_level: frozenset[str] = frozenset()) -> list[SettingEntry]: ...
def scan_project(root: Path, skip_top_level: frozenset[str] = frozenset()) -> tuple[list[SqlFinding], list[LocalIOFinding]]: ...
```

`skip_top_level` es simplemente el subconjunto de `classify_top_level_assemblies()` cuyo valor es `THIRD_PARTY_OR_FRAMEWORK` — `pipeline.py` hace esa reducción antes de llamar a `extract.py`, que nunca necesita saber sobre `AssemblyClassification` como concepto.

### 3. Cómo identificar el assembly APPLICATION

**Por intención, no por adivinanza**: es literalmente `assembly_path.stem` — el argumento que `pipeline.run_analysis()` ya pasó como primer `decompile()` (línea 35 de `pipeline.py`). Es la única entrada que sabemos con certeza que es la app real, porque fue la que el usuario/`app.py` eligió analizar. **No** se debe inferir por texto/patrón — la app real puede tener cualquier nombre, incluyendo uno que coincida por casualidad con un patrón de terceros.

Nota: el nombre del `.csproj` generado por `ilspycmd` es el `<AssemblyName>` real del assembly, que normalmente coincide con el stem del `.exe` pero no siempre (ver memoria de sesión: `CentiRL1`'s módulo `Classes.exe` decompila a un assembly llamado `Classes`, no `Classes.Borrar`). Por eso la clasificación debe hacer el match por el `.csproj` generado, no asumir que el nombre del archivo original es igual al nombre del assembly.

### 4. Cómo identificar THIRD_PARTY_OR_FRAMEWORK

Cualquier `.csproj` de nivel superior cuyo nombre (1) **no** sea el de APPLICATION, (2) **no** esté en `intentional_companion_stems` (los que `find_companion_assemblies()` ya decidió que valía la pena decompilar), y (3) **sí** matchee `THIRD_PARTY_ASSEMBLY_PATTERN` (reutilizado de `decompile.py`, ver punto 10).

Esto cubre explícitamente el caso ya confirmado en la investigación anterior (`DocumentFormat.OpenXml` decompilado pese a estar bloqueado) — aunque `decompile.py` haya fallado en *prevenir* la decompilación, la clasificación post-hoc sigue detectándolo correctamente porque vuelve a aplicar el mismo patrón contra lo que **realmente** existe en disco, no contra lo que se *pretendía* decompilar.

### 5. Cómo representar UNKNOWN_COMPANION

Cualquier `.csproj` de nivel superior que no sea APPLICATION y que **no** matchee `THIRD_PARTY_ASSEMBLY_PATTERN` — esto incluye tanto los companions intencionales (`intentional_companion_stems`, el caso normal de un `ClassLib` propio) como cualquier `.csproj` verdaderamente inesperado (punto 8). **Nunca se excluye del escaneo.** No necesita un tratamiento especial en el código más allá de "no está en el set de `skip_top_level`" — es, por diseño, el comportamiento por default de hoy (escanear todo), simplemente ahora nombrado explícitamente en vez de ser "todo lo que no es THIRD_PARTY" de forma implícita.

### 6. Cómo restringir `rglob()` sin cambiar la evidencia física

`root.rglob("*.cs")` sigue recorriendo el disco exactamente igual — el filtro se aplica **después**, sobre cada `Path` que ya se encontró, antes de abrirlo:

```python
def _top_level_folder(cs_file: Path, root: Path) -> str:
    return cs_file.relative_to(root).parts[0]

for cs_file in root.rglob("*.cs"):
    if _top_level_folder(cs_file, root) in skip_top_level:
        continue
    ...  # el resto del loop, sin cambios
```

Cero archivos se borran, mueven, ni se dejan de generar en la decompilación — la restricción vive enteramente en qué se **abre y procesa**, no en qué existe.

### 7. Qué debe ocurrir si no existe ningún `.csproj`

`classify_top_level_assemblies()` devuelve un diccionario vacío (o solo con APPLICATION si su carpeta existe pero sin `.csproj` — caso raro). `skip_top_level` queda vacío. `find_settings()`/`scan_project()` escanean **todo**, idéntico al comportamiento actual (ningún regresión posible — es literalmente el código de hoy con un parámetro opcional que nadie llenó). Esto cubre: decompilaciones viejas hechas antes de este incremento, o cualquier estructura de salida no generada por `ilspycmd -p`.

### 8. Qué debe ocurrir si existe un `.csproj` inesperado

Por diseño (puntos 4/5), un `.csproj` "inesperado" (no es APPLICATION, no estaba en la lista de companions intencionales) se clasifica según si matchea el patrón de terceros o no:
- Si matchea → `THIRD_PARTY_OR_FRAMEWORK`, se salta. Esto es intencional y **deseable**: es exactamente el mecanismo que atrapa una fuga como la de `DocumentFormat.OpenXml`.
- Si no matchea → `UNKNOWN_COMPANION`, se sigue escaneando. Nunca se lanza una excepción ni se detiene el análisis por un `.csproj` que no esperábamos — es justamente el escenario para el que existe esta categoría.

### 9. Cómo evitar falsos negativos

El diseño es una **lista de bloqueo explícita** (`THIRD_PARTY_ASSEMBLY_PATTERN`), nunca una lista de permitidos. Cualquier cosa no reconocida cae del lado seguro (se escanea). No hay heurísticas de "parece de terceros porque..." (ej. "tiene muchos archivos", "no tiene SQL") — solo coincidencia exacta de nombre contra un catálogo curado, igual que ya exige el proyecto para `CONFIDENCE_TABLE`/`FAILURE_CATALOG`. El riesgo real que queda (ampliar el patrón con evidencia futura) ya tiene precedente y proceso: así se agregó `DocumentFormat.OpenXml` ayer.

### 10. Cómo reutilizar `THIRD_PARTY_ASSEMBLY_PATTERN` sin duplicarlo

`analyzer/classification.py` hace `from .decompile import THIRD_PARTY_ASSEMBLY_PATTERN` — una sola fuente de verdad, usada tanto para decidir *qué compensar decompilar* (uso actual, en `decompile.py`) como *qué carpetas no escanear* (uso nuevo, en `classification.py`). Si el patrón se amplía en el futuro (como con `DocumentFormat.OpenXml`), ambos comportamientos se actualizan automáticamente sin tocar dos lugares.

### 11. Qué tests son necesarios

1. **Fixture nuevo** (`tests/fixtures/classification_case/` o similar) que replique la forma real de `ilspycmd -p`: una carpeta `AppReal/` con su `AppReal.csproj` + 1-2 archivos `.cs` con un trigger SQL real, y una carpeta `ClosedXML.Parser/` (o nombre de terceros ya conocido) con su propio `.csproj` + un `.cs` que **si se escaneara** produciría un falso hallazgo — para probar que efectivamente NO se escanea.
2. `classify_top_level_assemblies()`: devuelve APPLICATION/THIRD_PARTY_OR_FRAMEWORK/UNKNOWN_COMPANION correctamente contra el fixture.
3. **UNKNOWN_COMPANION nunca se descarta** — regresión directa reusando el fixture ya existente de `AFL.Dashboard`/`ClassLib` (o uno nuevo análogo) para confirmar que un companion propio real sigue apareciendo en `sql_findings`.
4. `find_settings()`/`scan_project()` con `skip_top_level` no vacío: confirmar que los archivos de la carpeta de terceros del fixture nunca se abren (se puede instrumentar con un contador de llamadas a `Path.read_text`, o simplemente confirmar que ningún finding proviene de esa carpeta).
5. **Degradación sin `.csproj`**: reusar cualquier fixture actual (que no tiene `.csproj`) sin pasar `skip_top_level` — debe comportarse exactamente igual que hoy (ya lo garantizan los tests existentes, con tal de no romperlos).
6. **Regresión de portafolio** (ver punto 12) — no es un test de pytest, es una corrida controlada aparte, mismo mecanismo ya usado en los Incrementos 2 y 3A.

### 12. Qué regresiones medir en el portafolio

- **Conteo de `sql_findings`/`settings` por app, antes vs. después**: debe ser **idéntico** (no se espera perder ningún hallazgo real — todo lo que hoy se encuentra vive en APPLICATION o en un companion que ya se decompiló intencionalmente, nunca en las carpetas que se van a excluir).
- **Verificación específica anti-falso-negativo**: antes de activar el filtro en producción, correr `classify_top_level_assemblies()` contra **todo** `decompiled/` ya existente (dry-run, sin tocar nada) y confirmar que ningún `sql_finding`/`setting` ya guardado en la BD real proviene de una carpeta que la nueva clasificación marcaría como `THIRD_PARTY_OR_FRAMEWORK`. Si aparece alguno, es una señal de un falso positivo del patrón que hay que resolver antes de activar el filtro, no después.
- **Tiempo de decompilación+escaneo por app**: debe mejorar notablemente para las apps con companions de terceros grandes (ej. `EtiquetasRH` — aunque el filtro de escaneo no acelera la *decompilación* en sí — ver decisión 5 sobre si también vale la pena atacar eso — sí acelera el *escaneo*, que hoy abre miles de archivos de terceros innecesariamente).

### 13. Archivos concretos que tendrían que modificarse

| Archivo | Cambio |
|---|---|
| `analyzer/classification.py` | **Nuevo**. `classify_top_level_assemblies()` y el tipo `AssemblyClassification`. |
| `analyzer/pipeline.py` | `run_analysis()` calcula la clasificación después de decompilar todo, reduce a `skip_top_level`, lo pasa a `find_settings()`/`scan_project()`. |
| `analyzer/extract.py` | `find_settings()`/`scan_project()` ganan el parámetro opcional `skip_top_level`, con el filtro descrito en el punto 6. `_find_appconfig_connection_strings()` — ver decisión 6, probablemente sin cambio. |
| `tests/fixtures/<nuevo>/` | Fixture nuevo descrito en el punto 11. |
| `tests/test_classification.py` (nuevo) | Los tests del punto 11. |

### 14. Archivos que NO deberían modificarse

- `analyzer/decompile.py` — se **lee** (`THIRD_PARTY_ASSEMBLY_PATTERN`), nunca se edita. El blocklist de prevención se conserva tal cual (regla 8 del pedido).
- `analyzer/db.py` — ningún cambio de esquema ni de `save_analysis()`. La clasificación es un dato transitorio de una corrida de análisis, no algo que se persista (ver decisión 4).
- `analyzer/evidence.py`, `confidence.py`, `failure_catalog.py` — sin relación con este incremento.
- Cualquier fixture existente — se agrega uno nuevo, ninguno se toca.
- `analyzer/techstack.py` — bug real relacionado (sección 1), pero fuera del alcance pedido explícitamente para este incremento.

---

## 3. Decisiones que necesito aprobar antes de implementar

1. **¿Módulo nuevo (`analyzer/classification.py`) o agregarlo dentro de `decompile.py`?**
   Recomiendo el módulo nuevo — separación de responsabilidades ya es el patrón establecido del proyecto, y `decompile.py` se queda enfocado en "invocar ilspycmd", no en "interpretar qué generó". La alternativa (meterlo en `decompile.py`) evita un archivo nuevo pero mezcla dos responsabilidades distintas en un módulo que ya es denso.

2. **¿Dónde se calcula la clasificación — una vez en `pipeline.py` y se pasa por parámetro, o cada función de `extract.py` la recalcula internamente?**
   Recomiendo calcularla una vez en `pipeline.py` (evita I/O duplicado — dos `rglob("*.csproj")` en vez de uno — y hace explícito, en la firma de `find_settings()`/`scan_project()`, que dependen de esta entrada). La alternativa (que cada función la calcule sola) no requeriría cambiar la firma de esas funciones ni tocar `pipeline.py`, pero duplica trabajo y esconde la dependencia.

3. **Cuando el nombre de un companion intencional (pasó el blocklist de `decompile.py`) no coincide con el nombre del `.csproj` que generó `ilspycmd`** (ej. por alguna normalización interna de ilspycmd) **¿confiamos en el nombre del `.dll` original o en el nombre del `.csproj` real?**
   Recomiendo el `.csproj` real (evidencia post-hoc) como fuente de verdad para TODO excepto la identidad de APPLICATION (que sí debe ser por intención — ver punto 3 de la sección 2). No tengo evidencia hoy de que esta discrepancia ocurra en la práctica; lo marco como riesgo teórico a vigilar, no una decisión con dos alternativas igual de sustentadas.

4. **¿Persistir la clasificación en la BD (nueva columna en `apps`, ej. `third_party_folders_json`) para poder auditar después qué se excluyó, o mantenerla puramente en memoria durante el análisis?**
   Recomiendo **no persistirla** por ahora (Principio 4 — no agregar complejidad de almacenamiento sin evidencia de que se necesita) — la evidencia física completa (todos los `.cs` de terceros) sigue en `decompiled/` sin cambios, así que cualquier auditoría futura puede re-derivar la clasificación corriendo `classify_top_level_assemblies()` de nuevo contra esa misma carpeta. Si en la práctica se necesita consultar "qué se excluyó" sin volver a correr el análisis, esa sería evidencia real para revisar esta decisión.

5. **¿Este incremento también debería evitar *decompilar* (no solo escanear) el companion cuando ya sabemos, por el blocklist, que es de terceros — o seguimos decompilando todo y solo filtramos en el escaneo?**
   El pedido del usuario ya fija esto (regla 8: "el blocklist se conserva como optimización, pero no será la única defensa") — se mantienen ambas capas. No lo marco como decisión abierta, solo lo confirmo explícitamente para que quede documentado junto a las demás.

6. **¿Aplicar la misma restricción a `_find_appconfig_connection_strings()` (`root.rglob("*.config")`)?**
   Riesgo bajo — un `.csproj` de terceros casi nunca trae su propio `app.config` en la salida de `ilspycmd`. Recomiendo **incluirlo por consistencia y por ser gratis** (mismo `skip_top_level`, una línea de cambio) en vez de dejarlo como una excepción sin justificación clara, pero no es crítico si se decide omitirlo en esta primera pasada.

7. **¿Extender esta misma clasificación a `analyzer/techstack.py` ahora (dado el bug real ya encontrado: `dotnet_target` incorrecto) o dejarlo como un incremento separado?**
   El pedido del usuario acotó el análisis a `extract.py`/`decompile.py`/`pipeline.py`/`db.py`. Recomiendo dejarlo **fuera de este incremento** pero registrado explícitamente (ya lo está, sección 1) como el primer candidato del próximo, dado que ya tenemos evidencia concreta del bug, no solo una sospecha.

---

## 4. Recomendación final

La estrategia más segura es la de **dos señales combinadas, calculada una sola vez en `pipeline.py`**:

1. **Intención** (`assembly_path.stem` = APPLICATION; el resultado de `find_companion_assemblies()` = candidatos UNKNOWN_COMPANION) — barata, ya disponible, sin I/O adicional.
2. **Evidencia post-hoc** (enumerar los `.csproj` reales en `output_dir` después de decompilar, clasificar cualquier nombre no cubierto por la señal 1 contra `THIRD_PARTY_ASSEMBLY_PATTERN`) — esta es la capa que **sí** atrapa el caso ya confirmado de fuga (`DocumentFormat.OpenXml`), porque no depende de que la prevención en `decompile.py` haya funcionado.

Combinar ambas es más seguro que cualquiera de las dos por separado: la señal de intención es la única forma confiable de identificar APPLICATION sin ambigüedad; la señal de evidencia es la única que sigue funcionando incluso cuando la prevención falla (como ya pasó). Ninguna carpeta se excluye salvo coincidencia positiva y explícita contra el mismo catálogo curado que el proyecto ya usa — todo lo demás, incluyendo cualquier caso nuevo o inesperado, se sigue escaneando exactamente como hoy. Esto satisface las 8 conclusiones ya confirmadas sin necesitar ninguna rama especial para single-file, sin tocar la base de datos, y sin poner en riesgo ningún hallazgo real del portafolio.

No se escribió código, no se crearon tests, no se modificó ningún archivo, no se hizo commit.
