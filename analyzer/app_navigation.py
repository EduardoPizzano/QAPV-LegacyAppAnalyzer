"""Application Flow -- Navigation Discovery, Incremento B (2026-08-20):
detecta navegacion ESTATICA entre Forms/Windows (instancia + .Show()/
.ShowDialog() dentro del MISMO metodo) a partir del codigo C# ya
decompilado. Concepto DISTINTO de Data Flow (reutiliza integramente
analyzer/app_structure.py del Incremento A: ClassInfo/MethodInfo/
Evidence/UnknownRecord, nunca reimplementa la deteccion de clases/metodos).

PRECISION > COBERTURA: es preferible declarar una navegacion como
`unresolved_navigation_target_unknown`/`unresolved_target_not_a_known_screen`
que inventar una relacion sin respaldo real de codigo. "No encontrado" (sin
evidencia disponible, ej. wiring WPF en BAML) NUNCA se confunde con "no hay
navegacion" (se escaneo el metodo completo y genuinamente no hay ningun
Show()/ShowDialog()).

Deliberadamente NO intenta (fuera de alcance de este incremento): Event
Wiring, Call Flow metodo->metodo, integracion con Data Flow, Functional
Flow, ni ninguna UI nueva. Nunca modifica resolve_data_flow(),
resolve_app_relations(), build_app_relations_diagram(), ni el diagrama
actual de Data Flow."""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import app_structure, confidence
from .__version__ import ANALYZER_VERSION
from .evidence import Evidence
from .unknown import UnknownRecord

from datetime import datetime, timezone

# --- Patrones de navegacion -----------------------------------------------
#
# Solo cuentan como navegacion: .Show() y .ShowDialog(). Deliberadamente NO
# se consideran Close()/Hide()/Dispose()/Activate()/Focus() -- representan
# comportamiento de ventana, no necesariamente transicion de pantalla (ver
# regla explicita del incremento).

# "x = new TypeName(" -- cubre tanto declaracion local ("var x = new X()",
# "TypeName x = new TypeName()") como reasignacion de un campo de clase ya
# declarado ("searchForm = new FormSearch();") con la MISMA regex, porque
# ambas formas comparten el sufijo "= new TypeName(" que es lo unico que
# necesitamos observar -- no se requiere una segunda logica para el caso de
# campo de clase (ver diagnostico, punto 2.5).
INSTANTIATION_RE = re.compile(r"(\w+)\s*=\s*new\s+(\w+)\s*\(")

# "new TypeName(...).Show()" / "new TypeName(...).ShowDialog()" -- inline,
# sin variable intermedia. `[^()]*` (sin parentesis anidados) es una
# limitacion deliberada y documentada, igual que otros regex ya existentes
# en el proyecto (ej. analyzer/extract.py) que tampoco manejan expresiones
# anidadas en argumentos de constructor.
INLINE_SHOW_RE = re.compile(r"new\s+(\w+)\s*\([^()]*\)\s*\.\s*(Show|ShowDialog)\s*\(")

# "varName.Show()" / "varName.ShowDialog()" -- el caracter antes de
# `varName` nunca es parte de una expresion "new X()..." (ese caso ya lo
# cubre INLINE_SHOW_RE arriba, y la ausencia de un identificador antes del
# "." en "new X()." hace que esta regex no la vuelva a capturar).
VAR_SHOW_RE = re.compile(r"(\w+)\s*\.\s*(Show|ShowDialog)\s*\(")

# Gap descubierto durante la validacion contra las 5 apps reales del
# portafolio (2026-08-20): el 100% (409/409) de los candidatos que caian en
# "unresolved_navigation_target_unknown" en las 5 apps eran en realidad
# `MessageBox.Show(...)` -- una clase ESTATICA del BCL
# (System.Windows.Forms.MessageBox / System.Windows.MessageBox), nunca una
# instancia de Form/Window. No es un falso positivo "resolved" (MessageBox
# nunca aparece en `known_screens`), pero saturaba por completo el bucket
# indeterminado con ruido no-navegacional. Se excluye explicitamente, con
# el MISMO tratamiento que Close/Hide/Dispose/Activate/Focus: cero edges,
# nunca "indeterminado" (aqui SI hay certeza de que no es navegacion, a
# diferencia de un target genuinamente desconocido).
_EXCLUDED_STATIC_RECEIVERS = frozenset({"MessageBox"})


@dataclass(frozen=True)
class NavigationEdge:
    """Una navegacion detectada: `source_class.source_method()` muestra
    (Show/ShowDialog) una instancia de `target_class` -- o de un tipo cuyo
    nombre se conoce pero no se pudo confirmar como Form/Window, o de un
    tipo que ni siquiera se pudo determinar dentro del mismo metodo (ver
    resolution_status)."""
    app_name: str
    source_class: str
    source_method: str
    target_class: str | None
    operation: str  # "show" | "show_dialog"
    resolution_status: str  # "resolved" | "unresolved_navigation_target_unknown" | "unresolved_target_not_a_known_screen"
    evidence: Evidence


@dataclass(frozen=True)
class ApplicationNavigation:
    app_name: str
    edges: tuple[NavigationEdge, ...]
    # Se HEREDAN de ApplicationStructure (ej. limitacion WPF de wiring en
    # BAML) -- nunca se duplica el concepto de Unknown, este incremento no
    # agrega NUEVOS UnknownRecord a nivel de clase, solo reutiliza los ya
    # calculados en el Incremento A.
    unknowns: tuple[UnknownRecord, ...]


