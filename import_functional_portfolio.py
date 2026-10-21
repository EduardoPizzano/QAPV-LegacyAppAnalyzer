"""
Portafolio Funcional — carga de "Funcionales en Piso" vs "Resto del Portafolio Tecnico"
------------------------------------------------------------------------------------------
Persiste en la base acumulativa las 67 filas del inventario funcional y sus mappings
hacia el universo tecnico de Analyzer, ya validados en el Human Validation Pack.

NO recalcula ningun mapping. NO ejecuta Data Lineage. NO modifica `apps`.
Lee unicamente analyzer/reference_data/functional_inventory.json y
analyzer/reference_data/functional_app_mapping.json (el registro versionado de
las decisiones ya tomadas) y los carga via analyzer.db.import_functional_portfolio().

Uso:
    python import_functional_portfolio.py
"""

import json
import sys
from pathlib import Path

from analyzer import db

BASE_DIR = Path(__file__).parent
REFERENCE_DATA_DIR = BASE_DIR / "analyzer" / "reference_data"


def main() -> int:
    inventory_rows = json.loads((REFERENCE_DATA_DIR / "functional_inventory.json").read_text(encoding="utf-8"))
    mapping_rows = json.loads((REFERENCE_DATA_DIR / "functional_app_mapping.json").read_text(encoding="utf-8"))

    db.init_db()
    summary = db.import_functional_portfolio(inventory_rows, mapping_rows)

    print(f"Functional inventory rows loaded: {summary['inventory_count']}")
    print(f"Mapping rows loaded:              {summary['mapping_count']}")
    print(f"  primary (is_primary=1):         {summary['primary_count']}")
    print(f"  alternative (is_primary=0):     {summary['alternative_count']}")

    partition = db.get_technical_partition()
    print()
    print(f"Technical universe total:         {partition['total']}")
    print(f"  ASSOCIATED_CONFIRMED:           {len(partition['associated_confirmed'])}")
    print(f"  ASSOCIATED_NEEDS_REVIEW:        {len(partition['associated_needs_review'])}")
    print(f"  TECHNICAL_REMAINDER:            {len(partition['technical_remainder'])}")

    if summary["unresolved_app_names"]:
        print()
        print("WARNING: analyzer app names not found in `apps` (analyzer_app_id left NULL):")
        for name in summary["unresolved_app_names"]:
            print(f"  - {name}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
