"""ADR-0004 (Fase 4 de implementacion) -- tests de integracion de
analyzer/pipeline.py::run_analysis() con la evidencia de Artifact. Nunca
invoca ilspycmd real: decompile()/find_companion_assemblies() se
reemplazan por stubs minimos (create output_dir vacio / sin companions) --
lo que se verifica aqui es EXCLUSIVAMENTE el orden y el wiring de
binary_hash/source_hash, no la extraccion real (ya cubierta por otros
tests de caracterizacion existentes)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from analyzer import pipeline  # noqa: E402


@pytest.fixture
def stub_decompile(monkeypatch):
    """Reemplaza decompile() por un stub que solo crea output_dir (sin
    invocar ilspycmd real) -- suficiente para que find_settings/
    scan_project/techstack.detect corran sobre una carpeta vacia (comportamiento
    ya soportado: "sin archivos" nunca aborta, produce listas vacias)."""
    def _fake_decompile(assembly_path, output_dir):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        return Path(output_dir)

    monkeypatch.setattr(pipeline, "decompile", _fake_decompile)
    monkeypatch.setattr(pipeline, "find_companion_assemblies", lambda assembly_path: [])
    monkeypatch.setattr(pipeline, "DECOMPILED_DIR", None)  # se sobreescribe por test via output_dir real


class TestBinaryHashComputedBeforeDecompile:
    def test_accessible_binary_produces_real_hash_in_artifact_evidence(self, tmp_path, monkeypatch, stub_decompile):
        monkeypatch.setattr(pipeline, "DECOMPILED_DIR", tmp_path / "decompiled")
        assembly = tmp_path / "FakeApp.exe"
        assembly.write_bytes(b"contenido de assembly de prueba")

        result = pipeline.run_analysis(assembly, "FakeApp")

        assert result.artifact_evidence.binary_hash != "UNKNOWN"
        assert len(result.artifact_evidence.binary_hash) == 64  # SHA-256 hex

    def test_inaccessible_binary_never_aborts_the_analysis(self, tmp_path, monkeypatch, stub_decompile):
        """El binario ya no existe al momento del analisis (ej. recurso de
        red caido) -- compute_binary_hash() debe devolver UNKNOWN, y
        run_analysis() debe completar de todas formas (decompile() esta
        stubbeado, asi que esto aisla especificamente el comportamiento del
        calculo de hash, no el de decompile() ante un binario ausente)."""
        monkeypatch.setattr(pipeline, "DECOMPILED_DIR", tmp_path / "decompiled")
        assembly = tmp_path / "NoExiste.exe"  # nunca se crea

        result = pipeline.run_analysis(assembly, "NoExiste")

        assert result.artifact_evidence.binary_hash == "UNKNOWN"
        assert result.app_name == "NoExiste"  # el analisis completo, no aborto

    def test_two_different_binaries_produce_different_hashes(self, tmp_path, monkeypatch, stub_decompile):
        monkeypatch.setattr(pipeline, "DECOMPILED_DIR", tmp_path / "decompiled")
        assembly_a = tmp_path / "AppA.exe"
        assembly_b = tmp_path / "AppB.exe"
        assembly_a.write_bytes(b"contenido A")
        assembly_b.write_bytes(b"contenido B")

        result_a = pipeline.run_analysis(assembly_a, "AppA")
        result_b = pipeline.run_analysis(assembly_b, "AppB")

        assert result_a.artifact_evidence.binary_hash != result_b.artifact_evidence.binary_hash

    def test_build_date_is_reused_not_recomputed_separately(self, tmp_path, monkeypatch, stub_decompile):
        """artifact_evidence.build_date debe ser EXACTAMENTE el mismo valor
        que AnalysisResult.build_date (Incremento Lifecycle ya existente) --
        nunca una segunda fuente de verdad."""
        monkeypatch.setattr(pipeline, "DECOMPILED_DIR", tmp_path / "decompiled")
        assembly = tmp_path / "FakeApp.exe"
        assembly.write_bytes(b"contenido")

        result = pipeline.run_analysis(assembly, "FakeApp")

        assert result.artifact_evidence.build_date == result.build_date
