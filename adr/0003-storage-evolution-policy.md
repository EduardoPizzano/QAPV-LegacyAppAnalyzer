# ADR-0003: Politica de evolucion del almacenamiento ante crecimiento de concurrencia

## Estado
Aprobado

Se apoya en el Principio 4 (Evolucion progresiva de infraestructura
basada en evidencia) de `ARCHITECTURAL_PRINCIPLES.md`.

## Contexto
`qapv_analyzer.db` es un unico archivo SQLite. Ya se confirmo
empiricamente contencion real de escritura bajo uso concurrente (8
agentes de revision en paralelo: un guardado se revirtio, hallazgos se
duplicaron por reintentos tras errores "database is locked"). SQLite
serializa escritores por diseno -- el modo WAL mejora la concurrencia
lectura-mientras-escritura, pero no resuelve escritura-contra-escritura
simultanea.

## Decision

### Mitigacion inmediata
Se habilita SQLite en modo WAL (`PRAGMA journal_mode=WAL`) -- sin cambio
de motor de base de datos, sin infraestructura nueva.

### Politica de evolucion (no una migracion programada)
Cuando las senales definidas abajo indiquen, en conjunto, que SQLite dejo
de cumplir adecuadamente con las necesidades de la plataforma, la
migracion hacia un motor de base de datos **cliente-servidor con
capacidad de concurrencia de escritura real** y soporte adecuado al
crecimiento operativo esperado debera evaluarse mediante el proceso de
gobernanza arquitectonica establecido en este proyecto (propuesta ->
revision -> aprobacion vía ADR, con auditoria ARB periodica -- el mismo
proceso ya demostrado en la cadena ADR-0000/0001/0002).

Esta decision fija una **categoria** de solucion (cliente-servidor,
concurrencia de escritura real), no un producto especifico.

### Seleccion tecnologica vigente (no permanente)
**PostgreSQL** es la seleccion tecnologica vigente para esa categoria --
una decision tecnologica actual, sujeta a revision, no una restriccion
arquitectonica permanente. Se documenta unicamente para no tener que
re-discutir "a que migrar" bajo presion si las senales aparecen. Puede
cambiar sin que este ADR necesite revisarse, siempre que la categoria
(cliente-servidor, concurrencia de escritura real) se preserve.

### Senales de evolucion (tres dimensiones)
Ninguna senal aislada dispara una migracion automatica -- todas
alimentan la evaluacion formal de gobernanza cuando, en conjunto,
indican que SQLite ya no es adecuado:

- **Cuantitativas**: crecimiento del volumen de datos, numero de
  escritores concurrentes sostenidos, tiempos de operacion, frecuencia
  de bloqueos ("database is locked"), degradacion medible de
  desempeno.
- **Cualitativas**: complejidad operativa creciente, mantenibilidad,
  experiencia del equipo de desarrollo con el motor actual frente a
  alternativas, restricciones ya conocidas (ver Riesgos).
- **Organizacionales**: crecimiento del numero de usuarios de la
  plataforma, criticidad operacional del programa de migracion que la
  plataforma soporta, necesidad de disponibilidad continua, requisitos
  de auditoria o gobierno.

## Consecuencias
- Cualquier proceso de respaldo/copia manual de `qapv_analyzer.db` debe
  hacer checkpoint del WAL antes de copiar solo el archivo principal, o
  copiar los tres archivos (`.db`, `-wal`, `-shm`) juntos.
- No existe monitoreo automatico de las senales cuantitativas -- depende
  de que el equipo las note y las traiga al proceso de gobernanza.
  Construir ese monitoreo queda fuera de alcance de este ADR.
- Cuando las senales indiquen que SQLite ya no es adecuado, se abre una
  evaluacion formal dentro del proceso de gobernanza -- **no** una
  migracion automatica ni una prioridad absoluta predeterminada sobre el
  resto del roadmap. La priorizacion resultante se decide en esa
  evaluacion, con el contexto vigente en ese momento.

## Alcance
**Incluye**: criterios de evolucion de almacenamiento, la capacidad
requerida (motor cliente-servidor con concurrencia de escritura real), y
las condiciones/senales que disparan una evaluacion de migracion.

**No incluye**: seleccion definitiva de proveedor mas alla de la opcion
vigente (PostgreSQL), infraestructura especifica, configuracion
operacional, ni estrategia detallada de migracion -- todo eso se define
en el momento de la evaluacion formal, con el contexto real disponible
entonces.
