import tomllib, pathlib
import drug_names as D

HERE = pathlib.Path(__file__).resolve().parent


def test_allowlist_signed_closed_and_pinned():
    import hashlib
    raw = (HERE / "data" / "drug_allowlist.toml").read_bytes()
    d = tomllib.loads(raw.decode())
    assert d["status"] == "signed" and d["signed_by"] == "Physician" and d["signed_on"] == "2026-10-16"
    assert len(d["names"]) == 86 and len(set(d["names"])) == 86
    pin = (HERE / "data" / "drug_allowlist.SHA256").read_text().split()[0]
    assert hashlib.sha256(raw).hexdigest() == pin, "allowlist changed: re-sign and re-pin"
    for n in ("paracetamol", "clavulanic acid", "amlodipine", "furosemide", "epinephrine", "levothyroxine"):
        assert D.allowed(n), n
    for n in ("acetaminophen", "frusemide", "adrenaline", "thyroxine", "bedlam", "cholesterol", "synthopril"):
        assert not D.allowed(n), n


def test_aliases_in_locale_pack_map_to_allowed_generics():
    al = D.local_aliases()
    want = {"acetaminophen": "paracetamol", "lasix": "furosemide", "lipitor": "atorvastatin",
            "panadol": "paracetamol", "ventolin": "salbutamol", "adrenaline": "epinephrine",
            "thyroxine": "levothyroxine", "frusemide": "furosemide", "augmentin": "amoxicillin/clavulanate"}
    import letters as L
    for a, g in want.items():
        assert L.drug_names()[a] == g, a
        for part in g.split("/"):
            assert D.allowed({"clavulanate": "clavulanic acid"}.get(part, part)), part


def test_fake_drugs_never_in_patient_lines_or_site_profiles():
    import glob
    _, names = D.fake()
    pat = __import__("re").compile(r"\b(" + "|".join(sorted(names)) + r")\b", __import__("re").I)
    files = [p for p in (HERE / "locale").rglob("*") if p.is_file()]
    files += [pathlib.Path(p) for p in glob.glob(str(HERE / "golden" / "*patient*.md"))]
    files += [HERE / "data" / "refusal_lines.toml", HERE / "drugs.txt"]
    hits = [str(p) for p in files if pat.search(p.read_text(encoding="utf-8", errors="ignore"))]
    assert hits == []
    import letters as L
    signed = [v for k, v in vars(L).items() if k.isupper() and isinstance(v, str)]
    assert not [s for s in signed if pat.search(s)]


def test_planted_fake_drug_in_profile_fails(tmp_path):
    _, names = D.fake()
    p = tmp_path / "profile.toml"; p.write_text(f'alias = "{sorted(names)[0]}"\n')
    import re
    assert re.search(r"\b(" + "|".join(names) + r")\b", p.read_text())


def test_class_words_allergy_fields_only():
    assert D.check_line("penicillin (rash)", field="Allergies") == []
    assert D.check_line("sulfa allergy", field="allergies") == []
    assert D.check_line("start penicillin 500 mg", field="Medicines") == [D.CLASS_ALLERGY_ONLY]
    assert D.check_line("sulfa", field="Plan") == [D.CLASS_ALLERGY_ONLY]


def test_mineral_order_must_name_salt():
    assert D.check_line("potassium chloride 600 mg daily", is_order=True) == []
    assert D.check_line("ferrous? no: iron polymaltose 100 mg", is_order=True) == []
    assert D.check_line("potassium 600 mg daily", is_order=True) == [D.SALT_REQUIRED]
    assert D.check_line("magnesium 400 mg nocte", is_order=True) == [D.SALT_REQUIRED]
    assert D.check_line("iron studies low; potassium 3.1", is_order=False) == []   # words fine outside orders


def test_folic_only_as_folic_acid():
    assert D.check_line("folic acid 5 mg weekly") == []
    assert D.check_line("folic 5 mg weekly") == [D.FOLIC_ACID_ONLY]


def test_each_rule_fails_on_its_own_id():
    assert D.check_line("penicillin", field="Plan") == [D.CLASS_ALLERGY_ONLY]
    assert D.check_line("iron 100 mg", is_order=True) == [D.SALT_REQUIRED]
    assert D.check_line("folic") == [D.FOLIC_ACID_ONLY]
    assert len({D.CLASS_ALLERGY_ONLY, D.SALT_REQUIRED, D.FOLIC_ACID_ONLY}) == 3


def test_signed_allowlist_allows_only_listed(tmp_path):
    p = tmp_path / "a.toml"
    p.write_text('status = "signed"\nsigned_by = "TEST"\nnames = ["paracetamol"]\n')
    assert D.allowed("Paracetamol", p) and not D.allowed("acetaminophen", p)
    p.write_text('status = "unsigned"\nsigned_by = ""\nnames = ["paracetamol"]\n')
    assert not D.allowed("paracetamol", p)


def test_made_up_medicine_always_labelled_in_patient_output():
    label, names = D.fake()
    assert label == "(made-up test medicine)" and names
    for n in names:
        assert D.patient_name(n).endswith(label)
        assert label in D.discharge_name(n, {})
    assert D.patient_name("paracetamol") == "paracetamol"


def test_made_up_medicines_are_not_real_recognised_drugs():
    import letters as L
    _, names = D.fake()
    assert not names & set(L.drug_names())


def test_discharge_list_shows_generic_then_local_alias():
    assert D.discharge_name("paracetamol", {"paracetamol": "Panadol"}) == "paracetamol (Panadol)"
    assert D.discharge_name("paracetamol") == "paracetamol (Panadol)"     # from the locale pack
    assert D.discharge_name("warfarin", {}) == "warfarin"


def test_brands_and_local_aliases_only_in_locale_pack():
    core = [l.strip() for l in (HERE / "drugs.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
    assert not [l for l in core if "=" in l], "brand=generic pairs belong in locale/<pack>/drug_aliases.txt"
    import letters as L
    assert L.drug_names()["panadol"] == "paracetamol" and L.drug_names()["frusemide"] == "furosemide"
