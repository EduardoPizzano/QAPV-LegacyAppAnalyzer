"""Tests de analyzer/failure_catalog.py (Fase 1, Paso 3). Nada en
enrich.py/report.py/extract.py usa este catalogo todavia -- ver
tests/test_characterization.py (TestEnrichGenericConnectionErrorMessage,
TestReportGenericQueryMessage) para la foto de "antes" que este catalogo
reemplazara en una fase futura."""

from analyzer.failure_catalog import FAILURE_CATALOG, VALID_SEVERITIES, get_failure_reason

REQUIRED_CODES = {
    "DYNAMIC_SQL", "REFLECTION", "SERVER_UNAVAILABLE",
    "UNRESOLVED_VARIABLE", "MISSING_SOURCE", "UNKNOWN",
}


class TestCatalogCompleteness:
    def test_all_required_codes_present(self):
        assert REQUIRED_CODES <= FAILURE_CATALOG.keys()

    def test_dict_key_matches_entry_code_field(self):
        """Evita el bug clasico de copiar-pegar una entrada y olvidar
        actualizar su campo .code interno."""
        for key, reason in FAILURE_CATALOG.items():
            assert key == reason.code, f"Clave '{key}' no coincide con su campo .code ('{reason.code}')"

    def test_codes_are_unique(self):
        codes = [r.code for r in FAILURE_CATALOG.values()]
        assert len(codes) == len(set(codes))


class TestCatalogFieldsComplete:
    def test_all_entries_have_non_empty_required_fields(self):
        for code, reason in FAILURE_CATALOG.items():
            assert reason.description.strip(), f"{code}: description vacia"
            assert reason.user_message_template.strip(), f"{code}: user_message_template vacio"
            assert reason.recommended_action.strip(), f"{code}: recommended_action vacio"
            assert reason.category.strip(), f"{code}: category vacia"

    def test_all_severities_are_valid(self):
        for code, reason in FAILURE_CATALOG.items():
            assert reason.severity in VALID_SEVERITIES, (
                f"{code}: severidad '{reason.severity}' no esta en {VALID_SEVERITIES}"
            )

    def test_all_categories_are_known(self):
        known_categories = {"connection", "sql", "integration", "unknown"}
        for code, reason in FAILURE_CATALOG.items():
            assert reason.category in known_categories, f"{code}: categoria '{reason.category}' no reconocida"


class TestGetFailureReason:
    def test_known_code_returns_matching_entry(self):
        reason = get_failure_reason("DYNAMIC_SQL")
        assert reason.code == "DYNAMIC_SQL"

    def test_unknown_code_falls_back_to_unknown_never_raises(self):
        reason = get_failure_reason("ESTO_NO_EXISTE_TODAVIA")
        assert reason.code == "UNKNOWN"
