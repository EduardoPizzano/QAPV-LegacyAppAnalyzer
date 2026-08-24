# ADR-0004: Artifact Identity, Deployment Evidence and Artifact Relationships

## Estado
Aprobado

## Contexto
ADR-0000/0001/0002 formalizan correctamente la identidad de NEGOCIO de una
aplicacion (`ApplicationIdentity`), independiente de sus atributos
tecnicos. No proveen, sin embargo, ningun mecanismo para responder una
pregunta distinta y anterior: "¿este binario es tecnicamente el mismo que
aquel?".

La investigacion del caso DataTransfer (Fases 1-3 de una sesion de
analisis, ver historial del proyecto) confirmo con evidencia real:

- Dos deployments (`Polaridad/Release`, `DataTransfer v2.46/Release`)
  resultaron ser, con SHA-256 identico del binario original y del codigo
  decompilado, el MISMO artefacto tecnico en dos workstations distintas.
- Un deployment (`Geometria/Release`) comparte 99.86% del codigo fuente con
  otro (`v2.46`) pero difiere en comportamiento de negocio real y
  verificado (una operacion UPDATE de base de datos y una rama de deteccion
  de retrabajo, ausentes en uno de los dos) -- sin que se pueda determinar,
  solo con esta evidencia, si es una version no actualizada o un fork
  deliberado.
- Archivos individuales dentro de la misma aplicacion evolucionan a ritmos
  distintos: 3 archivos auxiliares (`RL1.cs`, `PuntasCV.cs`,
  `ReportGenerator.cs`) permanecieron byte-identicos durante mas de 2 anios
  de evolucion del producto, mientras el archivo principal de negocio
  cambio sustancialmente entre versiones.
- El mismo patron de codigo compartido bajo nombres de aplicacion
  distintos se confirmo, de forma independiente de DataTransfer, en un
  componente de UI (`AutoCompleteComboBox.cs`) reutilizado identicamente en
  6 aplicaciones de dominios de negocio aparentemente no relacionados.
- Dentro de una misma carpeta de deployment ClickOnce, "Release" y
  "app.publish" pueden representar builds distintos y no sincronizados
  (confirmado con hash y build_date reales), o "app.publish" puede carecer
  por completo de manifest ClickOnce propio (evidencia de que podria ser un
  artefacto de build intermedio, no un deployment real).
- Ningun numero de version disponible hoy es confiable por si solo como
  identificador de "que build es este": se confirmaron, sobre el MISMO
  binario real, hasta 3 numeros de version mutuamente inconsistentes
  (version de despliegue ClickOnce, version de ensamblado congelada en
  "1.0.0.0", y un string editado a mano en la UI). Esto es ademas
  consistente con evidencia YA documentada en el proyecto:
  `analyzer/activity.py` registra, de un barrido completo de `decompiled/`,
  que 0 apps del portafolio usan el comodin `AssemblyVersion("1.0.*")` --
  todas usan el valor fijo por defecto de Visual Studio.

Ninguno de estos hallazgos contradice ADR-0000/0001/0002 -- todos son
compatibles -- pero ninguno de esos ADR provee el vocabulario ni el modelo
de datos para representarlos.

## Decision
Se introducen tres conceptos nuevos, aditivos, sin modificar
ADR-0000/0001/0002.

### Artifact
El assembly/binario TECNICO concreto que fue analizado -- NUNCA un archivo
`.cs` individual, NUNCA "toda la carpeta decompilada", NUNCA
necesariamente "el archivo principal de negocio", NUNCA un Deployment
completo, y NUNCA una `ApplicationIdentity`.

`anchor_file` es metadata analitica opcional (que archivo se uso como
referencia en una comparacion puntual) -- **nunca** define la identidad
del Artifact.

### Deployment
La fila ya existente de `apps` -- representa "este Artifact fue
encontrado/analizado en este `source_path` durante este analisis". No se
crea una tabla `deployments` nueva: `apps` ya cumple esta funcion
(`source_path`, `analyzed_at`, `review_status` por instancia).

### ArtifactRelationship
Relacion TECNICA entre dos Artifacts -- nunca implica ni decide
automaticamente `ApplicationIdentity`. Taxonomia elegida (forma legible,
auto-descriptiva, sobre las categorias A-E usadas durante la
investigacion -- nunca se mezclan ambos sistemas):
`identical` | `derived` | `fork` | `variant` | `similar` | `unknown`.

## Politica de evidencia
En orden de confiabilidad decreciente:

1. **SHA-256 del binario original** (`binary_hash`) -- evidencia primaria.
2. **SHA-256 del codigo decompilado completo** (`source_hash`) -- evidencia
   secundaria FUERTE, usada solo cuando el binario original no es
   accesible. Asume que la decompilacion es deterministica para el mismo
   binario de entrada -- no verificado experimentalmente en este proyecto;
   por eso nunca tiene la misma confianza que `binary_hash`.
