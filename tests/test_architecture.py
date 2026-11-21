"""Both front ends go through the same core: neither the browser app nor the desktop app touches the
database, files or tax rules directly, so they cannot drift apart."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "tracker"
FRONT_ENDS = [ROOT / "web.py", *sorted((ROOT / "desktop").glob("*.py"))]
CORE_ONLY = {"db", "invoices", "parsing", "rates", "taxes", "settings", "backup", "caen", "config", "profiles", "theme",
             "invoicing"}
ALLOWED_CONFIG_NAMES = {"SETTINGS_PATH", "STATIC_DIR", "TEMPLATES_DIR"}   # where the web pages live


def imports(path: Path) -> list[tuple[str, list[str]]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level > 0:
            module = (node.module or "").split(".")[-1]
            found.append((module, [a.name for a in node.names]))
    return found


@pytest.mark.parametrize("path", FRONT_ENDS, ids=lambda p: p.name)
def test_front_ends_only_use_the_shared_services(path):
    for module, names in imports(path):
        if module == "config":
            assert set(names) <= ALLOWED_CONFIG_NAMES, (path.name, names)
            continue
        assert module not in CORE_ONLY, f"{path.name} imports {module} directly"
        for name in names:
            assert name not in CORE_ONLY, f"{path.name} imports {name} directly"


def test_the_services_module_covers_every_action():
    from tracker import services
    for name in ("year_view", "available_years", "import_csv", "add_invoice", "add_expense", "delete", "proof",
                 "settings_view", "save_settings", "export_backup", "restore_backup", "invoicing_view",
                 "save_client", "delete_client", "save_product", "delete_product", "save_series", "delete_series",
                 "save_invoicing_settings", "invoice_totals", "generate_invoice", "issued_pdf",
                 "credit_note_view", "start_credit_note", "credit_all", "credit_note_totals", "generate_credit_note",
                 "delete_warning"):
        assert callable(getattr(services, name))