def _build_evidence(extractor_key: str, source_file: str, line_number: int, snippet: str) -> Evidence:
    return Evidence(
        source_file=source_file, line_number=line_number, snippet=snippet,
        extractor=extractor_key, confidence=confidence.resolve_confidence(extractor_key),
        analyzer_version=ANALYZER_VERSION, created_at=datetime.now(timezone.utc).isoformat(),
    )


def _build_edge(app_name, class_name, method_name, type_name, op, rel_file, abs_line_idx, raw_line,
                known_screens: set[str]) -> NavigationEdge:
    operation = "show" if op == "Show" else "show_dialog"
    snippet = raw_line.strip()
    line_number = abs_line_idx + 1

    if type_name is None:
        return NavigationEdge(
            app_name=app_name, source_class=class_name, source_method=method_name,
            target_class=None, operation=operation,
            resolution_status="unresolved_navigation_target_unknown",
            evidence=_build_evidence("APP_NAVIGATION_TARGET_TYPE_UNKNOWN", rel_file, line_number, snippet),
        )
    if type_name in known_screens:
        return NavigationEdge(
            app_name=app_name, source_class=class_name, source_method=method_name,
            target_class=type_name, operation=operation, resolution_status="resolved",
            evidence=_build_evidence("APP_NAVIGATION_INSTANTIATION_AND_SHOW", rel_file, line_number, snippet),
        )
    return NavigationEdge(
        app_name=app_name, source_class=class_name, source_method=method_name,
        target_class=type_name, operation=operation,
        resolution_status="unresolved_target_not_a_known_screen",
        evidence=_build_evidence("APP_NAVIGATION_TARGET_NOT_CONFIRMED_SCREEN", rel_file, line_number, snippet),
    )


def _find_navigation_in_method(lines: list[str], start: int, end: int, class_name: str, method_name: str,
                                app_name: str, rel_file: str, known_screens: set[str]) -> list[NavigationEdge]:
    """Heuristica de PROXIMIDAD explicita, acotada al intervalo de UN
    metodo (ya calculado por analyzer/app_structure.py): instanciacion y
    Show()/ShowDialog() deben aparecer dentro del MISMO metodo para
    asociarse -- nunca se asume relacion entre metodos distintos (regla
    critica del incremento: el caso real de codigo muerto en RefControl
    tiene una instancia creada en un metodo y jamas mostrada en ese mismo
    metodo). NO es analisis de control-flow/SSA/data-flow global -- es
    deliberadamente un extractor pequeno y auditable."""
    body = lines[start:end + 1]

    instantiations: dict[str, str] = {}
    for offset, line in enumerate(body):
        m = INSTANTIATION_RE.search(line)
        if m:
            instantiations[m.group(1)] = m.group(2)

    edges: list[NavigationEdge] = []
    inline_lines: set[int] = set()

    for offset, line in enumerate(body):
        m = INLINE_SHOW_RE.search(line)
        if m:
            type_name, op = m.group(1), m.group(2)
            abs_idx = start + offset
            edges.append(_build_edge(app_name, class_name, method_name, type_name, op, rel_file, abs_idx, line, known_screens))
            inline_lines.add(offset)

    for offset, line in enumerate(body):
        if offset in inline_lines:
            continue
        m = VAR_SHOW_RE.search(line)
        if not m:
            continue
        var_name, op = m.group(1), m.group(2)
        if var_name in _EXCLUDED_STATIC_RECEIVERS:
            continue
        type_name = instantiations.get(var_name)  # None si no se pudo determinar en este metodo
        abs_idx = start + offset
        edges.append(_build_edge(app_name, class_name, method_name, type_name, op, rel_file, abs_idx, line, known_screens))

    return edges


def discover_navigation(app_id: int) -> ApplicationNavigation | None:
    """Punto de entrada del incremento -- deriva EN VIVO desde
    decompiled/<raiz>/, reutilizando integramente
    analyzer.app_structure.discover_application_structure() (para el
    catalogo de Form/Window ya confirmados) y
    analyzer.app_structure.scan_app_files() (para los MISMOS intervalos de
    clase/metodo, sin reimplementar la deteccion). NUNCA usa
    resolve_data_flow_portfolio() ni recorre el portafolio completo -- el
    costo esta acotado a los archivos de ESTA app."""
    structure = app_structure.discover_application_structure(app_id)
    if structure is None:
        return None

    decompiled_root = app_structure.resolve_decompiled_root(structure.app_name)
    if not decompiled_root.is_dir():
        return ApplicationNavigation(app_name=structure.app_name, edges=(), unknowns=structure.unknowns)

    known_screens = {c.class_name for c in structure.classes if c.class_type in ("form", "window")}

    edges: list[NavigationEdge] = []
    for rel_file, _classes, _methods, _entry_points, method_intervals, lines in app_structure.scan_app_files(
        structure.app_name, decompiled_root
    ):
        for start, end, class_name, method_name in method_intervals:
            edges.extend(_find_navigation_in_method(
                lines, start, end, class_name, method_name,
                structure.app_name, rel_file, known_screens,
            ))

    return ApplicationNavigation(app_name=structure.app_name, edges=tuple(edges), unknowns=structure.unknowns)
