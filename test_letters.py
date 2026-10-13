"""Regression tests. Each rule has a test that plants its failure. Synthetic data only."""
import pathlib, re, subprocess, sys
import pytest
import letters as L
from letters import make_letter, build, check, parse, cumsum, Refused, INCIDENT, ND

HERE = pathlib.Path(__file__).parent
F = lambda n: (HERE / "fixtures" / n).read_text()
CLI = [sys.executable, str(HERE / "letters.py")]
GOLDEN = [
    ("chest_pain.txt", "referral", "Cardiology", "Dr A. Synth", None, "chest_pain_referral.md"),
    ("pneumonia_admission.txt", "discharge", "General practice", None, "gp", "pneumonia_discharge_gp.md"),
    ("pneumonia_admission.txt", "discharge", "Patient", None, "patient", "pneumonia_discharge_patient.md"),
    ("ckd_update.txt", "gp-update", "General practice", None, "gp", "ckd_update_gp.md"),
    ("podiatry_referral.txt", "referral", "Podiatry", None, None, "podiatry_referral.md"),
]


def section(md, head):
    m = re.search(rf"^## {re.escape(head)}\n\n(.*?)(?=\n## |\n---)", md, re.S | re.M)
    return m.group(1) if m else ""


# ---------- goldens and safety ----------
@pytest.mark.parametrize("fx,t,to,rec,aud,gold", GOLDEN)
def test_golden(fx, t, to, rec, aud, gold):
    assert make_letter(F(fx), t, to, rec, aud) == (HERE / "golden" / gold).read_text()


def test_patient_id_appears_once():
    md = make_letter(F("chest_pain.txt"), "referral", "Cardiology")
    assert md.count("SYNTH-1001") == 1 and "Re:" not in md


@pytest.mark.parametrize("text", [
    "Patient: Jane Doe\n1937-01-01 Problem: cough",
    "Patient: SYNTH-1\n2024-01-01 Problem: cough",
    "Patient: SYNTH-1\n03/04/2023 seen",
    "Patient: SYNTH-1\nMedicare 2123 45670 1\nProblem: cough",
    "Patient: SYNTH-1\nCall 0412 345 678\nProblem: cough",
])
def test_refuses_non_synthetic(text):
    with pytest.raises(Refused) as e:
        make_letter(text, "gp-update", "GP")
    assert INCIDENT in str(e.value)


def test_two_patients_refused():
    with pytest.raises(Refused):
        make_letter("SYNTH-1\nSYNTH-2\nProblem: x", "gp-update", "X")


def test_llm_provider_set_exits(monkeypatch):
    monkeypatch.setenv("LETTERS_LLM_PROVIDER", "anything")
    with pytest.raises(SystemExit):
        make_letter(F("ckd_update.txt"), "gp-update", "GP")


# ---------- sources and checker ----------
def test_every_body_line_cites_sources():
    md = make_letter(F("chest_pain.txt"), "referral", "Cardiology")
    body = md.split("\n---\n")[0].split("## ", 1)[1]
    for line in body.splitlines():
        if re.match(r"^(- |\d+\. )", line) and line[2:].strip() not in L.EMPTY.values():
            assert re.search(r"\[L\d+(, L\d+)*\]$", line), line


def _good():
    text = F("chest_pain.txt")
    md, _ = build(text, "referral", "Cardiology")
    return text, md, parse(text)[1]


def test_planted_number_refused():
    _, md, src = _good()
    with pytest.raises(Refused, match="number not in cited"):
        check(md.replace("aspirin 100 mg", "aspirin 300 mg"), src, "referral")


def test_planted_drug_refused():
    _, md, src = _good()
    with pytest.raises(Refused, match="word not in cited"):
        check(md.replace("aspirin 100 mg daily [L6]", "aspirin 100 mg daily, clopidogrel [L6]"), src, "referral")


def test_planted_unsourced_sentence_refused():
    _, md, src = _good()
    with pytest.raises(Refused, match="unsourced"):
        check(md.replace("## Allergies\n", "## Allergies\n\n- warfarin 5 mg daily\n"), src, "referral")


def test_planted_wrong_date_refused():
    _, md, src = _good()
    with pytest.raises(Refused, match="date not in cited"):
        check(md.replace("4.1 mmol/L on 2 Mar 1937", "4.1 mmol/L on 9 Mar 1937"), src, "referral")


def test_planted_added_certainty_refused():
    text = "SYNTH-7\n1937-04-01\nProblem: ?PE\nFollow-up: GP review in 1 week"
    md, _ = build(text, "gp-update", "GP")
    with pytest.raises(Refused):
        check(md.replace("possible PE", "confirmed PE"), parse(text)[1], "gp-update")


# ---------- latest state and negation ----------
def test_stopped_med_leaves_current_list():
    st = cumsum(parse(F("pneumonia_admission.txt"))[0])
    assert "metformin" not in st["meds"] and "metformin" in st["stopped"]


