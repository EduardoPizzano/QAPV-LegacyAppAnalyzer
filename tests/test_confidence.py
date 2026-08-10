"""Tests de analyzer/confidence.py (Fase 1, Paso 2). Nada en extract.py
llama a resolve_confidence() todavia -- estos tests validan la tabla en
aislamiento: es determinista, centralizada, y nunca lanza excepcion."""

from analyzer.confidence import CONFIDENCE_TABLE, resolve_confidence


class TestConfidenceTableContents:
    def test_expected_example_keys_present_with_expected_values(self):
        """Los 4 ejemplos exactos pedidos: APP_CONFIG_EXPLICIT_CONNECTION=98,
        SETTINGS_CLASS_LITERAL=85, DYNAMIC_SQL=40, UNKNOWN=20."""
        assert CONFIDENCE_TABLE["APP_CONFIG_EXPLICIT_CONNECTION"] == 98
        assert CONFIDENCE_TABLE["SETTINGS_CLASS_LITERAL"] == 85
        assert CONFIDENCE_TABLE["DYNAMIC_SQL"] == 40
        assert CONFIDENCE_TABLE["UNKNOWN"] == 20

    def test_all_values_are_valid_percentages(self):
        for key, value in CONFIDENCE_TABLE.items():
            assert isinstance(value, int), f"{key} no es un entero"
            assert 0 <= value <= 100, f"{key}={value} fuera de rango [0, 100]"

    def test_unknown_is_the_floor_never_zero(self):
        """UNKNOWN es el piso de la escala pero nunca 0 -- 0 implicaria 'sabemos
        que esto es falso', UNKNOWN significa 'no sabemos'."""
        assert CONFIDENCE_TABLE["UNKNOWN"] > 0
        assert CONFIDENCE_TABLE["UNKNOWN"] == min(CONFIDENCE_TABLE.values())

    def test_db_introspection_is_the_ceiling(self):
        """Informacion verificada contra SQL Server real debe ser la
        confianza mas alta -- nada puede superar una verificacion directa."""
        assert CONFIDENCE_TABLE["DB_INTROSPECT_DEFINITION"] == max(CONFIDENCE_TABLE.values())


class TestResolveConfidence:
    def test_known_extractor_returns_exact_score(self):
        assert resolve_confidence("APP_CONFIG_EXPLICIT_CONNECTION") == 98
        assert resolve_confidence("DYNAMIC_SQL") == 40

    def test_unknown_extractor_never_raises_and_falls_back(self):
        """Un nombre de extractor con typo o todavia no catalogado no debe
        tumbar el analisis -- cae al piso de la escala."""
        result = resolve_confidence("ESTO_NO_EXISTE_TODAVIA")
        assert result == CONFIDENCE_TABLE["UNKNOWN"]

    def test_empty_string_extractor_does_not_raise(self):
        assert resolve_confidence("") == CONFIDENCE_TABLE["UNKNOWN"]
