"""Incremento 3 (DISENO_INCREMENTO_3_CLASIFICACION.md): clasificacion
APPLICATION / THIRD_PARTY_OR_FRAMEWORK / UNKNOWN_COMPANION de las carpetas de
nivel superior que ilspycmd genera, para que extract.py pueda saltarse el
codigo de terceros sin borrar ni mover evidencia fisica.

El fixture classification_case/ imita la estructura real de un output_dir de
ilspycmd -p: tres carpetas hermanas -- AppReal (la app real, con un
SqlCommand real), Newtonsoft.Json (nombre que SI matchea
THIRD_PARTY_ASSEMBLY_PATTERN, con un SqlCommand sintetico que NUNCA deberia
aparecer si el filtro funciona) y UnknownVendor (un companion que ya paso el
blocklist de decompile.py, no matchea el patron, y por lo tanto debe seguir
escaneandose siempre)."""

from analyzer.classification import (
    APPLICATION,
    THIRD_PARTY_OR_FRAMEWORK,
    UNKNOWN_COMPANION,
    classify_decompiled_assemblies,
    third_party_folder_names,
    top_level_dir_names,
)
from analyzer.extract import find_settings, scan_project


class TestClassifyDecompiledAssemblies:
    def test_application_folder_classified_as_application(self, fixture_root):
        root = fixture_root("classification_case")
        classifications = classify_decompiled_assemblies(root, frozenset({"AppReal"}))
        assert classifications["AppReal"] == APPLICATION

    def test_pattern_match_classified_as_third_party(self, fixture_root):
        root = fixture_root("classification_case")
        classifications = classify_decompiled_assemblies(root, frozenset({"AppReal"}))
        assert classifications["Newtonsoft.Json"] == THIRD_PARTY_OR_FRAMEWORK

    def test_unrecognized_companion_classified_as_unknown_never_third_party(self, fixture_root):
        root = fixture_root("classification_case")
        classifications = classify_decompiled_assemblies(root, frozenset({"AppReal"}))
        assert classifications["UnknownVendor"] == UNKNOWN_COMPANION

    def test_missing_output_dir_returns_empty(self, tmp_path):
        classifications = classify_decompiled_assemblies(tmp_path / "no_existe", frozenset())
        assert classifications == {}


class TestThirdPartyFolderNames:
    def test_only_third_party_is_returned(self, fixture_root):
        root = fixture_root("classification_case")
        classifications = classify_decompiled_assemblies(root, frozenset({"AppReal"}))
        skip = third_party_folder_names(classifications)
        assert skip == frozenset({"Newtonsoft.Json"})

    def test_application_never_in_skip_set(self, fixture_root):
        root = fixture_root("classification_case")
        # Simular por error que el llamador tambien pasara "AppReal" -- no
        # deberia poder colarse a THIRD_PARTY_OR_FRAMEWORK bajo ninguna
        # combinacion, porque application_folder_names domina.
        classifications = classify_decompiled_assemblies(
            root, frozenset({"AppReal", "UnknownVendor"})
        )
        skip = third_party_folder_names(classifications)
        assert "AppReal" not in skip
        assert "UnknownVendor" not in skip


class TestTopLevelDirNames:
    def test_lists_only_directories(self, fixture_root):
        root = fixture_root("classification_case")
        names = top_level_dir_names(root)
        assert names == frozenset({"AppReal", "Newtonsoft.Json", "UnknownVendor"})

    def test_missing_dir_returns_empty(self, tmp_path):
        assert top_level_dir_names(tmp_path / "no_existe") == frozenset()


class TestExtractSkipsThirdPartyFolders:
    def test_scan_project_skips_third_party_finding(self, fixture_root):
        root = fixture_root("classification_case")
        sql_findings, _ = scan_project(root, skip_top_level=frozenset({"Newtonsoft.Json"}))
        raw_texts = [f.raw for f in sql_findings]
        assert not any("ThirdPartyLeakTable" in raw for raw in raw_texts)

    def test_scan_project_keeps_application_finding(self, fixture_root):
        root = fixture_root("classification_case")
        sql_findings, _ = scan_project(root, skip_top_level=frozenset({"Newtonsoft.Json"}))
        raw_texts = [f.raw for f in sql_findings]
        assert any("Pedidos" in raw for raw in raw_texts)

    def test_scan_project_keeps_unknown_companion_finding(self, fixture_root):
        """UNKNOWN_COMPANION nunca se salta -- Principio 3, ARCHITECTURAL_PRINCIPLES.md."""
        root = fixture_root("classification_case")
        sql_findings, _ = scan_project(root, skip_top_level=frozenset({"Newtonsoft.Json"}))
        raw_texts = [f.raw for f in sql_findings]
        assert any("UnknownVendorTable" in raw for raw in raw_texts)

    def test_scan_project_without_skip_top_level_scans_everything(self, fixture_root):
        """Degradacion exacta al comportamiento actual cuando no se pasa
        skip_top_level (default frozenset()) -- ningun fixture existente
        cambia de comportamiento."""
        root = fixture_root("classification_case")
        sql_findings, _ = scan_project(root)
        raw_texts = [f.raw for f in sql_findings]
        assert any("Pedidos" in raw for raw in raw_texts)
        assert any("ThirdPartyLeakTable" in raw for raw in raw_texts)
        assert any("UnknownVendorTable" in raw for raw in raw_texts)

    def test_find_settings_respects_skip_top_level(self, fixture_root):
        """find_settings() no tiene Settings.cs en este fixture -- se
        verifica solo que aceptar skip_top_level no rompe el default ni
        lanza, para las apps sin settings reales."""
        root = fixture_root("classification_case")
        settings = find_settings(root, skip_top_level=frozenset({"Newtonsoft.Json"}))
        assert settings == []
