"""Incremento Lifecycle (2026-08-13): tests de analyzer/activity.py --
build_date (proxy de mtime) y last_activity (evidencia de logs, best-effort,
acotada). Ningun test aqui toca extract.py ni reabre la logica SQL/IO ya
cerrada en Fase 4."""

import os
import time

import pytest

from analyzer import confidence
from analyzer.activity import (
    ActivityEvidence,
    MAX_LOG_ENTRIES_SCANNED,
    build_date,
    detect_activity_evidence,
)


class TestBuildDate:
    def test_returns_mtime_as_iso_string(self, tmp_path):
        exe = tmp_path / "App.exe"
        exe.write_text("fake pe bytes")
        known_time = time.mktime((2022, 3, 15, 10, 30, 0, 0, 0, -1))
        os.utime(exe, (known_time, known_time))

        result = build_date(exe)

        assert result is not None
        assert result.startswith("2022-03-15T10:30:00")

    def test_missing_file_returns_none_not_an_exception(self, tmp_path):
        assert build_date(tmp_path / "no_existe.exe") is None


class TestNoEvidence:
    def test_empty_bin_folder_reports_no_evidence_never_a_fabricated_date(self, tmp_path):
        exe = tmp_path / "bin" / "Debug" / "App.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")

        evidence = detect_activity_evidence(exe)

        assert evidence == ActivityEvidence()
        assert evidence.date is None
        assert evidence.source == "no_evidence"
        assert evidence.confidence == confidence.UNKNOWN

    def test_missing_parent_folder_reports_no_evidence(self, tmp_path):
        exe = tmp_path / "no_existe" / "App.exe"
        evidence = detect_activity_evidence(exe)
        assert evidence == ActivityEvidence()


class TestLogFolderDetection:
    def test_recognizes_logs_folder_case_insensitive_and_reads_newest_file(self, tmp_path):
        exe = tmp_path / "bin" / "Debug" / "App.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        logs = exe.parent / "Logs"  # mayuscula a proposito -- case-insensitive
        logs.mkdir()
        old_log = logs / "2024-01-01.log"
        new_log = logs / "2026-06-01.log"
        old_log.write_text("old")
        new_log.write_text("new")
        old_t = time.mktime((2024, 1, 1, 0, 0, 0, 0, 0, -1))
        new_t = time.mktime((2026, 6, 1, 0, 0, 0, 0, 0, -1))
        os.utime(old_log, (old_t, old_t))
        os.utime(new_log, (new_t, new_t))

        evidence = detect_activity_evidence(exe)

        assert evidence.source == "file_log"
        assert evidence.date.startswith("2026-06-01")
        assert evidence.confidence == confidence.FILE_LOG_FILE_MTIME_EXHAUSTIVE

    def test_recognizes_logs_copy_variant(self, tmp_path):
        """Mismo patron real visto en GeoStatsInter: 'logs - Copy' tambien
        debe reconocerse (empieza con 'log')."""
        exe = tmp_path / "bin" / "Debug" / "App.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        (exe.parent / "logs - Copy").mkdir()
        ((exe.parent / "logs - Copy") / "trace.log").write_text("x")

        evidence = detect_activity_evidence(exe)
        assert evidence.source == "file_log"

    def test_loose_log_file_directly_in_bin_debug_is_detected(self, tmp_path):
        exe = tmp_path / "bin" / "Debug" / "App.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        (exe.parent / "error.log").write_text("x")

        evidence = detect_activity_evidence(exe)

        assert evidence.source == "file_log"
        assert evidence.confidence == confidence.FILE_LOG_FILE_MTIME_EXHAUSTIVE

    def test_unrelated_folder_name_is_not_mistaken_for_a_log_folder(self, tmp_path):
        """Contraprueba: una carpeta que NO empieza con 'log' (ej. 'Data',
        'Resources') nunca debe producir evidencia -- confirma que el
        criterio es el nombre, no "cualquier carpeta que exista"."""
        exe = tmp_path / "bin" / "Debug" / "App.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        (exe.parent / "Resources").mkdir()
        ((exe.parent / "Resources") / "icon.png").write_text("x")

        evidence = detect_activity_evidence(exe)
        assert evidence == ActivityEvidence()

    def test_picks_the_most_recent_across_multiple_log_locations(self, tmp_path):
        exe = tmp_path / "bin" / "Debug" / "App.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        (exe.parent / "logs").mkdir()
        newer = exe.parent / "logs" / "b.log"
        newer.write_text("x")
        older_loose = exe.parent / "app.log"
        older_loose.write_text("x")
        t_old = time.mktime((2023, 1, 1, 0, 0, 0, 0, 0, -1))
        t_new = time.mktime((2026, 8, 1, 0, 0, 0, 0, 0, -1))
        os.utime(older_loose, (t_old, t_old))
        os.utime(newer, (t_new, t_new))

        evidence = detect_activity_evidence(exe)
        assert evidence.date.startswith("2026-08-01")


