"""Artifact identity evidence (ADR-0004): binary_hash del ensamblado
original y source_hash del resultado completo de decompilacion, como
evidencia TECNICA de identidad -- NUNCA de ApplicationIdentity (ADR-0000/
0001/0002, sin modificar). Mismo patron que analyzer/activity.py: modulo de
computacion pura, sin tocar la BD -- la persistencia/reutilizacion de
Artifact vive en analyzer/db.py.

binary_hash es evidencia PRIMARIA. source_hash es evidencia SECUNDARIA
FUERTE, usada solo cuando el binario original no es accesible -- nunca
reemplaza a binary_hash cuando este existe. Ninguno de los dos determina
ApplicationIdentity por si solo (ver ADR-0004, politica de resolucion
humana)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .classification import THIRD_PARTY_ASSEMBLY_PATTERN

# UNKNOWN es un valor explicito, nunca None/"" -- distingue "se intento
# calcular el hash y el binario no estaba disponible" de "no se intento".
# Ver ADR-0004, politica de binary_hash.
UNKNOWN = "UNKNOWN"


def compute_binary_hash(assembly_path: Path) -> str:
    """SHA-256 del binario ORIGINAL (nunca del codigo decompilado, nunca de
    un manifest, nunca del nombre/path). Devuelve UNKNOWN -- nunca None ni
    "" -- cuando el archivo no existe, no es legible, o cualquier error de
    E/S impide leerlo (ej. recurso de red inaccesible, confirmado caso real
    durante la investigacion de Fase 3: \\\\NAAMRT-QCS10\\... no accesible
    desde el entorno de analisis)."""
    try:
        data = Path(assembly_path).read_bytes()
    except OSError:
        return UNKNOWN
    return hashlib.sha256(data).hexdigest()


def compute_source_hash(output_dir: Path) -> str | None:
    """SHA-256 sobre el resultado COMPLETO de decompilacion de la app (todos
    sus .cs propios, excluyendo terceros con el mismo patron ya usado por
    Application Structure Discovery -- THIRD_PARTY_ASSEMBLY_PATTERN, nunca
    una segunda convencion), en orden alfabetico de ruta relativa para que
    el resultado sea deterministico dado el mismo arbol de archivos en
    disco.

    Evidencia SECUNDARIA FUERTE (ADR-0004) -- nunca primaria: este calculo
    asume que ilspycmd produce el mismo resultado para el mismo binario de
    entrada, algo no verificado experimentalmente en este proyecto (no se
    re-decompilo el mismo ensamblado dos veces para confirmarlo). Por eso
    NUNCA se usa solo para fusionar Artifacts con la misma confianza que
    binary_hash.

    Devuelve None (nunca un hash inventado de "nada") si output_dir no
    existe o no contiene ningun .cs propio -- ausencia real, no un caso a
    ocultar con un valor sentinela distinto de UNKNOWN (esa distincion es
    exclusiva de binary_hash, ver ADR-0004)."""
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        return None

    cs_files = []
    for cs_file in output_dir.rglob("*.cs"):
        rel = cs_file.relative_to(output_dir)
        top_level = rel.parts[0] if rel.parts else ""
        if THIRD_PARTY_ASSEMBLY_PATTERN.match(top_level):
            continue
        cs_files.append((rel, cs_file))
    if not cs_files:
        return None

    cs_files.sort(key=lambda pair: str(pair[0]).replace("\\", "/"))

    h = hashlib.sha256()
    for rel, cs_file in cs_files:
        h.update(str(rel).replace("\\", "/").encode("utf-8"))
        h.update(b"\x00")
        try:
            h.update(cs_file.read_bytes())
        except OSError:
            continue
        h.update(b"\x00")
    return h.hexdigest()


@dataclass(frozen=True)
class ArtifactEvidence:
    """Evidencia tecnica de identidad de UN analisis -- se computa una vez
    por corrida de run_analysis() y se persiste vía
    db.get_or_create_artifact(). build_date se REUTILIZA del calculo ya
    existente de Incremento Lifecycle (analyzer/activity.py) -- nunca se
    recalcula aqui, para no duplicar esa logica (ADR-0004, politica de
    build_date).

    assembly_version/product_version/file_version quedan en None
    deliberadamente en esta fase: no existe en el proyecto ninguna
    capacidad de lectura de metadata de version de PE, y
    analyzer/activity.py ya documenta, con evidencia real de portafolio
    completo, que AssemblyVersion esta congelado en "1.0.0.0" en el 100% de
    las apps -- implementar esa extraccion ahora seria inventar una
    capacidad nueva sin evidencia de que aporte valor (Principio 4,
    ARCHITECTURAL_PRINCIPLES.md)."""

    binary_hash: str = UNKNOWN
    source_hash: str | None = None
    build_date: str | None = None
    assembly_version: str | None = None
    product_version: str | None = None
    file_version: str | None = None
    anchor_file: str | None = None
