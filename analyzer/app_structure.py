"""Application Structure Discovery -- Flujo de Aplicacion, Incremento A
(2026-08-20): primera capacidad de "Flujo de Aplicacion" (concepto
DISTINTO de Data Flow: "que recorrido interno de ejecucion podemos
reconstruir" en vez de "que datos toca y que papel desempena").

Reconstruye, por evidencia estatica DERIVADA EN VIVO del codigo ya
decompilado (mismo principio que analyzer/server_resolution.py y
analyzer/data_flow.py: nunca persistencia nueva, nunca re-decompilar), la
estructura basica de una app:

    Application
        |-- Entry Point
        |-- Forms / Windows
        |-- Classes
        `-- Methods

Deliberadamente NO intenta (fuera de alcance de este incremento, ver
diagnostico previo): navegacion entre Forms (Incremento B), wiring de
eventos ni llamadas metodo->metodo (Incremento C), integracion con Data
Flow (Incremento D), ni ningun flujo funcional/de negocio (Incremento E).
Encontrar `new FormDeleteReference()` aqui NUNCA significa "la app navega
a FormDeleteReference" -- eso es evidencia de INSTANCIACION unicamente,
reservada para el incremento de Navigation Flow.

Este modulo NUNCA modifica resolve_data_flow(), resolve_data_flow_for_app(),
resolve_app_relations(), ni analyzer/diagram.py existente."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import confidence, db
from .__version__ import ANALYZER_VERSION
from .classification import THIRD_PARTY_ASSEMBLY_PATTERN
from .evidence import Evidence
from .extract import METHOD_SIG
from .pipeline import DECOMPILED_DIR
from .unknown import UnknownRecord

# --- Deteccion de namespace/clase/metodo --------------------------------

NAMESPACE_RE = re.compile(r"^\s*namespace\s+([\w.]+)")

# Extiende CLASS_SIG de analyzer/extract.py (mismo prefijo de modificadores
# + "class NOMBRE") agregando la captura del tipo base -- extract.py no lo
# necesita (solo etiqueta hallazgos SQL/IO con su clase contenedora), pero
# Application Structure Discovery SI necesita saber si esa clase es
# Form/Window para responder la pregunta L1. No es una segunda version de
# CLASS_SIG: es una extension con un proposito distinto (clasificar el TIPO
# de la clase, no solo nombrarla).
CLASS_DECL_RE = re.compile(
    r"^\s*(?:public|private|internal)\s+(?:partial\s+|sealed\s+|static\s+|abstract\s+)*class\s+(\w+)"
    r"(?:\s*:\s*([\w.,\s<>]+))?"
)

# METHOD_SIG se REUTILIZA (importado, no reimplementado) de analyzer/extract.py
# -- ya esta validado contra el portafolio real completo para esa misma
# pregunta ("es esta linea una declaracion de metodo"). Este modulo la usa de
# forma distinta (descubrir TODOS los metodos de una clase, no solo etiquetar
# un hallazgo ya conocido), pero el patron de deteccion es identico a proposito.

_FORM_BASE_TOKENS = frozenset({"Form", "System.Windows.Forms.Form"})
_WINDOW_BASE_TOKENS = frozenset({"Window", "System.Windows.Window"})

# Entry point, patron 1 (WinForms): Application.Run(...) dentro de un metodo
# -- confirmado real en 49 archivos del portafolio (Andon, CopyJDSU, etc.),
# incluso cuando esta envuelto en un guard de instancia unica (CopyJDSU).
APPLICATION_RUN_RE = re.compile(r"\bApplication\.Run\s*\(")

# Entry point, patron 2 (WPF): "App app = new App(); ... app.Run();" -- patron
# real confirmado en decompiled/AFL.Entrega/AFL.Entrega/App.cs. Deliberadamente
# un detector SEPARADO (no una regex gigante que intente cubrir ambos casos a
# la vez) porque son mecanismos de arranque distintos y el diagnostico pidio
# explicitamente no fusionarlos.
WPF_APP_INSTANCE_RE = re.compile(r"(\w+)\s*=\s*new\s+App\s*\(\s*\)")


def _wpf_app_run_var(body_lines: list[str]) -> str | None:
    """Devuelve el nombre de variable si el cuerpo crea `new App()` Y luego
    llama a `<esa_variable>.Run()` -- el patron real de App.cs. No basta con
    ver ".Run()" solo (podria ser cualquier otro `.Run()` sin relacion)."""
    for line in body_lines:
        m = WPF_APP_INSTANCE_RE.search(line)
        if m:
            var = m.group(1)
            run_re = re.compile(r"\b" + re.escape(var) + r"\.Run\s*\(\s*\)")
            if any(run_re.search(l) for l in body_lines):
                return var
    return None


@dataclass(frozen=True)
class EntryPoint:
    app_name: str
    class_name: str | None
    method_name: str
    file: str
    line: int | None
    pattern: str  # "application_run" | "wpf_app_run" | "bare_main"
    evidence: Evidence


@dataclass(frozen=True)
class ClassInfo:
    app_name: str
    class_name: str
    namespace: str | None
    file: str
    line: int | None
    class_type: str  # "form" | "window" | "class"
    base_type: str | None
    evidence: Evidence


@dataclass(frozen=True)
class MethodInfo:
    app_name: str
    class_name: str
    method_name: str
    file: str
    line: int | None
    signature: str | None
    evidence: Evidence


@dataclass(frozen=True)
class ApplicationStructure:
    app_name: str
    entry_points: tuple[EntryPoint, ...]
    classes: tuple[ClassInfo, ...]
    methods: tuple[MethodInfo, ...]
    unknowns: tuple[UnknownRecord, ...]


def _build_evidence(extractor_key: str, source_file: str | None = None,
                     line_number: int | None = None, snippet: str | None = None) -> Evidence:
    return Evidence(
        source_file=source_file, line_number=line_number, snippet=snippet,
        extractor=extractor_key, confidence=confidence.resolve_confidence(extractor_key),
        analyzer_version=ANALYZER_VERSION, created_at=datetime.now(timezone.utc).isoformat(),
    )


def _brace_matched_end(lines: list[str], start_idx: int, limit: int) -> int:
    """Mismo idioma de conteo de llaves que analyzer/extract.py::_find_method_end
    y analyzer/server_resolution.py::_find_method_body -- busca hacia adelante
    desde start_idx hasta que la profundidad de llaves vuelva a 0, acotado."""
    depth = 0
    started = False
    end = min(limit, len(lines))
    for i in range(start_idx, end):
        depth += lines[i].count("{") - lines[i].count("}")
        if "{" in lines[i]:
            started = True
        if started and depth <= 0:
            return i
    return end - 1


def _classify_base_type(base_type_text: str | None) -> tuple[str, str | None]:
    """Devuelve (class_type, base_type_normalizado). Solo Form/Window
    EXACTOS (o su forma completamente calificada) cuentan como tal -- por
    diseno explicito, UserControl u otras clases base NO se tratan como
    Form/Window en este incremento (evita sobre-alcance no autorizado)."""
    if not base_type_text:
        return "class", None
    first = base_type_text.split(",")[0].strip()
    first = re.sub(r"<.*>", "", first).strip()  # quita generics tipo IList<X>
    if first in _FORM_BASE_TOKENS:
        return "form", first
    if first in _WINDOW_BASE_TOKENS:
        return "window", first
    return "class", first


def _scan_file_structure(path: Path, app_name: str, rel_file: str) -> tuple[
    list[ClassInfo], list[MethodInfo], list[EntryPoint],
    list[tuple[int, int, str, str]], list[str]
]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    # Namespace: se asume UNO por archivo (caso real dominante en este
    # portafolio) -- se toma el ultimo `namespace X` visto antes de cada
    # clase, nunca se intenta resolver namespaces anidados/multiples con
    # precision de llaves (evita una regex/maquina de estados innecesaria
    # para un caso marginal).
    current_namespace: str | None = None
    namespace_at_line: list[str | None] = [None] * len(lines)
    for i, line in enumerate(lines):
        m = NAMESPACE_RE.match(line)
        if m:
            current_namespace = m.group(1)
        namespace_at_line[i] = current_namespace

    classes: list[ClassInfo] = []
    class_intervals: list[tuple[int, int, str]] = []  # (start, end, class_name)
    for i, line in enumerate(lines):
        m = CLASS_DECL_RE.match(line)
        if not m:
            continue
        class_name, base_type_text = m.group(1), m.group(2)
        class_type, base_type = _classify_base_type(base_type_text)
        # Gap descubierto en Incremento C (2026-08-20, validando
        # analyzer/app_interactions.py contra AFL_DataCenter): un limite
        # arbitrario de "i + 6000" lineas truncaba el cuerpo de clases
        # reales mas largas (DataCenter: 9668 lineas), cortando metodos
        # legitimos (ej. InitializeComponent en la linea 8379) fuera del
        # intervalo detectado. _brace_matched_end ya acota internamente a
        # len(lines) -- ese es el unico limite necesario, sin ventana
        # artificial adicional.
        end = _brace_matched_end(lines, i, len(lines))
        class_intervals.append((i, end, class_name))
        classes.append(ClassInfo(
            app_name=app_name, class_name=class_name, namespace=namespace_at_line[i],
            file=rel_file, line=i + 1, class_type=class_type, base_type=base_type,
            evidence=_build_evidence(
                "APP_STRUCTURE_CLASS_DECLARATION", source_file=rel_file, line_number=i + 1,
                snippet=line.strip(),
            ),
        ))

    methods: list[MethodInfo] = []
    method_intervals: list[tuple[int, int, str, str]] = []  # (start, end, class_name, method_name)
    for start, end, class_name in class_intervals:
        for i in range(start, end + 1):
            m = METHOD_SIG.match(lines[i])
            if not m:
                continue
            method_name = m.group(1)
            # Mismo gap: la ventana artificial "i + 2000" truncaba metodos
            # legitimos mas largos. El limite real y suficiente es el fin
            # YA CORREGIDO de la clase contenedora (end + 1).
            method_end = _brace_matched_end(lines, i, end + 1)
            method_intervals.append((i, method_end, class_name, method_name))
            methods.append(MethodInfo(
                app_name=app_name, class_name=class_name, method_name=method_name,
                file=rel_file, line=i + 1, signature=lines[i].strip(),
                evidence=_build_evidence(
                    "APP_STRUCTURE_METHOD_DECLARATION", source_file=rel_file, line_number=i + 1,
                    snippet=lines[i].strip(),
                ),
            ))

    def _enclosing_method(line_idx: int) -> tuple[str | None, str | None]:
        for start, end, class_name, method_name in method_intervals:
            if start <= line_idx <= end:
                return class_name, method_name
        return None, None

    entry_points: list[EntryPoint] = []
    for i, line in enumerate(lines):
        if APPLICATION_RUN_RE.search(line):
            class_name, method_name = _enclosing_method(i)
            entry_points.append(EntryPoint(
                app_name=app_name, class_name=class_name, method_name=method_name or "(desconocido)",
                file=rel_file, line=i + 1, pattern="application_run",
                evidence=_build_evidence(
                    "APP_STRUCTURE_ENTRY_POINT_APPLICATION_RUN", source_file=rel_file,
                    line_number=i + 1, snippet=line.strip(),
                ),
            ))

    for start, end, class_name, method_name in method_intervals:
        if method_name != "Main":
            continue
        body = lines[start:end + 1]
        if APPLICATION_RUN_RE.search("\n".join(body)):
            continue  # ya se registro arriba como application_run
        wpf_var = _wpf_app_run_var(body)
        if wpf_var:
            entry_points.append(EntryPoint(
                app_name=app_name, class_name=class_name, method_name=method_name,
                file=rel_file, line=start + 1, pattern="wpf_app_run",
                evidence=_build_evidence(
                    "APP_STRUCTURE_ENTRY_POINT_WPF_APP_RUN", source_file=rel_file,
                    line_number=start + 1, snippet=lines[start].strip(),
                ),
            ))
        else:
            entry_points.append(EntryPoint(
                app_name=app_name, class_name=class_name, method_name=method_name,
                file=rel_file, line=start + 1, pattern="bare_main",
                evidence=_build_evidence(
                    "APP_STRUCTURE_ENTRY_POINT_BARE_MAIN", source_file=rel_file,
                    line_number=start + 1, snippet=lines[start].strip(),
                ),
            ))

    return classes, methods, entry_points, method_intervals, lines


def resolve_decompiled_root(app_name: str) -> Path:
    """Misma convencion de raiz que analyzer/server_resolution.py::resolve_app()
    -- el nombre de app puede ser "Raiz/Modulo"; se escanea la raiz completa.
    Expuesta como funcion reutilizable (antes vivia inline aqui) para que
    otros modulos derivados en vivo (ej. analyzer/app_navigation.py) usen
    EXACTAMENTE la misma resolucion, nunca una segunda convencion."""
    return DECOMPILED_DIR / app_name.split("/")[0]


def scan_app_files(app_name: str, decompiled_root: Path):
    """Recorre TODOS los .cs relevantes de una app (excluyendo terceros) UNA
    sola vez -- devuelve, por archivo, (rel_file, classes, methods,
    entry_points, method_intervals, lines). Reutilizable por
    discover_application_structure() y por cualquier modulo derivado que
    necesite los MISMOS intervalos de clase/metodo (ej. Navigation
    Discovery) sin releer/re-parsear los archivos ni reimplementar la
    deteccion de clases/metodos."""
    results = []
    for cs_file in decompiled_root.rglob("*.cs"):
        rel = cs_file.relative_to(decompiled_root)
        top_level = rel.parts[0] if rel.parts else ""
        if THIRD_PARTY_ASSEMBLY_PATTERN.match(top_level):
            continue
        classes, methods, entry_points, method_intervals, lines = _scan_file_structure(cs_file, app_name, str(rel))
        results.append((str(rel), classes, methods, entry_points, method_intervals, lines))
    return results


def discover_application_structure(app_id: int) -> ApplicationStructure | None:
    """Punto de entrada del incremento -- deriva EN VIVO desde
    decompiled/<raiz>/ (nunca re-decompila, nunca persiste)."""
    data = db.get_app(app_id)
    if not data:
        return None
    app_name = data["app"]["name"]
    decompiled_root = resolve_decompiled_root(app_name)

    if not decompiled_root.is_dir():
        unknown = UnknownRecord(
            app_name=app_name, category="application_structure",
            reason_code="unresolved_no_source_file",
            impact="No se pudo derivar estructura: la carpeta decompilada no existe.",
        )
        return ApplicationStructure(app_name=app_name, entry_points=(), classes=(), methods=(), unknowns=(unknown,))

    all_classes: list[ClassInfo] = []
    all_methods: list[MethodInfo] = []
    all_entry_points: list[EntryPoint] = []
    unknowns: list[UnknownRecord] = []

    for _rel_file, classes, methods, entry_points, _method_intervals, _lines in scan_app_files(app_name, decompiled_root):
        all_classes.extend(classes)
        all_methods.extend(methods)
        all_entry_points.extend(entry_points)

    # WPF: el codigo C# puede demostrar "class X : Window" pero NUNCA puede
    # demostrar el wiring Click="..." si vive en BAML (confirmado en el
    # diagnostico: 0 archivos .Designer.cs, 659 .baml, 1 solo .xaml
    # recuperable y de una libreria de terceros). Se registra como Unknown
    # explicito -- NUNCA se declara "no tiene eventos", NUNCA se inventa
    # wiring inexistente. "No encontrado" != "no existe".
    for c in all_classes:
        if c.class_type == "window":
            unknowns.append(UnknownRecord(
                app_name=app_name, category="application_structure",
                reason_code="wpf_event_wiring_not_observable_in_cs",
                impact=(
                    f"La clase {c.class_name} es una Window WPF confirmada por evidencia C#, "
                    "pero el wiring de eventos (Click=\"...\" y similares) vive tipicamente en "
                    "BAML compilado, no observable por analisis estatico de .cs. No se infiere "
                    "ausencia de eventos -- solo se declara la limitacion explicitamente."
                ),
                evidence_file=c.file, evidence_class=c.class_name,
                suggested_action="Revisar XAML/BAML manualmente si se necesita el flujo de eventos de esta ventana.",
                priority="baja",
            ))

    return ApplicationStructure(
        app_name=app_name,
        entry_points=tuple(all_entry_points),
        classes=tuple(all_classes),
        methods=tuple(all_methods),
        unknowns=tuple(unknowns),
    )
