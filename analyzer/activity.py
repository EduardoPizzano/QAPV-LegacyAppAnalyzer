"""Lifecycle evidence de una app -- build_date y last_activity. Deliberadamente
separado de analyzer/evidence.py (Evidence es evidencia POR HALLAZGO; esto es
evidencia POR APP) y de extract.py (no toca SQL/IO, no reabre esa logica --
Fase 4 queda intacta).

Todo lo de este modulo es 100% estatico: solo lee metadatos (mtime) de
archivos/carpetas que ya existen en el share. Nunca ejecuta el legado, nunca
abre una conexion nueva, nunca asume que una fecha de archivo es una fecha de
compilacion verificada."""

import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import confidence

# Un logs folder real (GeoStatsInter\bin\Debug\logs) no termino de enumerarse
# ni en 180s+ SIN recursion -- confirmado empiricamente dos veces (11 y 13 de
# agosto de 2026). Estos topes son la unica defensa contra repetir ese
# colgado, ahora para un proposito nuevo (detectar actividad, no exes).
MAX_LOG_ENTRIES_SCANNED = 500
MAX_LOG_SCAN_SECONDS = 2.0

# Coincide con el mismo criterio ya usado en decompile.py (EXCLUDED_DIR_PREFIXES)
# para reconocer carpetas de log por nombre -- aqui para INSPECCIONARLAS, no
# para excluirlas.
LOG_DIR_PREFIX = "log"


@dataclass(frozen=True)
class ActivityEvidence:
    """"Ultima evidencia encontrada", NUNCA "ultima ejecucion confirmada".
    Ausencia de evidencia (source="no_evidence") no debe interpretarse ni
    reportarse como "la app no se usa" -- solo como "no encontramos nada que
    lo indique"."""

    date: str | None = None
    source: str = "no_evidence"  # 'file_log' | 'no_evidence'
    confidence: int = confidence.UNKNOWN


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return None


def build_date(assembly_path: Path) -> str | None:
    """PROXY practico de fecha de compilacion (mtime del ensamblado), NO una
    fecha de compilacion criptograficamente verificada. Investigacion previa
    (2026-08-13) descarto las dos alternativas teoricamente "mas correctas":

    - PE COFF TimeDateStamp: Roslyn escribe ahi un valor no-timestamp por
      defecto desde VS2015+ salvo /deterministic- explicito. Confirmado con
      evidencia real: apps net472/net48 del portafolio devuelven fechas
      absurdas (2098, 2082, 2088); dos apps net6.0-windows DISTINTAS
      (ApiDispositivo, DrawConfig2) devolvieron el MISMO valor exacto
      (2024-01-19 23:04:32 UTC), confirmando que es un valor derivado del
      compilador/SDK, no un timestamp real de build.
    - AssemblyVersion("1.0.*") (auto-incremento de Build/Revision que
      codificaria una fecha real): barrido completo de decompiled/ (cientos
      de AssemblyInfo.cs) -- 0 apps usan el comodin, todas usan el valor fijo
      por defecto de Visual Studio "1.0.0.0"."""
    return _mtime_iso(Path(assembly_path))


def _scan_log_dir_bounded(folder: Path) -> tuple[float | None, bool]:
    """mtime maximo entre los ARCHIVOS directos de folder (nunca recursivo --
    un solo nivel), con tope duro de entradas Y de tiempo de pared. Devuelve
    (mtime_maximo_o_None, exhaustivo). exhaustivo=False significa que se
    alcanzo un tope antes de terminar -- el valor devuelto es el maximo
    encontrado HASTA ESE PUNTO, nunca se afirma que es el archivo mas
    reciente real cuando el escaneo fue parcial."""
    start = time.monotonic()
    max_mtime: float | None = None
    exhaustive = True
    count = 0
    try:
        with os.scandir(folder) as it:
            for entry in it:
                count += 1
                if count > MAX_LOG_ENTRIES_SCANNED or (time.monotonic() - start) > MAX_LOG_SCAN_SECONDS:
                    exhaustive = False
                    break
                try:
                    if entry.is_file():
                        mt = entry.stat().st_mtime
                        if max_mtime is None or mt > max_mtime:
                            max_mtime = mt
                except OSError:
                    continue
    except OSError:
        return None, True
    return max_mtime, exhaustive


