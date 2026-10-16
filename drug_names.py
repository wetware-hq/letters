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
    p = pathlib.Path(path) if path else __import__("letters").LOCALE_DIR / "drug_aliases.txt"
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


# ---- word rules (each its own check id) ----
import re as _re
CLASS_ALLERGY_ONLY = "DRUG-CLASS-ALLERGY-ONLY"   # penicillin / sulfa only in allergy fields
SALT_REQUIRED = "DRUG-SALT-REQUIRED"             # iron / potassium / magnesium order must name the salt
FOLIC_ACID_ONLY = "DRUG-FOLIC-ACID-ONLY"         # 'folic' only as 'folic acid'
MINERALS = ("iron", "potassium", "magnesium")
_SALT = _re.compile(r"\b(?:iron|potassium|magnesium)\s+(?:chloride|sulfate|sulphate|citrate|gluconate|fumarate|"
                    r"bicarbonate|oxide|aspartate|polymaltose|sucrose|carboxymaltose|glycinate|orotate|phosphate|"
                    r"dextran|lactate)\b", _re.I)


def allergy_only(path=None):
    return {w.lower() for w in _toml(path or ALLOW_FILE).get("allergy_only", [])}


def check_line(text, field="", is_order=False):
    """Check ids this line breaks. field: the notes field ('Allergies' allows class words);
    is_order: a medicine order line (minerals must name the salt)."""
    out = []
    if not field.strip().lower().startswith("allerg"):
        if any(_re.search(rf"\b{_re.escape(w)}\b", text, _re.I) for w in allergy_only()):
            out.append(CLASS_ALLERGY_ONLY)
    if is_order:
        for w in MINERALS:
            for m in _re.finditer(rf"\b{w}\b", text, _re.I):
                if not _SALT.match(text, m.start()):
                    out.append(SALT_REQUIRED); break
            if SALT_REQUIRED in out:
                break
    if _re.search(r"\bfolic\b(?!\s+acid\b)", text, _re.I):
        out.append(FOLIC_ACID_ONLY)
    return out
