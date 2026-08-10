"""Clasifica las carpetas de nivel superior que ilspycmd genera al decompilar
una app, en APPLICATION / THIRD_PARTY_OR_FRAMEWORK / UNKNOWN_COMPANION, para
que extract.py pueda saltarse el codigo de terceros durante el escaneo sin
borrar ni mover ninguna evidencia fisica (DISENO_INCREMENTO_3_CLASIFICACION.md).

Reutiliza THIRD_PARTY_ASSEMBLY_PATTERN de decompile.py como unica fuente de
verdad -- no se duplica el catalogo aqui.

La identidad de APPLICATION nunca se decide por coincidencia de nombre (ver
DISENO_INCREMENTO_3_CLASIFICACION.md, Decision 1): un mismo assembly se
fragmenta en VARIAS carpetas de nivel superior, una por cada namespace
distinto que contiene (confirmado con evidencia real: decompilar solo
EtiquetasRH.exe produjo un unico EtiquetasRH.csproj pero seis carpetas
hermanas -- EtiquetasRH, EtiquetasRH.Properties, EtiquetasRH.View,
EtiquetasRH.ViewModel, Properties, view). Por eso pipeline.py determina
`application_folder_names` observando que' carpetas de nivel superior
aparecen justo despues de la UNICA llamada a decompile() para el assembly de
entrada -- evidencia de secuencia, nunca de texto."""

from pathlib import Path

from .decompile import THIRD_PARTY_ASSEMBLY_PATTERN

APPLICATION = "APPLICATION"
THIRD_PARTY_OR_FRAMEWORK = "THIRD_PARTY_OR_FRAMEWORK"
UNKNOWN_COMPANION = "UNKNOWN_COMPANION"


def top_level_dir_names(output_dir: Path) -> frozenset[str]:
    """Nombres de las carpetas de nivel superior de `output_dir` en este
    instante -- helper de solo lectura, usado por pipeline.py para tomar el
    "antes"/"despues" alrededor de cada llamada a decompile()."""
    if not output_dir.is_dir():
        return frozenset()
    return frozenset(p.name for p in output_dir.iterdir() if p.is_dir())


def classify_decompiled_assemblies(
    output_dir: Path, application_folder_names: frozenset[str]
) -> dict[str, str]:
    """Clasifica cada carpeta de nivel superior de `output_dir` (ya
    generadas por ilspycmd, no se decompila nada aqui). `application_folder_names`
    lo determina el llamador (ver pipeline.py) observando la secuencia real
    de llamadas a decompile(), no adivinando por nombre.

    Regla de seguridad (Principio 3, ARCHITECTURAL_PRINCIPLES.md): solo se
    clasifica como THIRD_PARTY_OR_FRAMEWORK cuando hay coincidencia POSITIVA
    contra THIRD_PARTY_ASSEMBLY_PATTERN -- cualquier otro caso (incluido uno
    inesperado que nunca se pidio decompilar, o un companion que ya paso el
    blocklist de decompile.py) cae en UNKNOWN_COMPANION, que extract.py
    siempre sigue escaneando."""
    result: dict[str, str] = {}
    if not output_dir.is_dir():
        return result
    for entry in output_dir.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        if name in application_folder_names:
            result[name] = APPLICATION
        elif THIRD_PARTY_ASSEMBLY_PATTERN.match(name):
            result[name] = THIRD_PARTY_OR_FRAMEWORK
        else:
            result[name] = UNKNOWN_COMPANION
    return result


def third_party_folder_names(classifications: dict[str, str]) -> frozenset[str]:
    """El subconjunto de nombres que extract.py debe saltarse -- nunca
    incluye APPLICATION ni UNKNOWN_COMPANION."""
    return frozenset(
        name for name, cls in classifications.items() if cls == THIRD_PARTY_OR_FRAMEWORK
    )