def test_stopped_then_restarted():
    t = ("SYNTH-8\n1937-01-01\nAdmission: fall\nMed: warfarin 3 mg daily\n1937-01-02\nStop: warfarin (reason: bleeding)\n"
         "1937-01-05\nMed: warfarin 2 mg daily (reason: bleeding settled)\nFollow-up: GP INR check in 3 days")
    md = make_letter(t, "discharge", "GP")
    assert "warfarin 2 mg daily" in section(md, "Current medicines")
    ch = section(md, "Medicine changes")
    assert "Stopped on 2 Jan 1937" in ch and "Restarted on 5 Jan 1937" in ch and "bleeding settled" in ch


def test_negation_no_chest_pain_clears_problem():
    t = "SYNTH-8\n1937-01-01\nProblem: chest pain\n1937-01-02\nNo chest pain since admission.\nFollow-up: GP review in 1 week"
    md = make_letter(t, "gp-update", "GP")
    assert "chest pain: resolved on 2 Jan 1937" in section(md, "Problems")


def test_ruled_out():
    t = "SYNTH-8\n1937-01-01\nProblem: ?PE\n1937-01-02\nRuled out: PE\nFollow-up: GP review in 1 week"
    md = make_letter(t, "gp-update", "GP")
    assert "ruled out on 2 Jan 1937" in section(md, "Problems")


def test_negation_across_sentence_kept_verbatim():
    t = "SYNTH-8\n1937-01-01\nNo fever or rigors overnight.\nFollow-up: GP review in 1 week"
    assert "No fever or rigors overnight." in make_letter(t, "gp-update", "GP")


# ---------- never cut ----------
def test_never_cut_sections_present_even_when_empty():
    md = make_letter("SYNTH-9\n1937-01-01 Seen today.", "gp-update", "GP")
    for s in ("Allergies not documented.", "Follow-up actions not documented.", "No results pending",
              "Current medicines not documented.", "No medicine changes documented."):
        assert s in md


def test_med_change_reason_kept_or_not_documented():
    t = "SYNTH-9\n1937-01-01\nMed: ramipril 2.5 mg daily\n1937-02-01\nMed: ramipril 5 mg daily (reason: BP above target)"
    md = make_letter(t, "gp-update", "GP")
    assert f"Reason for change: BP {L.FLAG} above target" in md
    md2 = make_letter(F("chest_pain.txt"), "referral", "Cardiology")
    assert f"Reason for change: {ND}" in md2


def test_pending_shows_who_follows_up():
    t = "SYNTH-9\n1937-01-01\nPending: TSH; follow-up: GP\nPending: ferritin"
    md = make_letter(t, "gp-update", "GP")
    assert f"Who follows up: GP {L.FLAG}" in md and f"ferritin, sent on 1 Jan 1937. Who follows up: {ND}" in md


def test_pending_cleared_by_result():
    t = "SYNTH-9\n1937-01-01\nPending: TSH\n1937-01-02\nResult: TSH 2.1 mU/L"
    assert "No results pending" in make_letter(t, "gp-update", "GP")


def test_followup_numbered_with_who_and_when():
    md = make_letter(F("pneumonia_admission.txt"), "discharge", "GP")
    acts = section(md, "Actions")
    assert acts.startswith(f"1. Who: GP {L.FLAG}. When: in 1 week.")
    assert f"2. Who: {ND}. When: in 6 weeks." in acts


# ---------- uncertainty, question, footer ----------
def test_uncertainty_preserved():
    t = "SYNTH-7\n1937-04-01\nProblem: ?PE\nProblem: likely viral illness\nFollow-up: GP review in 1 week"
    md = make_letter(t, "gp-update", "GP")
    assert f"possible PE {L.FLAG}" in md and "pulmonary embolism" not in md and "likely viral illness" in md and "?PE" not in md


def test_referral_without_question_fails():
    with pytest.raises(Refused, match="no 'Question:' line"):
        make_letter("SYNTH-1\n1937-01-01\nProblem: cough", "referral", "Respiratory")


def test_referral_opens_with_question():
    md = make_letter(F("chest_pain.txt"), "referral", "Cardiology")
    assert re.findall(r"^## (.+)$", md, re.M)[0] == "Question"


@pytest.mark.parametrize("fx,t", [("chest_pain.txt", "referral"), ("pneumonia_admission.txt", "discharge")])
def test_footer_unsigned_draft(fx, t):
    md = make_letter(F(fx), t, "GP")
    assert "Signature: [unsigned]" in md and md.rstrip().endswith("Draft: check against the record before sending.")


# ---------- labels and sensitive details ----------
def test_stigmatising_labels_rewritten_and_logged():
    t = ("SYNTH-5\n1937-01-01\nNon-compliant with insulin.\nDrug-seeking behaviour noted, pain 8 out of 10.\n"
         "Poor historian.\nFollow-up: GP review in 1 week")
    md, dropped = build(t, "gp-update", "GP")
    low = md.lower()
    for bad in ("compliant", "drug-seeking", "historian"):
        assert bad not in low
    assert "not taking insulin as prescribed" in md and "pain 8 out of 10" in md
    assert len([d for d in dropped if "stigmatising" in d]) == 3


