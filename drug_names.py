"""Drug-name rendering and checks (data-driven; nothing imported from WHO, SNOMED or AMT).
- allowed(name): closed allowlist from data/drug_allowlist.toml; empty or unsigned allows nothing (fails closed).
- patient_name(name): an invented test medicine always prints with "(made-up test medicine)".
- discharge_name(generic, aliases): "generic (local alias)" with the alias from the site profile (locale pack).
Not yet wired into letter output: the patient-facing "generic (local name)" line is pending Physician sign-off."""
import pathlib, tomllib

HERE = pathlib.Path(__file__).resolve().parent
ALLOW_FILE = HERE / "data" / "drug_allowlist.toml"
FAKE_FILE = HERE / "data" / "fake_test_drugs.toml"


def _toml(p):
    return tomllib.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def allowlist(path=None):
    d = _toml(path or ALLOW_FILE)
    if d.get("status") != "signed" or not str(d.get("signed_by", "")).strip():
        return set()
    return {n.strip().lower() for n in d.get("names", []) if n.strip()}


def allowed(name, path=None):
    return name.strip().lower() in allowlist(path)


def fake():
    d = _toml(FAKE_FILE)
    return d["label"], {n.lower() for n in d["names"]}


def patient_name(name):
    label, names = fake()
    return f"{name} {label}" if name.strip().lower() in names else name


def local_aliases(path=None):
    """{generic: local alias} from the locale pack's drug_aliases.txt (brand=generic lines; first alias wins)."""
    p = pathlib.Path(path) if path else HERE / "locale" / "au" / "drug_aliases.txt"
    out = {}
    for l in p.read_text(encoding="utf-8").splitlines():
        l = l.strip()
        if l and not l.startswith("#") and "=" in l:
            b, g = (x.strip() for x in l.split("=", 1))
            out.setdefault(g.lower(), b)
    return out


def discharge_name(generic, aliases=None):
    """Discharge medicine list entry: generic name first, local alias from the site profile in brackets."""
    a = (local_aliases() if aliases is None else aliases).get(generic.strip().lower())
    return patient_name(f"{generic} ({a})") if a else patient_name(generic)
