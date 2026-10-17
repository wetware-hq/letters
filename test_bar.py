"""BAR guard: the bar must point to the authoritative scan registry, not copy the ids.
Copying the category ids into prose would drift from the registry and can trip the scan on an
id that itself contains a scanned term, so the real work stays with jurisdiction_scan.toml,
whose own test holds every category to one planted id."""
import pathlib
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent
while not (ROOT / "jurisdiction_scan.toml").is_file():
    ROOT = ROOT.parent
CFG = tomllib.loads((ROOT / "jurisdiction_scan.toml").read_text())
BAR = (ROOT / "BAR.md").read_text()


def test_bar_names_the_authoritative_scan_registry():
    assert "jurisdiction_scan.toml" in BAR, "BAR.md must name jurisdiction_scan.toml as the authoritative scan registry"
    assert CFG.get("categories"), "scan registry has no categories to guard"