@pytest.mark.parametrize("line,cat", [("PMHx: HIV on treatment", "HIV"), ("PMHx: childhood abuse", "abuse"),
                                      ("PMHx: depression, gout", "psychiatric")])
def test_sensitive_cut_when_irrelevant_and_logged(line, cat):
    t = f"SYNTH-6\n{line}\n1937-01-01\nQuestion: Please review ankle.\nFollow-up: GP review in 1 week"
    md, dropped = build(t, "referral", "Orthopaedics")
    assert not re.search(r"HIV|abuse|depression", md) and any(cat in d for d in dropped)


def test_sensitive_kept_when_relevant():
    t = "SYNTH-6\nPMHx: depression\n1937-01-01\nQuestion: Please review mood.\nFollow-up: GP review in 1 week"
    assert "depression" in make_letter(t, "referral", "Psychiatry")


# ---------- Amendment 1: medicines ----------
def test_a1_lithium_in_podiatry_referral_indication_cut():
    md, dropped = build(F("podiatry_referral.txt"), "referral", "Podiatry")
    meds = section(md, "Current medicines")
    assert "lithium 400 mg" in meds and "bipolar" not in md
    assert any("indication for lithium cut" in d for d in dropped)


def test_a1_full_med_list_always():
    md = make_letter(F("podiatry_referral.txt"), "referral", "Podiatry")
    assert "lithium" in md and "metformin" in md


# ---------- Amendment 2: allergies ----------
def test_a2_allergy_reaction_or_not_documented():
    t = "SYNTH-3\nAllergy: penicillin (anaphylaxis), codeine\n1937-01-01\nFollow-up: GP review in 1 week"
    al = section(make_letter(t, "gp-update", "GP"), "Allergies")
    assert "penicillin: anaphylaxis" in al and "codeine: reaction not documented" in al


# ---------- Amendment 3: templates ----------
def test_a3_template_cannot_drop_never_cut_sections():
    tpl = "{{header}}\n{{problems}}\n{{footer}}\n"
    md, log = build(F("chest_pain.txt"), "referral", "Cardiology", template=tpl)
    for h in ("Question", "Allergies", "Current medicines", "Pending results", "Actions", "Medicine changes"):
        assert f"## {h}" in md, h
    assert any("never-cut section 'allergies'" in x for x in log)


def test_a3_template_without_footer_still_gets_footer():
    md = make_letter(F("chest_pain.txt"), "referral", "Cardiology", template="{{question}}\n")
    assert "Draft: check against the record before sending." in md


def test_a3_unknown_slot_rejected():
    with pytest.raises(ValueError):
        make_letter(F("chest_pain.txt"), "referral", "Cardiology", template="{{nonsense}}")


# ---------- Amendment 4: brand ----------
def test_a4_brand_draft_mark_next_to_logo():
    b = L.load_brand(HERE / "brands" / "example-clinic")
    md = make_letter(F("chest_pain.txt"), "referral", "Cardiology", brand=b)
    logo = next(l for l in md.splitlines() if l.startswith("!["))
    assert logo.endswith("**DRAFT, unsigned**") and "Example Clinic" in md


def test_a4_repo_ships_only_made_up_brand():
    packs = [p.name for p in (HERE / "brands").iterdir()]
    assert packs == ["example-clinic"]
    assert "Made-up" in (HERE / "brands" / "example-clinic" / "brand.toml").read_text()


def test_a4_brand_missing_field_rejected(tmp_path):
    (tmp_path / "brand.toml").write_text('name = "X"\n')
    with pytest.raises(ValueError):
        L.load_brand(tmp_path)


