"""Fixtures compartidos para toda la suite. Ningun test aqui depende de
decompiled/ (puede borrarse/regenerarse) ni de qapv_analyzer.db (vive y
cambia en produccion) -- todo corre contra copias congeladas en
tests/fixtures/, para que la suite sea 100% reproducible en cualquier
maquina sin red, sin BD y sin ilspycmd."""

import json
import sys
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GOLDEN_DIR = FIXTURES_DIR / "golden"

# Permite "import analyzer" sin instalar el paquete -- mismo patron que
# app.py/main.py, que corren desde la raiz del repo.
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def fixture_root():
    """Retorna una funcion que resuelve el path raiz de un fixture por nombre,
    ej. fixture_root()("sgi") -> tests/fixtures/sgi/."""
    def _resolve(name: str) -> Path:
        path = FIXTURES_DIR / name
        assert path.is_dir(), f"Fixture '{name}' no existe en {FIXTURES_DIR}"
        return path
    return _resolve


@pytest.fixture
def load_golden():
    """Retorna una funcion que carga el snapshot dorado JSON de un fixture."""
    def _load(name: str) -> dict:
        path = GOLDEN_DIR / f"{name}.json"
        assert path.is_file(), f"Snapshot dorado '{name}.json' no existe en {GOLDEN_DIR}"
        return json.loads(path.read_text(encoding="utf-8"))
    return _load
