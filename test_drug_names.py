import tomllib, pathlib
import drug_names as D

HERE = pathlib.Path(__file__).resolve().parent


def test_allowlist_is_closed_empty_and_unsigned_until_physician_signs():
    d = tomllib.loads((HERE / "data" / "drug_allowlist.toml").read_text())
    assert d["names"] == [] and d["status"] == "unsigned"
    assert not D.allowed("paracetamol") and not D.allowed("anything")


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
    assert L.drug_names()["panadol"] == "paracetamol" and "frusemide" in L.drug_names()
