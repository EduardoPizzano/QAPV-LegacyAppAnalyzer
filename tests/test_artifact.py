"""ADR-0004 -- tests de analyzer/artifact.py (Fase 4 de implementacion).
Funciones puras de computo de evidencia tecnica -- sin BD, mismo patron que
tests/test_lifecycle_activity.py para analyzer/activity.py. PRECISION > COBERTURA:
UNKNOWN es un valor EXPLICITO (nunca None/""), y source_hash es evidencia
SECUNDARIA -- estos tests verifican esa distincion directamente."""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from analyzer import artifact  # noqa: E402


class TestComputeBinaryHash:
    def test_accessible_binary_returns_real_sha256(self, tmp_path):
        exe = tmp_path / "fake.exe"
        content = b"contenido binario de prueba"
        exe.write_bytes(content)

        result = artifact.compute_binary_hash(exe)

        assert result == hashlib.sha256(content).hexdigest()
        assert result != artifact.UNKNOWN

    def test_inaccessible_binary_returns_explicit_unknown(self, tmp_path):
        missing = tmp_path / "no_existe.exe"

        result = artifact.compute_binary_hash(missing)

        assert result == "UNKNOWN"
        assert result is not None  # nunca None silencioso
        assert result != ""  # nunca cadena vacia

    def test_same_binary_content_in_two_different_paths_yields_same_hash(self, tmp_path):
        content = b"mismo binario, dos ubicaciones distintas"
        path_a = tmp_path / "workstation_a" / "app.exe"
        path_b = tmp_path / "workstation_b" / "app.exe"
        path_a.parent.mkdir()
        path_b.parent.mkdir()
        path_a.write_bytes(content)
        path_b.write_bytes(content)

        assert artifact.compute_binary_hash(path_a) == artifact.compute_binary_hash(path_b)

    def test_different_binaries_yield_different_hashes(self, tmp_path):
        path_a = tmp_path / "a.exe"
        path_b = tmp_path / "b.exe"
        path_a.write_bytes(b"contenido A")
        path_b.write_bytes(b"contenido B")

        assert artifact.compute_binary_hash(path_a) != artifact.compute_binary_hash(path_b)


class TestComputeSourceHash:
    def test_missing_output_dir_returns_none_not_a_fabricated_hash(self, tmp_path):
        assert artifact.compute_source_hash(tmp_path / "no_existe") is None

    def test_empty_output_dir_returns_none(self, tmp_path):
        empty = tmp_path / "vacio"
        empty.mkdir()
        assert artifact.compute_source_hash(empty) is None

    def test_deterministic_for_the_same_file_tree(self, tmp_path):
        root = tmp_path / "decompiled_app"
        (root / "Sub").mkdir(parents=True)
        (root / "Form1.cs").write_text("public class Form1 {}", encoding="utf-8")
        (root / "Sub" / "Helper.cs").write_text("public class Helper {}", encoding="utf-8")

        first = artifact.compute_source_hash(root)
        second = artifact.compute_source_hash(root)

        assert first == second
        assert first is not None

    def test_third_party_folder_is_excluded(self, tmp_path):
        root = tmp_path / "decompiled_app"
        root.mkdir()
        (root / "Form1.cs").write_text("public class Form1 {}", encoding="utf-8")
        without_third_party = artifact.compute_source_hash(root)

        third_party = root / "Newtonsoft.Json"
        third_party.mkdir()
        (third_party / "JsonConvert.cs").write_text("public class JsonConvert {}", encoding="utf-8")
        with_third_party_folder_present = artifact.compute_source_hash(root)

        # El contenido de terceros no debe afectar el hash -- misma
        # disciplina de exclusion que Application Structure Discovery.
        assert without_third_party == with_third_party_folder_present

    def test_different_code_yields_different_hash(self, tmp_path):
        root_a = tmp_path / "app_a"
        root_b = tmp_path / "app_b"
        root_a.mkdir()
        root_b.mkdir()
        (root_a / "Form1.cs").write_text("public class Form1 { void X() {} }", encoding="utf-8")
        (root_b / "Form1.cs").write_text("public class Form1 { void Y() {} }", encoding="utf-8")

        assert artifact.compute_source_hash(root_a) != artifact.compute_source_hash(root_b)


class TestArtifactEvidenceDefaults:
    def test_default_evidence_is_unknown_binary_and_no_source_hash(self):
        evidence = artifact.ArtifactEvidence()

        assert evidence.binary_hash == artifact.UNKNOWN
        assert evidence.source_hash is None
        assert evidence.assembly_version is None
        assert evidence.product_version is None
        assert evidence.file_version is None
