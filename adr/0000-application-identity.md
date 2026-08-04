# ADR-0000: Identidad canonica de una aplicacion

## Estado
Aprobado

## Contexto
Ninguno de los atributos tecnicos (name, source_path, version, hash del
binario) es confiable como identidad: todos pueden cambiar por razones
legitimas sin que la aplicacion "deje de ser la misma". Usar cualquiera de
ellos como clave de integridad hereda su fragilidad hacia todo el sistema.

Se apoya en el Principio 2 (Identidad estable, atributos tecnicos mutables)
de `ARCHITECTURAL_PRINCIPLES.md`.

## Decision
La plataforma distingue explicitamente:

- **Identidad canonica**: un identificador interno, estable y opaco,
  generado una sola vez, permanente, que NUNCA se recalcula ni se deriva
  de ningun atributo tecnico. Su representacion tecnica exacta (GUID u
  otro esquema equivalente) es un detalle de implementacion, no parte de
  esta decision.

  La identidad representa la **continuidad logica de la aplicacion como
  concepto de negocio** -- no del archivo ejecutable ni de una version
  especifica del binario. Dos analisis pueden corresponder a la misma
  identidad aunque el ejecutable se haya renombrado, movido, recompilado
  o incluso reescrito, siempre que representen la continuidad de la
  misma aplicacion desde la perspectiva de quien la entiende. Es la
  referencia unica para conocimiento curado, trazabilidad, y el
  historial de migracion durante todo el ciclo de vida del proyecto.

- **Atributos tecnicos**: name, source_path, dotnet_target, ui_framework,
  db_drivers, etc. -- mutables, se actualizan libremente sobre la misma
  identidad sin ninguna implicacion de integridad.

Como se determina, en la practica, si un analisis nuevo corresponde a una
identidad existente queda fuera de este ADR por diseno -- ver ADR-0002.

## Consecuencias
- ADR-0001 se ajusta: el UPDATE de save_analysis() busca/actualiza por
  identity_id, no por name ni source_path.
- findings deberia converger a referenciar identity_id en el futuro.
- Que la identidad sea sobre continuidad logica (no sobre el binario)
  implica que la decision de si dos versiones evolutivas de una app
  (ej. un fork o una reescritura) comparten identidad o no es, por
  naturaleza, un juicio humano -- nunca una inferencia automatica desde
  un atributo tecnico. Esto conecta directamente con ADR-0002.
