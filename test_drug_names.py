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
    want = {"acetaminophen": "paracetamol", "coversyl": "perindopril", "lasix": "furosemide", "lipitor": "atorvastatin",
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
    files = [p for p in list((HERE / "locale").rglob("*")) + list((HERE / "examples").rglob("*")) if p.is_file()]
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


def test_coversyl_renders_generic_first():
    assert D.discharge_name("perindopril") == "perindopril (Coversyl)"


def test_made_up_brand_in_unknown_test_is_on_no_list():
    import letters as L
    assert "velbrintax" not in L.drug_names() and not D.allowed("velbrintax")


def test_penicillin_as_medicine_order_fails_allergy_only_even_though_listed():
    import tomllib
    d = tomllib.loads((HERE / "data" / "drug_allowlist.toml").read_text())
    assert "penicillin" in d["names"] and "penicillin" in d["allergy_only"]      # on both: allergy-only wins
    assert D.check_line("penicillin 500 mg four times daily", field="Medicines", is_order=True) == [D.CLASS_ALLERGY_ONLY]
    assert D.check_line("penicillin (rash 1936)", field="Allergies") == []


def test_core_treats_coversyl_as_unknown_alias_only_in_example_profile():
    import letters as L
    core = (HERE / "drugs.txt").read_text().lower() + (HERE / "data" / "drug_allowlist.toml").read_text().lower()
    assert "coversyl" not in core and not D.allowed("coversyl")
    hits = [p for p in HERE.rglob("*") if p.is_file() and ".git" not in p.parts and "__pycache__" not in p.parts
            and p.suffix in (".txt", ".toml") and "coversyl" in p.read_text(errors="ignore").lower()]
    assert hits == [L.LOCALE_DIR / "drug_aliases.txt"] and L.LOCALE_DIR.name.endswith("_EXAMPLE")


def test_without_the_example_alias_coversyl_is_unknown(tmp_path):
    import shutil, subprocess, sys, letters as L
    prof = tmp_path / "profile"; shutil.copytree(L.LOCALE_DIR, prof)
    a = prof / "drug_aliases.txt"
    a.write_text("\n".join(l for l in a.read_text().splitlines() if not l.lower().startswith("coversyl")) + "\n")
    code = "import letters as L; print('coversyl' in L.drug_names(), L.needs_flag('Coversyl'))"
    r = subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True,
                       env={**__import__("os").environ, "LETTERS_SITE_PROFILE": str(prof)})
    assert r.returncode == 0, r.stderr
    assert r.stdout.split()[0] == "False"