def detect_activity_evidence(assembly_path: Path) -> ActivityEvidence:
    """Busca, UN SOLO NIVEL bajo la carpeta del ensamblado (ej. bin\\Debug --
    NUNCA output_dir/decompiled, que solo tiene codigo fuente extraido, jamas
    logs de runtime reales), carpetas cuyo nombre empiece con "log"
    (case-insensitive: "logs", "Logs", "logs - Copy", "LogFiles"...) y
    archivos ".log" sueltos en ese mismo nivel.

    best-effort por diseno: ausencia de evidencia NUNCA se reporta como "no
    se uso" -- source queda "no_evidence", date=None, confidence en el piso
    de la escala (confidence.UNKNOWN).

    Acotado con los mismos topes que _scan_log_dir_bounded (mismo tipo de
    riesgo: una carpeta -- aqui, la del propio ensamblado -- con un numero de
    entradas fuera de lo normal). Nunca materializa la carpeta completa en
    memoria (sin list(os.scandir(...))) ni recorre mas alla del primer tope
    que se cumpla; una carpeta padre inusualmente grande puede hacer que el
    escaneo se corte antes de ver una carpeta de logs mas al fondo -- ese es
    el mismo tipo de perdida de exhaustividad, ya aceptado, que
    _scan_log_dir_bounded documenta para su propio nivel."""
    parent = Path(assembly_path).parent
    best_mtime: float | None = None
    best_confidence = confidence.UNKNOWN

    start = time.monotonic()
    count = 0
    try:
        with os.scandir(parent) as it:
            for entry in it:
                count += 1
                if count > MAX_LOG_ENTRIES_SCANNED or (time.monotonic() - start) > MAX_LOG_SCAN_SECONDS:
                    break

                name_lower = entry.name.lower()
                candidate_mtime: float | None = None
                candidate_confidence = confidence.UNKNOWN
                try:
                    if entry.is_dir() and name_lower.startswith(LOG_DIR_PREFIX):
                        scanned_max, exhaustive = _scan_log_dir_bounded(Path(entry.path))
                        if scanned_max is not None:
                            candidate_mtime = scanned_max
                            candidate_confidence = (
                                confidence.FILE_LOG_FILE_MTIME_EXHAUSTIVE
                                if exhaustive
                                else confidence.FILE_LOG_FILE_MTIME_BOUNDED
                            )
                        else:
                            # Carpeta reconocida pero vacia/sin archivos legibles en
                            # ese nivel -- su propio mtime sigue siendo una senal
                            # barata (O(1), NTFS lo actualiza al crear/borrar un hijo
                            # directo), aunque puede quedarse atras si la app escribe
                            # siempre al mismo archivo en vez de crear archivos nuevos.
                            candidate_mtime = entry.stat().st_mtime
                            candidate_confidence = confidence.FILE_LOG_FOLDER_MTIME
                    elif entry.is_file() and name_lower.endswith(".log"):
                        # Un archivo suelto ya es, en si mismo, un "escaneo" trivial y
                        # completo -- no hay nada mas que enumerar para saber su mtime.
                        candidate_mtime = entry.stat().st_mtime
                        candidate_confidence = confidence.FILE_LOG_FILE_MTIME_EXHAUSTIVE
                except OSError:
                    continue

                if candidate_mtime is not None and (best_mtime is None or candidate_mtime > best_mtime):
                    best_mtime, best_confidence = candidate_mtime, candidate_confidence
    except OSError:
        return ActivityEvidence()

    if best_mtime is None:
        return ActivityEvidence()

    return ActivityEvidence(
        date=datetime.fromtimestamp(best_mtime).isoformat(timespec="seconds"),
        source="file_log",
        confidence=best_confidence,
    )
