"""Application Flow -- Event Wiring + Intra-Class Call Flow, Incremento C
(2026-08-20): detecta (1) asociaciones EXPLICITAS control.Evento += handler
en codigo C# decompilado, y (2) llamadas directas metodo->metodo dentro de
la MISMA clase. Concepto DISTINTO de Navigation Discovery (Incremento B:
instancia + Show()/ShowDialog() entre Forms/Windows) -- este modulo nunca
toca .Show()/.ShowDialog() ni analyzer/app_navigation.py.

Modulo SEPARADO de app_navigation.py (decision explicita, ver diagnostico):
Event Wiring y Call Flow son relaciones conceptualmente distintas de
Navigation (control->handler, metodo->metodo, en vez de instancia->pantalla).
Mantiene el mismo patron de responsabilidad unica que
app_structure.py/app_navigation.py. Reutiliza integramente
analyzer.app_structure.discover_application_structure()/scan_app_files()
(intervalos de clase/metodo ya calculados) -- nunca reimplementa el escaneo
de archivos ni el parsing de clases/metodos.

PRECISION > COBERTURA: es preferible producir un estado unresolved/unknown
que inventar una relacion. NO se implementa en este incremento: Call Flow
cross-class, resolucion de tipos/herencia/interfaces/DI/virtual dispatch,
overload resolution por conteo de argumentos, reflection, COM, integracion
con Data Flow/Application Map, ni ninguna UI nueva."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from . import app_structure, confidence
from .__version__ import ANALYZER_VERSION
from .evidence import Evidence
from .unknown import UnknownRecord

# --- Event Wiring -----------------------------------------------------------
#
# Solo se reconoce wiring EXPLICITO (control.Evento += ...) -- jamas por
# convencion de nombres (ej. "btnBorrar" + metodo "btnBorrar_Click" NO
# implica wiring sin el operador += observado realmente).

# "control.Evento += new TipoDelegado(handler);" -- patron real dominante
# confirmado en 222 archivos del portafolio (diagnostico previo). El nombre
# del tipo delegado (EventHandler, RoutedEventHandler, etc.) no se restringe
# a una lista fija -- cualquier "new Tipo(identificador)" en esta posicion
# es, en la practica de C#, siempre construccion de un delegado.
WIRING_WRAPPED_HANDLER_RE = re.compile(
    r"(?:this\.)?(\w+)\.(\w+)\s*\+=\s*new\s+[\w.]+\s*\(\s*(?:this\.)?(\w+)\s*\)"
)

# "control.Evento += handler;" -- method group directo, sin envoltura. Esta
# forma es SINTACTICAMENTE IDENTICA a un incremento numerico/de propiedad
# real (ej. "total.Monto += cantidad;"), asi que NUNCA se acepta sola: solo
# se confirma como wiring si el identificador de la derecha coincide con un
# metodo REAL ya declarado en esta clase (ver _find_event_wirings_in_method)
# -- de lo contrario se descarta en silencio (ninguna relacion, ni siquiera
# "unresolved"), para no convertir aritmetica ordinaria en wiring inventado.
WIRING_DIRECT_RE = re.compile(
    r"(?:this\.)?(\w+)\.(\w+)\s*\+=\s*(?:this\.)?(\w+)\s*;"
)

# "control.Evento += algo(...)" o "control.Evento += (parametros) => ...":
# la derecha tiene forma de LLAMADA o LAMBDA -- una asignacion numerica
# jamas tiene esta forma, asi que es una senal fuerte de wiring real cuyo
# handler concreto no podemos determinar con seguridad (ej. wrapper
# indirecto, lambda). Se registra como wiring existente pero handler
# desconocido -- NUNCA se inventa el nombre del metodo.
WIRING_COMPLEX_RHS_RE = re.compile(
    r"(?:this\.)?(\w+)\.(\w+)\s*\+=\s*(?:\([^()]*\)\s*=>|(?:this\.)?\w+\s*\()"
)


# --- Call Flow intra-class ---------------------------------------------------
#
# Solo llamadas SIN receptor (o con receptor "this") cuentan como candidatas
# intra-class: "Baja();", "this.Baja();" -- NUNCA "conn.Open()",
# "reader.Read()", "Console.WriteLine()", "MessageBox.Show()" (todas tienen
# un receptor DISTINTO de "this", quedan excluidas por construccion, ya que
# el lookbehind rechaza cualquier "." inmediatamente antes del identificador).
UNQUALIFIED_CALL_RE = re.compile(r"(?<![.\w])(?:this\.)\s*(\w+)\s*\(|(?<![.\w])(\w+)\s*\(")

# Palabras reservadas de C# que preceden "(" sin ser una llamada real --
# deben excluirse explicitamente (if/for/switch/etc. NUNCA son metodos).
_CSHARP_CONTROL_KEYWORDS = frozenset({
    "if", "for", "foreach", "while", "switch", "catch", "using", "lock",
    "fixed", "checked", "unchecked", "typeof", "sizeof", "default", "nameof",
    "return", "throw", "is", "as", "in", "when", "new", "base", "get", "set",
})

# Declaracion de metodo/funcion local -- con O SIN modificador de acceso
# (METHOD_SIG de extract.py exige modificador; una local function de C# 7+
# tipicamente NO lo tiene). Se excluye la linea COMPLETA de la deteccion de
# llamadas para no confundir "TipoRetorno Nombre(parametros)" con una
# invocacion real (ver Incremento C seccion 15 -- local functions NO se
# resuelven automaticamente en este incremento).
_BARE_DECL_RE = re.compile(
    r"^\s*(?:static\s+)?(?:async\s+)?[\w<>\[\],\.\?]+\s+(\w+)\s*\([^;{]*\)\s*(\{|=>|$)"
)


@dataclass(frozen=True)
class EventWiring:
    app_name: str
    source_class: str
    control_name: str
    event_name: str
    handler_method: str | None
    resolution_status: str  # "resolved" | "unresolved_event_handler_unknown"
    evidence: Evidence


@dataclass(frozen=True)
class CallEdge:
    app_name: str
    source_class: str
    source_method: str
    target_method: str | None
    # "resolved" | "unresolved_call_target_ambiguous" |
    # "unresolved_call_target_inherited" | "unresolved_call_target_unknown"
    resolution_status: str
    evidence: Evidence


@dataclass(frozen=True)
class ApplicationInteractions:
    app_name: str
    event_wirings: tuple[EventWiring, ...]
    call_edges: tuple[CallEdge, ...]
    # Heredados de ApplicationStructure (ej. limitacion WPF de wiring en
    # BAML) -- nunca se duplica el concepto de Unknown.
    unknowns: tuple[UnknownRecord, ...]


def _build_evidence(extractor_key: str, source_file: str, line_number: int, snippet: str) -> Evidence:
    return Evidence(
        source_file=source_file, line_number=line_number, snippet=snippet,
        extractor=extractor_key, confidence=confidence.resolve_confidence(extractor_key),
        analyzer_version=ANALYZER_VERSION, created_at=datetime.now(timezone.utc).isoformat(),
    )


def _find_event_wirings_in_method(lines: list[str], start: int, end: int, class_name: str,
                                   app_name: str, rel_file: str,
                                   file_class_methods: dict[str, Counter]) -> list[EventWiring]:
    wirings: list[EventWiring] = []
    class_methods = file_class_methods.get(class_name, Counter())

    for idx in range(start, end + 1):
        line = lines[idx]
        line_number = idx + 1
        consumed: list[tuple[int, int]] = []

        for m in WIRING_WRAPPED_HANDLER_RE.finditer(line):
            control, event, handler = m.group(1), m.group(2), m.group(3)
            wirings.append(EventWiring(
                app_name=app_name, source_class=class_name, control_name=control,
                event_name=event, handler_method=handler, resolution_status="resolved",
                evidence=_build_evidence("APP_EVENT_WIRING_EXPLICIT", rel_file, line_number, line.strip()),
            ))
            consumed.append(m.span())

        def _overlaps(span, spans=consumed):
            return any(span[0] < e and s < span[1] for s, e in spans)

        for m in WIRING_DIRECT_RE.finditer(line):
            if _overlaps(m.span()):
                continue
            control, event, handler = m.group(1), m.group(2), m.group(3)
            # Guarda de precision: solo se acepta si "handler" es un metodo
            # REAL ya declarado en esta clase -- de lo contrario esta misma
            # forma sintactica es indistinguible de un incremento numerico/
            # de propiedad ordinario (ej. "total.Monto += cantidad;"), y NO
            # se produce ninguna relacion (ni siquiera "unresolved").
            if class_methods.get(handler, 0) >= 1:
                wirings.append(EventWiring(
                    app_name=app_name, source_class=class_name, control_name=control,
                    event_name=event, handler_method=handler, resolution_status="resolved",
                    evidence=_build_evidence("APP_EVENT_WIRING_EXPLICIT", rel_file, line_number, line.strip()),
                ))
                consumed.append(m.span())

        for m in WIRING_COMPLEX_RHS_RE.finditer(line):
            if _overlaps(m.span()):
                continue
            control, event = m.group(1), m.group(2)
            wirings.append(EventWiring(
                app_name=app_name, source_class=class_name, control_name=control,
                event_name=event, handler_method=None, resolution_status="unresolved_event_handler_unknown",
                evidence=_build_evidence("APP_EVENT_WIRING_HANDLER_UNKNOWN", rel_file, line_number, line.strip()),
            ))
            consumed.append(m.span())

    return wirings


def _find_call_edges_in_method(lines: list[str], start: int, end: int, class_name: str, method_name: str,
                                app_name: str, rel_file: str, file_class_methods: dict[str, Counter],
                                class_base_type: dict[str, str | None],
                                all_class_methods: dict[str, set]) -> list[CallEdge]:
    edges: list[CallEdge] = []
    class_methods = file_class_methods.get(class_name, Counter())
    base_type = class_base_type.get(class_name)
    base_methods = all_class_methods.get(base_type, set()) if base_type else set()

    # El intervalo del metodo incluye su propia linea de declaracion
    # (start) -- se excluye para no re-detectar la firma del metodo actual
    # como si fuera una llamada a si mismo.
    for idx in range(start + 1, end + 1):
        line = lines[idx]
        if _BARE_DECL_RE.match(line):
            continue  # declaracion de metodo/local function, no una llamada
        line_number = idx + 1

        for m in UNQUALIFIED_CALL_RE.finditer(line):
            name = m.group(1) or m.group(2)
            if name in _CSHARP_CONTROL_KEYWORDS:
                continue
            preceding = line[:m.start()].rstrip()
            if preceding.endswith("new") and (len(preceding) == 3 or not preceding[-4].isalnum()):
                continue  # "new TypeName(" -- construccion, no llamada

            count_in_class = class_methods.get(name, 0)
            if count_in_class == 1:
                edges.append(CallEdge(
                    app_name=app_name, source_class=class_name, source_method=method_name,
                    target_method=name, resolution_status="resolved",
                    evidence=_build_evidence("APP_CALL_INTRA_CLASS", rel_file, line_number, line.strip()),
                ))
            elif count_in_class > 1:
                edges.append(CallEdge(
                    app_name=app_name, source_class=class_name, source_method=method_name,
                    target_method=name, resolution_status="unresolved_call_target_ambiguous",
                    evidence=_build_evidence("APP_CALL_TARGET_AMBIGUOUS", rel_file, line_number, line.strip()),
                ))
            elif name in base_methods:
                edges.append(CallEdge(
                    app_name=app_name, source_class=class_name, source_method=method_name,
                    target_method=name, resolution_status="unresolved_call_target_inherited",
                    evidence=_build_evidence("APP_CALL_TARGET_INHERITED", rel_file, line_number, line.strip()),
                ))
            else:
                edges.append(CallEdge(
                    app_name=app_name, source_class=class_name, source_method=method_name,
                    target_method=None, resolution_status="unresolved_call_target_unknown",
                    evidence=_build_evidence("APP_CALL_TARGET_UNKNOWN", rel_file, line_number, line.strip()),
                ))

    return edges


def discover_interactions(app_id: int) -> ApplicationInteractions | None:
    """Punto de entrada del incremento -- deriva EN VIVO desde
    decompiled/<raiz>/, reutilizando integramente
    analyzer.app_structure.discover_application_structure()/scan_app_files().
    NUNCA usa resolve_data_flow_portfolio() ni recorre el portafolio
    completo -- el costo esta acotado a los archivos de ESTA app.

    El conteo de metodos por clase (para overload/ambiguedad) se calcula
    POR ARCHIVO, no agregado a nivel de app: Geometria/Release demostro
    (Incremento B) que el mismo codigo puede existir fisicamente duplicado
    en dos carpetas (Release/ y app.publish/) -- agregar conteos a nivel de
    app confundiria esa duplicacion fisica con sobrecarga real, marcando
    TODAS las llamadas de esa clase como "ambiguas" por error."""
    structure = app_structure.discover_application_structure(app_id)
    if structure is None:
        return None

    decompiled_root = app_structure.resolve_decompiled_root(structure.app_name)
    if not decompiled_root.is_dir():
        return ApplicationInteractions(
            app_name=structure.app_name, event_wirings=(), call_edges=(), unknowns=structure.unknowns,
        )

    # Base para el hop unico de "unresolved_call_target_inherited": nombre
    # de clase -> base_type (tal como lo determino Incremento A), y el
    # conjunto de nombres de metodo declarados por cada clase A NIVEL DE
    # APP (usado solo para esta verificacion de existencia, nunca para
    # decidir ambiguedad -- eso permanece estrictamente por archivo).
    class_base_type: dict[str, str | None] = {}
    all_class_methods: dict[str, set] = {}
    for c in structure.classes:
        class_base_type.setdefault(c.class_name, c.base_type)
    for m in structure.methods:
        all_class_methods.setdefault(m.class_name, set()).add(m.method_name)

    event_wirings: list[EventWiring] = []
    call_edges: list[CallEdge] = []

    for rel_file, classes, methods, _entry_points, method_intervals, lines in app_structure.scan_app_files(
        structure.app_name, decompiled_root
    ):
        file_class_methods: dict[str, Counter] = {}
        for m in methods:
            file_class_methods.setdefault(m.class_name, Counter())[m.method_name] += 1

        for start, end, class_name, method_name in method_intervals:
            event_wirings.extend(_find_event_wirings_in_method(
                lines, start, end, class_name, structure.app_name, rel_file, file_class_methods,
            ))
            call_edges.extend(_find_call_edges_in_method(
                lines, start, end, class_name, method_name, structure.app_name, rel_file,
                file_class_methods, class_base_type, all_class_methods,
            ))

    return ApplicationInteractions(
        app_name=structure.app_name, event_wirings=tuple(event_wirings),
        call_edges=tuple(call_edges), unknowns=structure.unknowns,
    )