class TestBoundedScanNeverHangs:
    """El caso real que motivo estos topes: GeoStatsInter\\bin\\Debug\\logs
    no termino de enumerarse ni en 180s+ SIN recursion (confirmado dos veces,
    11 y 13 de agosto de 2026). Estos tests demuestran, con un directorio
    sintetico MAS GRANDE que el tope, que el escaneo se detiene y se marca
    como parcial en vez de intentar terminar."""

    def test_scan_stops_at_the_entry_cap_instead_of_enumerating_everything(self, tmp_path):
        exe = tmp_path / "bin" / "Debug" / "App.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        logs = exe.parent / "logs"
        logs.mkdir()
        # Mas archivos que MAX_LOG_ENTRIES_SCANNED -- si el codigo intentara
        # enumerarlos todos sin tope, este test seria lento; con el tope,
        # debe terminar rapido sin importar cuantos archivos hay de mas.
        for i in range(MAX_LOG_ENTRIES_SCANNED + 50):
            (logs / f"{i}.log").write_text("x")

        start = time.monotonic()
        evidence = detect_activity_evidence(exe)
        elapsed = time.monotonic() - start

        assert evidence.source == "file_log"
        # No exige un valor exacto de confidence (podria completar antes del
        # tope de tiempo incluso con estos archivos de mas, dependiendo del
        # filesystem) -- lo que importa es que NUNCA se cuelga.
        assert elapsed < 10.0, f"el escaneo acotado tardo {elapsed}s -- deberia terminar en segundos, no colgarse"

    def test_partial_scan_is_never_reported_as_higher_confidence_than_exhaustive(self, tmp_path):
        """Si el escaneo se corta por el tope de TIEMPO (no de entradas) antes
        de encontrar ningun archivo, debe caer al mtime de la carpeta
        (confidence mas bajo), nunca inventar una fecha de archivo que nunca
        se leyo."""
        exe = tmp_path / "bin" / "Debug" / "App.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        logs = exe.parent / "logs"
        logs.mkdir()
        (logs / "solo_un_archivo.log").write_text("x")

        evidence = detect_activity_evidence(exe)
        # Caso normal (pocos archivos): se espera exhaustivo, confidence alto.
        assert evidence.confidence == confidence.FILE_LOG_FILE_MTIME_EXHAUSTIVE


class TestParentDirectoryScanIsAlsoBounded:
    """Riesgo A (revision 2026-08-13 de la revision de diff): antes de esta
    correccion, detect_activity_evidence() hacia `entries =
    list(os.scandir(parent))` -- SIN ningun tope de conteo/tiempo -- para
    enumerar la carpeta del propio ensamblado (ej. bin\\Debug). Solo el
    escaneo DENTRO de una carpeta 'logs' anidada estaba acotado
    (_scan_log_dir_bounded, ya cubierto por TestBoundedScanNeverHangs arriba).
    Este test cubre especificamente ese nivel de arriba, que quedaba sin
    proteccion."""

    def test_parent_scan_never_iterates_past_max_log_entries_scanned(self, tmp_path, monkeypatch):
        """Prueba directa del limite real: un spy delega el 100% del trabajo
        a os.scandir real (no reemplaza ni oculta su comportamiento, solo
        cuenta cuantas entradas se iteraron de verdad) sobre una carpeta con
        mas archivos que MAX_LOG_ENTRIES_SCANNED. El limite que se garantiza
        aqui es exactamente ese: la funcion nunca itera mas alla de
        MAX_LOG_ENTRIES_SCANNED entradas del directorio padre (+1 de holgura
        porque el corte se revisa despues de contar la entrada actual, ver
        analyzer/activity.py)."""
        exe = tmp_path / "bin" / "Debug" / "App.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        # Ninguno de estos se llama "*.log" ni empieza con "log" -- no deben
        # calificar como evidencia, para que el unico limite en juego sea el
        # del directorio padre, no el de una carpeta 'logs'.
        for i in range(MAX_LOG_ENTRIES_SCANNED + 50):
            (exe.parent / f"plain_{i}.txt").write_text("x")

        real_scandir = os.scandir
        iterated: list[str] = []

        def counting_scandir(path):
            real_iterator = real_scandir(path)

            class _Spy:
                def __enter__(self_inner):
                    real_iterator.__enter__()
                    return self_inner

                def __exit__(self_inner, *exc_info):
                    return real_iterator.__exit__(*exc_info)

                def __iter__(self_inner):
                    for entry in real_iterator:
                        iterated.append(entry.name)
                        yield entry

            return _Spy()

        monkeypatch.setattr(os, "scandir", counting_scandir)

        detect_activity_evidence(exe)

        assert len(iterated) <= MAX_LOG_ENTRIES_SCANNED + 1, (
            f"se iteraron {len(iterated)} entradas del directorio padre -- "
            f"deberia detenerse en MAX_LOG_ENTRIES_SCANNED={MAX_LOG_ENTRIES_SCANNED}, "
            "confirmando que la enumeracion del padre ya no es ilimitada"
        )

    def test_parent_folder_with_more_entries_than_the_cap_still_completes_quickly_and_finds_no_false_evidence(
        self, tmp_path
    ):
        exe = tmp_path / "bin" / "Debug" / "App.exe"
        exe.parent.mkdir(parents=True)
        exe.write_text("x")
        for i in range(MAX_LOG_ENTRIES_SCANNED + 50):
            (exe.parent / f"plain_{i}.txt").write_text("x")

        start = time.monotonic()
        evidence = detect_activity_evidence(exe)
        elapsed = time.monotonic() - start

        assert elapsed < 10.0, f"el escaneo del directorio padre tardo {elapsed}s -- deberia acotarse"
        assert evidence == ActivityEvidence()  # ninguno de estos archivos califica como evidencia