3. **`build_date`, `file_size`, versiones de ensamblado (`assembly_version`/
   `product_version`/`file_version`)** -- evidencia secundaria. Confirmado
   con evidencia real que la version de ensamblado puede quedar congelada
   sin reflejar cambios reales de codigo.
4. **Nombre del EXE, `path`, version declarada en UI o en manifest de
   despliegue** -- evidencia debil. Nunca suficiente por si sola para
   declarar identidad tecnica.

No se define un score numerico unico de similitud -- la politica es de
niveles categoricos, nunca una formula magica.

## Politica de relaciones
`ArtifactRelationship` conserva: los dos Artifacts relacionados,
`relationship_type` (taxonomia de arriba), `evidence` (texto concreto de
que cambio -- nunca solo una magnitud), `confidence` (escala 0-100 ya
existente de `analyzer/confidence.py`), `detection_method`, `observed_at`,
y `human_resolution_state` (ver politica de resolucion humana).

## Politica de resolucion humana
Ninguna similitud tecnica, sin importar que tan alta, fusiona
automaticamente una `ApplicationIdentity` -- se mantiene integra la
politica de ADR-0002 (`New`/`Resolved`/`Candidate` + `confidence_score`,
confirmacion humana siempre requerida para `Candidate`).
`human_resolution_state` de una `ArtifactRelationship` nunca se
autoasigna `confirmed_same_identity`/`confirmed_different_identity` --
nace `NULL`/`pending` y solo un humano lo cambia.

## Politica de binary_hash
Capturado en `pipeline.run_analysis()`, inmediatamente despues de resolver
`assembly_path` y **antes** de `decompile()` -- mientras el binario
original sigue siendo accesible. Si no es accesible (confirmado con
evidencia real durante la investigacion: un share de red inalcanzable
desde el entorno de analisis), se persiste el valor explicito `"UNKNOWN"`
-- nunca `NULL` silencioso ni cadena vacia. `"UNKNOWN"` significa "se
intento determinar la identidad del binario pero no fue posible obtenerlo"
-- nunca se trata como coincidencia entre dos Artifacts distintos.

## Politica de Deployment
`apps` ya representa esta funcion -- no se crea tabla nueva salvo evidencia
futura que lo haga imprescindible (Principio 4,
`ARCHITECTURAL_PRINCIPLES.md`).

## Politica ClickOnce
Confirmado con evidencia real: "Release" y "app.publish" dentro de la
misma carpeta de deployment NO deben asumirse como el mismo build ni como
deployments igualmente validos -- deben verificarse individualmente (hash
+ manifest, cuando exista) caso por caso. Un manifest `.application`
ausente o incompleto en una de las dos carpetas es evidencia (no
concluyente por si sola) de que esa carpeta puede ser un artefacto de
build intermedio, no un deployment real -- nunca se asume automaticamente
en ninguna direccion.

## Consecuencias
**Positivas**: permite explicar con precision relaciones tecnicas entre
deployments sin comprometer la integridad de la identidad de negocio.
Cierra el gap identificado en la revision arquitectonica post-Incremento
E de Application Flow.

**Negativas**: introduce 2 tablas nuevas y una columna nueva; requiere
disciplina para no sobre-poblar `ArtifactRelationship` (riesgo de generar
relaciones de bajo valor si se automatiza sin criterio en el futuro).

## Alcance
**Incluye**: `artifacts`, `artifact_relationships`, `apps.artifact_id`,
calculo de `binary_hash`/`source_hash` en el pipeline existente, la
politica de evidencia/confianza/resolucion humana de arriba.

**No incluye** (Non-goals explicitos de esta fase):
- Fuzzy matching global de similitud de codigo a traves de todo el
  portafolio.
- Auto-fusion de `ApplicationIdentity` bajo ninguna circunstancia.
- Modelar Artifact por archivo individual (evaluado y descartado durante
  la investigacion por riesgo de sobre-modelado).
- UI de visualizacion de relaciones (fase posterior).
- Analisis de similitud parcial/porcentual automatizado (la investigacion
  de esta sesion fue manual).
- Migracion de motor de base de datos (ADR-0003 cubre esa politica por
  separado, sin relacion con esta decision).
- Extraccion de metadata de version de PE (`assembly_version`/
  `product_version`/`file_version`): el proyecto ya confirmo, con
  evidencia real de portafolio completo, que estos valores no aportarian
  senal -- las columnas existen para uso manual/futuro pero ningun
  extractor las puebla todavia.