# ---------- Amendment 5: dropped list ----------
def test_a5_dropped_list_screen_only_and_out_holds_only_letter(tmp_path):
    out = tmp_path / "out"
    r = subprocess.run(CLI + [str(HERE / "fixtures" / "podiatry_referral.txt"), "--brief", "referral to podiatry",
                              "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0 and "Dropped or changed" in r.stderr and "bipolar" in r.stderr
    assert [p.name for p in out.iterdir()] == ["letter.md"]
    letter = (out / "letter.md").read_text()
    assert "Dropped" not in letter and "bipolar" not in letter and "historian" not in letter
    assert r.stdout == ""


def test_a5_brand_and_template_cli(tmp_path):
    r = subprocess.run(CLI + [str(HERE / "fixtures" / "chest_pain.txt"), "--brief", "referral to cardiology",
                              "--brand", str(HERE / "brands" / "example-clinic"),
                              "--template", str(HERE / "templates" / "minimal.md"), "--out", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and [p.name for p in tmp_path.iterdir()] == ["letter.md"]


# ---------- Amendment 6: trends ----------
def test_a6_trends_are_values_with_dates():
    res = section(make_letter(F("pneumonia_admission.txt"), "discharge", "GP"), "Results")
    assert "180 mg/L on 1 Jun 1937, 90 mg/L on 3 Jun 1937, 40 mg/L on 4 Jun 1937" in res
    assert not L.DIRECTION.search(res)


@pytest.mark.parametrize("word", ["improving", "rising", "falling", "worsening", "stable", "trend down"])
def test_a6_planted_direction_word_refused(word):
    text = F("pneumonia_admission.txt")
    md, _ = build(text, "discharge", "GP")
    bad = md.replace("40 mg/L on 4 Jun 1937", f"40 mg/L on 4 Jun 1937, {word}")
    with pytest.raises(Refused, match="direction word"):
        check(bad, parse(text)[1], "discharge")


# ---------- Amendment 7: held-out harness ----------
def test_a7_heldout_reports_failure_rate(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("# type=gp-update\n# to=GP\nSYNTH-1\n1937-01-01\nMed: aspirin 100 mg daily\n")
    (tmp_path / "a.expect").write_text("must: aspirin 100 mg daily\n")
    (tmp_path / "b.txt").write_text("# type=gp-update\nSYNTH-2\n1937-01-01\nMed: aspirin 100 mg daily\n")
    (tmp_path / "b.expect").write_text("must-not: aspirin\n")          # planted failure
    (tmp_path / "c.txt").write_text("# expect=refused\nPatient: Real Name\n")
    rate = L.heldout(tmp_path)
    out = capsys.readouterr().out
    assert rate == pytest.approx(1 / 3) and "failure rate: 33.3%" in out and "aspirin" not in out


def test_a7_heldout_refuses_folder_inside_repo():
    with pytest.raises(SystemExit):
        L.heldout(HERE / "fixtures")


def test_a7_no_heldout_notes_in_repo():
    assert not list(HERE.rglob("*.expect")) and not (HERE / "heldout").exists()


# ---------- Educator style ----------
def test_resolved_abbreviation_expanded_on_first_use_only():
    md, _ = build(F("pneumonia_admission.txt"), "discharge", "GP", resolve=["tds=three times a day"])
    assert md.count("three times a day (tds)") == 1 and "tds for 4 more days" in md


def test_no_banned_shorthand():
    t = "SYNTH-4\n1937-01-01\nc/o SOB on exertion, ?AF\nFollow-up: GP review in 1 week"
    md = make_letter(t, "gp-update", "GP")
    assert "?AF" not in md and "atrial fibrillation" not in md and "shortness of breath" not in md
    assert f"c/o {L.FLAG} SOB {L.FLAG} on exertion, possible AF {L.FLAG}" in md


def test_planted_shorthand_refused():
    text = F("ckd_update.txt")
    md, _ = build(text, "gp-update", "GP")
    with pytest.raises(Refused, match="abbreviation not flagged or resolved: c/o"):
        check(md.replace("Clinic. [L3]", "Clinic, c/o SOB. [L3]"), parse(text)[1], "gp-update")


def test_point_first_opener():
    md = make_letter(F("pneumonia_admission.txt"), "discharge", "GP")
    assert "Discharged on 4 Jun 1937. Actions are numbered at the end." in md


def test_table_only_for_three_or_more_results():
    two = make_letter(F("chest_pain.txt"), "referral", "Cardiology")
    assert "| Test |" not in two
    t = "SYNTH-2\n1937-01-01\nResult: Hb 120 g/L\nResult: Na 139 mmol/L\nResult: K 4.1 mmol/L"
    assert "| Test | Results |" in make_letter(t, "gp-update", "GP")


def test_result_without_unit_dropped_and_logged():
    _, dropped = build("SYNTH-2\n1937-01-01\nResult: Hb 120", "gp-update", "GP")
    assert any("without a unit" in d for d in dropped)


def test_planted_long_sentence_refused():
    t = "SYNTH-2\n1937-01-01\n" + " ".join(["word"] * 30) + ".\nFollow-up: GP review in 1 week"
    with pytest.raises(Refused, match="sentence over"):
        make_letter(t, "gp-update", "GP")


def test_planted_overlong_letter_refused_but_discharge_exempt():
    t = "SYNTH-2\n" + "".join(f"1937-{m:02d}-{d:02d}\nSeen on ward, eating and walking well today.\n"
                           for m in range(1, 13) for d in range(1, 6))
    t += "Admission: fall\n"
    with pytest.raises(Refused, match="limit is about 400"):
        make_letter(t, "gp-update", "GP")
    assert make_letter(t, "discharge", "GP")


def test_patient_wording():
    md = make_letter(F("pneumonia_admission.txt"), "discharge", "Patient", audience="patient")
    assert "three times a day (tds)" in md and "three times daily" not in md and "Why you came to hospital" in md


# ---------- repo hygiene ----------
def test_fixtures_synthetic_1937():
    for p in (HERE / "fixtures").glob("*.txt"):
        s = p.read_text()
        assert L.check_safe(s).startswith("SYNTH-")
        assert all(y == "1937" for y in re.findall(r"\b(\d{4})-\d{2}-\d{2}\b", s))


ENGLISH = HERE / "wordlists" / "english.txt"
TELEGRAM_OK = {"telegram", "telegram's", "telegrams"}   # the dictionary word and its inflections, english.txt only


def _hygiene_hits(p):
    lines = p.read_text().splitlines()
    if p == ENGLISH:
        lines = [l for l in lines if l not in TELEGRAM_OK]
    s = "\n".join(lines)
    return [bad for bad in ("/workspace", "/home/", "BIO ", "telegram", "patient-store") if bad in s]


def test_no_internal_names_or_paths():
    scanned = []
    for p in HERE.rglob("*"):
        if p.is_file() and ".git" not in p.parts and p.suffix in (".py", ".md", ".txt", ".toml") \
                and p.name != "test_letters.py":
            assert _hygiene_hits(p) == [], p
            scanned.append(p)
    assert ENGLISH in scanned


def test_hygiene_allows_telegram_only_in_english_list(tmp_path):
    assert set(ENGLISH.read_text().splitlines()) >= TELEGRAM_OK
    other = tmp_path / "x.txt"; other.write_text("telegram\n")
    assert _hygiene_hits(other) == ["telegram"]


# ---------- checker hardening: expansions and rewrites must be earned by the cited lines ----------
def test_planted_expanded_diagnosis_not_in_notes_refused():
    _, md, src = _good()
    assert not any(re.search(r"\bAF\b|atrial|fibrillation", v[0] + " " + (v[1] or "")) for v in src.values())
    with pytest.raises(Refused, match="word not in cited lines.*(atrial|fibrillation)"):
        check(md.replace("exertional chest pain [L5]", "exertional chest pain, atrial fibrillation [L5]"), src, "referral")


def test_expansion_whose_abbreviation_is_in_uncited_line_refused():
    text = ("Patient: SYNTH-2001\n1937-04-01 Clinic review.\nProblem: palpitations\nPMHx: AF\n"
            "Follow-up: GP review in 2 weeks")
    md, _ = build(text, "gp-update", "General practice")
    src = parse(text)[1]
    line = next(l for l in md.splitlines() if l.startswith("- palpitations"))
    with pytest.raises(Refused, match="word not in cited lines"):
        check(md.replace(line, line.replace("palpitations", "palpitations, atrial fibrillation")), src, "gp-update")


def test_legit_expansion_still_passes_via_resolution():
    text = ("Patient: SYNTH-2002\n1937-04-01 Clinic review.\nProblem: AF\nFollow-up: GP review in 2 weeks")
    md, _ = build(text, "gp-update", "General practice", resolve=["AF=atrial fibrillation"])
    assert "atrial fibrillation (AF) [L3, R1]" in md


def test_rewritten_label_not_earned_by_cited_line_refused():
    _, md, src = _good()
    with pytest.raises(Refused, match="word not in cited lines"):
        check(md.replace("exertional chest pain [L5]", "exertional chest pain, not taking medicines as prescribed [L5]"),
              src, "referral")


# ---------- --final: source tags only ----------
CHEST_RESOLVE = ["--resolve", "GP=general practitioner", "--resolve", "LDL=low-density lipoprotein cholesterol",
                 "--resolve", "nocte=at night", "--resolve", "ramipril=ramipril", "--resolve", "atorvastatin=atorvastatin"]


def _cli(*extra):
    r = subprocess.run(CLI + [str(HERE / "fixtures" / "chest_pain.txt"), "--brief", "referral to cardiology",
                              *CHEST_RESOLVE, *extra],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _draft_intact(md):
    assert "**DRAFT, unsigned**" in md
    assert "Signature: [unsigned]" in md
    assert md.rstrip().endswith("Draft: check against the record before sending.")


@pytest.mark.parametrize("brand", [False, True], ids=["no-brand", "brand"])
def test_final_removes_tags_keeps_draft_mark_and_footer(brand):
    extra = ["--brand", str(HERE / "brands" / "example-clinic")] if brand else []
    draft, final = _cli(*extra), _cli(*extra, "--final")
    assert re.search(r"\[L\d+", draft) and not re.search(r"\[[LR]\d+", final)
    _draft_intact(draft); _draft_intact(final)
    if brand:
        assert re.search(r"logo\]\([^)]*\) \*\*DRAFT, unsigned\*\*", final)
    assert re.sub(r" \[[LR]\d+(?:, [LR]\d+)*\]", "", draft) == final


def test_final_text_otherwise_unchanged():
    text = F("pneumonia_admission.txt")
    flags = []
    build(text, "discharge", "General practice", audience="gp", flags=flags)
    res = sorted({f.split()[0] + "=meaning of " + f.split()[0] for f in flags})
    md, _ = build(text, "discharge", "General practice", audience="gp", resolve=res)
    fin = L.finalise(md)
    assert "[L" not in fin and fin.count("\n") == md.count("\n")
    _draft_intact(fin)


def test_readme_describes_final_exactly():
    assert ("`--final` removes source tags only. The draft mark stays until the signing clinician removes it."
            in (HERE / "README.md").read_text())


SM_EXPECTED = {
    "ECG": ("electrocardiogram", "a heart tracing"),
    "CRP": ("C-reactive protein", "a blood test for inflammation"),
    "eGFR": ("estimated glomerular filtration rate", "a blood test of how well your kidneys filter"),
    "HbA1c": ("glycated haemoglobin", "a blood test of your average sugar level over about three months"),
    "INR": ("international normalised ratio", "a blood test of how quickly your blood clots"),
    "bd": ("twice daily", "twice a day"),
    "tds": ("three times daily", "three times a day"),
    "qid": ("four times daily", "four times a day"),
    "CKD": ("chronic kidney disease", "long-term kidney disease"),
    "COPD": ("chronic obstructive pulmonary disease", "long-term lung disease that makes breathing harder"),
}


# ---------- abbreviations: flagged by default, expanded only via reviewed list or clinician --resolve ----------
AMBIG = "Patient: SYNTH-3001\n1937-05-01 Clinic review.\nProblem: {ab}\nFollow-up: review in 2 weeks"


def _notes(tmp_path, ab):
    p = tmp_path / "notes.txt"; p.write_text(AMBIG.format(ab=ab)); return p


@pytest.mark.parametrize("ab,guesses", [("MS", ["multiple sclerosis", "mitral stenosis"]),
                                        ("PE", ["pulmonary embolism", "pleural effusion"]),
                                        ("CP", ["chest pain", "cerebral palsy"]),
                                        ("RA", ["rheumatoid arthritis", "right atrium"])])
def test_ambiguous_abbreviation_flagged_not_expanded_and_final_refused(tmp_path, ab, guesses):
    flags = []
    md, _ = build(AMBIG.format(ab=ab), "gp-update", "General practice", flags=flags)
    assert f"- {ab} {L.FLAG} [L3]" in md and any(f.startswith(ab) for f in flags)
    assert not any(g in md.lower() for g in guesses)
    with pytest.raises(Refused, match="flag"):
        L.finalise(md)
    r = subprocess.run(CLI + [str(_notes(tmp_path, ab)), "--type", "gp-update", "--to", "GP", "--final"],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "--final refused" in r.stderr and r.stdout == ""
    assert f"- {ab} [L3]" in r.stderr or f"  - {ab}" in r.stderr   # flag listed in the terminal


def test_single_meaning_list_has_exactly_ten_signed_off_entries():
    p = HERE / "single_meaning.toml"
    sm = L.load_single_meaning(p)
    assert sorted(sm) == sorted(SM_EXPECTED) and len(sm) == 10
    assert all(sm[k] == {"clinical": c, "patient": pl} for k, (c, pl) in SM_EXPECTED.items())
    assert "IV" not in sm and "signed off by the clinician reviewer and the educator" in p.read_text()


def test_resolve_closes_flag_cites_r1_and_final_runs(tmp_path):
    text = AMBIG.format(ab="MS")
    md, _ = build(text, "gp-update", "General practice", resolve=["MS=multiple sclerosis"])
    assert "- multiple sclerosis (MS) [L3, R1]" in md and L.FLAG not in md
    assert L.finalise(md).count("multiple sclerosis (MS)") == 1
    r = subprocess.run(CLI + [str(_notes(tmp_path, "MS")), "--type", "gp-update", "--to", "GP",
                              "--resolve", "MS=multiple sclerosis", "--final"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "multiple sclerosis (MS)" in r.stdout and "[R1]" not in r.stdout and "**DRAFT, unsigned**" in r.stdout


def test_checker_accepts_expansion_only_via_cited_resolution():
    text = AMBIG.format(ab="MS")
    md, _ = build(text, "gp-update", "General practice", resolve=["MS=multiple sclerosis"])
    src = parse(text)[1]
    src["R1"] = ("MS = multiple sclerosis", None)
    assert check(md, src, "gp-update")
    with pytest.raises(Refused):            # same expansion, resolution not cited
        check(md.replace("[L3, R1]", "[L3]"), src, "gp-update")


def test_resolve_for_abbreviation_not_in_notes_rejected(tmp_path):
    with pytest.raises(ValueError, match="not in the notes"):
        build(AMBIG.format(ab="MS"), "gp-update", "GP", resolve=["AF=atrial fibrillation"])
    r = subprocess.run(CLI + [str(_notes(tmp_path, "MS")), "--type", "gp-update", "--to", "GP",
                              "--resolve", "AF=atrial fibrillation"], capture_output=True, text=True)
    assert r.returncode != 0 and "not in the notes" in r.stderr


def test_goldens_expand_no_abbreviation():
    for g in (HERE / "golden").glob("*.md"):
        t = g.read_text()
        assert "atrial fibrillation" not in t.lower(), g.name
        for ab in re.findall(r"\w \(([A-Za-z0-9/]{2,7})\)", t):          # only reviewed-list expansions
            assert ab in SM_EXPECTED, (g.name, ab)


# ---------- abbreviation detection edges ----------
def _one(line):
    flags = []
    md, _ = build(f"Patient: SYNTH-3101\n1937-05-01 Clinic review.\n{line}\nFollow-up: review in 2 weeks",
                  "gp-update", "General practice", flags=flags)
    return md, [f.split()[0] for f in flags]


def test_lowercase_known_shorthand_flagged():
    md, flags = _one("Problem: sob on exertion")
    assert f"sob {L.FLAG} on exertion" in md and "sob" in flags
    assert "shortness of breath" not in md


def test_suffix_shorthand_flagged_not_passed_through():
    md, flags = _one("PMHx: NSTEMIx")
    assert f"NSTEMIx {L.FLAG}" in md and "NSTEMIx" in flags
    with pytest.raises(Refused, match="not flagged"):
        check(md.replace(f"NSTEMIx {L.FLAG}", "NSTEMIx"),
              parse("Patient: SYNTH-3101\n1937-05-01 Clinic review.\nPMHx: NSTEMIx\nFollow-up: review in 2 weeks")[1],
              "gp-update")


@pytest.mark.parametrize("line", ["Problem: chronic kidney disease stage IV", "Problem: heart failure NYHA class III",
                                  "Problem: Stage II pressure injury"])
def test_roman_numeral_stage_not_flagged(line):
    md, flags = _one(line)
    assert not any(re.fullmatch(r"[IVX]+", f) for f in flags)
    assert not re.search(r"\b[IVX]+ \[flag", md)
    assert flags == (["NYHA"] if "NYHA" in line else [])


def test_iv_route_still_flagged():
    md, flags = _one("Problem: needs IV antibiotics")
    assert flags == ["IV"]


def test_units_not_flagged():
    md, flags = _one("Med: ramipril 5 mg daily\nMed: lactulose 10 mL twice a day\nResult: potassium 4.1 mmol/L\n"
                     "Result: CRP 12 mg/L\nResult: weight 70 kg")
    assert set(flags) == {"ramipril", "lactulose"}     # drug names flagged (acceptable); no unit flagged
    for u in ("5 mg", "10 mL", "4.1 mmol/L", "12 mg/L", "70 kg"):
        assert u in md
    assert all(not L.abbreviations(u) for u in ("mg", "mL", "mmol/L", "mg/L", "kg"))


# ---------- reviewed single-meaning list ----------
SM_NOTE = "Patient: SYNTH-3201\n1937-05-01 Clinic review.\nProblem: {ab} noted\nFollow-up: review in 2 weeks"


@pytest.mark.parametrize("ab", list(SM_EXPECTED))
@pytest.mark.parametrize("aud", ["gp", "specialist", "patient"])
def test_single_meaning_entry_expands_by_audience(ab, aud):
    clinical, plain = SM_EXPECTED[ab]
    flags = []
    md, _ = build(SM_NOTE.format(ab=ab), "gp-update", "General practice", audience=aud, flags=flags)
    want, other = (plain, clinical) if aud == "patient" else (clinical, plain)
    assert f"- {want} ({ab}) noted [L3]" in md and flags == []
    assert other not in md or other in want


@pytest.mark.parametrize("ab", list(SM_EXPECTED))
def test_single_meaning_expansion_refused_when_not_cited(ab):
    text = SM_NOTE.format(ab=ab)
    md, _ = build(text, "gp-update", "General practice")
    clinical = SM_EXPECTED[ab][0]
    bad = md.replace("Clinic review. [L2]", f"Clinic review, {clinical}. [L2]")
    assert bad != md
    with pytest.raises(Refused, match="word not in cited lines"):
        check(bad, parse(text)[1], "gp-update")


@pytest.mark.parametrize("ab", ["BD", "Bd", "IV", "TDS"])
def test_case_variants_and_iv_stay_flagged(ab):
    flags = []
    md, _ = build(SM_NOTE.format(ab=ab), "gp-update", "General practice", flags=flags)
    assert f"- {ab} {L.FLAG} noted [L3]" in md and [f.split()[0] for f in flags] == [ab]


# ---------- fail-safe flagging ----------
@pytest.mark.parametrize("tok", ["Ecg", "Crp", "nbm", "cx", "PEG", "MAP", "mane", "nocte", "stat"])
def test_failsafe_token_flagged_not_expanded(tok):
    md, flags = _one(f"Problem: review {tok} today")
    assert f"review {tok} {L.FLAG} today" in md and flags == [tok]
    for ab, (clinical, plain) in SM_EXPECTED.items():
        if ab.lower() == tok.lower():
            assert clinical not in md and plain not in md


def test_sentence_start_capitalised_word_clean():
    md, flags = _one("Problem: Persistent cough after viral illness")
    assert flags == [] and "Persistent cough after viral illness [L3]" in md


def test_sentence_start_capitalised_nonword_flagged():
    md, flags = _one("Problem: Nbm overnight")
    assert flags == ["Nbm"]


def test_failsafe_units_and_stage_clean():
    md, flags = _one("Problem: chronic kidney disease stage IV\nResult: potassium 4.1 mmol/L\nResult: weight 70 kg\n"
                     "Result: albumin 30 mg/L")
    assert flags == []


def test_shorthand_and_wordlist_files_documented():
    assert "reviewed by clinician" in (HERE / "shorthand.txt").read_text().lower()
    readme = (HERE / "wordlists" / "README.md").read_text()
    assert "SCOWL" in readme and "Licence" in readme and (HERE / "wordlists" / "SCOWL-COPYRIGHT").exists()
    assert {"mane", "nocte", "stat", "prn", "od"} <= L.SHORTHAND


def test_resolve_as_written_closes_flag_without_expansion():
    text = "Patient: SYNTH-3301\n1937-05-01 Clinic review.\nMed: ramipril 5 mg daily\nFollow-up: review in 2 weeks"
    md, _ = build(text, "gp-update", "General practice", resolve=["ramipril=ramipril"])
    assert "- ramipril 5 mg daily [L3, R1]" in md and L.FLAG not in md


# ---------- patient letters: plain words + (ABBR) first, plain words alone after ----------
def test_patient_plain_words_first_use_then_plain_alone():
    text = ("Patient: SYNTH-3401\n1937-05-01 Clinic review.\nProblem: CKD noted\nResult: eGFR 40 mL/min\n"
            "1937-06-01 Review.\nProblem: CKD reviewed\nResult: eGFR 38 mL/min\nFollow-up: review in 2 weeks")
    md = make_letter(text, "gp-update", "Patient", audience="patient")
    assert md.count("long-term kidney disease (CKD)") == 1
    assert "(CKD)" in md and md.count("CKD") == 1 and md.count("long-term kidney disease") >= 2
    gp = make_letter(text, "gp-update", "General practice", audience="gp")
    assert gp.count("chronic kidney disease (CKD)") == 1 and gp.count("CKD") >= 2


def test_patient_golden_tds_plain_after_first_use():
    md = (HERE / "golden" / "pneumonia_discharge_patient.md").read_text()
    assert md.count("three times a day (tds)") == 1 and " tds" not in md.replace("(tds)", "")


def test_readme_quickstart_final_command_exits_zero():
    import shlex
    block = (HERE / "README.md").read_text().split("```bash", 1)[1].split("```", 1)[0].replace("\\\n", " ")
    cmd = next(l for l in block.splitlines() if "--final" in l and "--resolve" in l)
    args = shlex.split(cmd.split("#")[0])[2:]
    assert '"SOB=shortness of breath"' in cmd and "ECG=" not in cmd
    r = subprocess.run(CLI + args, capture_output=True, text=True, cwd=HERE)
    assert r.returncode == 0, r.stderr
    assert "shortness of breath (SOB)" in r.stdout and L.FLAG not in r.stdout


# ---------- word list integrity ----------
def test_wordlist_checksums_unchanged():
    import hashlib
    sums = (HERE / "wordlists" / "SHA256SUMS").read_text().split("\n")
    rows = [l.split() for l in sums if l.strip()]
    assert {n for _, n in rows} == {"english.txt", "medical.txt"}
    for digest, name in rows:
        assert hashlib.sha256((HERE / "wordlists" / name).read_bytes()).hexdigest() == digest, name


# ---------- shorthand additions and dose units ----------
@pytest.mark.parametrize("tok", ["tab", "cap", "sat", "sats", "sub", "supp", "ac", "pc", "mdu", "inh", "ug", "iu"])
def test_reviewed_shorthand_flagged(tok):
    md, flags = _one(f"Problem: review {tok} today")
    assert f"review {tok} {L.FLAG} today" in md and flags == [tok]


@pytest.mark.parametrize("dose", ["10u", "10 u", "10U", "10 U", "1000 IU", "1000iu", "1000 iu", "1000IU"])
def test_dose_unit_u_iu_always_flagged(dose):
    md, flags = _one(f"Problem: needs {dose} weekly")
    assert f"{dose} {L.FLAG}" in md and len(flags) == 1 and md.count(L.FLAG) == 1


def test_10u_insulin_flagged_and_checker_refuses_unflagged():
    text = "Patient: SYNTH-3501\n1937-05-01 Clinic review.\nProblem: needs 10u insulin at night\nFollow-up: review in 2 weeks"
    md, flags = build(text, "gp-update", "General practice", flags=(fl := []))[0], fl
    assert f"10u {L.FLAG} insulin" in md and any(f.startswith("10u") for f in flags)
    with pytest.raises(Refused, match="dose unit not flagged: 10u"):
        check(md.replace(f"10u {L.FLAG}", "10u"), parse(text)[1], "gp-update")
    with pytest.raises(Refused, match="flag"):
        L.finalise(md)


@pytest.mark.parametrize("line", ["Result: ALT 40 U/L", "Result: TSH 2.1 mU/L", "Result: vitamin D 50 IU/L",
                                  "Problem: needs 10 units weekly"])
def test_lab_units_and_spelled_units_clean(line):
    md, flags = _one(line)
    assert not any(u in f for f in flags for u in ("U/L", "mU/L", "IU/L", "units"))
    assert not re.search(r"(U/L|units) \[flag", md)
