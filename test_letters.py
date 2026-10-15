import json
"""Regression tests. Each rule has a test that plants its failure. Synthetic data only."""
import os, pathlib, re, subprocess, sys, tomllib
import pytest
import letters as L
from letters import make_letter, build, build_from_template, check, parse, cumsum, Refused, INCIDENT, ND

HERE = pathlib.Path(__file__).parent
F = lambda n: (HERE / "fixtures" / n).read_text()
CLI = [sys.executable, str(HERE / "letters.py")]
READER = ["--reader-id", "SYNTH-REG-0007"]          # a GP reader, by id (names are never matched)
READER2 = READER + ["--reader-id", "SYNTH-REG-0008"]  # and a second, for a copy recipient with an id
GOLDEN = [
    ("chest_pain.txt", "referral", "Cardiology", "Dr A. Synth", None, "chest_pain_referral.md"),
    ("pneumonia_admission.txt", "discharge", "General practice", None, "gp", "pneumonia_discharge_gp.md"),
    ("pneumonia_admission.txt", "discharge", "Patient", None, "patient", "pneumonia_discharge_patient.md"),
    ("ckd_update.txt", "gp-update", "General practice", None, "gp", "ckd_update_gp.md"),
    ("podiatry_referral.txt", "referral", "Podiatry", None, None, "podiatry_referral.md"),
]


TPL = HERE / "fixtures" / "template"       # the CLI takes notes in the fixed template only (round 10)


def tpl(question="none", problems=(), history=None, meds=(), allergies=("No known allergies",), results=(), pending=(),
        actions=(), pid="SYNTH-9000", date="1937-06-03", recipient="General practice"):
    """Synthetic notes in the fixed template. Returns the text; line numbers follow the field order."""
    out = [f"Patient: {pid}", f"Date: {date}", f"Recipients: {recipient}", f"Question: {question}"]
    def lst(name, items):
        out.append(f"{name}:"); out.extend(f"- {i}" for i in items)
    lst("Problems", problems)
    out.append(f"History: {history or ''}".rstrip())
    lst("Medicines", meds); lst("Allergies", allergies); lst("Results", results); lst("Pending", pending)
    lst("Actions", actions)
    return "\n".join(out) + "\n"


def line_of(text, frag):
    return next(f"L{n}" for n, l in enumerate(text.splitlines(), 1) if frag in l)


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
    if "SYNTH-1" in text and re.search(r"\d{4}-\d\d-\d\d|\d+/\d+/\d+", text):
        assert str(e.value) == L.DATE_REFUSED.format(n=2)     # ruled: dates get the date refusal, not the incident text
    else:
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
    with pytest.raises(Refused, match="the number at .* isn't in the lines of the notes it cites"):
        check(md.replace("aspirin 100 mg", "aspirin 300 mg"), src, "referral")


def test_planted_drug_refused():
    _, md, src = _good()
    with pytest.raises(Refused, match="the word at .* isn't in the lines of the notes it cites"):
        check(md.replace("aspirin 100 mg daily [L6]", "aspirin 100 mg daily, clopidogrel [L6]"), src, "referral")


def test_planted_unsourced_sentence_refused():
    _, md, src = _good()
    with pytest.raises(Refused, match="doesn't cite a line of the notes"):
        check(md.replace("## Allergies\n", "## Allergies\n\n- warfarin 5 mg daily\n"), src, "referral")


def test_planted_wrong_date_refused():
    _, md, src = _good()
    with pytest.raises(Refused, match="the date at .* isn't in the lines of the notes it cites"):
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
    # round 11 matching-line rule (Physician, signed): a clinician letter prints no 'not documented' on a change line
    assert "Reason for change" not in md2 and "- Changed on 16 Mar 1937: from ramipril 2.5 mg daily. Now: ramipril 5 mg daily. [L7, L11]" in md2


def test_pending_shows_who_follows_up():
    t = "SYNTH-9\n1937-01-01\nPending: TSH; follow-up: GP\nPending: ferritin"
    md = make_letter(t, "gp-update", "GP")
    assert "Who follows up: GP." in md and f"GP {L.FLAG}" not in md and f"ferritin, sent on an undocumented date. Who follows up: {ND}" in md   # no sent date on the line


def test_pending_cleared_by_result():
    t = "SYNTH-9\n1937-01-01\nPending: TSH\n1937-01-02\nResult: TSH 2.1 mU/L"
    assert "No results pending" in make_letter(t, "gp-update", "GP")


def test_followup_numbered_with_who_and_when():
    md = make_letter(F("pneumonia_admission.txt"), "discharge", "GP")
    acts = section(md, "Actions")
    assert acts.startswith("1. Who: GP. When: in 1 week.")   # round 12: GP allowed (au pack)
    assert f"2. Who: {ND}. When: in 6 weeks." in acts


# ---------- uncertainty, question, footer ----------
def test_uncertainty_preserved():
    t = "SYNTH-7\n1937-04-01\nProblem: ?PE\nProblem: likely viral illness\nFollow-up: GP review in 1 week"
    md = make_letter(t, "gp-update", "GP")
    assert f"possible PE {L.FLAG}" in md and "pulmonary embolism" not in md and "likely viral illness" in md and "?PE" not in md


def test_referral_without_question_flagged_and_final_refused():
    flags = []
    res = build("SYNTH-1\n1937-01-01\nProblem: cough", "referral", "Respiratory", flags=flags); md, _ = res
    assert "Flag: this referral has no clear question. Add one before it's signed." in flags
    assert L.QUESTION_FLAG in md
    with pytest.raises(Refused, match="--final refused"):
        L.finalise(res)


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


def test_sensitive_kept_only_when_brief_includes_it():
    t = "SYNTH-6\nPMHx: depression\n1937-01-01\nQuestion: Please review mood.\nFollow-up: GP review in 1 week"
    assert "depression" not in make_letter(t, "referral", "Psychiatry")          # default: cut, even for psychiatry
    md, _ = build(t, "referral", "Psychiatry", include=["psychiatric history"])
    assert "depression" in md


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
    r = subprocess.run(CLI + [str(TPL / "podiatry_referral.txt"), "--brief", "referral to podiatry",
                              "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0 and re.search(r"^Cut from this letter: L\d+", r.stderr, re.M) and "bipolar" not in r.stderr
    assert [p.name for p in out.iterdir()] == ["letter.md"]
    letter = (out / "letter.md").read_text()
    assert "Dropped" not in letter and "bipolar" not in letter and "historian" not in letter
    assert r.stdout == ""


def test_a5_brand_and_template_cli(tmp_path):
    r = subprocess.run(CLI + [str(TPL / "chest_pain.txt"), "--brief", "referral to cardiology",
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
    with pytest.raises(Refused, match="the abbreviation at line L"):
        check(md.replace("Clinic. [L3]", "Clinic, c/o SOB. [L3]"), parse(text)[1], "gp-update")


def test_point_first_opener():
    md = make_letter(F("pneumonia_admission.txt"), "discharge", "GP")
    assert "Discharged on 4 Jun 1937. Actions are numbered at the end." in md


def test_table_only_for_three_or_more_results():
    two = make_letter(F("chest_pain.txt"), "referral", "Cardiology")
    assert "| Test |" not in two
    t = "SYNTH-2\n1937-01-01\nResult: Hb 120 g/L\nResult: Na 139 mmol/L\nResult: K 4.1 mmol/L"
    assert "| Test | Results |" in make_letter(t, "gp-update", "GP")


def test_result_without_unit_dropped_and_logged(tmp_path):
    _, dropped = build("SYNTH-2\n1937-01-01\nResult: Hb 120", "gp-update", "GP")
    assert any("without a unit" in d for d in dropped)
    # signed addition (round 12): exact screen line, GP and patient letters; never in letter.md; --final not blocked
    text = tpl(question="Please review.", problems=("review",), meds=("empagliflozin 10 mg daily",),
               results=("Hb 120",), actions=("review in 2 weeks, GP",))
    lid = line_of(text, "Hb 120")
    for brief in ("discharge to GP", "letter to the patient"):
        r, out = _run_t(tmp_path / brief.split()[-1], text, "--brief", brief, "--privacy",
                        "true" if "patient" in brief else "false", "--final")                    # check 19
        assert r.returncode == 0, r.stderr
        assert f"Line {lid} left out: no unit written." in r.stderr.splitlines()
        md = (out / "letter.md").read_text()
        assert "no unit written" not in md and "Hb 120" not in md


def test_planted_long_sentence_flagged_not_refused(tmp_path):
    t = "SYNTH-2\n1937-01-01\n" + " ".join(["word"] * 30) + ".\nFollow-up: GP review in 1 week"
    flags = []
    res = build(t, "gp-update", "GP", flags=flags); md, _ = res
    assert "Flag: sentence at L3 is over 25 words. Split it, or keep it as written." in flags
    assert L.SENTENCE_FLAG in md
    with pytest.raises(Refused, match="--final refused"):
        L.finalise(res)
    n = tmp_path / "n.txt"
    n.write_text(tpl(question="Any concerns?", history=" ".join(["word"] * 30) + ".", actions=["review in 1 week, practice nurse"]))
    r = subprocess.run(CLI + [str(n), "--type", "gp-update", "--to", "GP", "--final"], capture_output=True, text=True)
    assert r.returncode == 3 and "Flag: sentence at L6 is over 25 words. Split it, or keep it as written." in r.stderr


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
    with pytest.raises(Refused, match="the word at line L"):
        check(md.replace("exertional chest pain [L5]", "exertional chest pain, atrial fibrillation [L5]"), src, "referral")


def test_expansion_whose_abbreviation_is_in_uncited_line_refused():
    text = ("Patient: SYNTH-2001\n1937-04-01 Clinic review.\nProblem: palpitations\nPMHx: AF\n"
            "Follow-up: GP review in 2 weeks")
    md, _ = build(text, "gp-update", "General practice")
    src = parse(text)[1]
    line = next(l for l in md.splitlines() if l.startswith("- palpitations"))
    with pytest.raises(Refused, match="the word at .* isn't in the lines of the notes it cites"):
        check(md.replace(line, line.replace("palpitations", "palpitations, atrial fibrillation")), src, "gp-update")


def test_legit_expansion_still_passes_via_resolution():
    text = ("Patient: SYNTH-2002\n1937-04-01 Clinic review.\nProblem: AF\nFollow-up: GP review in 2 weeks")
    md, _ = build(text, "gp-update", "General practice", resolve=["AF=atrial fibrillation"])
    assert "atrial fibrillation (AF) [L3, R1]" in md


def test_rewritten_label_not_earned_by_cited_line_refused():
    _, md, src = _good()
    with pytest.raises(Refused, match="the word at .* isn't in the lines of the notes it cites"):
        check(md.replace("exertional chest pain [L5]", "exertional chest pain, not taking medicines as prescribed [L5]"),
              src, "referral")


# ---------- --final: source tags only ----------
CHEST_RESOLVE = ["--resolve", "LDL=low-density lipoprotein cholesterol",
                 "--resolve", "nocte=at night", "--resolve", "ramipril=ramipril", "--resolve", "atorvastatin=atorvastatin"]


def _cli(*extra):
    r = subprocess.run(CLI + [str(TPL / "chest_pain.txt"), "--brief", "referral to cardiology",
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
    res = sorted({f.split()[0] + "=meaning of " + f.split()[0] for f in flags
                  if not f.startswith(L.NO_REASON_NOTICE.split("{")[0])})    # round 11: no-reason notice, not an abbreviation
    built = build(text, "discharge", "General practice", audience="gp", resolve=res); md, _ = built
    fin = L.finalise(built)
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
AMBIG = "Patient: SYNTH-3001\n1937-05-01 Clinic review.\nProblem: {ab}\nFollow-up: review in 2 weeks\nQuestion: any concerns?"


def _notes(tmp_path, ab):
    p = tmp_path / "notes.txt"
    p.write_text(tpl(question="Is this getting worse?", problems=[ab], meds=["aspirin 100 mg daily reason: heart"],
                     actions=["review in 2 weeks, practice nurse"]))
    return p


@pytest.mark.parametrize("ab,guesses", [("MS", ["multiple sclerosis", "mitral stenosis"]),
                                        ("PE", ["pulmonary embolism", "pleural effusion"]),
                                        ("CP", ["chest pain", "cerebral palsy"]),
                                        ("RA", ["rheumatoid arthritis", "right atrium"])])
def test_ambiguous_abbreviation_flagged_not_expanded_and_final_refused(tmp_path, ab, guesses):
    flags = []
    res = build(AMBIG.format(ab=ab), "gp-update", "General practice", flags=flags); md, _ = res
    assert f"- {ab} {L.FLAG} [L3]" in md and any(f.startswith(ab) for f in flags)
    assert not any(g in md.lower() for g in guesses)
    with pytest.raises(Refused, match="flag"):
        L.finalise(res)
    r = subprocess.run(CLI + [str(_notes(tmp_path, ab)), "--type", "gp-update", "--to", "GP", "--final", *READER],
                       capture_output=True, text=True)
    assert r.returncode != 0 and "--final refused" in r.stderr and r.stdout == ""
    assert "Flag: an abbreviation on line L6 has no confirmed meaning." in r.stderr   # listed by line only
    assert ab not in r.stderr.replace("--resolve", "")


def test_single_meaning_list_has_exactly_ten_signed_off_entries():
    # r11 follow-up: the ten signed entries plus GTN (signed plain words "glyceryl trinitrate (GTN)")
    p = HERE / "single_meaning.toml"
    sm = L.load_single_meaning(p)
    assert sm.pop("GTN") == {"clinical": "glyceryl trinitrate", "patient": "glyceryl trinitrate"}
    assert sorted(sm) == sorted(SM_EXPECTED) and len(sm) == 10
    assert all(sm[k] == {"clinical": c, "patient": pl} for k, (c, pl) in SM_EXPECTED.items())
    assert "IV" not in sm and "signed off by the clinician reviewer and the educator" in p.read_text()


def test_resolve_closes_flag_cites_r1_and_final_runs(tmp_path):
    text = AMBIG.format(ab="MS")
    res = build(text, "gp-update", "General practice", resolve=["MS=multiple sclerosis"]); md, _ = res
    assert "- multiple sclerosis (MS) [L3, R1]" in md and L.FLAG not in md
    assert L.finalise(res).count("multiple sclerosis (MS)") == 1
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
_LAST = {}   # the last build result from a helper, for finalise() (it takes only a build result)


def _one(line):
    flags = []
    _LAST["res"] = build(f"Patient: SYNTH-3101\n1937-05-01 Clinic review.\n{line}\nFollow-up: review in 2 weeks\nQuestion: any concerns?",
                  "gp-update", "General practice", flags=flags)
    md, _ = _LAST["res"]
    return md, [f.split()[0] for f in flags if f != L.QUESTION_NOTICE_ANY]   # round 10: no-question flag on every letter


def test_lowercase_known_shorthand_flagged():
    md, flags = _one("Problem: sob on exertion")
    assert f"sob {L.FLAG} on exertion" in md and "sob" in flags
    assert "shortness of breath" not in md


def test_suffix_shorthand_flagged_not_passed_through():
    md, flags = _one("PMHx: NSTEMIx")
    assert f"NSTEMIx {L.FLAG}" in md and "NSTEMIx" in flags
    with pytest.raises(Refused, match="hasn't been flagged"):
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
    assert set(flags) == {"lactulose"}     # unknown drug name flagged; recognised ramipril never; no unit flagged
    for u in ("5 mg", "10 mL", "4.1 mmol/L", "12 mg/L", "70 kg"):
        assert u in md
    assert all(not L.abbreviations(u) for u in ("mg", "mL", "mmol/L", "mg/L", "kg"))


# ---------- reviewed single-meaning list ----------
SM_NOTE = "Patient: SYNTH-3201\n1937-05-01 Clinic review.\nProblem: {ab} noted\nFollow-up: review in 2 weeks\nQuestion: any concerns?"


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
    with pytest.raises(Refused, match="the word at .* isn't in the lines of the notes it cites"):
        check(bad, parse(text)[1], "gp-update")


@pytest.mark.parametrize("ab", ["BD", "Bd", "IV", "TDS"])
def test_case_variants_and_iv_stay_flagged(ab):
    flags = []
    md, _ = build(SM_NOTE.format(ab=ab), "gp-update", "General practice", flags=flags)
    assert f"- {ab} {L.FLAG} noted [L3]" in md and [f.split()[0] for f in flags] == [ab]


# ---------- fail-safe flagging ----------
@pytest.mark.parametrize("tok", ["Ecg", "Crp", "nbm", "cx", "PEG", "MAP", "stat"])   # r11: mane/nocte have signed plain words
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
    assert "- ramipril 5 mg daily [L3, R1]" in md and L.FLAG not in md          # round 11 freeze: no "reason not documented"


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
    res = build(text, "gp-update", "General practice", flags=(fl := [])); md, flags = res[0], fl
    assert f"10u {L.FLAG} insulin" in md and any(f.startswith("10u") for f in flags)
    with pytest.raises(Refused, match="the dose unit at line L"):
        check(md.replace(f"10u {L.FLAG}", "10u"), parse(text)[1], "gp-update")
    with pytest.raises(Refused, match="flag"):
        L.finalise(res)


@pytest.mark.parametrize("line", ["Result: ALT 40 U/L", "Result: TSH 2.1 mU/L", "Result: vitamin D 50 IU/L",
                                  "Problem: needs 10 units weekly"])
def test_lab_units_and_spelled_units_clean(line):
    md, flags = _one(line)
    assert not any(u in f for f in flags for u in ("U/L", "mU/L", "IU/L", "units"))
    assert not re.search(r"(U/L|units) \[flag", md)


# ---------- clashes: signed entries and the tool's own wording are never flagged ----------
@pytest.mark.parametrize("ab", ["eGFR", "HbA1c", "COPD"])
def test_signed_entries_not_flagged_despite_capitals(ab):
    assert not L.needs_flag(ab, "", tuple(L.load_single_meaning()))
    md, flags = _one(f"Problem: {ab} noted")
    assert flags == [] and f"({ab})" in md and L.FLAG not in md


FIXED_WORDING = ["DRAFT, unsigned", "not documented", "reason not documented", "Reason for change",
                 "Who follows up", "No results pending at time of writing.", "No medicine changes documented.",
                 "Current medicines not documented.", "Allergies not documented.", "Follow-up actions not documented.",
                 "No known drug allergies", "sent on an undocumented date", "Check against the record before sending."]


def test_fixed_wording_never_flagged():
    exact = tuple(L.load_single_meaning())
    heads = {h for aud in L.HEADINGS.values() for h in aud.values()}
    for phrase in FIXED_WORDING + sorted(heads) + list(L.OPENERS.values()) + [L.PATIENT_OPENER]:
        assert L.flagged_tokens(phrase.replace("{d}", ""), exact) == [], phrase
    md = make_letter("Patient: SYNTH-3601\n1937-05-01 Clinic review.\nPending: ferritin", "gp-update", "General practice")
    for phrase in ("DRAFT, unsigned", "not documented"):
        assert phrase in md and not re.search(re.escape(phrase) + r"\.? \[flag", md)


# ---------- drug-name flag label ----------
def test_non_wordlist_word_on_medicine_line_gets_drug_label():
    flags = []
    md, _ = build("Patient: SYNTH-3701\n1937-05-01 Clinic review.\nMed: synthopril 5 mg daily\nQuestion: any concerns?", "gp-update", "GP",
                  flags=flags)
    assert f"synthopril {L.DRUG_FLAG} 5 mg" in md and L.FLAG not in md
    assert flags == ["synthopril (drug name, check spelling) [L3]"]


def test_non_wordlist_word_elsewhere_keeps_abbreviation_label():
    md, flags = _one("Problem: nbm overnight")
    assert f"nbm {L.FLAG}" in md and L.DRUG_FLAG not in md


def test_shorthand_and_capitals_on_medicine_line_keep_abbreviation_label():
    md, _ = _one("Med: ramipril 5 mg nocte\nMed: GTN spray as needed")
    assert f"nocte {L.FLAG}" not in md and f"GTN {L.FLAG}" not in md   # r11: nocte and GTN have signed plain words
    assert "glyceryl trinitrate (GTN)" in md


def test_final_refused_while_drug_flag_open():
    md, _ = _one("Med: synthopril 5 mg daily")
    with pytest.raises(Refused, match="1 flag"):
        L.finalise(_LAST['res'])


# ---------- patient letters: each medicine with its reason ----------
def _pmeds(text, to="Patient", aud="patient"):
    _LAST["res"] = build(text, "gp-update", to, audience=aud)
    md = _LAST["res"][0]                                  # same text make_letter returns
    return md, section(md, "Your medicines now" if aud == "patient" else "Current medicines")


def test_patient_medicine_reason_from_cited_line_flagged_without_signed_plain_words():
    text = "Patient: SYNTH-3801\n1937-05-01 Clinic review.\nMed: metformin 500 mg bd (for type 2 diabetes)"
    md, meds = _pmeds(text)
    assert f"- metformin 500 mg twice a day (bd), for type 2 diabetes [L3]" in meds      # r11: ordinary English, no flag
    with pytest.raises(Refused, match="flag"):
        L.finalise(_LAST['res'])


def test_patient_medicine_reason_signed_entry_gets_plain_words_unflagged():
    text = "Patient: SYNTH-3802\n1937-05-01 Clinic review.\nMed: metformin 500 mg bd (for CKD)"
    _, meds = _pmeds(text)
    assert "- metformin 500 mg twice a day (bd), for long-term kidney disease (CKD) [L3]" in meds
    assert "[flag" not in meds


def test_patient_medicine_without_reason_says_reason_not_documented():
    _, meds = _pmeds(F("ckd_update.txt"))
    assert f"empagliflozin 10 mg once a day, reason not documented [L6]" in meds


def test_patient_keeps_sensitive_indication_other_audiences_cut():
    pmd, pmeds = _pmeds(F("podiatry_referral.txt"))
    assert f"lithium 400 mg at night, for bipolar disorder [L6]" in pmeds      # r11: ordinary English, no flag
    for to, aud in (("General practice", "gp"), ("Podiatry", "specialist")):
        md, dropped = build(F("podiatry_referral.txt"), "gp-update", to, audience=aud)
        assert "bipolar" not in md and any("indication for lithium cut" in d for d in dropped)


def test_patient_reason_checker_refuses_invented_reason():
    text = "Patient: SYNTH-3803\n1937-05-01 Clinic review.\nMed: metformin 500 mg bd (for type 2 diabetes)"
    md, _ = build(text, "gp-update", "Patient", audience="patient")
    with pytest.raises(Refused, match="the word at .* isn't in the lines of the notes it cites"):
        check(md.replace("for type 2 diabetes", "for heart failure"), parse(text)[1], "gp-update")


# ---------- patient letters: one reader, privacy line ----------
POD = str(TPL / "podiatry_referral.txt")


def _patient_cli(tmp_path, notes=POD, brief="update to patient", extra=()):
    out = tmp_path / "out"
    r = subprocess.run(CLI + [str(notes), "--brief", brief, "--audience", "patient", "--out", str(out), *extra],
                       capture_output=True, text=True)
    return r, out


PATIENT_TAIL = "A letter to the patient can't be copied to anyone else. Write a separate letter for each other reader."


def _copy_msg(where, tail):
    """Brief and template copy requests keep their wording; a notes line gets the round 10 wording."""
    if where.startswith("at L"):
        return L.NOTE_COPY_REFUSED.format(n=int(where[4:]))
    return f"Refused: copy request {where}. {tail}"


def _nline(text):
    return f"at L{len(text.splitlines())}"


PV_LINE = L.BY_HAND_LINES["PV-LETTER-BY-HAND"]


def _clin(r):
    """The clinician's screen after a refusal, without the CQ-LETTER-BY-HAND line (Patient's fallback)."""
    return "\n".join(x for x in r.stderr.strip().splitlines() if not x.startswith("For a clinician to write:"))


def _hand_ok(r):
    """stdout is empty, or exactly PV-LETTER-BY-HAND with CQ-LETTER-BY-HAND open on the clinician's screen."""
    if r.stdout == "":
        return "For a clinician to write:" not in r.stderr
    return r.stdout == L.BY_HAND_LINES["PV-LETTER-BY-HAND"] + "\n" and "For a clinician to write:" in r.stderr


def _assert_copy_refused(r, out, where):
    assert r.returncode == 2
    assert _clin(r) == _copy_msg(where, PATIENT_TAIL)
    assert _hand_ok(r) and r.stdout != "" and (not out.exists() or list(out.iterdir()) == [])


@pytest.mark.parametrize("brief", ["update to patient, cc GP", "update to patient copy to podiatry",
                                   "update to patient; copied to GP"])
def test_patient_letter_copy_via_brief_refused(tmp_path, brief):
    _assert_copy_refused(*_patient_cli(tmp_path, brief=brief), "in the brief")


@pytest.mark.parametrize("tpl", ["{{header}}\n{{meds}}\ncc: {{cc}}\n{{footer}}", "{{header}}\n{{meds}}\n{{copy}}\n{{footer}}",
                                 "{{header}}\n{{meds}}\nCopy to: GP\n{{footer}}"])
def test_patient_letter_copy_via_template_refused(tmp_path, tpl):
    t = tmp_path / "tpl.md"; t.write_text(tpl)
    _assert_copy_refused(*_patient_cli(tmp_path, extra=("--template", str(t))), "in the template")


@pytest.mark.parametrize("line", ["cc: GP", "Copy to: Dr A. Synth", "cc: podiatry on 1937-02-11"])
def test_patient_letter_copy_via_notes_refused(tmp_path, line):
    n = tmp_path / "notes.txt"; n.write_text((TPL / "podiatry_referral.txt").read_text() + "- " + line + "\n")
    _assert_copy_refused(*_patient_cli(tmp_path, notes=n), _nline(n.read_text()))
    assert "Synth" not in L.copy_refusal("at L1", True) and "podiatry" not in L.copy_refusal("at L1", True)
    with pytest.raises(Refused, match="This tool never sends copies"):
        build(F("podiatry_referral.txt") + line + "\n", "gp-update", "Patient", audience="patient")


def test_patient_letter_ends_with_privacy_line(tmp_path):
    r, out = _patient_cli(tmp_path)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert md.rstrip("\n").endswith("\n" + L.PATIENT_PRIVACY)
    assert L.PATIENT_PRIVACY == "This letter includes private details about your health. It's yours to share or keep private."
    assert L.flagged_tokens(L.PATIENT_PRIVACY, tuple(L.load_single_meaning())) == []
    assert not re.search(re.escape(L.PATIENT_PRIVACY) + r" \[flag", md)
    assert (HERE / "golden" / "pneumonia_discharge_patient.md").read_text().rstrip("\n").endswith(L.PATIENT_PRIVACY)


def test_non_patient_letters_refuse_copies_and_have_no_privacy_line(tmp_path):
    with pytest.raises(Refused, match="This tool never sends copies"):
        build(F("podiatry_referral.txt") + "cc: GP\n", "referral", "Podiatry")
    md, _ = build(F("podiatry_referral.txt"), "referral", "Podiatry")
    assert L.PATIENT_PRIVACY not in md and md.rstrip().endswith("Draft: check against the record before sending.")
    o = tmp_path / "o"
    r = subprocess.run(CLI + [POD, "--brief", "referral to podiatry, cc GP", "--out", str(o)],
                       capture_output=True, text=True)
    assert r.returncode == 2 and _clin(r) == "Refused: copy request in the brief. " + EXACT_TAIL
    assert not o.exists() or list(o.iterdir()) == []


# ---------- every letter: copy requests refused, never dropped silently ----------
CKD = TPL / "ckd_update.txt"
CKD_FREE = HERE / "fixtures" / "ckd_update.txt"
EXACT_TAIL = ("This tool can't send copies. Write a separate letter for each reader, "
              "so nobody assumes someone was copied in when they weren't.")


def _gp_cli(tmp_path, notes=CKD, brief="update to GP", extra=()):
    out = tmp_path / "out"
    ids = READER if "patient" in brief else READER2                       # the patient as copy recipient: no id
    r = subprocess.run(CLI + [str(notes), "--brief", brief, "--out", str(out), *ids, *extra],
                       capture_output=True, text=True)
    return r, out


def _assert_gp_refused(r, out, where, patient_named=False):
    assert r.returncode == 2
    assert _clin(r) == _copy_msg(where, EXACT_TAIL)
    assert r.stdout == (PV_LINE + "\n" if patient_named else "") and _hand_ok(r) and (not out.exists() or list(out.iterdir()) == [])


@pytest.mark.parametrize("brief", ["update to GP, cc cardiology", "update to GP copy to renal",
                                   "update to GP; copied to patient", "update to GP c.c. renal"])
def test_gp_letter_copy_via_brief_refused(tmp_path, brief):
    _assert_gp_refused(*_gp_cli(tmp_path, brief=brief), "in the brief", "patient" in brief)   # fails closed


@pytest.mark.parametrize("tpl", ["{{header}}\n{{meds}}\ncc: {{cc}}\n{{footer}}", "{{header}}\n{{copy_to}}\n{{footer}}",
                                 "{{header}}\nCopy to: renal clinic\n{{footer}}"])
def test_gp_letter_copy_via_template_refused(tmp_path, tpl):
    t = tmp_path / "t.md"
    t.write_text(tpl, encoding="utf-8")
    _assert_gp_refused(*_gp_cli(tmp_path, extra=("--template", str(t))), "in the template")


@pytest.mark.parametrize("line", ["cc: renal clinic", "Copy to: cardiology", "cc: SYNTH patient on 1937-03-02"])
def test_gp_letter_copy_via_notes_refused(tmp_path, line):
    n = tmp_path / "n.txt"
    n.write_text(CKD.read_text(encoding="utf-8").rstrip("\n") + "\n- " + line + "\n", encoding="utf-8")
    _assert_gp_refused(*_gp_cli(tmp_path, notes=n), _nline(n.read_text()))


def test_gp_letter_without_copy_still_written(tmp_path):
    r, out = _gp_cli(tmp_path)
    assert r.returncode == 0, r.stderr
    assert (out / "letter.md").exists()


def test_build_refuses_copy_for_gp():
    text = CKD_FREE.read_text(encoding="utf-8") + "\ncc: renal clinic\n"
    with pytest.raises(Refused, match="This tool never sends copies"):
        build(text, "gp-update", "GP")


# ---------- cc: dose unit vs chief complaint vs copy request ----------
@pytest.mark.parametrize("dose", ["5cc", "5 cc", "5CC", "2.5 cc"])
def test_dose_unit_cc_always_flagged_like_u(dose):
    md, flags = _one(f"Problem: needs {dose} saline")
    assert f"{dose} {L.FLAG}" in md and len(flags) == 1 and md.count(L.FLAG) == 1


def test_chief_complaint_heading_not_refused(tmp_path):
    md, _ = build(CKD_FREE.read_text(encoding="utf-8").rstrip("\n") + "\nCC: chest pain\n", "gp-update", "GP")
    assert md


@pytest.mark.parametrize("line", ["CC: Dr Synth", "CC: daughter", "cc: chest pain", "CC: renal clinic"])
def test_unclear_cc_heading_refused(tmp_path, line):
    n = tmp_path / "n.txt"
    n.write_text(CKD.read_text(encoding="utf-8").rstrip("\n") + "\n- " + line + "\n", encoding="utf-8")
    _assert_gp_refused(*_gp_cli(tmp_path, notes=n), _nline(n.read_text()))


def test_cc_dose_unit_not_refused_but_flagged(tmp_path):
    text = "Patient: SYNTH-3502\n1937-05-01 Clinic review.\nProblem: given 5 cc saline flush\nFollow-up: review in 2 weeks\nQuestion: any concerns?"
    flags = []
    md, _ = build(text, "gp-update", "GP", flags=flags)
    assert f"5 cc {L.FLAG}" in md and len(flags) == 1 and md.count(L.FLAG) == 1
    with pytest.raises(Refused, match="the dose unit at line L"):
        check(md.replace(f"5 cc {L.FLAG}", "5 cc"), parse(text)[1], "gp-update")
    assert "cc" in L._lines(L.SHORTHAND_FILE)


def test_cc_daughter_refused_with_line_and_no_name(tmp_path):
    n = tmp_path / "n.txt"
    n.write_text(CKD.read_text(encoding="utf-8").rstrip("\n") + "\n- cc: daughter Jane\n", encoding="utf-8")
    r, out = _gp_cli(tmp_path, notes=n)
    where = _nline(n.read_text())
    _assert_gp_refused(r, out, where)
    assert "Jane" not in r.stdout + r.stderr and "daughter" not in r.stdout + r.stderr
    assert where == f"at L{len(n.read_text().splitlines())}"
    rp, outp = _gp_cli(tmp_path / "p", notes=n, brief="update to patient", extra=("--audience", "patient"))
    assert rp.returncode == 2 and "Jane" not in rp.stdout + rp.stderr
    assert _clin(rp) == _copy_msg(where, PATIENT_TAIL)


# ---------- sensitive reason cut everywhere: medicine lines, background, results ----------
HIV_NOTES = """Patient: SYNTH-4101
1937-04-02 Clinic review.
Problem: type 2 diabetes
PMHx: HIV
PMHx: asthma
Med: abacavir (HIV) 600 mg daily
Med: dolutegravir 50 mg daily (for HIV)
Med: metformin 500 mg twice daily (for type 2 diabetes)
Result: HIV viral load <20 copies/mL
Result: HbA1c 52 mmol/mol
Follow-up: review in 3 months
"""
CUT_TERMS = ("HIV", "viral load", "VL", "CD4", "RVD", "undetectable")
MED_MSG = ("Flag: a medicine on line {} may tell the reader about something that was cut from this letter. "
           "Keep it, or explain it to the reader.")


@pytest.mark.parametrize("letter_type,to", [("gp-update", "Podiatry"), ("referral", "Dermatology"),
                                            ("gp-update", "GP")])
def test_sensitive_cut_everywhere_medicine_stays_and_flagged(letter_type, to):
    text = HIV_NOTES + ("Question: please review\n" if letter_type == "referral" else "")
    flags = []
    res = build(text, letter_type, to, flags=flags); md, dropped = res
    for t in CUT_TERMS:
        assert not re.search(rf"\b{re.escape(t)}\b", md, re.I), t
    for drug in ("abacavir", "dolutegravir", "metformin"):
        assert drug in md
    assert "for type 2 diabetes" in md
    assert MED_MSG.format("L6") in flags and MED_MSG.format("L7") in flags
    assert not any("L8" in f for f in flags if f.startswith("Flag: a medicine"))
    removed = " ".join(dropped)
    for t in ("'(HIV)'", "'(for HIV)'", "HIV viral load"):
        assert t in removed, t
    with pytest.raises(Refused, match="--final refused"):
        L.finalise(res)


def test_lithium_medicine_flagged_when_reason_cut():
    flags = []
    md, _ = build(F("podiatry_referral.txt"), "referral", "Podiatry", flags=flags)
    f = list(dict.fromkeys(x for x in flags if x.startswith("Flag: a medicine")))     # printed once per line
    assert len(f) == 1 and re.fullmatch(re.escape(MED_MSG).replace(r"\{\}", r"L\d+"), f[0])
    assert "lithium" not in f[0] and "bipolar" not in f[0]


def test_linked_terms_file_signed_off():
    raw = L.LINKED_TERMS_FILE.read_text()
    assert "Reviewed by clinician; needs fresh sign-off for any change" in raw
    assert "Version 2 signed off by the clinical reviewer, 13 Oct 2026. Frozen." in raw and "DRAFT" not in raw
    items = L.load_linked()
    hiv, psy, prep = items["HIV status"], items["psychiatric history"], items["PrEP"]
    assert {"HIV", "HIV+", "HIV-positive", "seropositive", "PLHIV", "HAART", "U=U", "CD4", "RVD",
            "antiretroviral", "S100 prescriber"} <= set(hiv["terms"])
    assert not {"VL", "viral load"} & set(hiv["terms"])
    assert {"Truvada", "Descovy", "tenofovir", "S100", "ritonavir", "lamivudine", "VL", "viral load"} <= set(hiv["flag_terms"])
    assert {"Biktarvy", "Triumeq", "Dovato", "Genvoya"} <= set(hiv["brands"])
    assert {"CPMS", "clozapine registry", "Li+", "lithium toxicity", "Li level"} <= set(psy["terms"])
    assert psy["brands"] == {"Clozaril": "clozapine", "Clopine": "clozapine", "Lithicarb": "lithium", "Quilonum": "lithium"}
    assert set(prep["terms"]) == {"PrEP", "pre-exposure prophylaxis"}
    assert set(prep["flag_terms"]) == {"sexual health clinic", "tenofovir"}
    assert set(prep["medicines"]) == {"tenofovir/emtricitabine", "emtricitabine"}
    assert set(prep["brands"]) == {"Truvada", "Descovy"}


@pytest.mark.parametrize("line,lost", [("Allergy: penicillin - rash", "rash"),
                                       ("Pending: echo - cardiology to arrange", "cardiology to arrange")])
def test_missing_required_detail_refused_names_line_only(line, lost):
    text = "Patient: SYNTH-4301\n1937-06-03 Clinic review.\n" + line + "\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "GP")
    L.refuse_missing_required(md, text)                      # complete letter passes
    with pytest.raises(Refused) as e:
        L.refuse_missing_required(md.replace(lost, "not documented"), text)
    assert str(e.value) == L.REQUIRED_REFUSED.format(lid="L3")
    assert lost not in str(e.value) and "penicillin" not in str(e.value) and "echo" not in str(e.value)


def test_sensitive_kept_for_relevant_reader():
    flags = []
    md, dropped = build(HIV_NOTES + "PMHx: undetectable VL\n", "gp-update", "Infectious diseases", flags=flags,
                        include=["HIV status"])
    assert "abacavir" in md and "HIV" in md and "viral load" in md.lower() and "undetectable VL" in md
    assert not any(f.startswith("Flag: a medicine") for f in flags)
    assert not any("HIV status" in d for d in dropped)


# ---------- documented allergy reactions and pending owners are kept ----------
def _two(lines):
    text = "Patient: SYNTH-4201\n1937-06-03 Clinic review.\n" + lines + "\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "GP")
    return md, text


@pytest.mark.parametrize("line,drug,react", [
    ("penicillin - rash", "penicillin", "rash"), ("penicillin (rash)", "penicillin", "rash"),
    ("amoxicillin \u2013 hives", "amoxicillin", "hives"), ("penicillin -> swelling", "penicillin", "swelling"),
    ("aspirin causes wheeze", "aspirin", "wheeze"), ("codeine: vomiting", "codeine", "vomiting"),
])
def test_allergy_reaction_kept(line, drug, react):
    md, _ = _two(f"Allergy: {line}")
    al = section(md, "Allergies")
    assert f"{drug}: {react}" in al and "reaction not documented" not in al


def test_allergy_without_reaction_still_says_not_documented():
    md, _ = _two("Allergy: penicillin")
    assert "penicillin: reaction not documented" in section(md, "Allergies")


@pytest.mark.parametrize("line,test,owner", [
    ("echo - cardiology to arrange", "echo", "cardiology to arrange"),
    ("echo (cardiology to arrange)", "echo", "cardiology to arrange"),
    ("chest X-ray \u2013 respiratory team to chase", "chest", "respiratory team to chase"),
    ("ferritin, practice nurse to follow up", "ferritin", "practice nurse to follow up"),
    ("urine culture: ward team will chase", "urine culture", "ward team will chase"),
    ("echo; who: cardiology", "echo", "cardiology"),
])
def test_pending_owner_kept(line, test, owner):
    md, _ = _two(f"Pending: {line}")
    p = section(md, "Results still to come")
    assert p.lstrip("- ").startswith(test) and owner in p and "Who follows up: not documented" not in p


def test_pending_without_owner_still_not_documented():
    md, _ = _two("Pending: echo")
    assert "Who follows up: not documented" in section(md, "Results still to come")


# ---------- brief parser ----------
BRIEF_MSG = ('Refused: brief not understood. Write it as "<kind> to <reader>", for example '
             '"discharge to GP" or "update to patient".')


@pytest.mark.parametrize("brief,kind,reader", [
    ("referral to cardiology", "referral", "Cardiology"), ("discharge to GP", "discharge", "GP"),
    ("update to GP", "gp-update", "GP"), ("reply to GP", "specialist-reply", "GP"),
    ("discharge summary for GP", "discharge", "GP"), ("a referral to renal", "referral", "Renal"),
    ("specialist reply to cardiology", "specialist-reply", "Cardiology"), ("update to the patient", "gp-update", "Patient"),
])
def test_brief_kinds(brief, kind, reader):
    assert L.parse_brief(brief) == (kind, reader)


@pytest.mark.parametrize("brief", ["please write something for Mrs Synthname", "Mrs Synthname", "summary of Mrs Synthname",
                                   "poem to Mrs Synthname", "", "to GP"])
def test_unknown_brief_refused_without_echo(tmp_path, brief):
    out = tmp_path / "o"
    r = subprocess.run(CLI + [str(CKD), "--brief", brief, "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 2 and _clin(r) == BRIEF_MSG and r.stdout == PV_LINE + "\n"   # fails closed
    assert "Synthname" not in r.stdout + r.stderr
    assert not out.exists() or list(out.iterdir()) == []


def test_update_to_patient_brief_is_patient_letter(tmp_path):
    out = tmp_path / "o"
    r = subprocess.run(CLI + [POD, "--brief", "update to patient", "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (out / "letter.md").read_text().rstrip("\n").endswith(L.PATIENT_PRIVACY)
    r = subprocess.run(CLI + [POD, "--brief", "update to patient, cc GP", "--out", str(tmp_path / "p")],
                       capture_output=True, text=True)
    assert r.returncode == 2 and _clin(r) == f"Refused: copy request in the brief. {PATIENT_TAIL}"


def test_copy_refusal_in_any_brief_kind(tmp_path):
    r, out = _gp_cli(tmp_path, brief="discharge summary to GP, cc renal")
    _assert_gp_refused(r, out, "in the brief")


def test_patient_letter_skips_linked_check_and_medicine_flag_but_keeps_abbreviation_flags():
    text = HIV_NOTES + "Plan: repeat CD4 and VL in 3 months\n"
    flags = []
    md_gp, _ = build(text, "gp-update", "Podiatry", flags=flags)
    assert "CD4" not in md_gp and any(f.startswith("Flag: a sentence on line") for f in flags)
    flags = []
    md, _ = build(text, "gp-update", "Patient", audience="patient", flags=flags)
    assert not any(f.startswith("Flag: a sentence on line") for f in flags) and "repeat CD4" in md
    assert not any(f.startswith("Flag: a medicine") for f in flags) and L.MED_FLAG not in md
    assert "abacavir" in md and "dolutegravir" in md
    assert f"CD4 {L.FLAG}" in md and f"VL {L.FLAG}" in md
    assert any(f.startswith("CD4") for f in flags) and any(f.startswith("VL") for f in flags)



# ---------- round 3: refuse-level and flag-only linked terms ----------
WORD_MSG = ("Flag: a word on line {} may tell the reader about something that was cut from this letter. "
            "Keep it, or reword it.")


@pytest.mark.parametrize("extra,word", [("PMHx: HIV-positive", "HIV-positive"), ("PMHx: schizophrenia", "schizophrenia"),
                                        ("PMHx: schizoaffective disorder", "schizoaffective")])
def test_terms_already_cut_by_clause_never_reach_letter(extra, word):
    md, dropped = build(F("podiatry_referral.txt") + extra + "\n", "referral", "Podiatry")
    assert word.lower() not in md.lower() and any(word in d for d in dropped)


def test_linked_terms_only_checked_when_item_dropped():
    text = "Patient: SYNTH-4501\n1937-04-02 Clinic review.\nProblem: ART reviewed, undetectable\nFollow-up: review in 3 months\n"
    flags = []
    md, dropped = build(text, "gp-update", "Podiatry", flags=flags)
    assert not dropped and not any(f.startswith("Flag: a word") for f in flags)


def test_patient_letter_keeps_sensitive_background():
    md, dropped = build(HIV_NOTES, "gp-update", "Patient", audience="patient")
    assert "HIV" in md.split("## Your health so far")[1].split("##")[0]
    assert not any("HIV status" in d for d in dropped)


# ---------- round 3: allergies and pending wording ----------
ALLERGY_MSG = "Flag: the allergy on line L3 has extra wording. Check whether it describes the reaction."
OWNER_MSG = "Flag: the pending test on line L3 has follow-up wording. Check who follows it up."


@pytest.mark.parametrize("line", ["penicillin rash and swelling 1930", "penicillin severe", "penicillin V tablets"])
def test_allergy_extra_wording_flagged_and_no_reaction_wording(line):   # round 10 item 8: both
    text = "Patient: SYNTH-4601\n1937-06-03 Clinic review.\nAllergy: " + line + "\nFollow-up: review in 2 weeks\n"
    flags = []
    md, _ = build(text, "gp-update", "GP", flags=flags)
    al = section(md, "Allergies")
    assert f"{line}: reaction not documented {L.ALLERGY_FLAG}" in al and ALLERGY_MSG in flags


@pytest.mark.parametrize("line", ["echo to be arranged", "ferritin chasing", "urine culture will follow", "echo - next week"])
def test_pending_owner_wording_flagged_not_not_documented(line):
    text = "Patient: SYNTH-4602\n1937-06-03 Clinic review.\nPending: " + line + "\nFollow-up: review in 2 weeks\n"
    flags = []
    md, _ = build(text, "gp-update", "GP", flags=flags)
    p = section(md, "Results still to come")
    assert "not documented" not in p and L.OWNER_FLAG in p and OWNER_MSG in flags


# ---------- round 3: checker messages give line ids only ----------
@pytest.mark.parametrize("mutate", [
    lambda md: md.replace("review in 2 weeks", "review in 9 weeks"),
    lambda md: md.replace("review in 2 weeks", "review in 2 Synthword weeks"),
    lambda md: md.replace("Clinic review. [L2]", "Clinic review."),
])
def test_checker_messages_line_only_no_letter_text(mutate):
    text = "Patient: SYNTH-4701\n1937-06-03 Clinic review.\nProblem: knee pain\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "GP")
    with pytest.raises(Refused) as e:
        check(mutate(md), parse(text)[1], "gp-update")
    for msg in str(e.value).splitlines():
        assert re.fullmatch(r"Refused: [A-Za-z ,0-9;']+ at (line L\d+|letter line \d+)[ ,.][^\n]*", msg), msg
    for tok in ("Synthword", "9", "review", "Clinic", "knee"):
        assert tok not in str(e.value), tok



# ---------- round 4: line-only printing, pending, reasons, questions, allergies ----------
REASON_ND = "reason not documented"


def _cli_out(tmp_path, notes_text, *args):
    n = tmp_path / "notes.txt"; n.write_text(notes_text)
    out = tmp_path / "out"
    r = subprocess.run(CLI + [str(n), *args, "--out", str(out)], capture_output=True, text=True)
    return r, out


FIXED_OK = set()


def _fixed_words():
    import letters as _L
    src = " ".join(str(v) for v in vars(_L).values() if isinstance(v, str))
    return {w.lower() for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9+\-=./]{3,}", src)}


SURNAME_NOTES = """Patient: SYNTH-4801
1937-06-03 Clinic review.
Problem: knee pain, seen by Dr Quorvex
PMHx: HIV
Med: abacavir 600 mg daily (for HIV)
Med: Zentrolib 5 mg daily
Allergy: penicillin - rash
Pending: MRI knee - ortho registrar to chase
Follow-up: GP review in 2 weeks
"""


SURNAME_T = tpl(pid="SYNTH-4801", question="Is the knee pain from the joint?", problems=["knee pain, seen by Dr Quorvex"],
                history="HIV", meds=["abacavir 600 mg daily reason: HIV", "Zentrolib 5 mg daily"], allergies=["penicillin: rash"],
                pending=["MRI knee, sent 1937-06-03, ortho registrar"], actions=["review in 2 weeks, GP"])


@pytest.mark.parametrize("args", [("--type", "gp-update", "--to", "Podiatry"), ("--brief", "update to GP"),
                                  ("--brief", "update to patient")])
def test_written_letter_prints_no_note_tokens(tmp_path, args):
    r, out = _cli_out(tmp_path, SURNAME_T, *args)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "" and (out / "letter.md").exists()
    fixed = _fixed_words()
    toks = {w.lower() for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9+\-=./]{3,}", SURNAME_T)}
    leaked = [w for w in toks - fixed if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", r.stderr.lower())]
    assert leaked == []
    assert "Quorvex" not in r.stderr and "Zentrolib" not in r.stderr
    for line in r.stderr.splitlines():
        assert re.fullmatch(r"(Flag|Cut from this letter): .*", line), line


def test_cut_list_is_line_ids_only(tmp_path):
    r, out = _cli_out(tmp_path, SURNAME_T, "--type", "gp-update", "--to", "Podiatry")
    cut = [l for l in r.stderr.splitlines() if l.startswith("Cut from this letter")]
    assert cut == [f"Cut from this letter: {line_of(SURNAME_T, 'History')}, {line_of(SURNAME_T, 'abacavir')}."]


@pytest.mark.parametrize("line,test,owner", [
    ("MRI knee renal fellow to chase", "MRI knee", "renal fellow to chase"),
    ("echo booked by ward team", "echo booked", "ward team"),
    ("ferritin GP chasing", "ferritin", "GP chasing"),
    ("bone scan - oncology registrar", "bone scan", "oncology registrar"),
    ("biopsy (district nurse to collect)", "biopsy", "district nurse to collect"),
])
def test_owner_recognition_generalised(line, test, owner):
    assert L.split_owner(line) == (test, owner)


@pytest.mark.parametrize("line", ["Echo pending, renal fellow to chase", "Awaiting blood cultures",
                                  "Thyroid function still pending", "Biopsy result outstanding - ward team"])
def test_untagged_pending_goes_to_pending_section(line):
    text = "Patient: SYNTH-4901\n1937-06-03 Clinic review.\nProblem: knee pain\n" + line + "\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "GP")
    p = section(md, "Results still to come")
    assert "No results pending" not in md and "[L4]" in p
    assert "[L4]" not in section(md, "Background")


@pytest.mark.parametrize("line,reason", [
    ("Med: allopurinol 100 mg daily for gout", "gout"), ("Med: allopurinol 100 mg daily (gout)", "gout"),
    ("Med: allopurinol 100 mg daily - for gout", "gout"), ("Med: allopurinol 100 mg daily to treat gout", "gout"),
    ("Med: allopurinol 100 mg daily; indication gout", "gout"), ("Med: allopurinol 100 mg daily (for gout)", "gout"),
])
def test_medicine_reason_found_never_reason_not_documented(line, reason):
    text = "Patient: SYNTH-5001\n1937-06-03 Clinic review.\nProblem: gout\n" + line + "\nFollow-up: review in 2 weeks\n"
    for aud, to in (("patient", "Patient"), (None, "GP")):
        md, _ = build(text, "gp-update", to, audience=aud)
        meds = section(md, "Your medicines now" if aud else "Current medicines")
        assert reason in meds and REASON_ND not in meds, (aud, meds)


@pytest.mark.parametrize("line", ["Med: allopurinol 100 mg daily", "Med: allopurinol 100 mg daily for 2 weeks",
                                  "Med: allopurinol 100 mg (twice daily)"])
def test_medicine_reason_missing_says_not_documented_for_patient(line):
    text = "Patient: SYNTH-5002\n1937-06-03 Clinic review.\nProblem: gout\n" + line + "\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "Patient", audience="patient")
    assert REASON_ND in section(md, "Your medicines now")


def test_patient_hiv_reason_never_with_not_documented():
    md, _ = build(HIV_NOTES, "gp-update", "Patient", audience="patient")
    meds = section(md, "Your medicines now")
    for l in meds.splitlines():
        assert not ("HIV" in l and REASON_ND in l), l


@pytest.mark.parametrize("line", ["penicillin - none documented", "penicillin (reaction not documented)",
                                  "penicillin: unknown", "penicillin - nil known", "penicillin - reaction unknown"])
def test_allergy_no_reaction_text_exact(line):
    text = "Patient: SYNTH-5201\n1937-06-03 Clinic review.\nAllergy: " + line + "\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "GP")
    al = section(md, "Allergies")
    assert al.startswith("- penicillin: reaction not documented [L3]"), al



def test_referral_without_question_cli_flag_not_refusal(tmp_path):
    r, out = _cli_out(tmp_path, tpl(problems=["knee pain"]),
                      "--brief", "referral to orthopaedics")
    assert r.returncode == 0 and (out / "letter.md").exists()
    assert "Flag: this referral has no clear question. Add one before it's signed." in r.stderr.splitlines()
    r, out = _cli_out(tmp_path, tpl(problems=["knee pain"]),
                      "--brief", "referral to orthopaedics", "--final")
    assert r.returncode == 3


@pytest.mark.parametrize("later", ["Result: echo normal", "Echo showed normal function", "Result: echo EF 55 %"])
def test_pending_cleared_by_later_result(later):
    text = ("Patient: SYNTH-5301\n1937-06-03 Clinic review.\nPending: echo - cardiology to arrange\n"
            "1937-06-10 Review.\n" + later + "\nFollow-up: review in 2 weeks\n")
    md, _ = build(text, "gp-update", "GP")
    assert "cardiology to arrange" not in section(md, "Results still to come")


def test_pending_stays_when_result_is_earlier():
    text = ("Patient: SYNTH-5302\n1937-06-03 Clinic review.\nResult: echo normal\n"
            "1937-06-10 Review.\nPending: echo - cardiology to arrange\nFollow-up: review in 2 weeks\n")
    md, _ = build(text, "gp-update", "GP")
    assert "cardiology to arrange" in section(md, "Results still to come")


def test_letter_saying_none_pending_while_pending_refused(monkeypatch):
    text = "Patient: SYNTH-5303\n1937-06-03 Clinic review.\nPending: echo - cardiology to arrange\nFollow-up: review in 2 weeks\n"
    real = L.section_items
    monkeypatch.setattr(L, "section_items", lambda n, st, a: [] if n == "pending" else real(n, st, a))
    monkeypatch.setattr(L, "refuse_missing_required", lambda md, text, ids=None: None)
    with pytest.raises(Refused) as e:
        build(text, "gp-update", "GP")
    assert str(e.value) == "Refused: line L3 has a test still pending, but this letter says none are. Nothing was written. Add the pending test to the letter, then run again."



# ---------- v2: medicine and brand names only on their own medication line; PrEP ----------
PSY = """Patient: SYNTH-5401
1937-06-03 Clinic review.
Problem: ingrown toenail
PMHx: bipolar disorder
Med: lithium 250 mg twice daily
Follow-up: review in 2 weeks
"""


def test_generic_on_own_medication_line_is_flagged_not_refused():
    flags = []
    md, _ = build(PSY, "gp-update", "Podiatry", flags=flags)
    assert "lithium" in section(md, "Current medicines") and MED_MSG.format("L5") in flags


def test_medicine_names_unchecked_when_item_included():
    md, dropped = build(PSY.replace("PMHx: bipolar disorder\n", "") + "Plan: continue Lithicarb\n", "gp-update", "Podiatry",
                        include=["psychiatric history"])
    assert "Lithicarb" in md and not dropped


PREP = """Patient: SYNTH-5501
1937-06-03 Clinic review.
Problem: ankle sprain
PMHx: on PrEP
Med: emtricitabine 1 tablet daily (for PrEP)
Follow-up: review in 2 weeks
"""


def test_prep_cut_and_medicine_flagged():
    flags = []
    md, dropped = build(PREP, "gp-update", "Podiatry", flags=flags)
    assert "PrEP" not in md and "emtricitabine" in md and any("PrEP" in d for d in dropped)
    assert MED_MSG.format("L5") in flags


@pytest.mark.xfail(raises=Refused, strict=True, reason="temporary combination-medicine refusal")
def test_prep_kept_for_relevant_reader_and_patient():
    for to, aud, inc in (("Sexual health", None, ["PrEP"]), ("Patient", "patient", None)):
        md, dropped = build(PREP + "Plan: continue Truvada\n", "gp-update", to, audience=aud, include=inc)
        assert "PrEP" in md and not any("PrEP" in d for d in dropped)


def test_prep_clause_cut_upstream():
    md, dropped = build(PREP + "Plan: pre-exposure prophylaxis reviewed\n", "gp-update", "Podiatry")
    assert "prophylaxis" not in md and any("L7" in d for d in dropped)




# frozen v2 content (parsed); the header-only edit must not change any term
FROZEN_V2 = json.loads(r'''{"HIV status": {"brands": {"Biktarvy": "bictegravir", "Dovato": "dolutegravir", "Genvoya": "cobicistat", "Triumeq": "dolutegravir"}, "flag_terms": ["undetectable", "ART", "ritonavir", "lamivudine", "Truvada", "Descovy", "tenofovir", "S100", "viral load", "VL"], "medicines": ["dolutegravir", "emtricitabine", "zidovudine", "abacavir", "bictegravir", "raltegravir", "rilpivirine", "darunavir", "efavirenz", "nevirapine", "cobicistat"], "terms": ["HIV", "HIV+", "HIV-positive", "seropositive", "PLHIV", "HAART", "U=U", "CD4", "RVD", "antiretroviral", "S100 prescriber"]}, "PrEP": {"brands": {"Descovy": "tenofovir/emtricitabine", "Truvada": "tenofovir/emtricitabine"}, "flag_terms": ["sexual health clinic", "tenofovir"], "medicines": ["tenofovir/emtricitabine", "emtricitabine"], "terms": ["PrEP", "pre-exposure prophylaxis"]}, "psychiatric history": {"brands": {"Clopine": "clozapine", "Clozaril": "clozapine", "Lithicarb": "lithium", "Quilonum": "lithium"}, "flag_terms": ["ANC", "bipolar", "psychosis"], "medicines": ["lithium", "clozapine"], "terms": ["lithium level", "serum lithium", "clozapine level", "clozapine monitoring", "ANC monitoring", "lithium toxicity", "Li level", "Li+", "clozapine clinic", "clozapine registry", "CPMS", "schizophrenia", "schizoaffective"]}}''')


H22_SIGNED_TERMS = {   # round 11 signed rulings: one linked-term entry per new item, detection words exactly
    "Mental Health Act status": ["Mental Health Act", "involuntary patient", "scheduled patient",
                                 "community treatment order", "sectioned"],
    "sexual health": ["STI", "chlamydia", "gonorrhoea", "syphilis", "sexual health clinic"],
    "pregnancy loss or termination": ["miscarriage", "termination of pregnancy"],
    "genetic results": ["BRCA", "pathogenic variant", "genetic test result"],
}


def test_linked_terms_parsed_identical_to_frozen_v2():
    """Version 3 keeps every version 2 entry unchanged and adds exactly one entry per H22 item."""
    got = json.loads(json.dumps(L.load_linked(), sort_keys=True))
    assert {k: v for k, v in got.items() if k in FROZEN_V2} == FROZEN_V2
    want = {k: {"terms": v} for k, v in H22_SIGNED_TERMS.items()}
    want["genetic results"]["numbered"] = ["BRCA"]                 # signed 14 Oct 2026: BRCA1, BRCA 1, BRCA-1 ...
    assert {k: v for k, v in got.items() if k not in FROZEN_V2} == want



# ---------- round 6: cut rule (sentences left out), brand swap, reason not included ----------
LEFT_MSG = ("Flag: a sentence on line {} was left out to keep something private. "
            "Check it doesn't hold a safety fact the reader needs.")


@pytest.mark.parametrize("extra", ["Problem: monitoring CD4 count", "Plan: RVD clinic", "PMHx: seropositive",
                                   "Plan: PLHIV clinic", "PMHx: on HAART", "Plan: explained U=U", "Plan: ART reviewed",
                                   "Plan: repeat viral load in 3 months", "Plan: see S100 prescriber",
                                   "Plan: review next week. Undetectable last time."])
def test_off_med_line_linked_sentence_left_out_and_flagged(extra):
    flags = []
    res = build(HIV_NOTES + extra + "\n", "gp-update", "Podiatry", flags=flags); md, _ = res
    assert LEFT_MSG.format("L12") in flags and L.LEFT_OUT_FLAG in md
    for w in ("CD4", "RVD", "seropositive", "PLHIV", "HAART", "U=U", "ART", "viral load", "S100", "Undetectable"):
        assert not re.search(rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])", md), w
    with pytest.raises(Refused, match="--final refused"):
        L.finalise(res)


def test_only_offending_sentence_dropped():
    md, _ = build(HIV_NOTES + "Plan: review next week. Undetectable last time.\n", "gp-update", "Podiatry")
    assert "review next week" in md and "Undetectable" not in md


@pytest.mark.parametrize("extra", ["Plan: continue Lithicarb", "Plan: continue lithium", "Plan: Clozaril review",
                                   "Plan: CPMS bloods", "Plan: check Li+ in 1 week", "Plan: clozapine registry updated",
                                   "Plan: check for lithium toxicity", "Plan: Li level in 1 week"])
def test_psych_linked_off_med_line_left_out(extra):
    flags = []
    md, _ = build(PSY + extra + "\n", "gp-update", "Podiatry", flags=flags)
    assert LEFT_MSG.format("L7") in flags
    body = md.split("## Current medicines")[0] + md.split("## Current medicines")[1].split("##", 1)[1]
    for w in ("Lithicarb", "lithium", "Clozaril", "CPMS", "Li+", "registry", "Li level"):
        assert w not in body, w


@pytest.mark.parametrize("medline,generic", [("Med: Lithicarb 250 mg twice daily", "lithium"),
                                             ("Med: Clozaril 100 mg daily", "clozapine"),
                                             ("Med: Quilonum 250 mg daily", "lithium")])
def test_brand_on_med_line_swapped_to_generic_and_flagged(medline, generic):
    text = PSY.replace("Med: lithium 250 mg twice daily", medline)
    flags = []
    md, _ = build(text, "gp-update", "Podiatry", flags=flags)
    meds = section(md, "Current medicines")
    assert generic in meds and medline.split()[1] not in md and MED_MSG.format("L5") in flags


def test_cut_reason_on_med_line_becomes_reason_not_included():
    text = PSY.replace("Med: lithium 250 mg twice daily", "Med: lithium 250 mg twice daily (for bipolar disorder)")
    flags = []
    md, _ = build(text, "gp-update", "Podiatry", flags=flags)
    meds = section(md, "Current medicines")
    # H22 (14 Oct 2026): the cut reason is dropped with no marker; the medicine flag is on screen only
    assert "lithium 250 mg twice daily [L5]" in meds and "reason not included" not in meds and "bipolar" not in md
    assert MED_MSG.format("L5") in flags and L.MED_FLAG not in md


FLAG_RX = re.compile(r" \[flag: [^\]]+\]")
MED_PART_MSG = ("Flag: part of the medicine entry on line {} was left out to keep something private. "
                "The medicine and its dose are still listed. Check nothing the reader needs was lost.")


def test_other_linked_words_on_med_line_left_out():
    text = PSY.replace("Med: lithium 250 mg twice daily", "Med: lithium 250 mg twice daily, CPMS registered")
    flags = []
    md, _ = build(text, "gp-update", "Podiatry", flags=flags)
    assert "CPMS" not in md and "lithium 250 mg twice daily" in md and MED_PART_MSG.format("L5") in flags


def test_every_listed_brand_maps_to_a_generic():
    for cat, v in L.load_linked().items():
        for b, g in v.get("brands", {}).items():
            assert g and all(x in v.get("medicines", []) or x in sum((w.get("medicines", []) for w in L.load_linked().values()), [])
                             or x in g for x in [g]), (cat, b)


def test_brand_without_generic_refused(monkeypatch):
    real = L.load_linked
    def fake(path=None):
        d = real(path); d["psychiatric history"] = dict(d["psychiatric history"], brands={"Lithicarb": ""}); return d
    monkeypatch.setattr(L, "load_linked", fake)
    text = PSY.replace("Med: lithium 250 mg twice daily", "Med: Lithicarb 250 mg twice daily")
    with pytest.raises(Refused) as e:
        build(text, "gp-update", "Podiatry")
    assert str(e.value) == "Refused: line L5 still mentions something this letter was meant to leave out. Nothing was written. Remove the mention, then run again."


def test_unknown_brand_like_drug_not_refused():
    text = PSY.replace("Med: lithium 250 mg twice daily", "Med: lithium 250 mg twice daily\nMed: Tritace 5 mg daily")
    flags = []
    md, _ = build(text, "gp-update", "Podiatry", flags=flags)
    assert "Tritace" in md


@pytest.mark.parametrize("medline", [pytest.param(m, marks=pytest.mark.xfail(raises=Refused, strict=True, reason="temporary combination-medicine refusal")) for m in ["Med: Truvada 1 tablet daily", "Med: Descovy 1 tablet daily"]])
def test_prep_brand_swapped(medline):
    md, _ = build(PREP.replace("Med: emtricitabine 1 tablet daily (for PrEP)", medline), "gp-update", "Podiatry")
    assert "tenofovir/emtricitabine" in md and medline.split()[1] not in md


@pytest.mark.parametrize("extra", [pytest.param("Plan: continue Truvada", marks=pytest.mark.xfail(raises=Refused, strict=True, reason="temporary combination-medicine refusal")), "Plan: emtricitabine supply", "Plan: sexual health clinic in 3 months"])
def test_prep_linked_off_med_line_left_out(extra):
    flags = []
    md, _ = build(PREP + extra + "\n", "gp-update", "Podiatry", flags=flags)
    # 'sexual health' is itself an H22 item since 14 Oct 2026, so that line is cut as sensitive (its own flag)
    assert LEFT_MSG.format("L7") in flags or ("sexual health" in extra and any("L7 was cut" in f for f in flags))
    body = md.split("## Current medicines")[0] + md.split("## Current medicines")[1].split("##", 1)[1]
    for w in ("Truvada", "emtricitabine", "sexual health"):
        assert w not in body, w


@pytest.mark.xfail(raises=Refused, strict=True, reason="temporary combination-medicine refusal")
def test_patient_letters_drop_nothing_for_linked_terms():
    for notes, extra in ((HIV_NOTES, "Plan: repeat CD4 and viral load\n"), (PSY, "Plan: continue Lithicarb\n"),
                         (PREP, "Plan: continue Truvada\n")):
        flags = []
        md, _ = build(notes + extra, "gp-update", "Patient", audience="patient", flags=flags)
        assert extra.split(": ")[1].strip().split()[-1] in md
        assert not any(f.startswith("Flag: a sentence on line") for f in flags) and L.LEFT_OUT_FLAG not in md


# ---------- round 6: reasons, pending, questions ----------
@pytest.mark.parametrize("line", ["Meds: allopurinol 100 mg daily, colchicine 500 mcg twice daily (for gout)",
                                  "Medications: allopurinol 100 mg daily; colchicine 500 mcg twice daily"])
def test_med_list_lines_split_never_list_not_documented(line):
    text = "Patient: SYNTH-5601\n1937-06-03 Clinic review.\nProblem: gout\n" + line + "\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "GP")
    meds = section(md, "Current medicines")
    assert "allopurinol" in meds and "colchicine" in meds and "Current medicines not documented" not in md
    for l in meds.splitlines():
        assert "reason not documented" not in l, l                          # round 11 freeze: matching-line rule


@pytest.mark.parametrize("later", ["Echo done, normal", "Result back for echo: normal", "Repeat echo was normal",
                                   "The echo came back clear"])
def test_pending_cleared_by_any_later_result_line(later):
    text = ("Patient: SYNTH-5701\n1937-06-03 Clinic review.\nPending: echo - cardiology to arrange\n"
            "1937-06-10 Review.\n" + later + "\nFollow-up: review in 2 weeks\n")
    md, _ = build(text, "gp-update", "GP")
    assert "cardiology to arrange" not in md.split("## Results still to come")[1].split("##")[0]


@pytest.mark.parametrize("q", ["Please advise re ongoing knee pain", "Would value your opinion on knee pain",
                               "For consideration of knee replacement", "Assess for knee replacement",
                               "Please review for surgery", "Is surgery needed?", "Question for orthopaedics: surgery?",
                               "Q: surgery?", "Request: please assess"])
def test_referral_question_unusual_forms(q):
    text = "Patient: SYNTH-5801\n1937-06-03 Clinic review.\nProblem: knee pain\n" + q + "\nFollow-up: review in 2 weeks\n"
    flags = []
    md, _ = build(text, "referral", "Orthopaedics", flags=flags)
    assert not any("no clear question" in f for f in flags) and "[L4]" in md.split("## ")[1]


def test_no_question_flag_blocks_final_cli(tmp_path):
    r, out = _cli_out(tmp_path, tpl(problems=["knee pain"]),
                      "--brief", "referral to orthopaedics", "--final")
    assert r.returncode == 3 and "Flag: this referral has no clear question. Add one before it's signed." in r.stderr
    assert not out.exists() or list(out.iterdir()) == []


# ---------- round 7: cut by default (GP included), include line, every cut flagged ----------
CUT_MSG = "Flag: something on line {} was cut to keep it private. Check the reader doesn't need it."


def test_default_cut_for_gp_and_flagged_blocks_final():
    flags = []
    res = build(PSY, "gp-update", "GP", flags=flags); md, _ = res
    assert "bipolar" not in md and CUT_MSG.format("L4") in flags and L.CUT_FLAG not in md   # round 11 freeze: screen only
    shown = L.printed_flags(flags, md)
    with pytest.raises(Refused, match="--final refused"):
        L.finalise(res)
    assert any(f.startswith(L.SCREEN_ONLY_PREFIXES) for f in shown)


PSY_T = tpl(pid="SYNTH-5401", question="Can you treat the toenail?", problems=["ingrown toenail"], history="bipolar disorder",
            meds=["lithium 250 mg twice daily"], actions=["review in 2 weeks, GP"])


def test_include_line_keeps_item(tmp_path):
    n = tmp_path / "n.txt"; n.write_text(PSY_T)
    out = tmp_path / "o"
    r = subprocess.run(CLI + [str(n), "--brief", "update to GP\ninclude: psychiatric history", "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert "bipolar" in md and "Cut from this letter" not in r.stderr and L.CUT_FLAG not in md


@pytest.mark.parametrize("brief", ["update to GP\nreferral to renal"])
def test_bad_include_or_two_briefs_refused_without_echo(tmp_path, brief):
    n = tmp_path / "n.txt"; n.write_text(PSY)
    r = subprocess.run(CLI + [str(n), "--brief", brief, "--out", str(tmp_path / "o")], capture_output=True, text=True)
    assert r.returncode == 2 and _clin(r) == BRIEF_MSG


def test_bad_include_names_item_per_ruling(tmp_path):
    # round 11 signed rulings: an off-list Include: item is refused as "an item", never echoing the brief
    n = tmp_path / "n.txt"; n.write_text(PSY)
    r = subprocess.run(CLI + [str(n), "--brief", "update to GP\ninclude: Mrs Synthname", "--out", str(tmp_path / "o")],
                       capture_output=True, text=True)
    assert r.returncode == 2 and _clin(r) == L.SENSITIVE_REFUSED.format(n=2)
    assert "Synthname" not in r.stderr + r.stdout and "Mrs" not in r.stderr + r.stdout
    assert not (tmp_path / "o").exists()


def test_patient_letters_exempt_from_cut():
    md, dropped = build(PSY, "gp-update", "Patient", audience="patient")
    assert "bipolar" in md and not dropped


# ---------- round 7: medicine entries ----------
@pytest.mark.parametrize("medline,keep", [
    ("Med: lithium 250 mg twice daily (CPMS)", "lithium 250 mg twice daily"),
    ("Med: clozapine 100 mg daily, clozapine registry", "clozapine 100 mg daily"),
    ("Med: lithium 250 mg CPMS twice daily", "lithium 250 mg"),
])
def test_med_line_drops_only_the_term_part(medline, keep):
    text = PSY.replace("Med: lithium 250 mg twice daily", medline)
    flags = []
    md, _ = build(text, "gp-update", "Podiatry", flags=flags)
    meds = section(md, "Current medicines")
    assert keep in FLAG_RX.sub("", meds) and "CPMS" not in md and "registry" not in md
    assert "reason not included" not in meds and MED_MSG.format("L5") in flags and L.MED_FLAG not in md   # H22


def test_med_entry_that_would_disappear_refused():
    text = PSY.replace("Med: lithium 250 mg twice daily", "Med: antiretroviral 1 tablet daily")
    with pytest.raises(Refused) as e:
        build(text, "gp-update", "Podiatry")
    assert str(e.value) == "Refused: line L5 still mentions something this letter was meant to leave out. Nothing was written. Remove the mention, then run again."


@pytest.mark.parametrize("medline,want", [
    ("Med: lithium 250 mg twice daily (for bipolar disorder)", None),      # H22: cut reason dropped, no marker
    ("Med: lithium 250 mg twice daily", None),                             # round 11 freeze: matching-line rule
])
def test_reason_not_included_only_when_a_reason_was_cut(medline, want):
    md, _ = build(PSY.replace("Med: lithium 250 mg twice daily", medline), "gp-update", "Podiatry")
    meds = section(md, "Current medicines")
    assert "reason not included" not in meds
    assert (want in meds) if want else ("reason not documented" not in meds)


@pytest.mark.parametrize("medline", ["Med: metformin 500 mg daily, indicated for type 2 diabetes",
                                     "Med: metformin 500 mg daily used for type 2 diabetes",
                                     "Med: paracetamol 1 g as needed for pain", "Med: metformin 500 mg daily, taken for type 2 diabetes"])
def test_reason_phrasings_never_with_not_documented(medline):
    text = "Patient: SYNTH-6001\n1937-06-03 Clinic review.\nProblem: type 2 diabetes\n" + medline + "\nFollow-up: review in 2 weeks\n"
    for aud, to in ((None, "GP"), ("patient", "Patient")):
        md, _ = build(text, "gp-update", to, audience=aud)
        meds = section(md, "Your medicines now" if aud else "Current medicines")
        assert "reason not documented" not in meds, meds


@pytest.mark.parametrize("drug", ["Tritace", "Zentrolib", "Coversyl"])
def test_capitalised_unknown_drug_gets_drug_name_flag(drug):
    text = "Patient: SYNTH-6002\n1937-06-03 Clinic review.\nProblem: high blood pressure\nMed: " + drug + \
           " 5 mg daily\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "GP")
    assert f"{drug} {L.DRUG_FLAG}" in md


# ---------- round 7: pending, questions, opener, allergies ----------
@pytest.mark.parametrize("line", ["Echo requested", "MRI knee booked", "Awaiting results of bone scan",
                                  "Thyroid function has been sent", "Biopsy results to follow"])
def test_more_pending_phrasings_reach_pending_section(line):
    text = "Patient: SYNTH-6101\n1937-06-03 Clinic review.\nProblem: knee pain\n" + line + "\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "GP")
    assert "[L4]" in section(md, "Results still to come") and "No results pending" not in md


@pytest.mark.parametrize("q", ["Query whether surgery is needed", "We wonder if surgery would help",
                               "Could you see her about the knee", "Kindly assess the knee", "Seeking advice on the knee",
                               "Opinion please on the knee", "For your thoughts on the knee"])
def test_more_question_forms(q):
    text = "Patient: SYNTH-6102\n1937-06-03 Clinic review.\nProblem: knee pain\n" + q + "\nFollow-up: review in 2 weeks\n"
    flags = []
    build(text, "referral", "Orthopaedics", flags=flags)
    assert not any("no clear question" in f for f in flags)


def test_no_question_opens_with_reason_for_referral():
    md, _ = build("SYNTH-1\n1937-01-01\nProblem: cough", "referral", "Respiratory")
    assert "Please answer the question below" not in md and "Please see" not in md
    assert "Reason for referral: cough [L3]" in md


@pytest.mark.parametrize("line,react", [("penicillin = rash", "rash"), ("penicillin / hives", "hives"),
                                        ("penicillin \u2192 swelling", "swelling")])
def test_allergy_reaction_separators_kept(line, react):
    text = "Patient: SYNTH-6103\n1937-06-03 Clinic review.\nAllergy: " + line + "\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "GP")
    assert f"penicillin: {react}" in section(md, "Allergies")


def test_allergy_line_with_linked_brand_never_dropped():
    text = PSY + "Allergy: Clozaril - neutropenia\n"
    flags = []
    md, _ = build(text, "gp-update", "Podiatry", flags=flags)
    assert "neutropenia" in section(md, "Allergies") and MED_MSG.format("L7") in flags


# ---------- permanent regression tests from Physician-scored failures (descriptions only) ----------
def test_reg_h3_brand_on_med_line_never_leaks():
    """heldout3: brand names leaked on the medication line when the item was cut."""
    md, _ = build(PSY.replace("Med: lithium 250 mg twice daily", "Med: Lithicarb 250 mg twice daily"), "gp-update", "GP")
    assert "Lithicarb" not in md and "lithium 250 mg twice daily" in md


def test_reg_h3_registry_and_prescriber_terms_never_leak():
    """heldout3: registry/prescriber terms leaked."""
    md, _ = build(PSY + "Plan: clozapine registry updated\n", "gp-update", "GP")
    assert "registry" not in md
    md, _ = build(HIV_NOTES + "Plan: see S100 prescriber\n", "gp-update", "GP")
    assert "S100" not in md


def test_reg_h3_cut_diagnosis_on_med_line_never_leaks():
    """heldout3: a cut diagnosis given as the reason on the medication line leaked."""
    md, _ = build(PSY.replace("Med: lithium 250 mg twice daily", "Med: lithium 250 mg twice daily (for bipolar disorder)"),
                  "gp-update", "GP")
    assert "bipolar" not in md and "reason not included" not in md and "lithium 250 mg twice daily" in md   # H22


def test_reg_h4_brand_without_diagnosis_still_cut_and_swapped():
    """heldout4 P01/P02/P04: cut text leaked with no left-out flag and no brand swap when the diagnosis was absent."""
    notes = PSY.replace("PMHx: bipolar disorder\n", "").replace("Med: lithium 250 mg twice daily",
                                                                "Med: Clozaril 100 mg daily") + "Plan: continue Clozaril. Review nails.\n"
    flags = []
    md, _ = build(notes, "gp-update", "Podiatry", flags=flags)
    assert "Clozaril" not in md and "clozapine 100 mg daily" in md
    assert any(f.startswith("Flag: a sentence on line") for f in flags) and any(f.startswith("Flag: a medicine") for f in flags)


def test_reg_h4_reason_and_not_documented_never_both():
    """Physician: a medicine showed its reason together with 'reason not documented'."""
    text = "Patient: SYNTH-6201\n1937-06-03 Clinic review.\nProblem: gout\nMed: allopurinol 100 mg daily, used for gout\nFollow-up: review\n"
    md, _ = build(text, "gp-update", "GP")
    for l in section(md, "Current medicines").splitlines():
        assert not ("gout" in l and "reason not documented" in l)


def test_reg_h4_pending_test_in_pending_section():
    """Physician: pending tests were missing from the pending section."""
    text = "Patient: SYNTH-6202\n1937-06-03 Clinic review.\nProblem: knee pain\nMRI knee requested, ortho registrar to chase\nFollow-up: review\n"
    md, _ = build(text, "gp-update", "GP")
    p = FLAG_RX.sub("", section(md, "Results still to come"))
    assert "MRI knee" in p and "ortho registrar to chase" in p


def test_reg_h4_unusual_question_found():
    """Physician: unusual question wording was missed."""
    text = "Patient: SYNTH-6203\n1937-06-03 Clinic review.\nProblem: knee pain\nWould value your opinion re surgery\nFollow-up: review\n"
    flags = []
    build(text, "referral", "Orthopaedics", flags=flags)
    assert not any("no clear question" in f for f in flags)


def test_reg_h4_tritace_like_brand_drug_flag_not_refused():
    """Physician: an unknown brand like Tritace got an abbreviation flag."""
    text = "Patient: SYNTH-6204\n1937-06-03 Clinic review.\nProblem: high blood pressure\nMed: Tritace 5 mg daily\nFollow-up: review\n"
    md, _ = build(text, "gp-update", "GP")
    assert f"Tritace {L.DRUG_FLAG}" in md


def test_reg_h4_medicine_flag_when_brand_swapped():
    """Physician: medicine flag missing when a brand was swapped or cut on the medication line."""
    flags = []
    build(PSY.replace("Med: lithium 250 mg twice daily", "Med: Quilonum 250 mg daily"), "gp-update", "GP", flags=flags)
    assert MED_MSG.format("L5") in flags


def test_reg_h4_allergy_reaction_kept():
    """Physician: an allergy reaction was lost."""
    text = "Patient: SYNTH-6205\n1937-06-03 Clinic review.\nAllergy: penicillin = anaphylaxis\nFollow-up: review\n"
    md, _ = build(text, "gp-update", "GP")
    assert "penicillin: anaphylaxis" in section(md, "Allergies")


def test_reg_h4_p08_no_question_opener():
    """P08: 'Please answer the question below' with no question."""
    md, _ = build("SYNTH-1\n1937-01-01\nProblem: rash on arm", "referral", "Dermatology")
    assert "Please answer the question below" not in md and "Reason for referral: rash on arm" in md


def test_include_lines_recorded_on_stderr(tmp_path):
    n = tmp_path / "n.txt"; n.write_text(PSY_T.replace("History: bipolar disorder", "History: bipolar disorder, on PrEP"))
    out = tmp_path / "o"
    r = subprocess.run(CLI + [str(n), "--brief", "update to GP\ninclude: Psychiatric History\ninclude: PrEP", "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    lines = r.stderr.splitlines()
    assert "Included by the brief: psychiatric history." in lines and "Included by the brief: PrEP." in lines
    assert [p.name for p in out.iterdir()] == ["letter.md"]


# ---------- round 8: medicines and allergies by content; post-write medicine guard (Physician, heldout5) ----------
R8 = """Patient: SYNTH-9801
Reactions on file: trimethoprim - itchy rash
1937-04-02 seen for knee pain after a fall.
Drug chart: propranolol 40 mg bd - tremor
Drug chart: atenolool 25 mg mane
"""


def _r8(text=R8, t="gp-update", to="GP", aud=None):
    flags = []
    md, _ = build(text, t, to, audience=aud, flags=flags)
    return md, flags


def test_r8_unfamiliar_headings_found_by_content():
    md, _ = _r8()
    meds, allergies = section(md, "Current medicines"), section(md, "Allergies")
    assert "propranolol 40 mg twice daily (bd)" in FLAG_RX.sub("", meds) and "Current medicines not documented" not in md
    assert "trimethoprim: itchy rash" in allergies and "Allergies not documented" not in md


def test_r8_misspelt_drug_under_unfamiliar_heading_gets_drug_flag():
    md, _ = _r8()
    assert f"atenolool {L.DRUG_FLAG}" in md and f"atenolool {L.FLAG}" not in md
    assert f"propranolol {L.FLAG}" not in md and f"propranolol {L.DRUG_FLAG}" not in md   # recognised: no flag


def test_r8_unknown_brand_on_drug_chart_gets_drug_flag_not_abbreviation():
    md, _ = _r8(R8.replace("atenolool 25 mg mane", "Synthaten 25mg mane"))
    assert f"Synthaten {L.DRUG_FLAG}" in md and f"Synthaten {L.FLAG}" not in md


def test_r8_patient_letter_drug_chart_and_reactions_kept_in_signed_plain_words():
    md, _ = _r8(t="gp-update", to="Patient", aud="patient")
    meds = FLAG_RX.sub("", md)
    assert "propranolol 40 mg twice a day" in meds and "atenolool 25 mg in the morning" in meds
    assert "trimethoprim: itchy rash" in section(md, "Allergies")


def test_r8_cut_medicine_with_dose_kept_passes():
    text = R8 + "Drug chart: lithium carbonate 400 mg nocte - mood\nPMHx: bipolar disorder\n"
    md, flags = _r8(text)
    meds = FLAG_RX.sub("", section(md, "Current medicines"))
    assert "lithium carbonate 400 mg nocte" in meds and L.MED_FLAG not in md and "bipolar" not in md   # H22: screen only
    assert any(f.startswith("Flag: a medicine on line") for f in flags)


def _guard(notes, letter_lines):
    md = "**Letter:** Update\n\n## Current medicines\n\n" + "\n".join(letter_lines) + "\n"
    L.refuse_missing_medicines(md, notes)


NOTE1 = "Patient: SYNTH-9802\n1937-04-02 review.\nDrug chart: Synthomed 10 mg daily\n"


def test_r8_guard_dose_dropped_refuses():
    _guard(NOTE1, ["- Synthomed 10 mg daily [L3]"])
    with pytest.raises(L.Refused, match=re.escape(L.MED_MISSING.format(lid="L3"))):
        _guard(NOTE1, ["- Synthomed daily [L3]"])


def test_r8_guard_dose_changed_refuses():
    with pytest.raises(L.Refused, match="a medicine on line L3"):
        _guard(NOTE1, ["- Synthomed 100 mg daily [L3]"])


def test_r8_guard_frequency_changed_refuses():
    with pytest.raises(L.Refused, match="a medicine on line L3"):
        _guard(NOTE1, ["- Synthomed 10 mg bd [L3]"])
    with pytest.raises(L.Refused, match="a medicine on line L3"):
        _guard(NOTE1, ["- Synthomed 10 mg twice daily [L3]"])


def test_r8_guard_nocte_written_as_at_night_passes():
    notes = NOTE1.replace("daily", "nocte")
    _guard(notes, ["- Synthomed 10 mg at night [L3]"])
    with pytest.raises(L.Refused):
        _guard(notes, ["- Synthomed 10 mg in the morning [L3]"])          # mane is not nocte


@pytest.mark.parametrize("abbr,plain", [("mane", "in the morning"), ("daily", "once a day"), ("bd", "twice a day"),
                                        ("tds", "three times a day"), ("qid", "four times a day"), ("prn", "when needed")])
def test_r8_guard_signed_frequency_equivalents(abbr, plain):
    _guard(NOTE1.replace("daily", abbr), [f"- Synthomed 10 mg {plain} [L3]"])


def test_r8_guard_od_has_no_equivalent():
    notes = NOTE1.replace("daily", "od")
    _guard(notes, ["- Synthomed 10 mg od [L3]"])
    with pytest.raises(L.Refused, match="a medicine on line L3"):
        _guard(notes, ["- Synthomed 10 mg once a day [L3]"])


def test_r8_guard_medicine_missing_entirely_refuses():
    with pytest.raises(L.Refused, match="a medicine on line L3"):
        _guard(NOTE1, ["- something else [L4]"])


def test_r8_guard_allergy_reaction_dropped_refuses():
    notes = "Patient: SYNTH-9803\nReactions on file: trimethoprim - itchy rash\n1937-04-02 review.\n"
    _guard(notes, ["## Allergies", "", "- trimethoprim - itchy rash [L2]"])
    with pytest.raises(L.Refused, match=re.escape(L.ALLERGY_MISSING.format(lid="L2"))):
        _guard(notes, ["## Allergies", "", "- trimethoprim [L2]"])


def test_r8_guard_stop_line_needs_drug_and_stop_word():
    notes = "Patient: SYNTH-9804\n1937-04-02 review.\nDrug chart: ramipril stopped - kidney injury\n"
    md, _ = _r8(notes)
    assert "ramipril" in md and "stopped" in md.lower()
    _guard(notes, ["- Stopped: ramipril [L3]"])
    with pytest.raises(L.Refused, match="a medicine on line L3"):
        _guard(notes, ["- ramipril [L3]"])


def test_r8_ceased_and_withheld_lines_reach_letter():
    for w in ("ceased", "withheld"):
        md, _ = _r8(f"Patient: SYNTH-9805\n1937-04-02 review.\nwarfarin {w} before surgery\n")
        assert "warfarin" in md and w in md.lower() or "stopped" in md.lower()


PRN = "Patient: SYNTH-9806\n1937-04-02 review.\nDrug chart: paracetamol 1 g prn {mx}\n"


def test_r8_prn_maximum_dropped_refuses():
    notes = PRN.format(mx="max 4 g in 24 h")
    _guard(notes, ["- paracetamol 1 g prn max 4 g in 24 h [L3]"])
    with pytest.raises(L.Refused, match=re.escape(L.MAX_MISSING.format(lid="L3"))):
        _guard(notes, ["- paracetamol 1 g prn [L3]"])


@pytest.mark.parametrize("mx,num", [("max 4 g in 24 h", "4 g"), ("max 8 tablets in 24hrs", "8 tablets"),
                                    ("maximum 4 g per 24 hours", "4 g")])
def test_r8_patient_24h_maximum_in_signed_plain_words(mx, num):
    md, _ = _r8(PRN.format(mx=mx), to="Patient", aud="patient")
    assert f"no more than {num} in 24 hours" in FLAG_RX.sub("", md)


def test_r8_weekly_maximum_keeps_its_period():
    md, _ = _r8(PRN.format(mx="max 20 mg per week"), to="Patient", aud="patient")
    assert "max 20 mg per week" in FLAG_RX.sub("", md) and "in 24 hours" not in md


def test_r8_pending_cleared_by_later_result_and_owner_kept():
    notes = ("Patient: SYNTH-9807\n1937-08-04 seen for tiredness.\nIron studies sent 1937-08-04 - await\n"
             "Thyroid test sent 1937-08-04 - practice nurse following\n1937-08-06 iron studies back: ferritin low 9\n")
    md, _ = _r8(notes, to="Patient", aud="patient")
    p = FLAG_RX.sub("", section(md, "Tests we are still waiting on"))
    assert "Iron studies" not in p and "Thyroid test" in p and "practice nurse following" in p and "await" not in p


def test_r8_referral_without_question_or_problem_opens_with_first_note_line():
    notes = "Patient: SYNTH-9808\n1937-10-13 headaches most mornings, worse lying down.\nDrug chart: paracetamol 1 g prn\n"
    md, _ = _r8(notes, t="referral", to="Neurology")
    assert "Reason for referral: headaches most mornings" in md and "Reason for referral not documented" not in md


# ---------- signed result-vs-medicine rule (13 Oct 2026) ----------
RES_NOTES = ("Patient: SYNTH-7101\n1937-03-02 Clinic.\nProblem: tiredness\n{line}\n"
             "Med: zorvatide 500 mg daily (for tiredness)\nPlan: review in 4 weeks\n")


@pytest.mark.parametrize("line,kind", [
    ("Serum lithium 0.9 mmol/L", "result"),
    ("Sodium 138 mmol/L", "result"),
    ("Bloods: CRP 12 mg/L", "result"),
    ("Zorvex 5 mg/L daily", "med"),
    ("Zorvex 5 mmol/L nocte", "med"),
    ("Zorvex 500 mg", "med"),
    ("Zorvex 2 g", "med"),
    ("Zorvex 250 mcg", "med"),
    ("Zorvex 10 units", "med"),
    ("Zorvex 5 mL", "med"),
])
def test_signed_result_or_medicine(line, kind):
    assert L.classify(line)[0] == kind


def test_untagged_result_kept_in_letter():
    md = make_letter(RES_NOTES.format(line="Sodium 138 mmol/L"), "referral", "Renal")
    assert "138 mmol/L" in md


def test_untagged_lithium_result_kept_for_patient():
    md = make_letter(RES_NOTES.format(line="Serum lithium 0.9 mmol/L"), "gp-update", "GP", audience="patient")
    assert "0.9 mmol/L" in md


def test_result_missing_from_letter_refused(monkeypatch):
    real = L.render
    monkeypatch.setattr(L, "render", lambda *a, **k: real(*a, **k).replace("138 mmol/L", "mmol/L"))
    with pytest.raises(L.Refused, match="a result on line"):
        make_letter(RES_NOTES.format(line="Sodium 138 mmol/L"), "referral", "Renal")


def test_template_cannot_drop_results():
    md = make_letter(RES_NOTES.format(line="Sodium 138 mmol/L"), "referral", "Renal", template="{{question}}\n")
    assert "138 mmol/L" in md


# ---------- temporary combination-medicine refusal (13 Oct 2026) ----------
COMBO_NOTES = ("Patient: SYNTH-7202\n1937-04-05 Clinic.\nProblem: tiredness\n{line}\nPlan: review in 4 weeks\n")
COMBO_MSG = "Refused: line L{n} has a combination medicine that this tool can't fully list yet. Nothing was written."


@pytest.mark.parametrize("brand", ["Biktarvy", "triumeq", "DOVATO", "Genvoya", "Truvada", "descovy"])
@pytest.mark.parametrize("audience", [None, "patient"])
def test_combination_brand_refused(brand, audience):
    with pytest.raises(Refused) as e:
        make_letter(COMBO_NOTES.format(line=f"Med: {brand} 1 tablet daily"), "gp-update", "GP", audience=audience)
    assert str(e.value) == COMBO_MSG.format(n=4)
    assert brand.lower() not in str(e.value).lower()


def test_combination_brand_outside_medication_line_refused():
    notes = COMBO_NOTES.format(line="Med: zorvatide 500 mg daily (for tiredness)") + \
        "Patient asked whether genvoya could be swapped.\n"
    with pytest.raises(Refused) as e:
        make_letter(notes, "referral", "Renal")
    assert str(e.value) == COMBO_MSG.format(n=6)


def test_combination_brand_cli_writes_nothing(tmp_path):
    notes = tmp_path / "n.txt"; out = tmp_path / "out"
    notes.write_text(tpl(question="Why is she tired?", problems=["tiredness"], history="on Truvada"))
    r = subprocess.run([sys.executable, str(HERE / "letters.py"), str(notes), "--brief", "referral to renal",
                        "--out", str(out)], capture_output=True, text=True)
    assert r.returncode != 0 and COMBO_MSG.format(n=7) in r.stderr + r.stdout
    assert not out.exists() or not any(out.iterdir())


# ---------- round 9: independent post-write check (reads the notes itself) ----------
# Each pair below has the structure of a letter the round 8 build wrote and the check let through. Synthetic only.
def _letter(sections):
    out = ["**DRAFT, unsigned**", "", "**Letter:** Update", ""]
    for head, lines in sections:
        out += [f"## {head}", ""] + [f"- {x}" for x in lines] + [""]
    return "\n".join(out + ["---", ""])


CK_BLOCK = """SYNTH-9901 :: F :: 60
Sensitivities: zorbamycin (nausea + vomiting)
1937-02-01 lipid clinic, LDL 1937-01-28 3.4 mmol/L.
Current Rx
* alphastatin 2 mg nocte - cholesterol
* betamibe 10 mg daily
"""


def _ck(notes, md, audience="gp", include=()):
    got = []
    L.verify_letter(md, notes, audience, include, collect=got)
    return got


def test_r9_checker_refuses_merged_medicine_lines():
    """Two bulleted medicine lines merged into one fake change: the first medicine is only in the change line."""
    md = _letter([("Background", ["1 Feb 1937: lipid clinic, LDL 1937-01-28 3.4 mmol/L. [L3]"]),
                  ("Allergies", ["zorbamycin: nausea + vomiting [L2]"]),
                  ("Medicine changes", ["Changed on 1 Feb 1937: from * alphastatin 2 mg nocte. Now: * betamibe 10 mg "
                                        "daily. Reason for change: not documented. [L5, L6]"]),
                  ("Current medicines", ["* betamibe 10 mg daily (for cholesterol) [L5, L6]"])])
    assert _ck(CK_BLOCK, md) == [L.WRONG_SECTION.format(what="a medicine", lid="L5")]   # only in the change line
    good = md.replace("- * betamibe 10 mg daily (for cholesterol) [L5, L6]",
                      "- alphastatin 2 mg nocte (for cholesterol) [L5]\n- betamibe 10 mg daily [L6]")
    assert _ck(CK_BLOCK, good) == []


def test_r9_checker_refuses_allergen_name_lost():
    notes = "SYNTH-9902 :: F :: 50\nIntolerances - zorvofen: indigestion\n1937-03-01 review.\nMed: alphazole 20 mg daily\n"
    bad = _letter([("Current medicines", ["alphazole 20 mg daily [L4]"]),
                   ("Allergies", ["indigestion: reaction not documented [L2]"])])
    assert _ck(notes, bad) == [L.ALLERGY_MISSING.format(lid="L2")]
    good = bad.replace("indigestion: reaction not documented", "zorvofen: indigestion")
    assert _ck(notes, good) == []


def test_r9_checker_refuses_allergen_without_reaction_name_lost():
    notes = "SYNTH-9903 :: F :: 50\nIntolerances - zorvostatin: muscle aches\n1937-03-01 review.\n"
    bad = _letter([("Allergies", ["muscle aches [flag: allergy has extra wording] [L2]"])])
    assert _ck(notes, bad) == [L.ALLERGY_MISSING.format(lid="L2")]


def test_r9_checker_refuses_lost_per_litre_result():
    md = _letter([("Current medicines", ["alphastatin 2 mg nocte [L5]", "betamibe 10 mg daily [L6]"]),
                  ("Allergies", ["zorbamycin: nausea + vomiting [L2]"])])
    assert _ck(CK_BLOCK, md) == [L.RESULT_MISSING.format(lid="L3")]


def test_r9_checker_refuses_block_medicine_outside_medicines():
    """A medicine under a 'Regular meds -' block, with no dose, written only in the background."""
    notes = "SYNTH-9904 :: F :: 45\n1937-07-02 seen for tiredness.\nRegular meds \u2014\n  combined pill daily\n"
    bad = _letter([("Background", ["2 Jul 1937: combined pill daily [L4]"]),
                   ("Current medicines", ["Current medicines not documented."])])
    assert _ck(notes, bad) == [L.SECTION_ND.format(sec="medicines", lid="L4"),
                               L.WRONG_SECTION.format(what="a medicine", lid="L4")]
    assert _ck(notes, _letter([("Current medicines", ["combined pill daily [L4]"])])) == []


def test_r9_checker_refuses_cut_reason_written_out():
    """A medicine linked to a cut item keeps drug, dose and frequency; its reason must not reach the letter."""
    notes = "SYNTH-9905 :: F :: 49\n1937-03-30 discharged after surgery.\nTablets taken:\n  lithium carbonate 300 mg twice daily - steadies her\n"
    bad = _letter([("Current medicines", ["lithium carbonate 300 mg twice daily (for steadies her) [flag: medicine may "
                                          "point to something cut] [L4]"])])
    assert _ck(notes, bad) == [L.LINKED_REFUSED.format(lid="L4")]
    good = bad.replace("(for steadies her)", ", reason not included")
    assert _ck(notes, good) == []
    assert _ck(notes, bad, audience="patient") == []                     # the patient keeps their own details


def test_r9_checker_refuses_cut_sentence_left_in():
    notes = ("SYNTH-9906 :: M :: 52\n1937-06-02 discharged after hernia repair.\nMedication chart:\n"
             "  clozapine 200 mg nocte\nClozapine bloods due weekly per clinic.\n")
    bad = _letter([("Medicine changes", ["Changed on 2 Jun 1937: from clozapine 200 mg nocte. Now: Clozapine bloods "
                                         "due weekly per clinic.. Reason for change: not documented. [L4, L5]"]),
                   ("Current medicines", ["Clozapine bloods due weekly per clinic., reason not documented [L4, L5]"])])
    assert _ck(notes, bad) == [L.WRONG_SECTION.format(what="a medicine", lid="L4"), L.LINKED_REFUSED.format(lid="L5")]
    good = _letter([("Current medicines", ["clozapine 200 mg nocte [L4]"])])
    assert _ck(notes, good) == []
    assert _ck(notes, bad.replace("Changed", "Changed"), include=["psychiatric history"]) == [L.WRONG_SECTION.format(what="a medicine", lid="L4")]


def test_r9_checker_raises_first_refusal_when_not_collecting():
    notes = "SYNTH-9902 :: F :: 50\nIntolerances - zorvofen: indigestion\n1937-03-01 review.\n"
    with pytest.raises(Refused, match=re.escape(L.ALLERGY_MISSING.format(lid="L2"))):
        L.verify_letter(_letter([("Allergies", ["indigestion [L2]"])]), notes)


def test_r9_checker_never_uses_parser(monkeypatch):
    """The check reads the notes itself: with the parser disabled it still runs and still refuses."""
    def boom(*a, **k):
        raise AssertionError("parser used")
    for name in ("parse", "classify", "cumsum", "split_med_lists", "_source_lines", "split_reaction", "content_kind",
                 "med_reason", "split_owner"):
        monkeypatch.setattr(L, name, boom)
    md = _letter([("Current medicines", ["* betamibe 10 mg daily [L5, L6]"])])
    assert L.MED_MISSING.format(lid="L5") in _ck(CK_BLOCK, md)


def test_r9_build_runs_the_independent_check(monkeypatch):
    called = []
    real = L.verify_letter
    monkeypatch.setattr(L, "verify_letter", lambda *a, **k: called.append(1) or real(*a, **k))
    make_letter(F("chest_pain.txt"), "referral", "Cardiology")
    assert called


# ---------- round 9: never merge two lines, never infer a change between drugs ----------
NM_NOTES = """SYNTH-9911 :: M :: 58
Allergy: zorbamycin - rash
1937-02-19 lipid clinic.
Current Rx
* alphastatin 2 mg nocte - cholesterol
* betamibe 10 mg daily
Question: target LDL given family history?
"""


def test_r9_bulleted_medicines_stay_separate():
    md, _ = build(NM_NOTES, "referral", "Cardiology")
    meds = FLAG_RX.sub("", section(md, "Current medicines"))
    assert "alphastatin 2 mg nocte" in meds and "[L5]" in meds and "betamibe 10 mg daily" in meds and "[L6]" in meds
    assert "[L5, L6]" not in md and "No medicine changes documented." in md and "Changed" not in md


def test_r9_brand_then_other_drug_not_a_change():
    notes = NM_NOTES.replace("* alphastatin 2 mg nocte - cholesterol", "* Nexium 20 mg daily - reflux")
    md, _ = build(notes, "gp-update", "GP")
    meds = FLAG_RX.sub("", section(md, "Current medicines"))
    assert "Nexium 20 mg daily (for reflux) [L5]" in meds and "betamibe 10 mg daily" in meds and "Changed" not in md


def test_r9_sentence_after_medicine_never_joined():
    notes = ("SYNTH-9912 :: M :: 52\nAllergy: penicillin - hives\n1937-06-02 discharged after hernia repair.\n"
             "Medication chart:\n  amlodipine 5 mg mane\nAmlodipine review due weekly per clinic.\n")
    md, _ = build(notes, "discharge", "GP")
    meds = FLAG_RX.sub("", section(md, "Current medicines"))
    assert "amlodipine 5 mg mane" in meds and "review due" not in meds and "Changed" not in md


def test_r9_block_line_without_dose_is_a_medicine():
    notes = "SYNTH-9913 :: F :: 45\n1937-07-02 seen for tiredness.\nRegular meds \u2014\n  combined pill daily\nQuestion: cause?\n"
    md, _ = build(notes, "referral", "Dermatology")
    assert "combined pill daily" in section(md, "Current medicines")


def test_r9_same_drug_rewritten_is_still_a_change():
    md = make_letter(F("chest_pain.txt"), "referral", "Cardiology")
    assert "from ramipril 2.5 mg daily. Now: ramipril 5 mg daily" in md


# ---------- round 9: per-section check ----------
SEC_NOTES = "SYNTH-9921 :: F :: 81\nSensitivities: zorvadol - hallucinations\n1937-05-14 falls review.\nMed: alphavit 1000 mg daily\n"


def test_r9_allergy_in_wrong_section_refused_exact_wording():
    md = _letter([("Background", ["Sensitivities: zorvadol - hallucinations [L2]"]),
                  ("Current medicines", ["alphavit 1000 mg daily [L4]"]), ("Allergies", ["penicillin: rash [L9]"])])
    assert _ck(SEC_NOTES, md) == [L.WRONG_SECTION.format(what="an allergy", lid="L2")]


def test_r9_medicine_in_wrong_section_refused_exact_wording():
    md = _letter([("Background", ["alphavit 1000 mg daily [L4]"]), ("Allergies", ["zorvadol: hallucinations [L2]"])])
    assert _ck(SEC_NOTES, md) == [L.WRONG_SECTION.format(what="a medicine", lid="L4")]


def test_r9_allergies_not_documented_contradiction_exact_wording():
    md = _letter([("Background", ["Sensitivities: zorvadol - hallucinations [L2]"]),
                  ("Current medicines", ["alphavit 1000 mg daily [L4]"]), ("Allergies", ["Allergies not documented."])])
    with pytest.raises(Refused) as e:
        L.verify_letter(md, SEC_NOTES)
    assert str(e.value) == L.SECTION_ND.format(sec="allergies", lid="L2")


def test_r9_medicines_not_documented_contradiction_exact_wording():
    md = _letter([("Current medicines", ["Current medicines not documented."]), ("Allergies", ["zorvadol: hallucinations [L2]"])])
    with pytest.raises(Refused) as e:
        L.verify_letter(md, SEC_NOTES)
    assert str(e.value) == L.SECTION_ND.format(sec="medicines", lid="L4")


def test_r9_nil_allergy_with_not_documented_refused():
    notes = "SYNTH-9922 :: F :: 49\nSensitivities: none\n1937-03-30 review.\n"
    md = _letter([("Allergies", ["Allergies not documented."])])
    assert _ck(notes, md) == [L.SECTION_ND.format(sec="allergies", lid="L2")]


def test_r9_patient_sections_checked_too():
    md = _letter([("Your health so far", ["Sensitivities: zorvadol - hallucinations [L2]"]),
                  ("Your medicines now", ["alphavit 1000 mg once a day [L4]"]), ("Allergies", ["Allergies not documented."])])
    assert L.SECTION_ND.format(sec="allergies", lid="L2") in _ck(SEC_NOTES, md, audience="patient")


# ---------- round 9: fixes (allergy headings, reasons, actions and owners, question flag) ----------
@pytest.mark.parametrize("line,want", [
    ("Sensitivities: zorbamycin (nausea + vomiting)", "zorbamycin: nausea + vomiting"),
    ("Intolerances - zorvostatin: muscle aches", "zorvostatin: muscle aches"),
    ("Allergic to: penicillin - hives", "penicillin: hives"),
    ("Allergy list: sulfur antibiotics -> rash and blistering", "sulfur antibiotics: rash and blistering"),
    ("Sensitivities: zorvadol - hallucinations", "zorvadol: hallucinations"),
])
@pytest.mark.parametrize("aud", [None, "patient"])
def test_r9_allergy_under_unusual_heading_goes_to_allergies(line, want, aud):
    notes = f"SYNTH-9931 :: F :: 60\n{line}\n1937-02-01 review.\nMed: alphazole 20 mg daily\n"
    md, _ = build(notes, "gp-update", "Patient" if aud else "GP", audience=aud)
    assert want in FLAG_RX.sub("", section(md, "Allergies"))
    assert "Allergies not documented" not in md


@pytest.mark.parametrize("nil", ["none", "nil", "none known", "nil known"])
def test_r9_nil_allergy_under_unusual_heading(nil):
    md, _ = build(f"SYNTH-9932 :: F :: 60\nSensitivities: {nil}\n1937-02-01 review.\n", "gp-update", "GP")
    assert "No known drug allergies [L2]" in md and "Allergies not documented" not in md


def test_r9_cut_reason_reads_reason_not_included():
    notes = ("SYNTH-9933 :: F :: 49\nSensitivities: none\n1937-03-30 discharged after surgery.\nTablets taken:\n"
             "  lithium carbonate 300 mg twice daily - steadies her\n")
    md, _ = build(notes, "discharge", "GP")
    meds = FLAG_RX.sub("", section(md, "Current medicines"))
    assert "lithium carbonate 300 mg twice daily [" in meds and "reason not included" not in meds   # H22
    assert "steadies" not in md and "reason not documented" not in meds


def test_r9_sensitive_reason_reads_reason_not_included():
    notes = "SYNTH-9934 :: F :: 54\n1937-09-17 update.\nCurrent Rx\n* zerotraline 50 mg daily - anxiety\n"
    md, _ = build(notes, "gp-update", "GP")
    assert "zerotraline 50 mg daily [" in FLAG_RX.sub("", md) and "anxiety" not in md   # H22: no marker
    assert "reason not included" not in md and L.MED_FLAG not in md


@pytest.mark.parametrize("line,reason", [("Ceased: perindopril 1937-05 due to cough", "due to cough"),
                                         ("Stopped: naproxen 1937-08 because of stomach upset", "because of stomach upset"),
                                         ("Stop: amlodipine - ankle swelling", "ankle swelling")])
def test_r9_stated_stop_reason_kept(line, reason):
    md, _ = build(f"SYNTH-9935 :: F :: 71\n1937-07-19 review.\n{line}\n", "gp-update", "GP")
    ch = FLAG_RX.sub("", section(md, "Medicine changes"))
    assert f"Reason for change: {reason}." in ch and "Reason for change: not documented" not in ch


@pytest.mark.parametrize("line,who", [("Wound check 1937-04-06 - practice nurse", "practice nurse"),
                                      ("Recheck in 4 weeks - you can book at the front desk", "you can book at the front desk"),
                                      ("Your next blood test is 1937-09-17 - practice nurse", "practice nurse")])
def test_r9_followup_and_owner_go_to_actions(line, who):
    md, _ = build(f"SYNTH-9936 :: F :: 58\n1937-09-03 review.\n{line}\n", "gp-update", "GP")
    acts = FLAG_RX.sub("", section(md, "Actions"))
    assert f"Who: {who}." in acts and "Follow-up actions not documented" not in md
    assert line.split(" - ")[0].split()[0] not in FLAG_RX.sub("", section(md, "Background"))


def test_r9_pending_awaiting_is_not_an_owner():
    md, _ = build("SYNTH-9937 :: F :: 45\n1937-07-02 seen.\nVitamin B12 sent 1937-07-02 - awaiting lab\n", "gp-update", "GP")
    p = FLAG_RX.sub("", section(md, "Results still to come"))
    assert "Who follows up: not documented." in p and "awaiting lab" not in p.split("Who follows up:")[1]


def test_r9_pending_named_owner_still_kept():
    md, _ = build("SYNTH-9938 :: F :: 45\n1937-07-02 seen.\nEcho sent 1937-07-02 - renal fellow to chase\n", "gp-update", "GP")
    assert "Who follows up: renal fellow to chase." in FLAG_RX.sub("", md)


def test_r9_no_question_flag_when_question_cut():
    notes = "SYNTH-9939 :: F :: 45\n1937-07-02 seen for tremor.\nProblem: tremor\nQuestion: is the lithium level too high?\n"
    flags = []
    md, _ = build(notes, "referral", "Neurology", flags=flags)
    assert "lithium level" not in md and L.QUESTION_FLAG in md and L.QUESTION_NOTICE in flags


def test_r9_no_question_flag_when_no_question():
    flags = []
    md, _ = build("SYNTH-9940 :: F :: 45\n1937-07-02 seen for tremor.\nProblem: tremor\n", "referral", "Neurology", flags=flags)
    assert L.QUESTION_FLAG in md and flags.count(L.QUESTION_NOTICE) == 1


def test_r9_result_in_a_sentence_is_kept():
    md, _ = build("SYNTH-9941 :: M :: 58\n1937-02-19 lipid clinic, LDL 1937-02-12 3.6 mmol/L.\nQuestion: target?\n",
                  "referral", "Cardiology")
    assert "3.6 mmol/L" in md


# ---------- round 9: copy-request regression (root cause: 'copy' had to be followed straight by 'to') ----------
COPY_SENTENCES = ["Please send a copy of this to her son as well.", "Copies of this letter to the practice please.",
                  "Can you copy her daughter in?", "Forward a copy to the physio.", "Daughter would like a copy."]
COPY_BASE = "SYNTH-9951 :: F :: 81\nAllergy: zorvadol - hallucinations\n1937-05-14 falls review.\nMed: alphavit 1000 mg daily\n"


@pytest.mark.parametrize("sentence", COPY_SENTENCES)
@pytest.mark.parametrize("brief,tail", [("letter to the patient", L.PATIENT_COPY_TAIL), ("update to GP", L.COPY_TAIL)])
@pytest.mark.parametrize("final", [False, True])
def test_r9_copy_request_in_a_sentence_refused_exit_2_nothing_written(tmp_path, sentence, brief, tail, final):
    n = tmp_path / "notes.txt"; out = tmp_path / "out"
    n.write_text(tpl(pid="SYNTH-9951", question="Is she safe at home?", history=sentence,
                     meds=["alphavit 1000 mg daily"], allergies=["zorvadol: hallucinations"]))
    r = subprocess.run(CLI + [str(n), "--brief", brief, "--out", str(out)] + (["--final"] if final else []),
                       capture_output=True, text=True)
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert _clin(r) == L.NOTE_COPY_REFUSED.format(n=6) and _hand_ok(r)   # History line
    assert not out.exists()
    assert "son" not in r.stderr and "daughter" not in r.stderr.lower()


@pytest.mark.parametrize("sentence", COPY_SENTENCES)
def test_r9_copy_request_in_a_sentence_refused_by_build(sentence):
    with pytest.raises(Refused, match=r"^Refused: line L5 mentions a copy\. "):
        build(COPY_BASE + sentence + "\n", "gp-update", "Patient", audience="patient")


def test_r9_copies_as_a_lab_unit_is_not_a_copy_request():
    md, _ = build(COPY_BASE + "Result: viral load <20 copies/mL\n", "gp-update", "Patient", audience="patient")
    assert "copies/mL" in md


# ---------- round 9: queued signed items ----------
@pytest.mark.parametrize("line,allergen", [("Allergy: penicillin", "penicillin"), ("Allergy: Zorvacillin - unknown", "Zorvacillin"),
                                           ("Sensitivities: zorbamycin (reaction not known)", "zorbamycin")])
def test_r9_allergy_without_reaction_clinician_wording(line, allergen):
    md, _ = build(f"SYNTH-9961 :: F :: 60\n{line}\n1937-02-01 review.\n", "gp-update", "GP")
    assert f"{allergen}: reaction not documented" in FLAG_RX.sub("", section(md, "Allergies"))


@pytest.mark.parametrize("line,allergen", [("Allergy: penicillin", "penicillin"), ("Allergy: Zorvacillin - unknown", "Zorvacillin")])
def test_r9_allergy_without_reaction_patient_wording(line, allergen):
    md, _ = build(f"SYNTH-9962 :: F :: 60\n{line}\n1937-02-01 review.\n", "gp-update", "Patient", audience="patient")
    assert (f"{allergen}: the reaction wasn't written in your notes. Tell your next doctor what happened."
            in FLAG_RX.sub("", section(md, "Allergies")))
    assert "reaction not documented" not in md


def test_r9_drugs_list_additions():
    names = L.drug_names()
    assert names["augmentin"] == "amoxicillin/clavulanate" and names["amoxicillin/clavulanate"] == "amoxicillin/clavulanate"
    for g in ("tenofovir", "ritonavir", "lamivudine"):
        assert names[g] == g and not L.needs_flag(g)


def test_r9_new_hiv_names_stay_flag_only_for_cutting():
    linked = L.load_linked()["HIV status"]
    for g in ("tenofovir", "ritonavir", "lamivudine"):
        assert g in linked["flag_terms"] and g not in linked["terms"] and g not in linked.get("medicines", [])
    md, dropped = build("SYNTH-9963 :: M :: 40\n1937-02-01 review.\nMed: ritonavir 100 mg daily\n", "gp-update", "Podiatry")
    assert not dropped and "ritonavir 100 mg daily" in md and L.DRUG_FLAG not in md


def test_r9_augmentin_recognised_not_flagged():
    md, _ = build("SYNTH-9964 :: M :: 40\n1937-02-01 review.\nMed: Augmentin 875 mg bd\n", "gp-update", "GP")
    assert f"Augmentin {L.DRUG_FLAG}" not in md and "Augmentin 875 mg" in md


def test_r9_combination_refusal_and_linked_terms_unchanged():
    import hashlib
    digest = (HERE / "LINKED_TERMS.SHA256").read_text().split()[0]
    assert hashlib.sha256((HERE / "linked_terms.toml").read_bytes()).hexdigest() == digest
    with pytest.raises(Refused, match="combination medicine"):
        build("SYNTH-9965 :: M :: 40\n1937-02-01 review.\nMed: Biktarvy 1 tablet daily\n", "gp-update", "GP")


# ---------- round 10: fixed notes template ----------
def _run_t(tmp_path, text, *args):
    tmp_path.mkdir(parents=True, exist_ok=True)
    n = tmp_path / "n.txt"; n.write_text(text); out = tmp_path / "o"
    r = subprocess.run(CLI + [str(n), *args, *READER, "--out", str(out)], capture_output=True, text=True)
    return r, out


T_MSG = "Refused: line L{n} of the notes doesn't follow the template. Nothing was written. Rewrite the line to follow the template, then run again."


@pytest.mark.parametrize("mutate,n", [
    (lambda t: t.replace("Date: 1937-06-03\n", ""), 2),                          # missing required field
    (lambda t: t.replace("Question: none", "Query: none"), 4),                   # unknown field
    (lambda t: t.replace("History: \n", "") + "History: later\n", 0),            # out of order
    (lambda t: t.replace("- knee pain", "knee pain"), 6),                         # list item without '- '
    (lambda t: t.replace("Allergies:\n", ""), 0),                                # missing heading of a list field
])
def test_r10_template_refusals_name_first_line(tmp_path, mutate, n):
    text = mutate(tpl(problems=["knee pain"], actions=["review in 2 weeks, GP"]))
    r, out = _run_t(tmp_path, text, "--brief", "update to GP")
    exp = n or next(k for k, l in enumerate(text.splitlines(), 1)
                    if not L.FIELD_RX.match(l) or l.split(":")[0] in ("History", "Medicines", "Results", "Pending", "Actions")
                    and n == 0 and l.startswith(("History: later", "Medicines:")))
    assert r.returncode == 2 and r.stdout == "" and not out.exists()
    assert re.fullmatch(r"Refused: line L\d+ of the notes doesn't follow the template\. Nothing was written\. Rewrite the line to follow the template, then run again\.", r.stderr.strip())
    if n:
        assert _clin(r) == T_MSG.format(n=n)


def test_r10_template_free_text_notes_refused(tmp_path):
    r, out = _run_t(tmp_path, F("chest_pain.txt"), "--brief", "referral to cardiology")
    assert r.returncode == 2 and _clin(r) == T_MSG.format(n=2) and not out.exists()


def test_r10_recipients_line_never_in_letter_and_only_one(tmp_path):
    text = tpl(problems=["knee pain"], recipient="Dr Quillfeather", question="Is it the joint?")
    r, out = _run_t(tmp_path, text, "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    assert "Quillfeather" not in (out / "letter.md").read_text() + r.stderr
    r, out = _run_t(tmp_path / "two", tpl(recipient="Dr Quillfeather, Dr Ashgrove"), "--brief", "update to GP")
    assert r.returncode == 2 and not out.exists() and _clin(r) == (
        "Refused: line L3 lists more than one recipient. Nothing was written. Write a separate letter for each reader.")


@pytest.mark.parametrize("item", ["zorbaline 5 mg", "zorbaline daily 5 mg", "zorbaline 5 mg daily reason: gout max 2 in 24 hours",
                                  "zorbaline 5 mg daily for 5 days max 2 in 24 hours"])
def test_r10_medicine_line_out_of_order_refused(tmp_path, item):
    text = tpl(meds=[item])
    r, _ = _run_t(tmp_path, text, "--brief", "update to GP")
    assert r.returncode == 2 and _clin(r) == T_MSG.format(n=int(line_of(text, "zorbaline")[1:]))


def test_r10_medicine_line_grammar_parts():
    m = L.med_item("zorbaline 5 mg daily oral max 2 tablets in 24 hours for 5 days reason: gout; stopped 1937-06-01 - rash")
    assert (m["drug"], m["dose"], m["freq"], m["route"], m["dur"], m["reason"], m["stop"], m["sdate"], m["sreason"]) == (
        "zorbaline", "5 mg", "daily", "oral", "for 5 days", "gout", "stopped", "1937-06-01", "rash")
    assert m["max"] == "max 2 tablets in 24 hours"
    assert L.med_item("zorbaline 5 mg daily for 5 days")["reason"] is None       # a duration is never a reason


def test_r10_empty_list_fields_print_none_recorded(tmp_path):
    text = tpl(question="Is it the joint?", problems=["knee pain"], allergies=())
    r, out = _run_t(tmp_path, text, "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    for head in ("Current medicines", "Allergies", "Results still to come", "Actions"):
        assert section(md, head).strip() == "- none recorded.", head
    assert "No known" not in md


def test_r10_explicit_no_known_allergies_line(tmp_path):
    r, out = _run_t(tmp_path, tpl(question="Is it the joint?", problems=["knee pain"]), "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    assert section((out / "letter.md").read_text(), "Allergies").startswith("- No known drug allergies")


# ---------- round 10 item 2: copy check (copy, send, post, forward, give) and --not-copy ----------
@pytest.mark.parametrize("line", ["Can you also post this to my daughter please.", "Please forward the letter to Mr Quell.",
                                  "Give a copy of the notes to her carer.", "Send this to the practice nurse.",
                                  "I'd like a copy given to Mr Hale next door too."])
@pytest.mark.parametrize("final", [False, True])
@pytest.mark.parametrize("brief", ["letter to the patient", "update to GP"])
def test_r10_copy_words_refused_exit_2_both_modes(tmp_path, line, final, brief):
    text = tpl(question="Is she safe at home?", history=line)
    r, out = _run_t(tmp_path, text, "--brief", brief, *(["--final"] if final else []))
    n = int(line_of(text, "History")[1:])
    assert r.returncode == 2 and _hand_ok(r) and not out.exists()
    assert _clin(r) == L.NOTE_COPY_REFUSED.format(n=n)


def test_r10_copies_per_ml_is_not_a_copy_request(tmp_path):
    r, out = _run_t(tmp_path, tpl(question="Is it suppressed?", history="viral load <20 copies/mL"),
                    "--brief", "letter to the patient")
    assert r.returncode == 0, r.stderr


def test_r10_not_copy_covers_one_line_and_prints_beside_cut_list(tmp_path):
    text = tpl(question="Is it the joint?", problems=["knee pain"], history="Reviewed copy of old notes.")
    n = int(line_of(text, "History")[1:])
    r, out = _run_t(tmp_path, text, "--brief", "update to GP")
    assert r.returncode == 2 and _clin(r) == L.NOTE_COPY_REFUSED.format(n=n)
    r, out = _run_t(tmp_path / "b", text, "--brief", "update to GP", "--not-copy", f"L{n}")
    assert r.returncode == 0, r.stderr
    assert f"Line L{n} marked as not a copy request" in r.stderr.splitlines()
    assert [p.name for p in out.iterdir()] == ["letter.md"]


def test_r10_not_copy_is_one_line_per_flag(tmp_path):
    text = tpl(question="Is it the joint?", problems=["knee pain", "old copy of scan reviewed"], history="Reviewed copy of old notes.")
    a, b = int(line_of(text, "old copy")[1:]), int(line_of(text, "History")[1:])
    r, _ = _run_t(tmp_path, text, "--brief", "update to GP", "--not-copy", f"L{a}")
    assert r.returncode == 2 and _clin(r) == L.NOTE_COPY_REFUSED.format(n=b)
    r, _ = _run_t(tmp_path / "b", text, "--brief", "update to GP", "--not-copy", f"L{a}", "--not-copy", f"L{b}")
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("brief", ["update to GP", "letter to the patient", "referral to cardiology"])
@pytest.mark.parametrize("line", ["Please send a copy to her son.", "Post this to my daughter."])
def test_r10_not_copy_never_covers_a_line_naming_someone(tmp_path, brief, line):
    text = tpl(question="Is it the joint?", problems=["knee pain"], history=line)
    n = int(line_of(text, "History")[1:])
    r, out = _run_t(tmp_path, text, "--brief", brief, "--not-copy", f"L{n}")
    assert r.returncode == 2 and not out.exists() and _hand_ok(r)
    assert _clin(r) == L.NOT_COPY_REFUSED.format(n=n)


def test_r10_gp_letter_not_copy_send_a_copy_to_her_son_still_refuses(tmp_path):
    text = tpl(question="Is it the joint?", problems=["knee pain"], history="send a copy to her son")
    n = int(line_of(text, "History")[1:])
    r, out = _run_t(tmp_path, text, "--brief", "update to GP", "--not-copy", f"L{n}")
    assert r.returncode == 2 and _clin(r) == L.NOT_COPY_REFUSED.format(n=n)
    assert not out.exists()


def test_r10_sent_specimen_is_not_a_copy_request(tmp_path):
    r, _ = _run_t(tmp_path, tpl(question="Is it infected?", pending=["urine culture, sent 1937-06-02, GP"],
                                history="Urine culture sent to the lab."), "--brief", "update to GP")
    assert r.returncode == 0, r.stderr


# ---------- round 10 item 1: reverse check (dates, numbers, names, owners must be in the notes or brief) ----------
REV_T = tpl(question="Is the dose right?", problems=["knee pain"], date="1937-04-15",
            meds=["zorbaline 50 mg tds reason: knee pain", "trimeloxin 10 mg daily; ceased 1937-04-12 - nausea"],
            results=["sodium 133 mmol/L 1937-04-11"], pending=["urine culture, sent 1937-04-13, practice nurse"],
            actions=["review in 2 weeks, practice nurse"])


def _rev_md():
    return build_from_template_md(REV_T)


def build_from_template_md(t, **k):
    return L.build_from_template(t, "gp-update", "GP", **k)[0]


def test_r10_reverse_dates_come_from_the_line_never_the_letter_date():
    md = _rev_md()
    assert "Stopped on 12 Apr 1937" in md and "15 Apr 1937: trimeloxin" not in md
    assert "133 mmol/L on 11 Apr 1937" in md and "sent on 13 Apr 1937" in md


@pytest.mark.parametrize("old,new", [("12 Apr 1937", "15 Apr 1937"),          # letter's own date for a stop date
                                     ("133 mmol/L", "134 mmol/L"),            # number not in the notes
                                     ("11 Apr 1937", "10 Apr 1937"),          # recalculated date
                                     ("Who follows up: practice nurse", "Who follows up: Dr Quell")])
def test_r10_reverse_check_refuses_with_letter_line(old, new):
    md = _rev_md()
    bad = md.replace(old, new, 1)
    n = next(k for k, l in enumerate(md.split("\n"), 1) if old in l)
    with pytest.raises(Refused) as e:
        L.reverse_check(bad, L.from_template(REV_T), "GP", template=True)
    assert str(e.value) == f"Refused: line L{n} of this letter has a date, number or name that isn't in the notes. Nothing was written. Correct it to match the notes, then run again."


def test_r10_reverse_check_allows_signed_rewording_and_reformatted_dates():
    t = tpl(question="Is it the joint?", meds=["paracetamol 1 g prn max 8 tablets in 24 hours reason: pain",
                                               "zorbaline 5 mg mane reason: gout"])
    md, _ = L.build_from_template(t, "gp-update", "Patient", audience="patient")
    assert "when needed" in md and "in the morning" in md
    L.reverse_check(md, L.from_template(t), "Patient")


def test_r10_reverse_check_names_from_brief_allowed_but_not_invented():
    md = _rev_md()
    L.reverse_check(md, L.from_template(REV_T), "GP")
    with pytest.raises(Refused, match="line L\\d+ of this letter"):
        L.reverse_check(md.replace("Dear colleague,", "Dear Dr Quell,"), L.from_template(REV_T), "GP")


# ---------- round 10 item 3: placement by content (template field) and the per-section check ----------
PLACE_T = tpl(question="Is the plan right?", problems=["knee pain"], history="Walks with a stick.",
              meds=["zorbaline 5 mg daily reason: gout", "emollient cream bd reason: dry skin",
                    "trimeloxin 10 mg nocte; withheld 1937-06-01 - dizziness"],
              allergies=["sulfa drugs: rash"], actions=["review the knee in 2 weeks, physio"])


def test_r10_placement_follows_the_field():
    md = build_from_template_md(PLACE_T)
    assert "emollient cream" in section(md, "Current medicines")
    assert "trimeloxin" in section(md, "Medicine changes") and "trimeloxin" not in section(md, "Background")
    assert "physio" in section(md, "Actions") and "sulfa drugs: rash" in section(md, "Allergies")


@pytest.mark.parametrize("frag,what", [("emollient cream", "a medicine"), ("Who: physio", "an action"),
                                       ("sulfa drugs", "an allergy"), ("trimeloxin", None)])
def test_r10_per_section_check_catches_each_misplacement(frag, what):
    md = build_from_template_md(PLACE_T)
    lines = md.split("\n")
    i = next(k for k, l in enumerate(lines) if frag in l)
    moved = lines[i]
    del lines[i]
    j = lines.index("## Background") + 2
    lines.insert(j, moved if moved.startswith("- ") else "- " + moved.split(". ", 1)[1])
    with pytest.raises(Refused, match="wrong section|missing from this letter"):
        L.verify_letter("\n".join(lines), L.from_template(PLACE_T), "gp")


# ---------- round 10 item 4: 'No known allergies', 'NKDA' and 'nil known' mean no allergies ----------
@pytest.mark.parametrize("item", ["No known allergies", "NKDA", "nil known", "Nil known allergies"])
@pytest.mark.parametrize("aud", [None, "patient"])
def test_r10_no_allergy_statements_are_not_an_allergy(item, aud):
    t = tpl(question="Is it the joint?", problems=["knee pain"], allergies=[item])
    md, _ = L.build_from_template(t, "gp-update", "Patient" if aud else "GP", audience=aud)
    al = section(md, "Allergies")
    assert al.startswith("- No known drug allergies") and "reaction" not in al and L.ALLERGY_FLAG not in al


def test_r10_no_allergy_statement_inline_on_the_heading(tmp_path):
    t = tpl(question="Is it the joint?", problems=["knee pain"], allergies=()).replace("Allergies:\n", "Allergies: NKDA\n")
    r, out = _run_t(tmp_path, t, "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    assert section((out / "letter.md").read_text(), "Allergies").startswith("- No known drug allergies")


# ---------- round 10 item 5: the question line is always kept; none gives the no-question flag ----------
@pytest.mark.parametrize("brief,head", [("discharge to GP", "What we are asking"), ("update to GP", "What we are asking"),
                                        ("letter to the patient", "What we want to find out"),
                                        ("referral to cardiology", "Question")])
def test_r10_question_kept_in_every_letter(tmp_path, brief, head):
    r, out = _run_t(tmp_path, tpl(question="Can I swim again?", problems=["asthma"]), "--brief", brief)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert "Can I swim again?" in section(md, head) and L.QUESTION_FLAG not in md


@pytest.mark.parametrize("brief", ["discharge to GP", "update to GP", "letter to the patient", "referral to cardiology"])
def test_r10_no_question_flag_fires_in_every_letter(tmp_path, brief):
    r, out = _run_t(tmp_path, tpl(question="none", problems=["asthma"]), "--brief", brief)
    assert r.returncode == 0, r.stderr
    assert L.QUESTION_FLAG in (out / "letter.md").read_text()
    assert any(l.startswith("Flag: this") and "no clear question" in l for l in r.stderr.splitlines())
    r, out = _run_t(tmp_path / "f", tpl(question="none", problems=["asthma"]), "--brief", brief, "--final")
    assert r.returncode == 3 and not out.exists()


# ---------- round 10 item 6: reasons; a kept stop reason still goes through the cut rules ----------
@pytest.mark.parametrize("line", ["Ceased: zorbaline 5 mg daily due to schizophrenia relapse",
                                  "Ceased: zorbaline 5 mg daily because of schizophrenia relapse",
                                  "Ceased: zorbaline 5 mg daily owing to schizophrenia relapse",
                                  "Ceased: zorbaline 5 mg daily - schizophrenia relapse"])
def test_r10_cut_stop_reason_each_trigger_word_non_patient(line):
    flags = []
    t = f"SYNTH-9301\n1937-06-01 review.\n{line}\nQuestion: is she well?\n"
    md, _ = build(t, "gp-update", "GP", flags=flags)
    ch = section(md, "Medicine changes")
    assert "Reason for change" not in ch and "zorbaline" in ch and L.MED_FLAG not in md     # H22/H29: no marker
    assert any(f.startswith("Flag: a medicine on line") for f in flags)
    assert "schizophrenia" not in md.lower() and "not documented" not in ch
    pmd, _ = build(t, "gp-update", "Patient", audience="patient")       # patient letters keep everything
    assert "schizophrenia relapse" in pmd


def test_r10_cut_stop_reason_in_template_line():
    t = tpl(question="Is she well?", meds=["zorbaline 5 mg daily; ceased 1937-06-01 - schizophrenia relapse"])
    flags = []
    md, _ = L.build_from_template(t, "gp-update", "GP", flags=flags)
    ch = section(md, "Medicine changes")
    assert "Reason for change" not in ch and "zorbaline" in ch and L.MED_FLAG not in md and "schizophrenia" not in md.lower()
    assert any(f.startswith("Flag: a medicine on line") for f in flags)                 # H22: on screen only


@pytest.mark.parametrize("word", ["after", "following"])
def test_r10_after_and_following_kept_as_written_never_a_reason(word):
    md, _ = build(f"SYNTH-9302\n1937-06-01 review.\nCeased: zorbaline 5 mg daily {word} 2 weeks\nQuestion: ok?\n",
                  "gp-update", "GP")
    ch = L.FLAG_ANY.sub("", section(md, "Medicine changes"))
    assert f"zorbaline 5 mg daily {word} 2 weeks" in ch and f"Reason for change: {word}" not in ch


def test_r10_text_after_dash_is_a_reason():
    md, _ = build("SYNTH-9303\n1937-06-01 review.\nCeased: zorbaline 5 mg daily - rash\nQuestion: ok?\n", "gp-update", "GP")
    assert "Reason for change: rash." in section(md, "Medicine changes")


# ---------- round 10 item 7: patient plain words, include record, 'To:' ----------
@pytest.mark.parametrize("freq,plain", [("prn", "when needed"), ("mane", "in the morning"), ("PRN", "when needed"),
                                        ("Mane", "in the morning"), ("nocte", "at night")])
def test_r10_patient_letters_use_signed_plain_words_never_a_flag(freq, plain):
    t = tpl(question="Is it working?", meds=[f"zorbaline 5 mg {freq} reason: pain"])
    md, _ = L.build_from_template(t, "gp-update", "Patient", audience="patient")
    meds = section(md, "Your medicines now")
    assert plain in meds and freq not in meds and L.FLAG not in meds


@pytest.mark.parametrize("brief", ["update to GP\ninclude: psychiatric history", "update to GP. Include: psychiatric history",
                                   "update to GP; include: psychiatric history"])
def test_r10_include_record_always_printed_and_to_is_the_reader(tmp_path, brief):
    r, out = _run_t(tmp_path, PSY_T, "--brief", brief)
    assert r.returncode == 0, r.stderr
    assert "Included by the brief: psychiatric history." in r.stderr.splitlines()
    md = (out / "letter.md").read_text()
    assert "**To:** GP  " in md and "nclude" not in md


def test_r10_brief_text_never_goes_into_to(tmp_path):
    r, out = _run_t(tmp_path, PSY_T, "--brief", "update to GP. Please be kind")
    assert r.returncode == 2 and _clin(r) == BRIEF_MSG and not out.exists()


# ---------- round 10 item 8: allergy with no reaction, multi-word allergen ----------
@pytest.mark.parametrize("aud,wording", [(None, "reaction not documented"), ("patient", L.ALLERGY_NO_REACTION_PATIENT)])
def test_r10_multiword_allergen_no_reaction_gets_flag_and_wording(aud, wording):
    t = tpl(question="Is it the joint?", allergies=["sulfa drugs"])
    flags = []
    md, _ = L.build_from_template(t, "gp-update", "Patient" if aud else "GP", audience=aud, flags=flags)
    al = section(md, "Allergies")
    assert al.startswith(f"- sulfa drugs: {wording} {L.ALLERGY_FLAG}")
    assert any("extra wording" in f for f in flags)


def test_r10_single_word_allergen_no_reaction_wording_only():
    md, _ = L.build_from_template(tpl(question="ok?", allergies=["codeine"]), "gp-update", "GP")
    assert section(md, "Allergies").startswith("- codeine: reaction not documented [L") and L.ALLERGY_FLAG not in md


# ---------- round 10 item 9: under --final the refusal count equals the flags printed ----------
@pytest.mark.parametrize("brief", ["update to GP", "letter to the patient", "referral to cardiology"])
def test_r10_final_count_equals_flags_printed(tmp_path, brief):
    t = tpl(question="none", problems=["ACS query", "MS"], history="HIV", allergies=["sulfa drugs"],
            meds=["zorbaline 5 mg prn reason: knee pain", "abacavir 600 mg daily reason: HIV", "ramipril 5 mg od"])
    r, out = _run_t(tmp_path, t, "--brief", brief, "--final")
    assert r.returncode == 3 and not out.exists()
    lines = r.stderr.splitlines()
    shown = [l for l in lines if l.startswith("Flag: ")]
    m = re.search(r"--final refused: (\d+) flag", r.stderr)
    assert m and int(m.group(1)) == len(shown) > 0


# ---------- round 10: course length kept exactly (when the notes give one) ----------
COURSE_T = tpl(question="Is it clearing?", meds=["amoxicillin 500 mg tds for 5 days reason: chest infection",
                                                  "zorbaline 5 mg daily reason: gout"])


@pytest.mark.parametrize("aud", [None, "patient"])
def test_r10_course_length_kept_exactly(aud):
    md, _ = L.build_from_template(COURSE_T, "gp-update", "Patient" if aud else "GP", audience=aud)
    meds = L.FLAG_ANY.sub("", section(md, "Your medicines now" if aud else "Current medicines"))
    assert "for 5 days" in meds and "then stop" not in md


@pytest.mark.parametrize("change", ["", "for 7 days"])
def test_r10_dropped_or_changed_course_length_refused(change):
    md, _ = L.build_from_template(COURSE_T, "gp-update", "GP")
    bad = md.replace(" for 5 days", f" {change}" if change else "", 1)
    lid = line_of(COURSE_T, "amoxicillin")
    with pytest.raises(Refused) as e:
        L.verify_letter(bad, L.from_template(COURSE_T), "gp")
    assert str(e.value) == L.COURSE_MISSING.format(lid=lid)


def test_r10_no_course_length_in_notes_none_required():
    md, _ = L.build_from_template(COURSE_T, "gp-update", "GP")
    assert L.verify_letter(md, L.from_template(COURSE_T), "gp")


def test_r10_dropped_course_length_build_refuses(monkeypatch):
    monkeypatch.setattr(L, "render", (lambda f: lambda *a, **k: f(*a, **k).replace(" for 5 days", ""))(L.render))
    with pytest.raises(Refused, match="course length"):
        L.build_from_template(COURSE_T, "gp-update", "GP")


# ---------- round 10 E: empty Allergies or Medicines field ----------
EMPTY_T = tpl(question="Is it the joint?", problems=["knee pain"], meds=(), allergies=())


@pytest.mark.parametrize("brief", ["update to GP", "referral to cardiology", "discharge to GP"])
def test_r10_empty_allergies_and_medicines_non_patient(tmp_path, brief):
    r, out = _run_t(tmp_path, EMPTY_T, "--brief", brief)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert section(md, "Allergies").strip() == "- none recorded." and section(md, "Current medicines").strip() == "- none recorded."
    a, m = line_of(EMPTY_T, "Allergies:"), line_of(EMPTY_T, "Medicines:")
    assert f"Flag: the allergies field on line {a} is empty. Check allergies before this letter is used." in r.stderr.splitlines()
    assert f"Flag: the medicines field on line {m} is empty. Check medicines before this letter is used." in r.stderr.splitlines()
    assert "No known" not in md
    r, out = _run_t(tmp_path / "f", EMPTY_T, "--brief", brief, "--final")
    assert r.returncode == 3 and not out.exists()


def test_r10_empty_allergies_and_medicines_patient_wording(tmp_path):
    r, out = _run_t(tmp_path, EMPTY_T, "--brief", "letter to the patient")
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert section(md, "Allergies").strip() == "- No allergies were written in your notes. If you have any, tell your next doctor."
    assert section(md, "Your medicines now").strip() == (
        "- No medicines were written in your notes. If you take any, tell your next doctor.")
    assert "none recorded" not in section(md, "Allergies") + section(md, "Your medicines now")
    assert any(l.startswith("Flag: the allergies field on line") for l in r.stderr.splitlines())
    assert any(l.startswith("Flag: the medicines field on line") for l in r.stderr.splitlines())
    r, out = _run_t(tmp_path / "f", EMPTY_T, "--brief", "letter to the patient", "--final")
    assert r.returncode == 3 and not out.exists()
    m = re.search(r"--final refused: (\d+) flag", r.stderr)
    assert int(m.group(1)) == len([l for l in r.stderr.splitlines() if l.startswith("Flag: ")])


# ---------- round 11 ----------
@pytest.mark.parametrize("nka", ["No known allergies", "NKDA", "nil known", "Nil known allergies."])
def test_r11_no_dash_no_known_allergies_line_accepted(tmp_path, nka):
    text = tpl(question="Please review.", problems=("cough",), meds=("amlodipine 5 mg daily",), allergies=(),
               actions=("review in 2 weeks, GP",)).replace("Allergies:\n", f"Allergies:\n{nka}\n")
    r, out = _run_t(tmp_path / nka.replace(" ", "_"), text, "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert "No known drug allergies" in md
    assert "allergies field" not in r.stderr and "Refused" not in r.stderr


@pytest.mark.parametrize("freq", ["every 2 weeks", "weekly", "fortnightly", "once a month", "every other day"])
@pytest.mark.parametrize("brief", ["update to GP", "update to patient"])
def test_r11_off_list_frequency_kept_as_written_no_flag(tmp_path, freq, brief):
    text = tpl(question="Please review.", problems=("rheumatoid arthritis",),
               meds=(f"methotrexate 10 mg {freq}",), actions=("review in 2 weeks, GP",))
    r, out = _run_t(tmp_path / freq.replace(" ", "_"), text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert f"methotrexate 10 mg {freq}" in md
    lid = line_of(text, "methotrexate")
    assert not any(lid in l for l in r.stderr.splitlines()), r.stderr


@pytest.mark.parametrize("mx", ["max 4 g per day", "max 30 mg per week", "max 8 puffs per week", "max 2 doses in 7 days"])
@pytest.mark.parametrize("brief", ["update to GP", "update to patient"])
def test_r11_dose_limit_other_period_kept_as_written(tmp_path, mx, brief):
    text = tpl(question="Please review.", problems=("pain",), meds=(f"paracetamol 1 g daily {mx}",),
               actions=("review in 2 weeks, GP",))
    r, out = _run_t(tmp_path / mx.replace(" ", "_"), text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert mx in md and "in 24 hours" not in md
    lid = line_of(text, "paracetamol")
    assert not any(lid in l for l in r.stderr.splitlines()), r.stderr


@pytest.mark.parametrize("brief,items", [
    ("referral to podiatry\nInclude: psychiatric history", ["psychiatric history"]),
    ("referral to podiatry. Include: psychiatric history", ["psychiatric history"]),
    ("referral to podiatry\nInclude: psychiatric history, HIV status", ["psychiatric history", "HIV status"]),
    ("referral to podiatry\nInclude: psychiatric history\ninclude: HIV status", ["psychiatric history", "HIV status"]),
])
def test_r11_brief_include_line_accepted(tmp_path, brief, items):
    r, out = _run_t(tmp_path, (pathlib.Path(L.__file__).parent / "fixtures/template/podiatry_referral.txt").read_text(),
                    "--brief", brief)
    assert r.returncode == 0, r.stderr
    for i in items:
        assert f"Included by the brief: {i}." in r.stderr
    assert "bipolar" in (out / "letter.md").read_text()


def test_r11_brief_include_unknown_item_still_refused(tmp_path):
    r, out = _run_t(tmp_path, (pathlib.Path(L.__file__).parent / "fixtures/template/podiatry_referral.txt").read_text(),
                    "--brief", "referral to podiatry\nInclude: alcohol history")
    assert r.returncode == 2 and _clin(r) == L.SENSITIVE_REFUSED.format(n=2)



@pytest.mark.parametrize("reason", ["high blood pressure", "type 2 diabetes", "heart failure", "chest infection"])
def test_r11_ordinary_english_reason_kept_no_flag(tmp_path, reason):
    text = tpl(question="none", problems=("review",), meds=(f"amlodipine 5 mg daily reason: {reason}",),
               actions=("review in 2 weeks, GP",))
    r, out = _run_t(tmp_path / reason.replace(" ", "_"), text, "--brief", "update to patient")
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert f"for {reason} [L" in md and L.REASON_FLAG not in md
    assert "no signed plain words" not in r.stderr


def test_r11_reason_with_unsigned_shorthand_still_flagged():
    assert L._patient_reason("pain od") == f"for pain od {L.REASON_FLAG}"
    assert L._patient_reason("sob") == f"for sob {L.REASON_FLAG}"
    assert L._patient_reason("pain at night") == "for pain at night"


@pytest.mark.parametrize("tok,flag", [("nocte", False), ("mane", False), ("prn", False), ("bd", False),
                                      ("od", True), ("stat", True), ("nbm", True), ("sob", True)])
def test_r11_shorthand_flag_only_without_signed_plain_words(tok, flag):
    assert L.needs_flag(tok, exact=tuple(L.load_single_meaning())) is flag


GENERICS = ["amlodipine", "atorvastatin", "omeprazole", "ramipril", "apixaban", "metformin", "paracetamol",
            "furosemide", "bisoprolol", "salbutamol"]


@pytest.mark.parametrize("drug", GENERICS)
def test_r11_common_generics_do_not_flag(tmp_path, drug):
    assert drug in L.drug_names(), f"{drug} missing from drugs.txt"
    text = tpl(question="none", problems=("review",), meds=(f"{drug} 5 mg daily",), actions=("review in 2 weeks, GP",))
    r, out = _run_t(tmp_path / drug, text, "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    assert "misspelled" not in r.stderr and L.DRUG_FLAG not in (out / "letter.md").read_text()


def test_r11_copy_refusal_wins_over_template_error(tmp_path):
    text = tpl(question="Please review.", problems=("cough",), meds=("amlodipine 5 mg daily",),
               actions=("review in 2 weeks, GP",))
    text = text.replace("History:", "History: please send a copy of this letter to her son")
    text = text.replace("Medicines:\n- amlodipine 5 mg daily", "Medicines:\namlodipine 5 mg daily")   # template error
    assert "Refused: line" in str(pytest.raises(L.Refused, L.from_template, text).value)
    r, out = _run_t(tmp_path, text, "--brief", "update to GP")
    assert r.returncode == 2 and not out.exists()
    n = int(line_of(text, "History:")[1:])
    assert _clin(r) == L.NOTE_COPY_REFUSED.format(n=n) or _clin(r) == L.NOT_COPY_REFUSED.format(n=n)
    assert "template" not in r.stderr


def test_r11_recipient_refusal_wins_over_template_error(tmp_path):
    text = tpl(question="Please review.", problems=("cough",), recipient="GP and cardiology",
               actions=("review in 2 weeks, GP",)).replace("Problems:\n- cough", "Problems:\ncough")
    r, out = _run_t(tmp_path, text, "--brief", "update to GP")
    assert r.returncode == 2 and _clin(r) == L.RECIPIENTS_REFUSED.format(n=3)


def test_r11_brief_copy_wins_over_template_error(tmp_path):
    text = tpl(question="Please review.", problems=("cough",)).replace("Problems:\n- cough", "Problems:\ncough")
    r, out = _run_t(tmp_path, text, "--brief", "update to GP, cc cardiology")
    assert r.returncode == 2 and r.stderr.startswith("Refused: copy request in the brief.")


CHECK = [sys.executable, "-m", "check_template"]


def _check(tmp_path, text, *args):
    tmp_path.mkdir(parents=True, exist_ok=True)
    n = tmp_path / "n.txt"; n.write_text(text)
    return subprocess.run(CHECK + [str(n), *args], capture_output=True, text=True, cwd=pathlib.Path(L.__file__).parent)


def test_r11_check_template_pass_exit_0(tmp_path):
    r = _check(tmp_path, (pathlib.Path(L.__file__).parent / "fixtures/template/podiatry_referral.txt").read_text())
    assert r.returncode == 0 and r.stdout.strip() == L.TEMPLATE_OK and r.stderr == ""
    assert sorted(p.name for p in tmp_path.iterdir()) == ["n.txt"]


def test_r11_check_template_fail_exit_2_signed_wording_and_line(tmp_path):
    text = tpl(question="Please review.", problems=("cough",)).replace("Problems:\n- cough", "Problems:\ncough")
    r = _check(tmp_path, text)
    assert r.returncode == 2 and r.stdout == ""
    assert _clin(r) == L.TEMPLATE_REFUSED.format(n=int(line_of(text, "cough")[1:]))


def test_r11_check_template_brief_file_and_copy_first(tmp_path):
    text = tpl(question="Please review.", problems=("cough",)).replace("Problems:\n- cough", "Problems:\ncough")
    b = tmp_path / "b.txt"; tmp_path.mkdir(parents=True, exist_ok=True); b.write_text("update to GP, cc cardiology\n")
    r = _check(tmp_path, text, "--brief", str(b))
    assert r.returncode == 2 and r.stderr.startswith("Refused: copy request in the brief.")
    b.write_text("nonsense\n")
    r = _check(tmp_path, tpl(question="Please review."), "--brief", str(b))
    assert r.returncode == 2 and _clin(r) == L.BRIEF_REFUSED


@pytest.mark.parametrize("mutate", [lambda t: t, lambda t: t.replace("Problems:\n- cough", "Problems:\ncough"),
                                    lambda t: t.replace("Recipients: General practice", "Recipients: GP, cardiology"),
                                    lambda t: t.replace("History:", "History: please send a copy of this letter to her son")])
def test_r11_check_template_agrees_with_letter_command(tmp_path, mutate):
    text = mutate(tpl(question="Please review.", problems=("cough",), actions=("review in 2 weeks, GP",)))
    c = _check(tmp_path / "c", text)
    r, _ = _run_t(tmp_path / "r", text, "--brief", "update to GP")
    if c.returncode == 2:
        assert r.returncode == 2 and _clin(r) == _clin(c)
    else:
        assert c.returncode == 0 and "Refused" not in r.stderr


def test_r11_check_template_shares_the_letters_parser(monkeypatch, capsys, tmp_path):
    import check_template
    assert check_template.check_template_main is L.check_template_main
    calls = []
    real_ft, real_pre = L.from_template, L.pre_template_checks
    monkeypatch.setattr(L, "from_template", lambda t: calls.append("from_template") or real_ft(t))
    monkeypatch.setattr(L, "pre_template_checks", lambda *a: calls.append("pre") or real_pre(*a))
    n = tmp_path / "n.txt"; n.write_text(tpl(question="Please review."))
    assert L.check_template_main([str(n)]) == 0
    assert calls == ["pre", "from_template"]
    calls.clear()
    out = tmp_path / "o"
    assert L.main([str(n), "--brief", "update to GP", "--out", str(out)]) == 0
    assert calls[:2] == ["pre", "from_template"]          # the letter command runs the very same functions, in order


# ---------- round 11 follow-up ----------
@pytest.mark.parametrize("d", ["1930-01-01", "1934-02-28", "1936-02-29", "1937-12-31", "1939-07-15"])
def test_r11f_template_date_accepts_any_valid_date(d):
    conv = L.from_template(tpl(question="Please review.", date=d))
    assert d in conv.splitlines()


@pytest.mark.parametrize("d", ["1937-02-30", "1937-13-01", "1935-2-3", "37-02-01"])
def test_r11f_template_date_invalid_refused(d):
    text = tpl(question="Please review.", date=d)
    with pytest.raises(L.Refused) as e:
        L.from_template(text)
    assert str(e.value) == L.TEMPLATE_REFUSED.format(n=2)


def test_r11f_parsers_take_any_year_tripwire_is_the_only_year():
    assert L.HEAD.match("1936-05-01 seen in clinic").group(1) == "1936-05-01"
    assert L.med_item("aspirin 100 mg daily; stopped 1938-01-02 - bleeding")["sdate"] == "1938-01-02"
    assert L.OUTDATE.search("on 2 May 1935").group(3) == "1935"
    assert L.V_DATE.match("1939-01-01: x")
    src = pathlib.Path(L.__file__).read_text()
    assert src.count('"1930-01-01", "1939-12-31"') == 1 and "SYNTH_YEAR" not in src   # range lives in one place


@pytest.mark.parametrize("brief,inc", [("referral to podiatry", []),
                                       ("referral to podiatry\nInclude: psychiatric history", ["psychiatric history"]),
                                       ("referral to podiatry\nInclude: psychiatric history\n", ["psychiatric history"])])
def test_r11f_brief_one_line_or_two_with_include(tmp_path, brief, inc):
    assert L.read_brief(brief)[:3] == ("referral", "Podiatry", inc)
    notes = (pathlib.Path(L.__file__).parent / "fixtures/template/podiatry_referral.txt").read_text()
    r, out = _run_t(tmp_path / "cli", notes, "--brief", brief)
    assert r.returncode == 0, r.stderr
    b = tmp_path / "b.txt"; b.write_text(brief)
    c = _check(tmp_path / "chk", notes, "--brief", str(b))
    assert c.returncode == 0, c.stderr


def test_r11f_brief_include_on_first_line_or_three_lines_refused():
    for brief in ("Include: psychiatric history\nreferral to podiatry\nupdate to GP", "referral to podiatry\nupdate to GP"):
        with pytest.raises(L.Refused):
            L.read_brief(brief)



@pytest.mark.parametrize("ab", ["MS", "PE", "CP", "RA", "BD", "IV"])
@pytest.mark.parametrize("brief", ["update to GP", "update to patient"])
def test_r11f_multi_meaning_shorthand_flagged_inside_medicine_reason(tmp_path, ab, brief):
    text = tpl(question="Please review.", problems=("review",), meds=(f"amlodipine 5 mg daily reason: {ab}",),
               actions=("review in 2 weeks, GP",))
    r, out = _run_t(tmp_path / ab, text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert f"{ab} {L.FLAG}" in md
    assert any("abbreviation on line " + line_of(text, "amlodipine") in l for l in r.stderr.splitlines())


def test_r11f_unknown_word_in_reason_flagged_single_meaning_exempt(tmp_path):
    text = tpl(question="Please review.", problems=("review",),
               meds=("amlodipine 5 mg daily reason: cx", "metformin 500 mg bd reason: CKD"),
               actions=("review in 2 weeks, GP",))
    r, out = _run_t(tmp_path, text, "--brief", "update to GP")
    md = (out / "letter.md").read_text()
    assert f"cx {L.FLAG}" in md and L.DRUG_FLAG not in md            # cx is on shorthand.txt (ruling 14 Oct): flagged
    assert "chronic kidney disease (CKD)" in md and f"CKD {L.FLAG}" not in md


R11F_GENERICS = ["prednisone", "frusemide", "glyceryl trinitrate", "dabigatran", "ticagrelor", "prasugrel", "clopidogrel",
                 "insulin glargine", "insulin detemir", "insulin aspart", "insulin lispro", "insulin isophane",
                 "semaglutide", "dulaglutide", "liraglutide", "empagliflozin", "dapagliflozin", "sitagliptin", "gliclazide",
                 "tapentadol", "buprenorphine", "fentanyl", "hydromorphone", "methadone", "tramadol", "pregabalin",
                 "gabapentin", "duloxetine", "venlafaxine", "desvenlafaxine", "mirtazapine", "escitalopram", "citalopram",
                 "fluoxetine", "quetiapine", "olanzapine", "risperidone", "aripiprazole", "lithium", "sodium valproate",
                 "lamotrigine", "levetiracetam", "carbamazepine", "spironolactone", "eplerenone", "sacubitril/valsartan",
                 "bisoprolol", "metoprolol", "carvedilol", "candesartan", "irbesartan", "telmisartan", "rosuvastatin",
                 "ezetimibe", "rivaroxaban", "enoxaparin", "pantoprazole", "esomeprazole", "ondansetron", "metoclopramide"]


def test_r11f_sixty_generics_recognised_once():
    assert len(R11F_GENERICS) == 60
    lines = [l.strip().lower() for l in (pathlib.Path(L.__file__).parent / "drugs.txt").read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    for g in R11F_GENERICS:
        assert lines.count(g) == 1, g                                     # present, never duplicated
        assert all(not L.needs_flag(w) for w in re.split(r"[\s/]+", g)), g


@pytest.mark.parametrize("drug", ["frusemide", "glyceryl trinitrate", "insulin glargine", "sacubitril/valsartan",
                                  "tapentadol", "ezetimibe", "furosemide"])
@pytest.mark.parametrize("brief", ["update to GP", "update to patient"])
def test_r11f_generics_kept_as_written_no_flag(tmp_path, drug, brief):
    text = tpl(question="Please review.", problems=("review",), meds=(f"{drug} 10 mg daily",),
               actions=("review in 2 weeks, GP",))
    r, out = _run_t(tmp_path / re.sub(r"\W", "_", drug), text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert f"{drug} 10 mg" in md                                           # exactly as the notes wrote it
    assert ("frusemide" in md) == (drug == "frusemide") and ("furosemide" in md) == (drug == "furosemide")
    lid = line_of(text, drug)
    assert not any(lid in l for l in r.stderr.splitlines()), r.stderr


@pytest.mark.parametrize("part", ["sacubitril", "valsartan"])
def test_r11f_combination_needs_both_parts(part):
    notes = "Patient: SYNTH-7301\n1937-04-05 Clinic.\nMed: sacubitril/valsartan 49/51 mg bd\nPlan: review in 4 weeks\n"
    md = make_letter(notes, "gp-update", "GP")
    assert "sacubitril/valsartan 49/51 mg" in md
    L.verify_letter(md, notes)
    with pytest.raises(L.Refused):
        L.verify_letter(md.replace("sacubitril/valsartan", part), notes)


@pytest.mark.parametrize("audience", [None, "patient"])
def test_r11f_entresto_combination_brand_refused(audience):
    with pytest.raises(L.Refused) as e:
        make_letter(COMBO_NOTES.format(line="Med: Entresto 49/51 mg bd"), "gp-update", "GP", audience=audience)
    assert str(e.value) == COMBO_MSG.format(n=4)



@pytest.mark.parametrize("brief", ["update to patient", "update to GP"])
def test_r11f_gtn_signed_plain_words_no_flag(tmp_path, brief):
    text = tpl(question="Please review.", problems=("angina",), meds=("GTN 400 mcg prn",),
               actions=("review in 2 weeks, GP",))
    r, out = _run_t(tmp_path, text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert "glyceryl trinitrate (GTN) 400 mcg" in md and f"GTN {L.FLAG}" not in md
    assert not any(line_of(text, "GTN") in l for l in r.stderr.splitlines()), r.stderr



@pytest.mark.parametrize("d,ok", [("1930-01-01", True), ("1939-12-31", True), ("1935-06-15", True),
                                  ("1929-12-31", False), ("1940-01-01", False), ("1936-02-30", False)])
def test_r11g_safeguard_accepts_1930s_real_dates_only(d, ok):
    if ok:
        assert L.check_safe(f"SYNTH-1 seen {d}") == "SYNTH-1"
    else:
        with pytest.raises(L.Refused) as e:
            L.check_safe(f"SYNTH-1 seen {d}")
        assert "date outside 1 Jan 1930 to 31 Dec 1939" in str(e.value)


@pytest.mark.parametrize("d", ["1930-01-01", "1939-12-31", "1929-12-31", "1940-01-01"])
def test_r11g_checker_and_letter_command_agree_on_dates(tmp_path, d):
    text = tpl(question="Please review.", problems=("cough",), date=d, actions=("review in 2 weeks, GP",))
    c = _check(tmp_path / "c", text)
    r, _ = _run_t(tmp_path / "r", text, "--brief", "update to GP")
    ok = d.startswith("193")
    assert (c.returncode == 0) == ok and (r.returncode == 0) == ok, (c.stderr, r.stderr)
    if not ok:
        assert _clin(c) == _clin(r) and "1 Jan 1930 to 31 Dec 1939" in c.stderr


def test_r11g_checker_calls_the_same_safeguard(monkeypatch, tmp_path):
    calls = []
    real = L.check_safe
    monkeypatch.setattr(L, "check_safe", lambda t: calls.append(1) or real(t))
    n = tmp_path / "n.txt"; n.write_text(tpl(question="Please review."))
    assert L.check_template_main([str(n)]) == 0 and calls


@pytest.mark.parametrize("ab", ["ms", "pe", "MS", "Pe"])
@pytest.mark.parametrize("brief", ["update to GP", "update to patient"])
def test_r11g_shorthand_flagged_in_reason_whatever_case(tmp_path, ab, brief):
    text = tpl(question="Please review.", problems=("review",), meds=(f"amlodipine 5 mg daily reason: {ab}",),
               actions=("review in 2 weeks, GP",))
    r, out = _run_t(tmp_path / ab, text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    assert f"{ab} {L.FLAG}" in (out / "letter.md").read_text()


@pytest.mark.parametrize("brief", ["update to GP", "update to patient"])
def test_r11g_reflux_in_reason_not_flagged(tmp_path, brief):
    text = tpl(question="Please review.", problems=("review",), meds=("omeprazole 20 mg daily reason: reflux",),
               actions=("review in 2 weeks, GP",))
    r, out = _run_t(tmp_path, text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert "for reflux" in md and f"reflux {L.FLAG}" not in md and L.DRUG_FLAG not in md and L.REASON_FLAG not in md
    assert not any(line_of(text, "omeprazole") in l for l in r.stderr.splitlines()), r.stderr


def test_r11g_reason_flag_rule_unit():
    assert all(L.reason_flag(t) for t in ("ms", "pe", "MS", "QZX"))
    assert not any(L.reason_flag(t) for t in ("reflux", "heartburn")) and L.reason_flag("cx")


# ---- round 11 follow-up 9: off-list frequencies, written from the LOCKED medicine-line syntax only ----
# '- drug dose frequency [route] [max X in 24 hours | max X per <period>] [for N days] [reason: <why>]
#  [; stopped|ceased|withheld <date> - <reason>]'. Each case runs through the letter command AND the checker.
LOCKED_NOTES = ("Patient: SYNTH-9300\nDate: 1934-03-12\nRecipients: General practice\nQuestion: Please review.\n"
                "Problems:\n- rheumatoid arthritis\nHistory:\nMedicines:\n- {med}\nAllergies:\n- No known allergies\n"
                "Results:\nPending:\nActions:\n- review in 2 weeks\n")
LOCKED_FREQ_MEDS = [
    "methotrexate 15 mg every 1 week reason: arthritis",
    "methotrexate 15 mg every 2 weeks reason: arthritis",
    "methotrexate 15 mg every 2 weeks oral reason: arthritis",
    "methotrexate 15 mg every 2 weeks max 15 mg per week reason: arthritis",
    "methotrexate 15 mg every 2 weeks max 15 mg in 24 hours reason: arthritis",
    "methotrexate 15 mg every 2 weeks for 28 days reason: arthritis",
    "methotrexate 15 mg every 2 weeks reason: arthritis; stopped 1934-03-01 - nausea",
    "methotrexate 15 mg every 2 weeks oral max 15 mg per week for 28 days reason: arthritis; ceased 1934-03-01 - nausea",
    "methotrexate 15 mg every 2 weeks subcut reason: arthritis; withheld 1934-03-01 - infection",
    "methotrexate 15 mg every 2 weeks",
    "methotrexate 15 mg every 4 weeks reason: arthritis",
    "methotrexate 15 mg fortnightly reason: arthritis",
    "methotrexate 15 mg weekly reason: arthritis",
    "methotrexate 15 mg once a month reason: arthritis",
    "methotrexate 15 mg every 4 weeks oral max 15 mg per week for 28 days reason: arthritis; stopped 1934-03-01 - nausea",
    "methotrexate 15 mg fortnightly oral for 28 days reason: arthritis",
    "methotrexate 15 mg weekly oral max 15 mg per week reason: arthritis",
    "methotrexate 15 mg once a month oral reason: arthritis; ceased 1934-03-01 - nausea",
]


@pytest.mark.parametrize("med", LOCKED_FREQ_MEDS)
@pytest.mark.parametrize("brief", ["update to GP", "update to patient"])
def test_r11h_locked_off_list_frequency_letter_and_checker(tmp_path, med, brief):
    text = LOCKED_NOTES.format(med=med)
    c = _check(tmp_path / "chk", text)
    assert c.returncode == 0 and c.stdout.strip() == L.TEMPLATE_OK, c.stderr
    r, out = _run_t(tmp_path / "cli", text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    assert "template" not in r.stderr, r.stderr
    freq = re.search(r"mg (every \d+ weeks?|fortnightly|weekly|once a month)", med).group(1)
    md = (out / "letter.md").read_text()
    assert f"methotrexate 15 mg {freq}" in md, md
    lid = line_of(text, "methotrexate")
    assert not any(lid in l for l in r.stderr.splitlines()), r.stderr



# ---- round 11 follow-up 10: two-line brief, Include: straight after the kind-and-reader line ----
PODIATRY_NOTES = ("Patient: SYNTH-9301\nDate: 1938-11-20\nRecipients: Podiatry clinic\n"
                  "Question: Please assess the left forefoot callus.\nProblems:\n- left forefoot callus\n"
                  "History: bipolar disorder, HIV on antiretrovirals. Painful callus left forefoot.\nMedicines:\n"
                  "- lithium 400 mg nocte reason: bipolar disorder\nAllergies:\n- No known allergies\nResults:\n"
                  "Pending:\nActions:\n- review in 4 weeks\n")
TWO_LINE_BRIEFS = [(f"referral to podiatry\n{blank}{word} {items}", inc)
                   for blank in ("", "\n")
                   for word in ("Include:", "include:", "INCLUDE:")
                   for items, inc in (("psychiatric history", ["psychiatric history"]),
                                      ("psychiatric history, HIV status", ["psychiatric history", "HIV status"]),
                                      ("psychiatric history and HIV status", ["psychiatric history", "HIV status"]))]


@pytest.mark.parametrize("brief,inc", TWO_LINE_BRIEFS)
def test_r11h_two_line_brief_letter_and_checker(tmp_path, brief, inc):
    assert L.read_brief(brief)[:3] == ("referral", "Podiatry", inc)
    b = tmp_path / "b.txt"; b.write_text(brief)
    c = _check(tmp_path / "chk", PODIATRY_NOTES, "--brief", str(b))
    assert c.returncode == 0 and c.stdout.strip() == L.TEMPLATE_OK, c.stderr
    for how, arg in (("text", brief), ("file", str(b))):
        r, out = _run_t(tmp_path / how, PODIATRY_NOTES, "--brief", arg)
        assert r.returncode == 0, (how, r.stderr)
        for item in inc:
            assert f"Included by the brief: " in r.stdout + r.stderr and item in r.stdout + r.stderr



# ---- round 11 follow-up 11: Ms before a name and ms after a number are not shorthand (ruling 14 Oct 2026) ----
def _ctx_notes(recipient="General practice", question="Please review.", result="ECG normal", reason="arthritis",
               history=""):
    return ("Patient: SYNTH-9302\nDate: 1936-08-05\nRecipients: " + recipient + "\nQuestion: " + question +
            "\nProblems:\n- palpitations\nHistory: " + history + "\nMedicines:\n- methotrexate 15 mg weekly reason: "
            + reason + "\nAllergies:\n- No known allergies\nResults:\n- " + result +
            " 1936-08-05\nPending:\nActions:\n- review in 2 weeks\n")


CTX = {"recipients": dict(recipient="Ms Brown"),
       "body": dict(question="Ms Brown asks about the QTc 480 ms result.", history="Seen with Ms Brown. QTc 480 ms."),
       "results": dict(result="QTc 480 ms, reviewed with Ms Brown"),
       "reason": dict(reason="QTc 480 ms per Ms Brown")}


@pytest.mark.parametrize("where", list(CTX))
@pytest.mark.parametrize("brief", ["update to GP", "update to patient"])
def test_r11i_title_and_milliseconds_not_flagged(tmp_path, where, brief):
    text = _ctx_notes(**CTX[where])
    c = _check(tmp_path / "chk", text)
    assert c.returncode == 0, c.stderr
    r, out = _run_t(tmp_path / "cli", text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert f"Ms {L.FLAG}" not in md and f"ms {L.FLAG}" not in md, md
    assert "Ms Brown" in md or where == "recipients"


@pytest.mark.parametrize("where,kw", [("body", dict(history="Seen for ms.")), ("body", dict(history="Seen for pe.")),
                                      ("reason", dict(reason="ms")), ("reason", dict(reason="pe")),
                                      ("reason", dict(reason="Ms")), ("reason", dict(reason="cx"))])
def test_r11i_shorthand_without_context_still_flagged(tmp_path, where, kw):
    r, out = _run_t(tmp_path, _ctx_notes(**kw), "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    assert L.FLAG in (out / "letter.md").read_text()


def test_r11i_whole_words_only_and_context_unit():
    assert L.flagged_tokens("Ms Brown, interval 480 ms, 480ms") == []
    assert L.flagged_tokens("status permit crawl rapid excess") == []                 # ms, pe, cp, ra, cx only as whole words
    assert L.flagged_tokens("ms noted") == ["ms"] and L.flagged_tokens("Ms noted") == ["Ms"]


# ---- round 11 follow-up 12: signed route list (ruling 14 Oct 2026), written from the rule ----
# Signed routes are kept exactly as written in the route slot of a medicine line and never get the shorthand flag
# there; any other word in that slot is flagged, not refused; patient letters add the signed plain words.
ROUTE_NOTES = ("Patient: SYNTH-9310\nDate: 1934-03-12\nRecipients: General practice\nQuestion: Please review.\n"
               "Problems:\n- rheumatoid arthritis\nHistory: {history}\nMedicines:\n- {med}\nAllergies:\n"
               "- No known allergies\nResults:\n- {result} 1934-03-12\nPending:\nActions:\n- review in 2 weeks\n")
SIGNED_ROUTES = [("oral", "by mouth"), ("IV", "into a vein"), ("IM", "into a muscle"), ("subcut", "under the skin"),
                 ("SC", "under the skin"), ("SL", "under the tongue"), ("inhaled", "breathed in"),
                 ("nebulised", "breathed in as a mist"), ("topical", "on the skin"),
                 ("transdermal", "as a patch on the skin"), ("PR", "into the back passage"), ("PV", "into the vagina"),
                 ("nasal", "into the nose"), ("eye", "into the eye"), ("ear", "into the ear"),
                 # round 11 signed rulings (reconciles 8a3bccf, which flagged these): plain words in patient letters
                 ("PO", "by mouth"), ("intravenous", "into a vein"), ("intramuscular", "into a muscle"),
                 ("subcutaneous", "under the skin"), ("sublingual", "under the tongue"), ("by mouth", None)]


def _route_notes(med, history="", result="sodium 140 mmol/L"):
    return ROUTE_NOTES.format(med=med, history=history, result=result)


def test_r11j_signed_route_list_matches_ruling():
    assert set(L.ROUTES) == {r for r, _ in SIGNED_ROUTES}
    assert all(L.ROUTE_PLAIN.get(r) == p for r, p in SIGNED_ROUTES)


@pytest.mark.parametrize("route,plain", SIGNED_ROUTES)
@pytest.mark.parametrize("brief", ["update to GP", "update to patient"])
def test_r11j_signed_route_checker_and_letters(tmp_path, route, plain, brief):
    text = _route_notes(f"methotrexate 15 mg every 2 weeks {route} reason: arthritis")
    c = _check(tmp_path / "chk", text)
    assert c.returncode == 0 and c.stdout.strip() == L.TEMPLATE_OK, c.stderr
    r, out = _run_t(tmp_path / "cli", text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    lid = line_of(text, "methotrexate")
    med = next(l for l in md.splitlines() if lid in l and "methotrexate" in l)
    if brief.endswith("patient"):
        want = f"{route} ({plain})" if plain else f"{route}, for arthritis"     # 'by mouth' stays as written
        assert f"methotrexate 15 mg every 2 weeks {want}" in med, med
        assert "((" not in med and "))" not in med and med.count("(") == med.count(")") <= 1, med   # never doubled
    else:
        assert f"methotrexate 15 mg every 2 weeks {route} (for arthritis)" in med, med
    assert "[flag" not in med, med
    assert not any(lid in l for l in r.stderr.splitlines()), r.stderr


@pytest.mark.parametrize("brief,want", [("update to GP", "every 2 weeks SC (for arthritis)"),
                                        ("update to patient", "every 2 weeks SC (under the skin), for arthritis")])
def test_r11j_every_2_weeks_sc_ruled_example(tmp_path, brief, want):
    text = _route_notes("methotrexate 15 mg every 2 weeks SC reason: arthritis")
    assert _check(tmp_path / "chk", text).returncode == 0
    r, out = _run_t(tmp_path / "cli", text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    assert want in (out / "letter.md").read_text()


@pytest.mark.parametrize("route", ["intrathecal", "rectal", "intravitreal"])
@pytest.mark.parametrize("brief", ["update to GP", "update to patient"])
def test_r11j_unknown_route_flagged_not_refused(tmp_path, route, brief):
    text = _route_notes(f"methotrexate 15 mg every 2 weeks {route} reason: arthritis")
    c = _check(tmp_path / "chk", text)
    assert c.returncode == 0 and c.stdout.strip() == L.TEMPLATE_OK, c.stderr
    r, out = _run_t(tmp_path / "cli", text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    lid = line_of(text, "methotrexate")
    assert L.ROUTE_NOTICE.format(lid=lid) in r.stderr
    assert f"every 2 weeks {route} {L.ROUTE_FLAG}" in (out / "letter.md").read_text()
    f = subprocess.run(CLI + [str(tmp_path / "cli" / "n.txt"), "--brief", brief, "--final"],
                       capture_output=True, text=True)
    assert f.returncode != 0                                    # a route flag blocks --final


@pytest.mark.parametrize("where,kw", [("results", dict(result="ECG PR 160 ms")),
                                      ("results", dict(result="IV fluid balance 500 mL")),
                                      ("history", dict(history="Given IV fluids. PR exam normal.")),
                                      ("reason", dict(med="methotrexate 15 mg every 2 weeks SC reason: PR bleed")),
                                      ("reason", dict(med="methotrexate 15 mg every 2 weeks SC reason: IV line"))])
@pytest.mark.parametrize("brief", ["update to GP", "update to patient"])
def test_r11j_route_words_outside_route_slot_still_flagged(tmp_path, where, kw, brief):
    kw.setdefault("med", "methotrexate 15 mg every 2 weeks oral reason: arthritis")
    text = _route_notes(**kw)
    r, out = _run_t(tmp_path, text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert re.search(r"\b(?:PR|IV) \[flag: abbreviation", md), md


def test_r11j_route_slot_only_in_medicine_lines_unit():
    assert L.flagged_tokens("PR 160 ms") == ["PR"] and L.flagged_tokens("IV fluids") == ["IV"]
    assert L.med_item("morphine 2 mg every 4 hours IV max 10 mg in 24 hours reason: pain")["route"] == "IV"
    assert L.med_item("methotrexate 15 mg every 2 weeks reason: arthritis")["route"] is None
    assert L.med_item("methotrexate 15 mg weekly intrathecal reason: arthritis")["route"] == "intrathecal"



# ---- round 11 follow-up 13: the Include: list matches hazard H22 (ruling 14 Oct 2026), written from the rule ----
H22 = ["HIV status", "PrEP", "abuse", "psychiatric history", "Mental Health Act status", "sexual health",
       "pregnancy loss or termination", "genetic results"]
NEW_H22 = {"Mental Health Act status": "Detained under the Mental Health Act in 1930",
           "sexual health": "Chlamydia treated at a sexual health clinic in 1931",
           "pregnancy loss or termination": "Miscarriage in 1932",
           "genetic results": "BRCA2 pathogenic variant on genetic testing in 1933"}
H22_NOTES = ("Patient: SYNTH-9311\nDate: 1938-11-20\nRecipients: Podiatry clinic\n"
             "Question: Please assess the left forefoot callus.\nProblems:\n- left forefoot callus\n"
             "History: {hist}. Painful callus left forefoot.\nMedicines:\n- paracetamol 1 g four times daily oral "
             "reason: pain\nAllergies:\n- No known allergies\nResults:\nPending:\nActions:\n- review in 4 weeks\n")
SENS_MSG = ("Refused: line L{n} of the brief includes an item that isn't on the sensitive-items list. Nothing was written. "
            "Remove the item from the brief, or use one from the list.")


def test_r11k_include_list_is_h22():
    assert list(L.SENSITIVE) == H22
    assert L.SENSITIVE_REFUSED.format(n=2, item="x") == SENS_MSG.format(n=2, item="x")


@pytest.mark.parametrize("item", H22)
@pytest.mark.parametrize("case", [str, str.upper, str.lower])
def test_r11k_every_h22_item_accepted(item, case):
    assert L.read_brief(f"referral to podiatry\nInclude: {case(item)}")[2] == [case(item)]


@pytest.mark.parametrize("item,sentence", list(NEW_H22.items()))
def test_r11k_new_items_cut_by_default(tmp_path, item, sentence):
    text = H22_NOTES.format(hist=sentence)
    r, out = _run_t(tmp_path, text, "--brief", "referral to podiatry")
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert sentence.split()[0] not in md and "1930" not in md and "1931" not in md and "1932" not in md, md
    assert "Cut from this letter" in r.stderr or "cut" in r.stderr.lower()
    f = subprocess.run(CLI + [str(tmp_path / "n.txt"), "--brief", "referral to podiatry", "--final"],
                       capture_output=True, text=True)
    assert f.returncode != 0                                    # a sensitive cut blocks --final, as for the first four


@pytest.mark.parametrize("item,sentence", list(NEW_H22.items()))
def test_r11k_new_items_kept_when_included(tmp_path, item, sentence):
    text = H22_NOTES.format(hist=sentence)
    r, out = _run_t(tmp_path, text, "--brief", f"referral to podiatry\nInclude: {item}")
    assert r.returncode == 0, r.stderr
    assert f"Included by the brief: {item}." in r.stderr
    assert sentence.split()[0] in (out / "letter.md").read_text()


@pytest.mark.parametrize("item,sentence", list(NEW_H22.items()))
def test_r11k_new_items_kept_for_patient(tmp_path, item, sentence):
    r, out = _run_t(tmp_path, H22_NOTES.format(hist=sentence), "--brief", "update to patient")
    assert r.returncode == 0, r.stderr
    assert sentence.split()[0] in (out / "letter.md").read_text()


@pytest.mark.parametrize("brief,n,item", [
    ("referral to podiatry\nInclude: smoking history", 2, "smoking history"),
    ("referral to podiatry\n\nInclude: sexual health, alcohol use", 3, "alcohol use"),
    ("referral to podiatry. Include: family history", 1, "family history"),
    ("referral to podiatry\nInclude: genetic results\nInclude: driving status", 3, "driving status"),
])
def test_r11k_off_list_item_refused_as_an_item_no_echo(tmp_path, brief, n, item):
    text = H22_NOTES.format(hist="No other history")
    want = SENS_MSG.format(n=n, item=item)
    with pytest.raises(Refused) as e:
        L.read_brief(brief)
    assert str(e.value) == want
    r, out = _run_t(tmp_path / "cli", text, "--brief", brief)
    assert r.returncode == 2 and _clin(r) == want and not out.exists()
    b = tmp_path / "b.txt"; b.write_text(brief)
    c = _check(tmp_path / "chk", text, "--brief", str(b))
    assert c.returncode == 2 and _clin(c) == want
    assert item not in r.stderr + c.stderr + str(e.value)                    # never echoes the brief's text


def test_r11k_library_call_unknown_item_gives_no_line():
    with pytest.raises(Refused) as e:
        build(H22_NOTES.format(hist="No other history"), "gp-update", "GP", include=["Mrs Synthname"])
    assert str(e.value) == "Refused: the brief includes an item that isn't on the sensitive-items list. Nothing was written. Remove the item from the brief, or use one from the list."
    assert not re.search(r"\bL\d", str(e.value)) and "Synthname" not in str(e.value)


@pytest.mark.parametrize("brief", ["gibberish", "referral to podiatry\nupdate to GP", "Include: HIV status"])
def test_r11k_unparseable_brief_still_not_understood(tmp_path, brief):
    with pytest.raises(Refused) as e:
        L.read_brief(brief)
    assert str(e.value) == L.BRIEF_REFUSED


# ---------- ruled date refusal (one line, the signed bytes; 15 Oct 10:01), names the line ----------
DATE_LINES = ("Refused: line L{n} of the notes has a date outside 1 Jan 1930 to 31 Dec 1939. Nothing was written. "
              "If these are real notes, delete them and use synthetic data only.",)


@pytest.mark.parametrize("d,ok", [("1929-12-31", False), ("1930-01-01", True), ("1939-12-31", True), ("1940-01-01", False)])
def test_r11h_boundary_dates(d, ok):
    assert (L.first_out_of_range_line(f"SYNTH-1\n\nseen {d}") is None) == ok
    if not ok:
        with pytest.raises(Refused) as e:
            L.check_safe(f"SYNTH-1\n\nseen {d}")
        assert str(e.value).splitlines() == [DATE_LINES[0].format(n=3)]
        assert INCIDENT not in str(e.value)


@pytest.mark.parametrize("d", ["1929-12-31", "1940-01-01"])
def test_r11h_check_template_and_letter_give_the_two_ruled_lines(tmp_path, d):
    text = tpl(question="Please review.", problems=("cough",), date=d, actions=("review in 2 weeks, GP",))
    c = _check(tmp_path / "c", text)
    r, out = _run_t(tmp_path / "r", text, "--brief", "update to GP")
    want = [DATE_LINES[0].format(n=2)]
    assert c.returncode == 2 and c.stderr.strip().splitlines() == want, c.stderr
    assert r.returncode == 2 and want[0] in r.stderr, r.stderr
    assert not (out / "letter.md").exists()


def test_r11h_date_line_counts_blank_lines_and_iso_with_time():
    assert L.first_out_of_range_line("SYNTH-1\n\n\n1937-01-01\n1941-02-03T08:00") == 5
    assert L.first_out_of_range_line("SYNTH-1 31/12/1939 01/01/1930") is None
    assert L.first_out_of_range_line("SYNTH-1\n1/1/1940") == 2


# ---- round 11 ruling: dates with a time joined on, both directions, both forms (same cases as figures) ----
TIMED = [("1937-02-03T08:00", True), ("1937-02-03 08:00", True), ("1941-02-03T08:00", False), ("1941-02-03 08:00", False)]
DATE_TWO_LINES = list(DATE_LINES)                    # one line now: the signed bytes (15 Oct 10:01)


@pytest.mark.parametrize("ts,ok", TIMED)
def test_r11i_safeguard_dates_with_time_joined_on(ts, ok):
    text = f"SYNTH-1\nseen {ts}"
    if ok:
        assert L.check_safe(text) == "SYNTH-1"
    else:
        with pytest.raises(L.Refused) as e:
            L.check_safe(text)
        assert str(e.value).splitlines() == [DATE_TWO_LINES[0].format(n=2)]


@pytest.mark.parametrize("ts,ok", TIMED)
def test_r11i_checker_and_letter_command_dates_with_time(tmp_path, ts, ok):
    text = tpl(question="Please review.", problems=("cough",), actions=("review in 2 weeks, GP",))
    text = text.replace("Question: Please review.", f"Question: Please review. Seen {ts}.")
    n = line_of(text, "Seen ")[1:]
    c = _check(tmp_path / "c", text)
    r, _ = _run_t(tmp_path / "r", text, "--brief", "update to GP")
    if ok:
        assert c.returncode == 0 and r.returncode == 0, (c.stderr, r.stderr)
        assert "If these are real notes" not in c.stderr + r.stderr
    else:
        assert c.returncode != 0 and r.returncode != 0
        assert _clin(c) == _clin(r)
        assert c.stderr.strip().splitlines()[-1:] == [DATE_TWO_LINES[0].format(n=n)]


# ---- round 11 signed rulings, items 2, 4, 5: detection words, sensitive result cut record, --keep-out ----
# Director override: the sensitive-cut flag is printed on screen only, never written into letter.md.
SENS_RES_NOTES = tpl(question="Please review.", problems=("cough",), meds=("paracetamol 1 g four times daily oral reason: pain",),
                     results=("sodium 1937-06-01 140 mmol/L", "chlamydia PCR 1937-06-01 1 index"),
                     actions=("review in 2 weeks",))
SENS_LID = line_of(SENS_RES_NOTES, "chlamydia")
SENS_FLAG = ("Flag: a result on line {lid} was left out as sensitive. If this reader needs it, add it to the brief's "
             "Include: line once the patient agrees. Otherwise confirm it stays out.")


@pytest.mark.parametrize("cat,word", [(c, w) for c, ws in H22_SIGNED_TERMS.items() for w in ws])
@pytest.mark.parametrize("case", [str, str.upper, str.lower])
def test_r11l_signed_detection_words_mark_their_item(cat, word, case):
    assert L.sensitive_cat(f"history: {case(word)} noted") == cat


@pytest.mark.parametrize("text", ["scheduled review next week", "termination of employment", "contract termination",
                                  "CTO noted", "TOP noted", "MHA noted", "abortion", "BRCAX", "chlamydial"])
def test_r11l_bare_scheduled_termination_and_shorthand_never_detect(text):
    assert L.sensitive_cat(text) is None


def test_r11l_cto_and_top_on_shorthand_list():
    assert {"cto", "top"} <= L.SHORTHAND
    assert "CTO" in L.flagged_tokens("CTO in place") and "TOP" in L.flagged_tokens("TOP in 1934")


def test_r11l_scheduled_review_kept_in_gp_letter(tmp_path):
    text = H22_NOTES.format(hist="Scheduled review in 1938. Termination of the old dressing plan")
    r, out = _run_t(tmp_path, text, "--brief", "referral to podiatry")
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert "Scheduled review" in md and "Cut from this letter" not in r.stderr, (md, r.stderr)


def test_r11m_sensitive_result_cut_record_and_flag_on_screen(tmp_path):
    r, out = _run_t(tmp_path, SENS_RES_NOTES, "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    err = r.stderr.splitlines()
    assert f"Cut as sensitive: line {SENS_LID}." in err
    assert SENS_FLAG.format(lid=SENS_LID) in err
    f = subprocess.run(CLI + [str(tmp_path / "n.txt"), "--brief", "update to GP", "--final"], capture_output=True, text=True)
    assert f.returncode == 3 and "--final refused: 1 flag(s)" in f.stderr          # the flag blocks --final


@pytest.mark.parametrize("args", [(), ("--keep-out", SENS_LID), ("--not-copy", SENS_LID)])
def test_r11m_letter_md_has_no_trace_of_sensitive_cut(tmp_path, args):
    r, out = _run_t(tmp_path, SENS_RES_NOTES, "--brief", "update to GP", *args)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    for trace in ("chlamydia", "Chlamydia", "PCR", "index", SENS_LID, "left out as sensitive", "Cut as sensitive",
                  "Include:", "stays out", "Kept out", "flag: something cut"):
        assert trace not in md, trace
    assert sorted(p.name for p in out.iterdir()) == ["letter.md"]


def test_r11n_keep_out_clears_only_that_flag_and_adds_nothing(tmp_path):
    base, _ = _run_t(tmp_path / "a", SENS_RES_NOTES, "--brief", "update to GP")
    r, out = _run_t(tmp_path / "b", SENS_RES_NOTES, "--brief", "update to GP", "--keep-out", SENS_LID)
    assert r.returncode == 0, r.stderr
    assert f"Kept out by the clinician: line {SENS_LID}." in r.stderr.splitlines()
    assert SENS_FLAG.format(lid=SENS_LID) not in r.stderr
    assert (out / "letter.md").read_text() == (tmp_path / "a" / "o" / "letter.md").read_text()   # adds nothing
    assert [l for l in base.stderr.splitlines() if l != SENS_FLAG.format(lid=SENS_LID)] == \
           [l for l in r.stderr.splitlines() if not l.startswith("Kept out by the clinician")]   # other flags unchanged
    f = subprocess.run(CLI + [str(tmp_path / "b" / "n.txt"), "--brief", "update to GP", "--keep-out", SENS_LID, "--final"],
                       capture_output=True, text=True)
    assert f.returncode == 0, f.stderr


@pytest.mark.parametrize("which", ["sodium", "paracetamol", "Question"])
def test_r11n_keep_out_on_line_without_sensitive_cut_refused(tmp_path, which):
    lid = line_of(SENS_RES_NOTES, which)
    msg = f"Refused: line {lid} has nothing marked as sensitive, so `--keep-out` can't be used on it. Nothing was written. Check the line number, then run again."
    r, out = _run_t(tmp_path / "r", SENS_RES_NOTES, "--brief", "update to GP", "--keep-out", lid)
    assert r.returncode == 2 and _clin(r) == msg and not out.exists() and _hand_ok(r)
    c = _check(tmp_path / "c", SENS_RES_NOTES, "--keep-out", lid)
    assert c.returncode == 2 and _clin(c) == msg


def test_r11n_keep_out_refused_when_brief_includes_or_patient(tmp_path):
    msg = f"Refused: line {SENS_LID} has nothing marked as sensitive, so `--keep-out` can't be used on it. Nothing was written. Check the line number, then run again."
    for brief in ("update to GP\nInclude: sexual health", "update to patient"):
        r, out = _run_t(tmp_path / brief[-3:], SENS_RES_NOTES, "--brief", brief, "--keep-out", SENS_LID)
        assert r.returncode == 2 and _clin(r) == msg and not out.exists()
        b = tmp_path / f"{brief[-3:]}.txt"; b.write_text(brief)
        c = _check(tmp_path / f"c{brief[-3:]}", SENS_RES_NOTES, "--brief", str(b), "--keep-out", SENS_LID)
        assert c.returncode == 2 and _clin(c) == msg


def test_r11n_checker_accepts_keep_out_on_sensitive_cut(tmp_path):
    c = _check(tmp_path, SENS_RES_NOTES, "--keep-out", SENS_LID)
    assert c.returncode == 0 and c.stdout.strip() == L.TEMPLATE_OK, c.stderr


def test_r11n_not_copy_never_clears_sensitive_flag(tmp_path):
    r, _ = _run_t(tmp_path, SENS_RES_NOTES, "--brief", "update to GP", "--not-copy", SENS_LID)
    assert SENS_FLAG.format(lid=SENS_LID) in r.stderr and "Kept out by the clinician" not in r.stderr


def test_r11n_keep_out_format_checked_in_letters_and_checker(tmp_path):
    msg = "Refused: --keep-out takes one line, like L3. Nothing was written."
    r, out = _run_t(tmp_path / "r", SENS_RES_NOTES, "--brief", "update to GP", "--keep-out", "13")
    c = _check(tmp_path / "c", SENS_RES_NOTES, "--keep-out", "13")
    assert r.returncode == 2 and _clin(r) == msg and not out.exists()
    assert c.returncode == 2 and _clin(c) == msg


def test_r11n_sensitive_result_included_by_brief_has_no_cut(tmp_path):
    r, out = _run_t(tmp_path, SENS_RES_NOTES, "--brief", "update to GP\nInclude: sexual health")
    assert r.returncode == 0, r.stderr
    assert "chlamydia" in (out / "letter.md").read_text().lower() and "Cut as sensitive" not in r.stderr


def test_r11n_result_missing_without_cut_record_still_refused_h03():
    """Free-text notes: a lost result with no cut record is refused (H03 unchanged by the cut record)."""
    md = _letter([("Current medicines", ["alphastatin 2 mg nocte [L5]", "betamibe 10 mg daily [L6]"]),
                  ("Allergies", ["zorbamycin: nausea + vomiting [L2]"])])
    assert _ck(CK_BLOCK, md) == [L.RESULT_MISSING.format(lid="L3")]


def test_r11n_template_result_missing_without_cut_record_refused_h03():
    text = L.from_template(SENS_RES_NOTES)
    md, _ = build(text, "gp-update", "GP")
    assert "140 mmol/L" in md
    with pytest.raises(Refused, match="a result on line"):
        L.verify_letter(md.replace("140 mmol/L", "mmol/L"), text, "gp", set())


# ---- round 11 signed rulings (a), (b), (d) ----
def test_r11o_include_item_names_exact_for_new_groups():
    new = ["Mental Health Act status", "sexual health", "pregnancy loss or termination", "genetic results"]
    assert list(L.SENSITIVE)[-4:] == new and list(L.H22_ITEMS) == new
    for item in new:
        assert L.read_brief(f"update to GP\nInclude: {item}")[2] == [item]


def test_r11o_patient_letter_cuts_nothing_no_cut_record(tmp_path):
    r, out = _run_t(tmp_path, SENS_RES_NOTES, "--brief", "update to patient")
    assert r.returncode == 0, r.stderr
    assert "chlamydia" in (out / "letter.md").read_text().lower()
    assert "Cut as sensitive" not in r.stderr and "left out as sensitive" not in r.stderr


def test_r11o_sensitive_word_in_medicine_reason_keeps_medicine_rule(tmp_path):
    text = tpl(question="Please review.", problems=("cough",),
               meds=("doxycycline 100 mg twice daily oral reason: chlamydia",), actions=("review in 2 weeks",))
    r, out = _run_t(tmp_path, text, "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert "doxycycline 100 mg" in md and "chlamydia" not in md.lower()          # medicine stays, reason cut
    assert "Cut as sensitive" not in r.stderr and "left out as sensitive" not in r.stderr   # Results: lines only
    assert "Cut from this letter" in r.stderr


# ---- H22 (signed 14 Oct 2026): a medicine reason naming a cut item is dropped with no marker; the medicine flag is
# on screen only. Each of the four new groups and the existing lithium case, GP letter, no Include: line.
MED_REASON_CASES = [
    ("olanzapine", "olanzapine 10 mg nocte oral reason: community treatment order", ["community treatment order"]),
    ("doxycycline", "doxycycline 100 mg twice daily oral reason: chlamydia", ["chlamydia"]),
    ("misoprostol", "misoprostol 400 mcg daily oral reason: miscarriage", ["miscarriage"]),
    ("tamoxifen", "tamoxifen 20 mg daily oral reason: BRCA pathogenic variant", ["BRCA", "pathogenic variant"]),
    ("lithium", "lithium 400 mg nocte oral reason: bipolar disorder", ["bipolar"]),
]


@pytest.mark.parametrize("drug,med,words", MED_REASON_CASES)
def test_h22_cut_medicine_reason_dropped_flag_on_screen_only(tmp_path, drug, med, words):
    text = tpl(question="Please review.", problems=("cough",), meds=(med,), actions=("review in 2 weeks",))
    r, out = _run_t(tmp_path, text, "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert drug in md
    reason = med.split("reason: ")[1]
    for gone in [reason, *words, "reason not included", L.MED_FLAG, "may point to something cut"]:
        assert gone.lower() not in md.lower(), gone
    assert L.MED_NOTICE.format(lid=line_of(text, drug)) in r.stderr.splitlines()
    f = subprocess.run(CLI + [str(tmp_path / "n.txt"), "--brief", "update to GP", "--final"], capture_output=True, text=True)
    assert f.returncode == 3                                     # the screen-only flag still blocks --final


# ---- signed ruling 14 Oct 2026: stopped or changed medicine with a cut reason (H22, H29) ----
STOP_CASES = [
    ("olanzapine", "community treatment order ended", ["community treatment order"]),
    ("doxycycline", "chlamydia treated", ["chlamydia"]),
    ("misoprostol", "miscarriage complete", ["miscarriage"]),
    ("tamoxifen", "BRCA2 pathogenic variant review", ["BRCA", "pathogenic variant"]),
    ("lithium", "bipolar disorder review", ["bipolar"]),
]


@pytest.mark.parametrize("drug,reason,words", STOP_CASES)
def test_h29_cut_stop_reason_dropped_stop_printed_flag_on_screen(tmp_path, drug, reason, words):
    text = tpl(question="Please review.", problems=("cough",), meds=(f"{drug} 10 mg daily; ceased 1937-06-01 - {reason}",),
               actions=("review in 2 weeks",))
    r, out = _run_t(tmp_path, text, "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    lid = line_of(text, drug)
    ch = FLAG_RX.sub("", next(l for l in md.splitlines() if drug in l))
    assert f"{drug} 10 mg daily" in ch and "Stopped" in ch and "1 Jun 1937" in ch and lid in ch       # H29: in full
    assert_no_empty_reason_label(ch)
    for gone in [reason, *words, "reason not included", "Reason for change", L.MED_FLAG, "may point to something cut"]:
        assert gone.lower() not in md.lower(), gone
    assert L.MED_NOTICE.format(lid=lid) in r.stderr.splitlines()


# ---- signed ruling 14 Oct 2026: BRCA with optional space or hyphen and digit, any case ----
@pytest.mark.parametrize("line", ["BRCA", "BRCA1", "BRCA2", "BRCA 1", "BRCA-1", "brca2", "BRCA1/2", "BRCA1/BRCA2"])
def test_r11p_brca_forms_detected_on_their_own_line(line):
    assert L.sensitive_cat(line) == "genetic results"
    assert L._term_re("BRCA").search(line)


@pytest.mark.parametrize("line", ["BRCA1", "BRCA 1", "brca2", "BRCA1/BRCA2"])
def test_r11p_brca_forms_cut_from_gp_letter(tmp_path, line):
    text = H22_NOTES.format(hist=f"{line} carrier")
    r, out = _run_t(tmp_path, text, "--brief", "referral to podiatry")
    assert r.returncode == 0, r.stderr
    assert "brca" not in (out / "letter.md").read_text().lower()


@pytest.mark.parametrize("line", ["BRCAX", "XBRCA1", "BRCA12"])
def test_r11p_brca_not_matched_inside_other_words(line):
    assert L.sensitive_cat(line) is None


# ---- signed ruling 14 Oct 2026: "top" stays flagged in any case ----
@pytest.mark.parametrize("w", ["top", "Top", "TOP", "tOp"])
def test_r11p_top_always_flagged(w):
    assert "top" in L.SHORTHAND and w in L.flagged_tokens(f"{w} of the foot")


EMPTY_LABEL = re.compile(r"\b(?:Reason for change|Reason|Why|Because|Indication)\s*:\s*(?:\[|\.|$)", re.I)


def assert_no_empty_reason_label(line):
    """No reason label left with nothing after it (before the source tag or at the end of the line)."""
    assert not EMPTY_LABEL.search(L.FLAG_ANY.sub("", line)), line
    assert "Reason for change" not in line and not re.search(r"\breason\s*:", line, re.I), line


# ---- signed ruling 14 Oct 2026: changed medicine (dose change) with a cut reason (H22, H29) ----
# The template grammar has no '; changed' part, so this uses the existing change syntax: the same drug written again
# at a later date with a new dose and 'reason: <why>'.
CHANGE_CASES = [
    ("olanzapine", "community treatment order review", ["community treatment order"]),
    ("doxycycline", "chlamydia not cleared", ["chlamydia"]),
    ("misoprostol", "miscarriage incomplete", ["miscarriage"]),
    ("tamoxifen", "BRCA2 pathogenic variant", ["BRCA", "pathogenic variant"]),
    ("lithium", "bipolar disorder relapse", ["bipolar"]),
]


@pytest.mark.parametrize("drug,reason,words", CHANGE_CASES)
def test_h29_cut_change_reason_dropped_change_printed_flag_on_screen(drug, reason, words, monkeypatch):
    # The letter command takes template notes only, so this dated-progress-notes form runs through the library build.
    monkeypatch.setattr(L, "_TEMPLATE", [])          # free-text notes: no template state left by an earlier in-process run
    monkeypatch.setattr(L, "_EMPTY_FIELDS", set())
    text = (f"SYNTH-9400\n1937-06-01 review.\nMed: {drug} 100 mg daily\n1937-06-10 review.\n"
            f"Med: {drug} 200 mg daily reason: {reason}\nQuestion: is she well?\n")
    flags = []
    res = build(text, "gp-update", "GP", flags=flags); md, _ = res
    ch = FLAG_RX.sub("", next(l for l in md.splitlines() if l.startswith("- Changed")))
    assert ch == f"- Changed on 10 Jun 1937: from {drug} 100 mg daily. Now: {drug} 200 mg daily. [L3, L5]"   # H29
    assert_no_empty_reason_label(ch)
    for gone in [reason, *words, "reason not included", "Reason for change", L.MED_FLAG, "may point to something cut"]:
        assert gone.lower() not in md.lower(), gone
    assert "reason not documented" not in md.lower()
    assert L.MED_NOTICE.format(lid="L5") in L.printed_flags(flags, md)                # on screen only
    with pytest.raises(Refused, match="--final refused"):
        L.finalise(res)


@pytest.mark.parametrize("brief", ["update to GP", "update to patient"])
@pytest.mark.parametrize("reason", ["bipolar relapse", "chlamydia not cleared", "dose review"])
def test_r11q_template_changed_tail_refused_never_partly_parsed(tmp_path, brief, reason):
    text = tpl(question="Please review.", problems=("cough",),
               meds=(f"lithium 10 mg daily; changed 1937-03-01 from 5 mg daily - {reason}",), actions=("review in 2 weeks",))
    msg = T_MSG.format(n=line_of(text, "lithium")[1:])
    r, out = _run_t(tmp_path / "r", text, "--brief", brief)
    assert r.returncode == 2 and _clin(r) == msg and not out.exists() and _hand_ok(r)
    c = _check(tmp_path / "c", text)
    assert c.returncode == 2 and _clin(c) == msg and c.stdout == ""
    with pytest.raises(Refused) as e:
        L.from_template(text)
    assert str(e.value) == msg


# ---- signed ruling (round 11): matching lines. A stop or change with no reason written prints exactly the same line
# as one whose reason was cut as sensitive (GP and other clinician letters). On screen only: a missing reason gets the
# non-blocking notice "No reason was written for line Ln."; a cut reason keeps the sensitive flag, which blocks --final.
def _change_notes(drug, reason=None):
    tail = f" reason: {reason}" if reason else ""
    return (f"Patient: SYNTH-9401\n1937-06-01 review.\nMed: {drug} 100 mg daily\n1937-06-10 review.\n"
            f"Med: {drug} 200 mg daily{tail}\nQuestion: is she well?\n")


def _change_build(monkeypatch, drug, reason=None, to="GP", audience=None):
    monkeypatch.setattr(L, "_TEMPLATE", [])
    monkeypatch.setattr(L, "_EMPTY_FIELDS", set())
    flags = []
    _LAST["res"] = build(_change_notes(drug, reason), "gp-update", to, audience=audience, flags=flags); md, _ = _LAST["res"]
    return md, flags, next(l for l in md.splitlines() if l.startswith("- Changed"))


def _kind_notes(kind, drug, reason=None):
    tail = f" (reason: {reason})" if reason else ""
    head = "Patient: SYNTH-9402\nAllergy: nil known\n1937-06-01 review.\n"
    body = {
        "changed": f"Med: {drug} 100 mg daily\n1937-06-10 review.\nMed: {drug} 200 mg daily{tail}\n",
        "stopped": f"Med: ramipril 5 mg daily\nMed: {drug} 100 mg daily\n1937-06-10 review.\nStop: {drug} 100 mg daily{tail}\n",
        "started": f"Admission: chest infection\n1937-06-10 review.\nMed: {drug} 100 mg daily{tail}\n",
        "restarted": (f"Med: {drug} 100 mg daily\n1937-06-05 review.\nStop: {drug} 100 mg daily\n1937-06-10 review.\n"
                      f"Med: {drug} 100 mg daily{tail}\n"),
    }[kind]
    return head + body + "Question: is she well?\n"


def _kind_build(monkeypatch, kind, drug, reason=None):
    monkeypatch.setattr(L, "_TEMPLATE", [])
    monkeypatch.setattr(L, "_EMPTY_FIELDS", set())
    flags = []
    _LAST["res"] = build(_kind_notes(kind, drug, reason), "gp-update", "GP", flags=flags); md, _ = _LAST["res"]
    lines = [l for l in md.splitlines() if l.startswith("- " + kind.capitalize() + " on")]
    assert len(lines) == 1, md
    return md, flags, lines[0]


def _final_form(line):
    return re.sub(r" \[[LR]\d+(?:, [LR]\d+)*\]", "", line)     # exactly what --final does to a line


@pytest.mark.parametrize("kind", ["changed", "stopped", "started", "restarted"])
@pytest.mark.parametrize("drug,reason,words", CHANGE_CASES)
def test_r11r_change_line_missing_reason_identical_to_cut_reason(drug, reason, words, kind, monkeypatch):
    """Draft: the line is compared byte for byte with nothing stripped, and the letter header too (no inline marker
    may differ). Final: the --final form of both lines is byte-identical."""
    cmd, cflags, cut = _kind_build(monkeypatch, kind, drug, reason)
    cres = _LAST["res"]
    mmd, mflags, missing = _kind_build(monkeypatch, kind, drug, None)
    assert missing.encode() == cut.encode()                                         # draft, nothing stripped
    head = lambda md: next(l for l in md.splitlines() if l.startswith("**Letter:**"))
    assert head(mmd).encode() == head(cmd).encode()                                 # no header marker differs
    assert _final_form(missing).encode() == _final_form(cut).encode()               # final
    assert "not documented" not in missing and "Reason for change" not in mmd + cmd
    assert L.FLAG_ANY.sub("", missing).endswith("]") and "reason" not in L.FLAG_ANY.sub("", missing).lower()
    lid = _line_ids(missing)[-1]
    assert L.NO_REASON_NOTICE.format(lid=lid) in L.printed_flags(mflags, mmd)       # screen only
    assert L.MED_NOTICE.format(lid=lid) in L.printed_flags(cflags, cmd)
    with pytest.raises(Refused, match="--final refused"):                            # the cut still blocks --final
        L.finalise(cres)


def _line_ids(line):
    return re.findall(r"\b[LR]\d+\b", re.findall(r"\[[LR][^\]]*\]\s*$", line)[-1])


@pytest.mark.parametrize("kind", ["changed", "started", "restarted"])
def test_r11r_missing_reason_final_letter_line_matches_cut_draft_line(kind, monkeypatch):
    _, _, cut = _kind_build(monkeypatch, kind, "ramipril", "BRCA2 pathogenic variant")
    md, flags, missing = _kind_build(monkeypatch, kind, "ramipril", None)
    shown = L.printed_flags(flags, md)
    fin = L.finalise(_LAST["res"])                                             # the notice doesn't block
    assert _final_form(cut) in fin.splitlines() and _final_form(missing) in fin.splitlines()


def _stop_text(drug, reason=None):
    return tpl(question="Please review.", problems=("cough",),
               meds=(f"{drug} 10 mg daily; ceased 1937-06-01" + (f" - {reason}" if reason else ""),),
               actions=("review in 2 weeks",))


@pytest.mark.parametrize("drug,reason,words", STOP_CASES)
def test_r11r_stop_line_missing_reason_identical_to_cut_reason(tmp_path, drug, reason, words):
    """Template notes through the letter command: the whole draft letter.md is byte-identical, nothing stripped."""
    files, lines = [], []
    for name, why in (("cut", reason), ("missing", None)):
        r, out = _run_t(tmp_path / name, _stop_text(drug, why), "--brief", "update to GP")
        assert r.returncode == 0, r.stderr
        raw = (out / "letter.md").read_bytes()
        files.append(raw)
        line = next(l for l in raw.decode().splitlines() if l.startswith("- Stopped"))
        assert "not documented" not in line and "Reason for change" not in raw.decode()
        lines.append(line.encode())
    assert lines[0] == lines[1] and files[0] == files[1]                         # draft, byte for byte
    f, out = _run_t(tmp_path / "final", _stop_text(drug), "--brief", "update to GP", "--final")
    if f.returncode == 0:                                                     # final: the missing-reason letter
        fin = (out / "letter.md").read_text().splitlines()
        assert _final_form(lines[0].decode()) in fin
    else:                                     # held only by another open flag (spelling, linked medicine), never the notice
        assert f.returncode == 3 and any(l.startswith("Flag: ") for l in f.stderr.splitlines()), f.stderr


@pytest.mark.parametrize("drug", ["ramipril", "metformin", "atorvastatin"])
def test_r11r_missing_reason_notice_on_screen_does_not_block_final(tmp_path, drug):
    text = _stop_text(drug)
    lid = line_of(text, drug)
    r, out = _run_t(tmp_path / "d", text, "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    assert L.NO_REASON_NOTICE.format(lid=lid) in r.stderr.splitlines()
    assert "No reason was written" not in (out / "letter.md").read_text()      # screen only
    f, out2 = _run_t(tmp_path / "f", text, "--brief", "update to GP", "--final")
    assert f.returncode == 0, f.stderr                                        # notice does not block --final
    assert "--final refused" not in f.stderr and "[L" not in (out2 / "letter.md").read_text()


def test_r11r_missing_change_reason_notice_does_not_block_final_library(monkeypatch):
    md, flags, _ = _change_build(monkeypatch, "ramipril", None)
    shown = L.printed_flags(flags, md)
    assert L.NO_REASON_NOTICE.format(lid="L5") in shown and "No reason was written" not in md
    screen = sum(f.startswith((L.SENS_RESULT_NOTICE.split("{")[0], L.MED_NOTICE.split("{")[0])) for f in shown)
    assert screen == 0
    assert "[L" not in L.finalise(_LAST["res"])                    # not refused


@pytest.mark.parametrize("drug,reason,words", STOP_CASES)
def test_r11r_cut_reason_flag_still_blocks_final(tmp_path, drug, reason, words):
    text = _stop_text(drug, reason)
    r, out = _run_t(tmp_path, text, "--brief", "update to GP", "--final")
    assert r.returncode == 3 and "--final refused" in r.stderr
    assert L.MED_NOTICE.format(lid=line_of(text, drug)) in r.stderr.splitlines()
    assert "No reason was written" not in r.stderr


def test_r11r_patient_letter_unchanged_missing_reason(monkeypatch):
    md, flags, line = _change_build(monkeypatch, "lithium", None, to="patient", audience="patient")
    assert "Reason for change: not documented." in line
    assert not any(f.startswith("No reason was written") for f in flags)


def test_r11r_allergy_reaction_not_documented_untouched(tmp_path):
    text = tpl(question="Please review.", problems=("cough",), meds=("ramipril 5 mg daily; ceased 1937-06-01",),
               allergies=("penicillin",), actions=("review in 2 weeks",))
    r, out = _run_t(tmp_path, text, "--brief", "update to GP")
    assert r.returncode == 0, r.stderr
    assert "penicillin: reaction not documented" in section((out / "letter.md").read_text(), "Allergies")


# ---- round 11 freeze (Physician + Scientist): matching medicines line, no cut marker in letter.md, unbracketed stop
# reason refused ----
@pytest.mark.parametrize("drug,reason,words", CHANGE_CASES)
def test_r11s_current_medicines_line_missing_reason_identical_to_cut_reason(drug, reason, words, monkeypatch):
    cmd, _, _ = _kind_build(monkeypatch, "started", drug, reason)
    mmd, _, _ = _kind_build(monkeypatch, "started", drug, None)
    line = lambda md: next(l for l in section(md, "Current medicines").splitlines() if drug in l)
    assert line(mmd).encode() == line(cmd).encode()                           # nothing stripped
    assert "reason not documented" not in line(mmd)
    assert mmd.encode() == cmd.encode()                                       # whole draft letter is byte-identical


def test_r11s_patient_current_medicines_line_unchanged(monkeypatch):
    monkeypatch.setattr(L, "_TEMPLATE", [])
    monkeypatch.setattr(L, "_EMPTY_FIELDS", set())
    gp = (HERE / "golden" / "pneumonia_discharge_patient.md").read_text()
    assert "reason not documented" in gp                                     # patient golden keeps its wording


def _all_letters(tmp_path):
    for g in GOLDEN:
        yield (HERE / "golden" / g[-1]).read_text()
    for i, text in enumerate([PSY_T, H22_NOTES.format(hist="BRCA1 carrier")] +
                             [_stop_text(d, r) for d, r, _ in STOP_CASES]):
        r, out = _run_t(tmp_path / f"t{i}", text, "--brief", "update to GP")
        assert r.returncode == 0, r.stderr
        yield (out / "letter.md").read_text()


def test_r11s_no_letter_md_ever_contains_cut_marker(tmp_path, monkeypatch):
    seen = 0
    for md in _all_letters(tmp_path):
        assert L.CUT_FLAG not in md and "something cut to keep it private" not in md
        seen += 1
    for kind in ("changed", "stopped", "started", "restarted"):
        for drug, reason, _ in CHANGE_CASES:                                  # lithium = a linked medicine
            md, _, _ = _kind_build(monkeypatch, kind, drug, reason)
            assert L.CUT_FLAG not in md
            seen += 1
    monkeypatch.setattr(L, "_TEMPLATE", [])
    flags = []
    md, _ = build(PSY, "gp-update", "GP", flags=flags)                         # a history cut
    assert L.CUT_FLAG not in md and CUT_MSG.format("L4") in flags
    assert seen > 20


def test_r11s_cut_flag_on_screen_still_blocks_final(tmp_path):
    r, out = _run_t(tmp_path, PSY_T, "--brief", "update to GP", "--final")
    assert r.returncode == 3 and "--final refused" in r.stderr and not (out / "letter.md").exists()
    assert any(l.startswith("Flag: something on line ") for l in r.stderr.splitlines())


STOP_UNBRACKETED = ["Stop: ramipril 5 mg daily reason: cough", "Stop: ramipril 5 mg daily, reason: cough",
                    "Stop: ramipril 5 mg daily - reason: chlamydia", "Ceased: ramipril 5 mg daily Reason: cough"]


@pytest.mark.parametrize("stop", STOP_UNBRACKETED)
def test_r11s_unbracketed_stop_reason_refused_nothing_written(stop, monkeypatch):
    monkeypatch.setattr(L, "_TEMPLATE", [])
    monkeypatch.setattr(L, "_EMPTY_FIELDS", set())
    text = ("Patient: SYNTH-9403\nAllergy: nil known\n1937-06-01 review.\nMed: metformin 500 mg daily\n"
            f"Med: ramipril 5 mg daily\n1937-06-10 review.\n{stop}\nQuestion: is she well?\n")
    flags = []
    with pytest.raises(Refused) as e:
        build(text, "gp-update", "GP", flags=flags)
    assert str(e.value) == "Refused: line L7 of the notes has a stop reason that isn't in brackets. Nothing was written. Put the stop reason in brackets, then run again."
    assert "cough" not in str(e.value) and "chlamydia" not in str(e.value) and not flags


def test_r11s_bracketed_stop_reason_still_works(monkeypatch):
    monkeypatch.setattr(L, "_TEMPLATE", [])
    monkeypatch.setattr(L, "_EMPTY_FIELDS", set())
    text = ("Patient: SYNTH-9404\nAllergy: nil known\n1937-06-01 review.\nMed: metformin 500 mg daily\n"
            "Med: ramipril 5 mg daily\n1937-06-10 review.\nStop: ramipril 5 mg daily (reason: dry cough)\nQuestion: is she well?\n")
    md, _ = build(text, "gp-update", "GP")
    assert "Stopped on 10 Jun 1937: ramipril 5 mg daily. Reason for change: dry cough." in md


# ---- round 11 freeze, severity 5: finalise() takes only a build result, so screen-only flags travel with the letter ----
def test_r11s_finalise_refuses_raw_md_string(tmp_path):
    res = build(PSY, "gp-update", "GP", flags=[])
    md, _ = res
    for raw in (md, "", "# Letter\nNo flags here.\n"):
        with pytest.raises(Refused) as e:
            L.finalise(raw)
        assert str(e.value) == L.FINAL_RAW_REFUSED and "Nothing was written" in str(e.value)
    assert list(tmp_path.iterdir()) == []                                          # nothing written


def test_r11s_cut_blocks_finalise_until_cleared_by_include():
    flags = []
    res = build_from_template(PSY_T, "gp-update", "GP", flags=flags, resolve=["GP=general practitioner"])
    assert CUT_MSG.format(lid := line_of(PSY_T, "bipolar")) in flags and L.CUT_FLAG not in res.md
    with pytest.raises(Refused, match="--final refused"):
        L.finalise(res)
    ok = build_from_template(PSY_T, "gp-update", "GP", flags=[], include=["psychiatric history"],
                             resolve=["GP=general practitioner"])          # unrelated abbreviation flag closed too
    assert "bipolar" in ok.md and not any("cut to keep it private" in f for f in ok.flags)
    fin = L.finalise(ok)
    assert "bipolar" in fin and "[L" not in fin


def test_r11s_sensitive_cut_blocks_finalise_until_cleared_by_keep_out():
    res = build_from_template(SENS_RES_NOTES, "gp-update", "GP", flags=[])
    assert SENS_FLAG.format(lid=SENS_LID) in res.flags
    with pytest.raises(Refused, match="--final refused"):
        L.finalise(res)
    ok = build_from_template(SENS_RES_NOTES, "gp-update", "GP", flags=[], keep_out=[SENS_LID])
    assert SENS_FLAG.format(lid=SENS_LID) not in ok.flags
    assert "[L" not in L.finalise(ok) and "chlamydia" not in L.finalise(ok).lower()


@pytest.mark.parametrize("notes,lid_of", [(PSY_T, "bipolar"), (SENS_RES_NOTES, "chlamydia")])
def test_r11s_not_copy_does_not_clear_cut_through_library(notes, lid_of):
    res = build_from_template(notes, "gp-update", "GP", flags=[], not_copy=[line_of(notes, lid_of)])
    with pytest.raises(Refused, match="--final refused"):
        L.finalise(res)


# ---- round 11 freeze: tests of already-signed rules (template stop form, H29 patient letters) ----
@pytest.mark.parametrize("med", ["ramipril 5 mg daily stopped reason: dry cough",
                                 "ramipril 5 mg daily stopped 1937-06-01 reason: dry cough",
                                 "ramipril 5 mg daily ceased - dry cough",
                                 "ramipril 5 mg daily withheld",
                                 "ramipril 5 mg daily reason: cough; stopped 1937-06-01 reason: dry cough"])
def test_r11s_template_unbracketed_stop_reason_refused_never_partly_parsed(tmp_path, med):
    text = tpl(question="Please review.", problems=("cough",), meds=(med,), actions=("review in 2 weeks",))
    n = int(line_of(text, "ramipril")[1:])
    with pytest.raises(Refused) as e:
        L.build_from_template(text, "gp-update", "Patient", audience="patient")
    assert str(e.value) == L.TEMPLATE_REFUSED.format(n=n)
    r, out = _run_t(tmp_path, text, "--brief", "update to patient")
    assert r.returncode == 2 and r.stdout == L.BY_HAND_LINES["PV-LETTER-BY-HAND"] + "\n" and _hand_ok(r)
    assert _clin(r) == L.TEMPLATE_REFUSED.format(n=n)
    assert not out.exists()                                                         # nothing written


H29_PATIENT = {
    ("changed", "dry cough"): "- Changed on 10 Jun 1937: from metformin 100 mg once a day. Now: metformin 200 mg once a day. "
                              "Reason for change: dry cough. [L4, L6]",
    ("stopped", "dry cough"): "- Stopped on 10 Jun 1937: metformin 100 mg once a day. Reason for change: dry cough. [L5, L7]",
    ("changed", None): "- Changed on 10 Jun 1937: from metformin 100 mg once a day. Now: metformin 200 mg once a day. "
                       "Reason for change: not documented. [L4, L6]",
    ("stopped", None): "- Stopped on 10 Jun 1937: metformin 100 mg once a day. Reason for change: not documented. [L5, L7]",
}


def _patient_kind(monkeypatch, kind, reason):
    monkeypatch.setattr(L, "_TEMPLATE", [])
    monkeypatch.setattr(L, "_EMPTY_FIELDS", set())
    md, dropped = build(_kind_notes(kind, "metformin", reason), "gp-update", "Patient", audience="patient")
    return md, [l for l in md.splitlines() if l.startswith("- " + kind.capitalize() + " on")]


@pytest.mark.parametrize("kind", ["changed", "stopped"])
def test_r11s_h29_patient_stop_and_change_in_full_plain_words_with_reason(monkeypatch, kind):
    md, lines = _patient_kind(monkeypatch, kind, "dry cough")
    assert lines == [H29_PATIENT[(kind, "dry cough")]]                              # byte for byte, in full
    assert "daily" not in lines[0] and "once a day" in lines[0]                     # plain words


@pytest.mark.parametrize("kind", ["changed", "stopped"])
def test_r11s_h29_patient_no_reason_still_names_stop_and_change_never_dropped(monkeypatch, kind):
    md, lines = _patient_kind(monkeypatch, kind, None)
    assert lines == [H29_PATIENT[(kind, None)]]                                     # still there, in full
    assert "daily" not in lines[0] and "once a day" in lines[0]



# ---------- round 12: inline brief and file brief give identical results (test only; no logic change) ----------
R12_BRIEFS = ["discharge to GP", "letter to the patient", "referral to respiratory",
              "discharge to GP. Include: HIV status", "discharge to GP. Include: astrology",
              "discharge to GP, cc physio", "letter to the patient, copy to her son", "please write something"]
R12_NOTES = {
    "plain": dict(question="Please review.", problems=("review",), meds=("empagliflozin 10 mg daily",),
                  actions=("review in 2 weeks, GP",)),
    "combination": dict(question="Please review.", problems=("review",), meds=("Entresto 49/51 mg bd",),
                        actions=("review in 2 weeks, GP",)),
    "hiv": dict(question="Please review.", problems=("HIV", "review"), meds=("dolutegravir 50 mg daily",),
                actions=("review in 2 weeks, GP",)),
    "note-copy": dict(question="Please review.", problems=("review",), meds=("empagliflozin 10 mg daily",),
                      actions=("review in 2 weeks, GP",), recipient="General practice; cc physiotherapy"),
}


def _r12_cli(tmp_path, tag, text, brief_arg, prog=None):
    d = tmp_path / tag; d.mkdir()
    n = d / "n.txt"; n.write_text(text); out = d / "o"
    cmd = (prog or CLI) + [str(n), "--brief", brief_arg] + ([] if prog else ["--out", str(out)])
    r = subprocess.run(cmd, capture_output=True, text=True)
    written = (out / "letter.md").read_text() if (out / "letter.md").exists() else None
    return r.returncode, r.stderr, r.stdout if prog else None, written


@pytest.mark.parametrize("notes", list(R12_NOTES))
@pytest.mark.parametrize("brief", R12_BRIEFS)
def test_r12_letter_command_inline_and_file_brief_identical(tmp_path, notes, brief):
    text = tpl(**R12_NOTES[notes])
    f = tmp_path / "brief.txt"; f.write_text(brief + "\n", encoding="utf-8")
    assert _r12_cli(tmp_path, "inline", text, brief) == _r12_cli(tmp_path, "file", text, str(f))


@pytest.mark.parametrize("notes", list(R12_NOTES))
@pytest.mark.parametrize("brief", R12_BRIEFS)
def test_r12_check_template_file_brief_matches_inline_parse(tmp_path, notes, brief, monkeypatch, capsys):
    """check_template takes a brief file only; its result must equal the same checks run on the inline brief."""
    text = tpl(**R12_NOTES[notes])
    f = tmp_path / "brief.txt"; f.write_text(brief + "\n", encoding="utf-8")
    n = tmp_path / "n.txt"; n.write_text(text)
    rc = L.check_template_main([str(n), "--brief", str(f)]); got = (rc, capsys.readouterr())
    def inline():
        try:
            kind, reader, include, copy = L.read_brief(brief)
            L.pre_template_checks(text, None, copy, L.is_patient_letter(reader, None))
            L.check_safe(L.from_template(text))
        except L.Refused as e:
            return 2, str(e)
        return 0, L.TEMPLATE_OK
    rc2, msg = inline()
    assert got[0] == rc2 and msg in (got[1].err + got[1].out)


@pytest.mark.parametrize("notes", list(R12_NOTES))
@pytest.mark.parametrize("brief", R12_BRIEFS)
def test_r12_library_call_file_and_inline_brief_identical(tmp_path, notes, brief):
    text = tpl(**R12_NOTES[notes])
    f = tmp_path / "brief.txt"; f.write_text(brief + "\n", encoding="utf-8")
    def run(btxt):
        try:
            kind, reader, include, copy = L.read_brief(btxt)
            aud = "patient" if L.is_patient_letter(reader, None) else None
            L.pre_template_checks(text, None, copy, L.is_patient_letter(reader, aud))
            res = L.build_from_template(text, kind, reader, audience=aud, include=include)
            return ("letter", res.md if hasattr(res, "md") else res[0])
        except L.Refused as e:
            return ("refused", str(e))
    assert run(f.read_text(encoding="utf-8")) == run(brief)


# ---------- round 12 bug 2: allowed ordinary clinical words (core list + 'au' locale pack) ----------
def _r12_raw_allowed():
    out = {}
    for p in L.ALLOWED_SHORTHAND_FILES:
        for cls, words in tomllib.loads(p.read_text(encoding="utf-8"))["allowed"].items():
            for w in words:
                out[w] = f"{p.parent.name}/{cls}"
    return out


def test_r12_allowed_list_has_no_detection_word_or_ambiguous_shorthand():
    raw = _r12_raw_allowed()
    banned = L.SHORTHAND | L._detection_words() | {"top", "cto", "ct", "ms", "ng", "pe", "cp", "ra", "iv", "u", "iu", "cc"}
    assert raw and not {w for w in raw if w.lower() in banned}
    assert set(raw) == set(L.ALLOWED_SHORTHAND)                 # the loader dropped nothing: the files are clean
    assert not set(raw) & set(L.load_single_meaning())          # signed expansions stay in single_meaning.toml
    core = {w for w, c in raw.items() if c.startswith("core/")}
    au = {w for w, c in raw.items() if c.startswith("au/")}
    assert not core & au and {"GP", "UEC", "EUC", "FBC"} <= au


def test_r12_loader_drops_banned_entries(tmp_path):
    p = tmp_path / "a.toml"
    p.write_text('[allowed]\nx = ["top", "TOP", "CTO", "HIV", "PrEP", "MS", "FBC"]\n')
    assert L.load_allowed_shorthand([p]) == frozenset({"FBC"})


@pytest.mark.parametrize("tok", ["top", "TOP", "Top", "tOP", "CTO", "cto", "MS", "PE", "IV", "CT"])
def test_r12_ambiguous_shorthand_still_flagged(tok):
    assert L.needs_flag(tok, "", tuple(L.load_single_meaning()))
    assert L.flagged_tokens(f"plan {tok} review")


@pytest.mark.parametrize("tok", ["Gp", "gp", "Fbc", "fbc", "Mri", "mmhg", "uec"])
def test_r12_allowed_words_exact_case_only(tok):
    assert L.needs_flag(tok, "", tuple(L.load_single_meaning()))


@pytest.mark.parametrize("tok", sorted(L.ALLOWED_SHORTHAND))
def test_r12_allowed_word_not_flagged(tok):
    assert not L.needs_flag(tok, "", tuple(L.load_single_meaning()))


R12_ORDINARY = dict(question="Please review blood pressure and kidney function.", problems=("hypertension",),
                    meds=("empagliflozin 10 mg daily",),
                    results=("FBC Hb 132 g/L 1937-08-01", "UEC creatinine 90 micromol/L 1937-08-01",
                             "LFT ALT 20 U/L 1937-08-01", "blood pressure 150/90 mmHg 1937-08-01",
                             ),
                    pending=("TSH, sent 1937-08-01, GP", "CXR, sent 1937-08-01, GP"),
                    actions=("recheck EUC in 3 months, GP", "MRI brain in 3 months, GP"))


def test_r12_ordinary_clinical_words_can_final(tmp_path):
    """Clinician letter: allowed words carry no flag and the letter finalises."""
    text = tpl(**R12_ORDINARY)
    r, out = _run_t(tmp_path, text, "--brief", "discharge to GP", "--final")
    assert r.returncode == 0, r.stderr
    assert "abbreviation" not in r.stderr
    md = (out / "letter.md").read_text()
    for w in ("FBC", "UEC", "LFT", "mmHg", "CXR", "MRI", "TSH", "EUC"):
        assert w in md


def test_r12_allowed_word_without_plain_words_flagged_in_patient_letter(monkeypatch):
    """Adapted after the signed plain words: the allowed list alone never clears the flag in a patient letter;
    only a signed plain-words entry does. Clinician letters are not affected."""
    monkeypatch.setattr(L, "PLAIN_WORDS", {k: v for k, v in L.PLAIN_WORDS.items() if k != "FBC"})
    monkeypatch.setattr(L, "_PATIENT_LETTER", [True])
    assert L.needs_flag("FBC") and not L.needs_flag("UEC")
    monkeypatch.setattr(L, "_PATIENT_LETTER", [])
    assert not L.needs_flag("FBC")


def test_r12_top_still_blocks_final(tmp_path):
    text = tpl(**{**R12_ORDINARY, "actions": ("recheck EUC in 3 months, GP", "MRI brain in 3 months, GP",
                                                 "discuss TOP at review, GP")})
    r, out = _run_t(tmp_path, text, "--brief", "discharge to GP", "--final")
    assert r.returncode == 3 and not (out / "letter.md").exists()


# ---------- round 12 ruling 1 (severity 5): a Results line with no value is never dropped silently ----------
def _r12_res(*results):
    return tpl(question="Please review.", problems=("review",), meds=("empagliflozin 10 mg daily",),
               results=results, actions=("review in 2 weeks, GP",))


@pytest.mark.parametrize("brief", ["discharge to GP", "letter to the patient"])
def test_r12_result_without_value_kept_word_for_word_and_cited(tmp_path, brief):
    text = _r12_res("Hb 132 g/L 1937-08-01", "CXR clear 1937-08-01")
    r, out = _run_t(tmp_path, text, "--brief", brief)
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    lid = line_of(text, "CXR clear")
    assert re.search(rf"^- CXR(?: \(chest X-ray\))? clear 1937-08-01 \[{lid}\]$", md, re.M), md
    assert "Cut from this letter" not in r.stderr


@pytest.mark.parametrize("line", ["HIV negative 1937-08-01", "BRCA1 not detected 1937-08-01"])
def test_r12_sensitive_result_without_value_cut_in_gp_letter(tmp_path, line):
    text = _r12_res("Hb 132 g/L 1937-08-01", line)
    lid = line_of(text, line.split()[0])
    r, out = _run_t(tmp_path, text, "--brief", "discharge to GP")
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert line.split()[0] not in md and lid not in md               # nothing in letter.md
    assert L.SENS_RESULT_NOTICE.format(lid=lid) in r.stderr           # sensitive flag, screen only
    r, out2 = _run_t(tmp_path / "f", text, "--brief", "discharge to GP", "--final")
    assert r.returncode == 3 and not (out2 / "letter.md").exists()   # blocks --final


@pytest.mark.parametrize("line", ["HIV negative 1937-08-01", "BRCA1 not detected 1937-08-01"])
def test_r12_sensitive_result_without_value_kept_in_patient_letter(tmp_path, line):
    text = _r12_res("Hb 132 g/L 1937-08-01", line)
    lid = line_of(text, line.split()[0])
    r, out = _run_t(tmp_path, text, "--brief", "letter to the patient")
    assert r.returncode == 0, r.stderr
    md = FLAG_RX.sub("", (out / "letter.md").read_text())
    assert f"- {line} [{lid}]" in re.sub(r" +", " ", md)
    assert "sensitive" not in r.stderr


@pytest.mark.parametrize("brief", ["discharge to GP", "letter to the patient"])
def test_r12_unparseable_result_flagged(tmp_path, brief):
    text = _r12_res("Hb 132 g/L 1937-08-01", "1937-08-01")
    lid = next(f"L{n}" for n, l in enumerate(text.splitlines(), 1) if l == "- 1937-08-01")
    r, out = _run_t(tmp_path, text, "--brief", brief)
    assert r.returncode == 0 and L.RESULT_UNREAD_NOTICE.format(lid=lid) in r.stderr
    assert f"- 1937-08-01 [{lid}]" in (out / "letter.md").read_text()
    r, out2 = _run_t(tmp_path / "f", text, "--brief", brief, "--final")
    assert r.returncode == 3 and not (out2 / "letter.md").exists()


# ---------- round 12: signed patient-letter plain words (locale/core + locale/au plain_words.toml) ----------
R12_PW = dict(question="Please review.", problems=("review",), meds=("empagliflozin 10 mg daily",),
              results=("TSH 2.1 mU/L 1937-08-01", "CXR clear 1937-08-01", "CXR clear 1937-08-20",
                       "CT clear 1937-08-01", "FBC (full blood count) normal 1937-08-01"),
              actions=("GP to review TSH in 2 weeks, GP",))


def test_r12_plain_words_files_signed_and_on_allowed_list():
    for p in L.PLAIN_WORDS_FILES:
        assert "Signed by clinical reviewer; first set." in p.read_text()
    raw = {}
    for p in L.PLAIN_WORDS_FILES:
        raw.update(tomllib.loads(p.read_text())["plain_words"])
    assert set(raw) == set(L.PLAIN_WORDS) and set(raw) <= L.ALLOWED_SHORTHAND and "CT" not in raw
    assert L.PLAIN_WORDS["GP"] == "your family doctor" and L.PLAIN_WORDS["CXR"] == "chest X-ray"


def test_r12_plain_words_printed_on_every_use_in_patient_letter(tmp_path):
    text = tpl(**R12_PW)
    r, out = _run_t(tmp_path, text, "--brief", "letter to the patient")
    assert r.returncode == 0, r.stderr
    md = (out / "letter.md").read_text()
    assert md.count("CXR (chest X-ray)") == 2 and md.count("CXR") == 2
    assert md.count("GP (your family doctor)") == md.count("GP") >= 2
    assert md.count("TSH (a blood test of thyroid function)") == md.count("TSH") >= 2
    for w in ("CXR", "GP", "TSH"):
        assert f"{w} {L.FLAG}" not in md


def test_r12_plain_words_no_double_explanation(tmp_path):
    text = tpl(**R12_PW)
    r, out = _run_t(tmp_path, text, "--brief", "letter to the patient")
    md = (out / "letter.md").read_text()
    assert "FBC (full blood count) normal" in md and "FBC (full blood count, a blood test" not in md


def test_r12_unlisted_word_and_ct_still_flagged_in_patient_letter(tmp_path):
    text = tpl(**{**R12_PW, "results": R12_PW["results"] + ("ECHO clear 1937-08-01",)})
    r, out = _run_t(tmp_path, text, "--brief", "letter to the patient")
    md = (out / "letter.md").read_text()
    assert f"CT {L.FLAG}" in md and f"ECHO {L.FLAG}" in md
    r, out2 = _run_t(tmp_path / "f", text, "--brief", "letter to the patient", "--final")
    assert r.returncode == 3 and not (out2 / "letter.md").exists()


def test_r12_plain_words_clinician_letter_unchanged(tmp_path):
    text = tpl(**R12_PW)
    r, out = _run_t(tmp_path, text, "--brief", "discharge to GP")
    md = (out / "letter.md").read_text()
    for w, plain in L.PLAIN_WORDS.items():
        assert f"{w} ({plain})" not in md
    assert "- CXR clear 1937-08-01" in md and f"CXR {L.FLAG}" not in md and f"CT {L.FLAG}" in md


# ---------- round 13 item 1: units (locale/core/units.toml) ----------
def _r13(results=(), meds=("empagliflozin 10 mg daily",)):
    return tpl(question="Please review.", problems=("review",), meds=meds, results=results,
               actions=("review in 2 weeks, GP",))


def _r13_run(tmp_path, text, brief, *extra):
    r, out = _run_t(tmp_path, text, "--brief", brief, *extra)
    md = (out / "letter.md").read_text() if (out / "letter.md").exists() else ""
    return r, md


def test_r13_each_word_has_exactly_one_entry():
    files = [L.UNITS_FILE] + L.PLAIN_WORDS_FILES
    seen = {}
    for p in files:
        d = tomllib.loads(p.read_text())
        for w in d.get("units", {}) if p == L.UNITS_FILE else d.get("plain_words", {}):
            seen.setdefault(w, []).append(p.name)
    assert all(len(v) == 1 for v in seen.values()), {k: v for k, v in seen.items() if len(v) > 1}
    for w in ("fL", "pg", "cm", "mm", "kPa", "mmHg"):
        assert w in L.UNIT_WORDING and w not in L.PLAIN_WORDS and w not in L.ALLOWED_SHORTHAND


@pytest.mark.parametrize("brief", ["discharge to GP", "letter to the patient"])
def test_r13_alt_u_per_litre_passes(tmp_path, brief):
    r, md = _r13_run(tmp_path, _r13(("ALT 40 U/L 1937-08-01",)), brief)
    assert r.returncode == 0, r.stderr
    assert "40 U/L" in md and f"U/L {L.FLAG}" not in md
    assert ("40 U/L (units per litre)" in md) == (brief != "discharge to GP")


@pytest.mark.parametrize("s", ["dose 10 U", "dose 10 IU", "IU", "10 cc", "ug", "mU"])
def test_r13_always_flagged_units(s):
    assert L.flagged_tokens(s, tuple(L.load_single_meaning())) or L.DOSE_U.search(s)


@pytest.mark.parametrize("s", ["wait 30 s", "drank 2 L", "Hb 132 g/L", "TSH 2.1 mU/L", "vitamin D 50 IU/L",
                               "pulse 80 bpm", "MCV 90 fL", "mg", "kPa", "insulin 10 units"])
def test_r13_units_pass(s):
    assert not L.flagged_tokens(s, tuple(L.load_single_meaning())) and not L.DOSE_U.search(s)


def test_r13_citation_bracket_never_unit_matched():
    t = "- CXR clear 1937-08-01 [L14]"
    assert L.patient_units(t) == t and not L.UNIT_AFTER_NUMBER.search("on 1937 [L14]")


def test_r13_patient_results_wording_no_double_and_no_brackets_for_percent_and_celsius(tmp_path):
    text = _r13(("Hb 132 g/L (grams per litre) 1937-08-01", "sats 95 % 1937-08-01", "temperature 38 °C 1937-08-01",
                 "vitamin D 50 IU/L 1937-08-01", "TSH 2.1 mU/L 1937-08-01"))
    assert L.patient_units("Hb 132 g/L (grams per litre)") == "Hb 132 g/L (grams per litre)"
    assert L.patient_units("sats 95 %, 38 °C") == "sats 95 %, 38 °C"
    assert L.patient_units("50 IU/L, 2.1 mU/L") == ("50 IU/L (international units per litre), "
                                                     "2.1 mU/L (milliunits per litre)")
    r, md = _r13_run(tmp_path, text, "letter to the patient")
    assert "(grams per litre) (grams per litre)" not in md and "% (" not in md and "°C (" not in md


@pytest.mark.parametrize("brief", ["discharge to GP", "letter to the patient"])
def test_r13_insulin_units_in_full_no_brackets(tmp_path, brief):
    r, md = _r13_run(tmp_path, _r13(meds=("insulin glargine 10 units nocte",)), brief)
    assert r.returncode == 0, r.stderr
    assert "10 units" in md and "units (" not in md and f"units {L.FLAG}" not in md


def test_r13_medicine_lines_get_no_unit_wording_and_same_dose_check(tmp_path):
    """Ruling 1: unit wording in results only; dose checks read the source line, so a medicine line gives the same
    dose-check result with and without plain words."""
    med = "paracetamol 1 g qid max 4 g in 24 hours"
    text = _r13(meds=(med,))
    rp, mdp = _r13_run(tmp_path / "p", text, "letter to the patient")
    rg, mdg = _r13_run(tmp_path / "g", text, "discharge to GP")
    assert rp.returncode == rg.returncode == 0, rp.stderr + rg.stderr
    assert "(grams)" not in mdp
    notes = L.from_template(text)
    for md, aud in ((mdp, "patient"), (mdg, "gp")):
        assert L.verify_letter(md, notes, aud)
        bad = re.sub(r"no more than 4 g in 24 hours|max 4 g in 24 hours", "", md)
        with pytest.raises(L.Refused, match="maximum dose"):
            L.verify_letter(bad, notes, aud)


@pytest.mark.parametrize("brief", ["discharge to GP", "letter to the patient"])
def test_r13_blood_pressure_kept_together_as_written(tmp_path, brief):
    r, md = _r13_run(tmp_path, _r13(("blood pressure 150/90 mmHg 1937-08-01",)), brief, "--privacy",
                     "true" if "patient" in brief else "false", "--final")   # check 19: explicit privacy field
    assert r.returncode == 0, r.stderr
    assert "150/90 mmHg" in md and "150 /90" not in md
    assert "H-R13-BP" in (HERE / "HAZARD_LOG.md").read_text()


# ---------- round 13 item 2: micro sign U+00B5 and Greek mu U+03BC are the same ----------
@pytest.mark.parametrize("mu", ["\u00b5", "\u03bc"])
@pytest.mark.parametrize("brief", ["discharge to GP", "letter to the patient"])
def test_r13_mu_per_litre_allowed_both_characters(tmp_path, mu, brief):
    r, md = _r13_run(tmp_path, _r13((f"vitamin B12 300 {mu}g/L 1937-08-01",)), brief)
    assert r.returncode == 0, r.stderr
    assert f"300 {mu}g/L" in md and f"{mu}g/L {L.FLAG}" not in md
    assert (f"300 {mu}g/L (micrograms per litre)" in md) == (brief != "discharge to GP")


@pytest.mark.parametrize("dose", ["50 \u00b5g", "50 \u03bcg", "50\u00b5g", "50\u03bcg"])
def test_r13_mu_g_after_number_is_a_dose_flagged(dose):
    assert L.DOSE_U.search(f"Med: thyroxine {dose} daily")


def test_r13_ug_flagged():
    assert "ug" in L.flagged_tokens("levothyroxine 50 ug daily", tuple(L.load_single_meaning()))
    assert L.flagged_tokens("ug", ()) == ["ug"]


# ---------- round 13 item 3: test names (locale/core/test_names.toml) ----------
R13_TN = ("Na 140 mmol/L 1937-08-01", "K 4.1 mmol/L 1937-08-01", "Cr 90 micromol/L 1937-08-01",
          "urea 6.0 mmol/L 1937-08-01", "LDL 2.1 mmol/L 1937-08-01", "ferritin 50 \u00b5g/L 1937-08-01")


def test_r13_test_names_file_clean_and_one_entry_each():
    raw = tomllib.loads(L.TEST_NAMES_FILE.read_text())["test_names"]
    assert set(raw) == set(L.TEST_NAMES) and "Ca" not in raw
    assert not {w for w in raw if w.lower() in L.SHORTHAND | L._detection_words()}
    others = set(L.UNIT_WORDING) | set(L.load_plain_words()) | set(L.load_single_meaning())
    assert not set(raw) & others


def test_r13_test_names_clear_flag_in_clinician_letter(tmp_path):
    r, md = _r13_run(tmp_path, _r13(R13_TN), "discharge to GP", "--final")
    assert r.returncode == 0, r.stderr
    for w in ("Na", "K", "Cr", "urea", "LDL", "ferritin"):
        assert re.search(rf"\b{w}\b", md) and f"{w} (" not in md


def test_r13_test_names_patient_wording_every_use(tmp_path):
    text = _r13(R13_TN + ("Na 138 mmol/L 1937-08-20",))
    r, md = _r13_run(tmp_path, text, "letter to the patient")
    assert r.returncode == 0, r.stderr
    for w, plain in (("Na", "sodium"), ("K", "potassium"), ("Cr", "creatinine, a kidney test"),
                     ("urea", "a kidney test"), ("LDL", '"bad" cholesterol'), ("ferritin", "iron stores")):
        assert f"{w} ({plain})" in md and f"{w} {L.FLAG}" not in md


def test_r13_ca_not_treated_as_calcium_in_gp_letter(tmp_path):
    r, md = _r13_run(tmp_path, _r13(("Ca 2.4 mmol/L 1937-08-01",)), "discharge to GP")
    assert r.returncode == 0, r.stderr
    assert f"Ca {L.FLAG}" in md and "calcium" not in md.lower()


@pytest.mark.parametrize("tok", ["PCR", "PT", "PSA", "Ca"])
def test_r13_unlisted_test_shorthand_stays_flagged(tok):
    assert L.needs_flag(tok, "", tuple(L.load_single_meaning()))


# ---------- round 13: safety-check audit (docs/SAFETY_CHECKS.md) ----------
def test_r13_audit_dose_check_same_with_and_without_plain_words():
    text = _r13(("Hb 132 g/L 1937-08-01", "TSH 2.1 mU/L 1937-08-01"),
                meds=("paracetamol 1 g qid max 4 g in 24 hours",))
    notes = L.from_template(text)
    out = {}
    for aud, to in (("patient", "Patient"), (None, "GP")):
        md = L.build_from_template(text, "discharge", to, audience=aud).md
        ok = L.verify_letter(md, notes, aud or "gp")
        bad = re.sub(r"no more than 4 g in 24 hours|max 4 g in 24 hours", "", md)
        got = []
        L.verify_letter(bad, notes, aud or "gp", collect=got)
        out[aud] = (ok, [g for g in got if "maximum dose" in g])
    assert out["patient"] == out[None] and out[None][1]
    assert "(grams per litre)" in L.build_from_template(text, "discharge", "Patient", audience="patient").md


R13_HIV = _r13(meds=("empagliflozin 10 mg daily",)).replace("- review\n", "- HIV\n- review\n", 1)


def test_r13_audit_leak_check_source_side():
    flags = []
    md = L.build_from_template(R13_HIV, "discharge", "GP", flags=flags).md
    assert "HIV" not in md and any("L6" in f for f in flags)


def test_r13_audit_leak_check_output_side_introduced_leak(monkeypatch):
    real = L.render
    def inject(*a, **k):                         # a leak introduced while writing, not present in the output source
        return real(*a, **k).replace("## Problems\n\n", f"## Problems\n\n- HIV {L.FLAG} [L6]\n", 1)
    monkeypatch.setattr(L, "render", inject)
    flags = []
    md = L.build_from_template(R13_HIV, "discharge", "GP", flags=flags).md
    assert "HIV" not in md and any("L6" in f for f in flags)


def test_r13_audit_leak_refusal_on_finished_letter():
    notes = L.from_template(R13_HIV)
    md = L.build_from_template(R13_HIV, "discharge", "GP").md
    leaked = md.replace("## Problems\n\n", "## Problems\n\n- HIV [L6]\n", 1)
    with pytest.raises(L.Refused):
        L.verify_letter(leaked, notes, "gp")


# ---------- round 13 item 4: pending and action lines (Physician ruling) ----------
R13_PA = dict(question="Please review.", problems=("review",), meds=("empagliflozin 10 mg daily",),
              pending=("FBC, sent 1937-08-01, GP", "XYZ panel, sent 1937-08-02, cardiology"),
              actions=("repeat FBC in 1 week, GP", "do not restart empagliflozin until review, GP",
                       "discuss TOP in 2 weeks, GP"))


@pytest.mark.parametrize("brief", ["discharge to GP", "letter to the patient"])
def test_r13_pending_action_lines_keep_shorthand_check(tmp_path, brief):
    """Ruling: the shorthand check stays on pending and action lines in every letter, clinician letters included."""
    text = tpl(**R13_PA)
    r, md = _r13_run(tmp_path, text, brief)
    assert r.returncode == 0, r.stderr
    assert f"XYZ {L.FLAG}" in md and f"TOP {L.FLAG}" in md
    for frag in ("XYZ", "TOP"):
        assert line_of(text, frag) in r.stderr
    r, out = _run_t(tmp_path / "f", text, "--brief", brief, "--final")
    assert r.returncode == 3 and not (out / "letter.md").exists()


@pytest.mark.parametrize("brief", ["discharge to GP", "letter to the patient"])
def test_r13_pending_action_facts_as_written_in_signed_layout(tmp_path, brief):
    """Ruling: item, owner, timing and negation stay as written; layout is the signed one."""
    text = tpl(**R13_PA)
    r, md = _r13_run(tmp_path, text, brief)
    plain = re.sub(r" \([^()]*\)", "", L.FLAG_ANY.sub("", md))           # drop signed wording brackets and flags
    plain = re.sub(r" +", " ", plain)
    assert f"- FBC, sent on 1 Aug 1937. Who follows up: GP. [{line_of(text, 'FBC, sent')}]" in plain
    assert f"- XYZ panel, sent on 2 Aug 1937. Who follows up: cardiology. [{line_of(text, 'XYZ')}]" in plain
    assert f"1. Who: GP. When: in 1 week. Action: repeat FBC in 1 week [{line_of(text, 'repeat FBC')}]" in plain
    assert ("2. Who: GP. When: not documented. Action: do not restart empagliflozin until review "
            f"[{line_of(text, 'do not restart')}]") in plain
    assert "3. Who: GP. When: in 2 weeks. Action: discuss TOP in 2 weeks" in plain


def test_r13_pending_action_patient_needs_signed_plain_words(tmp_path):
    """Ruling: in patient letters a flagged term needs a signed plain-word entry: FBC and GP have one; XYZ doesn't."""
    r, md = _r13_run(tmp_path, tpl(**R13_PA), "letter to the patient")
    assert "FBC (full blood count, a blood test that counts your blood cells), sent on 1 Aug 1937" in md
    assert "Who follows up: GP (your family doctor)." in md
    assert f"XYZ {L.FLAG} panel" in md and "XYZ (" not in md


# ---------- round 13 stability: no per-letter state outlives an in-process CLI call ----------
def test_r13_main_leaves_no_template_state(tmp_path):
    n = tmp_path / "n.txt"; n.write_text(tpl(question="Please review."))
    assert L.main([str(n), "--brief", "update to GP", "--out", str(tmp_path / "o")]) == 0
    assert not L._TEMPLATE and not L._EMPTY_FIELDS and not L._ROUTES and not L._PATIENT_LETTER
    md = make_letter("Patient: SYNTH-7401\n1937-04-05 Clinic.\nProblem: tiredness\nPlan: review in 4 weeks\n",
                     "gp-update", "GP")
    assert "5 Apr 1937" in md


# ---------- check 19: privacy line, explicit privacy field (wording reviewed by Patient, signed by Physician) ----------
R19 = dict(question="Please review.", problems=("review",), meds=("empagliflozin 10 mg daily",),
           actions=("review in 2 weeks, GP",))


def test_c19_privacy_true_line_missing_blocks_final(tmp_path):
    r, out = _run_t(tmp_path, tpl(**R19), "--brief", "discharge to GP", "--privacy", "true", "--final")
    assert r.returncode == 3 and not (out / "letter.md").exists()
    assert r.stderr.strip().splitlines()[-1] == L.PRIVACY_BLOCKED["missing"]


@pytest.mark.parametrize("brief", ["discharge to GP", "letter to the patient"])
def test_c19_privacy_false_passes_unblocked(tmp_path, brief):
    r, out = _run_t(tmp_path, tpl(**R19), "--brief", brief, "--privacy", "false", "--final")
    assert r.returncode == 0, r.stderr
    assert not any(v in r.stderr for v in L.PRIVACY_BLOCKED.values())


def test_c19_privacy_field_absent_on_patient_letter_blocks_final(tmp_path):
    r, out = _run_t(tmp_path, tpl(**R19), "--brief", "letter to the patient", "--final")
    assert r.returncode == 3 and not (out / "letter.md").exists()
    assert _clin(r).splitlines()[-1] == L.PRIVACY_BLOCKED["unset"] and _hand_ok(r)
    r, out = _run_t(tmp_path / "t", tpl(**R19), "--brief", "letter to the patient", "--privacy", "true", "--final")
    assert r.returncode == 0, r.stderr                      # the patient letter carries the line: true passes


def test_c19_privacy_never_inferred_from_notes():
    text = tpl(**{**R19, "problems": ("review", "privacy: false")})
    res = L.build_from_template(text, "gp-update", "Patient", audience="patient")
    assert res.privacy is None
    with pytest.raises(L.Refused, match=re.escape(L.PRIVACY_BLOCKED["unset"])):
        L.finalise(res)


# ---------- RULES.md and scripts/rulecheck.py ----------
def test_rulecheck_exits_0():
    r = subprocess.run([sys.executable, str(HERE / "scripts" / "rulecheck.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_rulecheck_fails_on_row_with_missing_test(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "rulecheck.py").write_text((HERE / "scripts" / "rulecheck.py").read_text())
    (tmp_path / "test_x.py").write_text("def test_real():\n    pass\n")
    rows = "".join(f"| {n} | c | Physician | safety | test_real |\n" for n in range(1, 19))
    (tmp_path / "RULES.md").write_text(rows + "| 19 | c | Physician | safety | test_not_there |\n")
    r = subprocess.run([sys.executable, str(tmp_path / "scripts" / "rulecheck.py")], capture_output=True, text=True)
    assert r.returncode == 1 and "rule 19" in r.stdout


# ---------- replay: same input twice gives byte-identical output and screen messages ----------
@pytest.mark.parametrize("brief", ["discharge to GP", "letter to the patient", "referral to cardiology"])
def test_replay_same_input_byte_identical(tmp_path, brief):
    text = tpl(question="Please review.", problems=("review", "HIV"), meds=("empagliflozin 10 mg daily",),
               results=("Hb 132 g/L 1937-08-01", "CXR clear 1937-08-01", "Hb 120", "XYZ 5 mmol/L 1937-08-01"),
               pending=("FBC, sent 1937-08-01, GP",), actions=("review in 2 weeks, GP",))
    runs = []
    for i in range(2):
        r, out = _run_t(tmp_path / f"run{i}", text, "--brief", brief)
        runs.append((r.returncode, r.stdout.encode(), r.stderr.encode(), (out / "letter.md").read_bytes()))
    assert runs[0] == runs[1]
    lib = [L.build_from_template(text, *L.read_brief(brief)[:2]) for _ in range(2)]
    assert lib[0].md.encode() == lib[1].md.encode() and lib[0].flags == lib[1].flags


# ---------- check 14 (cut rule): direct tests, one per behaviour a mutant could break (round 13 step 5) ----------
C14_NOTE = """Patient: SYNTH-9001
1937-06-03 Clinic review.
Problem: ingrown toenail
Med: lithium 250 mg twice daily, with food, clozapine monitoring weekly
Plan: discuss schizophrenia at review
Med: dolutegravir 50 mg daily HIV-positive
Med: lithium 250 mg daily serum lithium checked
Med: Lithicarb 250 mg daily
Med: lithium level 250 mg daily
"""
C14_PSY = ["L99: psychiatric history linked words, not relevant to Podiatry"]
C14_PART = L.MED_PART_NOTICE.format(lid="{}")
C14_MED = L.MED_NOTICE.format(lid="{}")


def _c14(md, dropped=C14_PSY, title="Update"):
    flags = []
    return L.apply_cut_rule(md, C14_NOTE, dropped, flags, title), flags


def test_c14_med_line_keeps_plain_parts_drops_linked_part_and_names_the_medicine_line():
    out, flags = _c14("**Letter:** Update\n- lithium 250 mg twice daily, with food, clozapine monitoring weekly [L3, L4]")
    assert out == "**Letter:** Update\n- lithium 250 mg twice daily, with food [flag: part of medicine entry left out] [L3, L4]"
    assert flags == [C14_PART.format("L4"), C14_MED.format("L4")]      # the medicine line, never the first cited id


def test_c14_longest_detection_word_is_removed_whole():
    out, flags = _c14("- dolutegravir 50 mg daily HIV-positive [L3, L6]", ["L99: HIV status linked words, not relevant to GP"])
    assert out == "- dolutegravir 50 mg daily [flag: part of medicine entry left out] [L3, L6]"
    assert flags == [C14_PART.format("L6"), C14_MED.format("L6")]


def test_c14_flag_already_on_the_medicine_stays_when_the_term_is_matched_directly():
    out, _ = _c14("- lithium 250 mg daily serum lithium [flag: abbreviation], checked [L3, L7]")
    assert out == "- lithium 250 mg daily [flag: abbreviation], checked [flag: part of medicine entry left out] [L3, L7]"


def test_c14_medicine_that_would_disappear_is_refused_with_the_medicine_line():
    with pytest.raises(Refused) as e:
        _c14("- lithium level 250 mg daily [L3, L9]")
    assert str(e.value) == L.LINKED_REFUSED.format(lid="L9")


def test_c14_brand_with_no_generic_is_refused_with_the_line_naming_it(monkeypatch):
    monkeypatch.setattr(L, "_brand_map", lambda items: {"Lithicarb": ""})
    with pytest.raises(Refused) as e:
        _c14("- Lithicarb 250 mg daily [L3, L8]")
    assert str(e.value) == L.LINKED_REFUSED.format(lid="L8")


def test_c14_sentence_left_out_flag_goes_on_the_letter_heading_and_names_the_source_line():
    out, flags = _c14("# Head\n**Letter:** Update\n- Discuss schizophrenia at review. Keep walking. [L3, L5]")
    assert out == "# Head\n**Letter:** Update [flag: sentence left out]\n- Keep walking. [L3, L5]"
    assert flags == [L.LEFT_OUT_NOTICE.format(lid="L5")]


def test_c14_sentence_left_out_with_no_title_flags_the_first_line():
    out, flags = _c14("- Discuss schizophrenia at review. Keep walking. [L3, L5]", title=None)
    assert out == "- Keep walking. [L3, L5] [flag: sentence left out]"
    assert flags == [L.LEFT_OUT_NOTICE.format(lid="L5")]


def test_c14_cut_rule_same_letter_without_a_flags_list():
    md = "# Head\n**Letter:** Update\n- Discuss schizophrenia at review. Keep walking. [L3, L5]"
    assert L.apply_cut_rule(md, C14_NOTE, C14_PSY, None, "Update") == _c14(md)[0]


def test_c14_flag_cuts_without_note_text_flags_every_cut_line():
    flags = []
    assert L.flag_cuts("x", ["L4: HIV status cut", "L6: other note"], flags, "Update") == "x"
    assert flags == [L.CUT_NOTICE.format(lid="L4")]
    assert L.flag_cuts("x", ["L4: HIV status cut"], None, "Update") == "x"


def test_c14_flag_cuts_result_line_gets_its_own_flag_and_keep_out_clears_it(monkeypatch):
    note = "Patient: SYNTH-9002\n1937-06-03 Clinic review.\nResult: HIV negative 1937-06-01\nProblem: cough\n"
    monkeypatch.setattr(L, "_SENS_CUTS", [])
    monkeypatch.setattr(L, "_KEPT_OUT", [])
    flags = []
    L.flag_cuts("x", ["L3: HIV status result, not relevant to GP", "L4: HIV status linked words"], flags, "Update", note)
    assert flags == [L.SENS_RESULT_NOTICE.format(lid="L3"), L.CUT_NOTICE.format(lid="L4")] and L._SENS_CUTS == ["L3"]
    monkeypatch.setattr(L, "_KEEP_OUT", ["L3"])
    flags = []
    L.flag_cuts("x", ["L3: HIV status result, not relevant to GP"], flags, "Update", note)
    assert flags == [] and L._KEPT_OUT == ["L3"]


# ---------- check 6/7 (no-value result lines): direct cumsum tests (round 13 step 5) ----------
C6_NOTE = "Patient: SYNTH-9003\n1937-08-02 Clinic review.\nResult: chest X-ray clear 1937-08-01\nResult: CD4 not done 1937-08-01\n"


def _c6(dropped, include=(), keep=False, monkeypatch=None):
    monkeypatch.setattr(L, "_INCLUDE", set(include))
    st = cumsum(parse(C6_NOTE)[0], "GP", dropped, keep_indications=keep)
    return [r[1] for r in st["results_text"]], dropped


def test_c6_line_already_cut_as_sensitive_is_not_kept_again(monkeypatch):
    kept, dropped = _c6(["L3: HIV status cut (earlier)"], monkeypatch=monkeypatch)
    assert ["L3"] not in kept and dropped == ["L3: HIV status cut (earlier)", "L4: HIV status result, not relevant to GP"]


def test_c6_a_cut_on_another_line_does_not_hide_this_line(monkeypatch):
    kept, _ = _c6(["L9: HIV status cut (earlier)"], monkeypatch=monkeypatch)
    assert kept == [["L3"]]


def test_c6_linked_word_result_cut_unless_included(monkeypatch):
    kept, dropped = _c6([], monkeypatch=monkeypatch)
    assert kept == [["L3"]] and dropped == ["L4: HIV status result, not relevant to GP"]
    kept, dropped = _c6([], include=["hiv status"], monkeypatch=monkeypatch)
    assert kept == [["L3"], ["L4"]] and dropped == []
    kept, dropped = _c6([], keep=True, monkeypatch=monkeypatch)     # patient letter: kept word for word
    assert kept == [["L3"], ["L4"]] and dropped == []


# ---------- checks 2, 3, 4, 8, 9, 12, 13: direct tests for behaviours mutation testing showed untested ----------
def test_c2_combination_refusal_names_the_parsed_line_not_a_comment():
    t = "Patient: SYNTH-9004\n# Biktarvy aside\n1937-06-03 Clinic review.\nMed: Biktarvy 1 tablet daily\n"
    with pytest.raises(Refused) as e:
        L.refuse_combination_brands(t)
    assert str(e.value) == L.COMBO_REFUSED.format(lid="L4")


def test_c3_copy_refusal_defaults_to_clinician_wording():
    for fn in (L.refuse_copies, L.pre_template_checks):
        with pytest.raises(Refused) as e:
            fn("Patient: SYNTH-9008\n", brief="copy to GP")
        assert str(e.value) == L.copy_refusal("in the brief", False)


def test_c3_recipients_line_with_two_readers_refused_before_template_check():
    with pytest.raises(Refused) as e:
        L.pre_template_checks("Patient: SYNTH-9008\nRecipients: GP, Cardiology\n")
    assert str(e.value) == L.RECIPIENTS_REFUSED.format(n=2)
    assert L.pre_template_checks("Patient: SYNTH-9008\nRecipients: Cardiology clinic\n") is not False


C4_TPL = (HERE / "fixtures" / "template" / "chest_pain.txt").read_text()


def test_c4_template_recipients_two_readers_refused():
    with pytest.raises(Refused) as e:
        L.from_template(C4_TPL.replace("Recipients: Cardiology clinic", "Recipients: Cardiology clinic, GP"))
    assert str(e.value) == L.RECIPIENTS_REFUSED.format(n=3)


def test_c4_template_list_header_value_only_nil_known_allergies():
    assert "Allergy: nil known" in L.from_template(C4_TPL.replace("Allergies:\n- penicillin: rash", "Allergies: nil known"))
    for bad in (C4_TPL.replace("Allergies:\n- penicillin: rash", "Allergies: penicillin"),
                C4_TPL.replace("Problems:\n- exertional chest pain", "Problems: nil known")):
        with pytest.raises(Refused) as e:
            L.from_template(bad)
        assert str(e.value).startswith(L.TEMPLATE_REFUSED.split("{")[0])


def test_c8_sensitive_result_lines_default_is_clinician_and_reads_results_only():
    note = C6_NOTE + "PMHx: CD4 count monitored\n"
    assert L.sensitive_result_lines(note) == ["L4"]
    assert L.sensitive_result_lines(note, patient=True) == []


def test_c9_lines_before_the_first_heading_are_not_body_lines():
    assert L.check("- unsourced claim here\n## Plan\n", {}, "referral") is True


def test_c9_patient_route_plain_words_are_sourced():
    t = C4_TPL.replace("- aspirin 100 mg daily", "- aspirin 100 mg daily oral")
    md = L.build_from_template(t, "discharge", "patient", audience="patient", privacy=True, flags=[])[0]
    assert "oral (by mouth)" in md


def test_c9_signed_route_plain_words_are_never_read_as_shorthand(monkeypatch):
    monkeypatch.setitem(L.ROUTE_PLAIN, "oral", "by mouth with SIP")     # signed wording is not re-checked for shorthand
    t = C4_TPL.replace("- aspirin 100 mg daily", "- aspirin 100 mg daily oral")
    md = L.build_from_template(t, "discharge", "patient", audience="patient", privacy=True, flags=[])[0]
    assert "oral (by mouth with SIP)" in md


def test_c12_not_documented_section_refused_once_per_section():
    n = "Patient: SYNTH-9006\n1937-06-03 Clinic review.\nMed: aspirin 100 mg daily\nMed: ramipril 5 mg daily\n" \
        "Allergy: penicillin - rash\nAllergy: sulfa - hives\n"
    md = "**Date:** 3 Jun 1937\n## Current medicines\n- Current medicines not documented.\n## Allergies\n" \
         "- Allergies not documented.\n"
    got = []
    L.verify_letter(md, n, collect=got)
    nd = [g for g in got if "not documented" in g]
    assert nd == [L.SECTION_ND.format(sec="medicines", lid="L3"), L.SECTION_ND.format(sec="allergies", lid="L5")]


def test_c12_cut_result_not_required_in_clinician_letter_but_required_for_patient():
    n = "Patient: SYNTH-9007\n1937-06-03 Clinic review.\nResult: CD4 0.35 ×10^9/L 1937-06-01\nResult: Hb 120 g/L 1937-06-01\n"
    md = "**Date:** 3 Jun 1937\n## Results\n- Hb 120 g/L on 1 Jun 1937 [L4]\n"
    assert L.verify_letter(md, n, "gp") is True
    with pytest.raises(Refused) as e:
        L.verify_letter(md, n, "patient")
    assert str(e.value) == L.RESULT_MISSING.format(lid="L3")


C13_NOTE = "Patient: SYNTH-9005\n1937-06-03 Clinic review.\nPlan: review in 2 weeks\n"


def _c13(line, extra="", pre=""):
    return L.reverse_check(pre + "**Date:** 3 Jun 1937\n" + line, C13_NOTE, extra)


@pytest.mark.parametrize("line", ["- review 4 Jun 1937 [L3]", "- review 1937-06-09 [L3]", "- review in 7 weeks [L3]",
                                  "- review with Smithers [L3]", "- Who follows up: physio. [L3]"])
def test_c13_each_unsourced_kind_refused(line):
    with pytest.raises(Refused) as e:
        _c13(line)
    assert str(e.value) == L.REVERSE_REFUSED.format(n=2)


def test_c13_sourced_line_passes_header_above_date_skipped_and_no_extra_ok():
    assert _c13("- review in 2 weeks [L3]") is None
    assert _c13("- review in 2 weeks [L3]", pre="Smithers clinic 99\n") is None
    assert _c13("- review in 2 weeks [L3]", extra=None) is None


# ---------- check 1: event-id references (SYNTH-EP-<episode>#<event>) beside the one patient id ----------
EV_NOTE = "Patient: SYNTH-1002\n1937-06-03 Clinic review. Event SYNTH-EP-1002#7 noted.\nPlan: review event SYNTH-EP-1002#7 in 2 weeks\n"


def test_c1_event_id_beside_patient_id_is_allowed():
    assert L.check_safe(EV_NOTE) == "SYNTH-1002"


@pytest.mark.parametrize("bad", ["SYNTH-EP-1002", "SYNTH-EP-1002#7x", "SYNTH-EP-1002#", "SYNTH-EP#7", "SYNTH-EP-1002#7#2",
                                 "SYNTH-EP-1002#7-3"])
def test_c1_malformed_event_id_still_counts_as_a_second_identifier(bad):
    with pytest.raises(Refused) as e:
        L.check_safe(EV_NOTE.replace("SYNTH-EP-1002#7 noted", bad + " noted"))
    assert str(e.value) == L.CHECK_SAFE_MULTI_ID


def test_c1_event_id_never_hides_a_second_patient_or_a_missing_one():
    with pytest.raises(Refused, match="more than one patient identifier"):
        L.check_safe(EV_NOTE + "Plan: see SYNTH-2002\n")
    with pytest.raises(Refused, match="no SYNTH- identifier"):
        L.check_safe("1937-06-03 Event SYNTH-EP-1002#7 noted.\n")


@pytest.mark.parametrize("aud", ["gp", "patient"])
def test_c1_no_abbreviation_flag_inside_event_id(aud):
    flags = []
    md = build(EV_NOTE, "gp-update", "GP" if aud == "gp" else "patient", audience=aud, flags=flags, privacy=True)[0]
    assert "Event SYNTH-EP-1002#7 noted." in md and "review event SYNTH-EP-1002#7 in 2 weeks" in md
    assert not any("SYNTH" in f or f.startswith("EP") for f in flags)


def test_c1_in_event_id_exact_bounds():
    s = "Event SYNTH-EP-1002#7 noted EP"
    at = lambda tok, i=0: list(re.finditer(re.escape(tok), s))[i]
    assert L.in_event_id(at("SYNTH")) and L.in_event_id(at("7")) and L.in_event_id(at("EP"))
    assert L.in_event_id(at("SYNTH-EP-1002#7"))
    assert not L.in_event_id(at("Event")) and not L.in_event_id(at("noted")) and not L.in_event_id(at("EP", 1))
    assert not L.in_event_id(at("Event SYNTH")) and not L.in_event_id(at("7 noted"))


# ---------- template frequency 'four times a day' (signed qid patient wording, single_meaning.toml) ----------
def test_template_four_times_a_day_is_signed_frequency():
    assert L.load_single_meaning()["qid"]["patient"] == "four times a day"      # only allowed because it is signed
    assert L.med_item("paracetamol 1 g four times a day oral for 5 days")["freq"] == "four times a day"
    t = C4_TPL.replace("- aspirin 100 mg daily", "- paracetamol 1 g four times a day oral for 5 days")
    pmd = L.build_from_template(t, "discharge", "patient", audience="patient", privacy=True, flags=[])[0]
    gmd = L.build_from_template(t, "discharge", "GP", audience="gp", flags=[])[0]
    assert "paracetamol 1 g four times a day oral (by mouth) for 5 days" in pmd
    assert "paracetamol 1 g four times a day oral for 5 days" in gmd


def test_template_unsigned_a_day_wordings_still_refused():
    for f in ("twice a day", "three times a day", "4 times a day"):
        with pytest.raises(Refused, match="doesn't follow the template"):
            L.from_template(C4_TPL.replace("- aspirin 100 mg daily", f"- paracetamol 1 g {f} oral"))


# ---------- check 14: prefix-overlapping linked terms give the same letter in either list order (Scientist) ----------
def test_c14_prefix_linked_terms_same_letter_and_cuts_in_both_orders(monkeypatch):
    import copy
    base = L.load_linked()
    note = ("Patient: SYNTH-9301\n1937-06-03 Clinic review.\nProblem: ingrown toenail\nPMHx: bipolar disorder\n"
            "Med: lithium 250 mg twice daily heart failure clinic\nPlan: discuss heart failure clinic. Keep walking.\n"
            "Plan: heart check next visit. Rest feet.\nFollow-up: review in 2 weeks\n")
    runs = []
    for terms in (["heart", "heart failure"], ["heart failure", "heart"]):
        d = copy.deepcopy(base)
        d["psychiatric history"]["terms"] = terms + d["psychiatric history"]["terms"]
        monkeypatch.setattr(L, "load_linked", lambda d=d: d)
        flags = []
        md, dropped = build(note, "gp-update", "Podiatry", flags=flags)
        runs.append((md.encode(), json.dumps(dropped).encode(), json.dumps(flags).encode()))
    assert runs[0] == runs[1]
    md = runs[0][0].decode()
    assert "heart" not in md and "Keep walking." in md and "Rest feet." in md and "lithium 250 mg" in md


# ---------- generalisation guard (same as stack): no specialty, ward, discipline or drug name in code ----------
sys.path.insert(0, str(HERE / "scripts"))
import domainscan  # noqa: E402


def test_letters_code_holds_no_domain_literals():
    hits = [h for h in domainscan.scan(HERE) if not h[0].endswith("domainscan.py")]
    assert hits == [], "\n".join(f"{f}:{ln} {k} {w}" for f, ln, k, w in hits)


def test_scanner_catches_a_planted_literal_and_skips_docstrings(tmp_path):
    p = tmp_path / "x.py"
    p.write_text('"""Module about aspirin (prose)."""\nDRUG = "warfarin 5 mg"\nWARD = "ICU"\nok = "edit"\n')
    hits = {(k, w.lower()) for _, _, k, w in domainscan.scan_file(p)}
    assert hits == {("drug", "warfarin"), ("ward", "icu")}


def test_vocabulary_is_data_not_code():
    assert domainscan.TERMS_FILE == HERE / "data" / "domain_terms.toml"
    assert set(domainscan.TERMS) == {"discipline", "specialty", "ward", "drug"}
    assert "medicine" not in domainscan.TERMS["discipline"]     # plain word for medications in letters



# ---------- check 2: combination list in the locale pack; refusal and written-by-hand (Physician and Director) ----------
def test_c2_all_current_combination_brands_in_locale_pack():
    assert L.load_combination() == ("biktarvy", "triumeq", "dovato", "genvoya", "truvada", "descovy", "entresto")
    assert L.COMBINATION_FILE == HERE / "locale" / L.LOCALE / "combination.toml"
    assert set(L.COMBO_BRANDS) <= {t.lower() for t in domainscan.TERMS["drug"]}       # can't come back into code


C2_PACK = 'source = "s"\nsigned_off_by = "Physician"\nsigned_off_on = "2026-10-14"\nbrands = ["truvada"]\n'


@pytest.mark.parametrize("case,pack", [
    ("no file", None),
    ("no list", C2_PACK.replace('brands = ["truvada"]\n', "")),
    ("empty list", C2_PACK.replace('["truvada"]', "[]")),
    ("no source", C2_PACK.replace('source = "s"\n', "")),
    ("blank source", C2_PACK.replace('"s"', '" "')),
    ("no sign-off", C2_PACK.replace('signed_off_by = "Physician"\n', "")),
    ("no sign-off date", C2_PACK.replace('signed_off_on = "2026-10-14"\n', "")),
    ("not toml", "brands = ["),
    ("blank brand only", C2_PACK.replace('["truvada"]', '[" "]')),
    ("non-text brand only", C2_PACK.replace('["truvada"]', "[1]")),
])
def test_c2_letters_refuses_to_run_without_a_signed_combination_list(tmp_path, monkeypatch, case, pack):
    p = tmp_path / "combination.toml"
    if pack is not None:
        p.write_text(pack)
    monkeypatch.setattr(L, "COMBINATION_FILE", p)
    with pytest.raises(Refused) as e:
        make_letter(COMBO_NOTES.format(line="Med: aspirin 100 mg daily"), "gp-update", "GP")
    assert str(e.value) == L.COMBO_PACK_REFUSED


def test_c2_complete_pack_runs(tmp_path, monkeypatch):
    p = tmp_path / "combination.toml"; p.write_text(C2_PACK)
    monkeypatch.setattr(L, "COMBINATION_FILE", p)
    assert "aspirin" in make_letter(COMBO_NOTES.format(line="Med: aspirin 100 mg daily"), "gp-update", "GP")
    with pytest.raises(Refused):
        make_letter(COMBO_NOTES.format(line="Med: Truvada 1 tablet daily"), "gp-update", "GP")


@pytest.mark.parametrize("brand", ["Truvada", "Descovy"])
@pytest.mark.parametrize("aud,to", [("gp", "GP"), ("patient", "patient")])
def test_c2_truvada_descovy_refused_written_by_hand(tmp_path, brand, aud, to):
    notes = tmp_path / "n.txt"; out = tmp_path / "out"
    notes.write_text(tpl(question="Why is she tired?", problems=["tiredness"], meds=[f"{brand} 1 tablet daily"]))
    r = subprocess.run([sys.executable, str(HERE / "letters.py"), str(notes), "--type", "gp-update", "--to", to,
                        "--audience", aud, "--privacy", "true", "--out", str(out)], capture_output=True, text=True)
    screen = r.stdout + r.stderr
    assert r.returncode == 2 and COMBO_MSG.format(n=9) in screen                     # check 2's signed line
    assert L.letter_by_hand(2)["clinician"]["line"] in screen                        # CQ-LETTER-BY-HAND, open
    assert not out.exists() or not any(out.iterdir())                               # no letter made
    assert brand.lower() not in screen.lower()                                       # brand in no output
    with pytest.raises(L.ComboRefused) as e:
        L.refuse_combination_brands(notes.read_text())
    assert e.value.check == 2 and brand.lower() not in str(e.value).lower()


def test_c2_by_hand_lines_match_signed_wording_and_patient_sees_no_details():
    h = L.letter_by_hand(2)
    assert h["patient"] == "Your care team will write this letter for you themselves. You don't need to do anything."
    assert h["clinician"] == {"id": "CQ-LETTER-BY-HAND", "open": True, "line": "For a clinician to write: this letter "
                              "couldn't be made automatically (check 2). It stays open until you write it."}
    low = h["patient"].lower()
    assert not any(w in low for w in ["check", "2", "medicine", "refus"] + list(L.load_combination()) +
                   [c.lower() for c in L.SENSITIVE])
    import paths
    signed = paths.reused_signed() or HERE.parent / "wording" / "forms_orders_lines_SIGNED.md"   # the one source
    rows = dict(re.findall(r"^\| ([A-Z][A-Z0-9-]+) \| (.+?) \|$", signed.read_text(), re.M))  # missing file: fails
    for k, v in L.BY_HAND_LINES.items():
        assert rows[k] == v, k                                              # word for word; fails if the source changes


def test_c2_clinician_entry_stays_open_until_a_letter_is_written_by_hand():
    e = L.letter_by_hand(2)["clinician"]
    assert L.close_letter_by_hand(e, "")["open"] is True
    assert L.close_letter_by_hand(e, "   ")["open"] is True
    assert L.close_letter_by_hand(e, None)["open"] is True
    assert L.close_letter_by_hand(e, "Dear colleague, ...")["open"] is False and e["open"] is True


# ---------- safety_functions.toml lists every safety function (Director's ruling) ----------
import safetyscan  # noqa: E402


def test_safety_functions_listed():
    miss = safetyscan.missing((HERE / "letters.py").read_text(encoding="utf-8"), safetyscan.listed())
    assert miss == {}, "not in safety_functions.toml: " + ", ".join(f"{n} ({', '.join(k)})" for n, k in miss.items())


def test_safety_functions_list_names_real_functions_and_rows():
    import ast
    names = {n.name for n in ast.walk(ast.parse((HERE / "letters.py").read_text())) if isinstance(n, ast.FunctionDef)}
    fs = tomllib.loads((HERE / "safety_functions.toml").read_text())["functions"]
    assert all(f["name"] in names and f["rows"] and all(1 <= r <= 19 for r in f["rows"]) for f in fs)
    assert len({f["name"] for f in fs}) == len(fs)


def test_safety_functions_scan_catches_a_planted_omission(tmp_path):
    src = (HERE / "letters.py").read_text(encoding="utf-8")
    names = safetyscan.listed() - {"privacy_check"}
    assert safetyscan.missing(src, names) == {"privacy_check": ["refuses"]}
    planted = src + '\n\ndef sneaky(x):\n    raise Refused("Refused: x. Nothing was written.")\n'
    assert safetyscan.missing(planted, safetyscan.listed()) == {"sneaky": ["refuses"]}
    planted = src + '\n\ndef opens(x):\n    return {"id": "CQ-LETTER-BY-HAND"}\n\n\ndef writes(p):\n    p.write_text("x")\n'
    assert set(safetyscan.missing(planted, safetyscan.listed())) == {"opens", "writes"}


# ---------- refusal codes: static listing against data/refusal_lines.toml ----------
import refusalcodes  # noqa: E402


def _refusal_registry():
    return {c["key"]: c for c in tomllib.loads((HERE / "data" / "refusal_lines.toml").read_text())["code"]}


def test_refusal_codes_listed():
    code, reg = refusalcodes.codes(), _refusal_registry()
    assert set(code) == set(reg), (sorted(set(code) - set(reg)), sorted(set(reg) - set(code)))
    assert all(reg[k]["text"] == t for k, t in code.items())
    assert all(reg[k]["status"] in ("signed", "held", "signed-earlier") and reg[k]["source"].strip() for k in reg)


def test_combo_pack_line_signed_and_clinician_only():
    """COMBO_PACK_REFUSED was awaiting sign-off; Physician and Educator signed it on 14 Oct 2026 (also used when the
    list lacks source or sign-off). It stays clinician-only: no patient written-by-hand view of its own."""
    reg = _refusal_registry()
    assert reg["COMBO_PACK_REFUSED"]["status"] == "signed" and reg["COMBO_PACK_REFUSED"]["text"] == L.COMBO_PACK_REFUSED \
        == _signed_in_both()["COMBO_PACK_REFUSED"]                          # expected bytes from the signing files
    assert not [c for c in reg.values() if c["status"] not in ("signed", "held", "signed-earlier")]
    assert L.COMBO_PACK_REFUSED not in L.letter_by_hand(2)["patient"]
    assert not issubclass(type(Refused(L.COMBO_PACK_REFUSED)), L.ComboRefused)
    rulings = (HERE / "docs" / "RULINGS.md").read_text()
    assert "COMBO_PACK_REFUSED" in rulings.split("### Signed refusal lines")[1]


def test_refusal_codes_listing_catches_a_new_code():
    src = (HERE / "letters.py").read_text() + '\n\nNEW_REFUSED = "Refused: something new. Nothing was written."\n'
    assert set(refusalcodes.codes(src)) - set(_refusal_registry()) == {"NEW_REFUSED"}


# ---------- whole-list mutation: direct tests (Director ruling, 14 Oct 21:35) ----------
_WL_PSY = """Patient: SYNTH-5401
1937-06-03 Clinic review.
Problem: ingrown toenail
PMHx: bipolar disorder
Med: lithium 250 mg twice daily (for bipolar disorder)
Follow-up: review in 2 weeks
"""
_WL_MED_L5 = L.MED_NOTICE.format(lid="L5")


def test_wl_question_flag_sits_on_the_letter_line():
    md, _ = L.build(_WL_PSY, "gp-update", "Podiatry")
    line = next(x for x in md.split("\n") if L.QUESTION_FLAG in x)
    assert line.startswith("**Letter:**")


def test_wl_build_rejects_unknown_type():
    import pytest
    with pytest.raises(ValueError, match="unknown type"):
        L.build(_WL_PSY, "no-such-type", "GP")


def test_wl_allowed_word_stays_flagged_in_patient_letter_only(monkeypatch):
    monkeypatch.setattr(L, "ALLOWED_SHORTHAND", frozenset(L.ALLOWED_SHORTHAND) | {"ZQX"})
    t = "Patient: SYNTH-5401\n1937-06-03 Clinic review.\nProblem: ingrown toenail\nFollow-up: ZQX in 2 weeks, GP\n"
    for aud, to, want in (("gp", "GP", []), ("patient", "Patient", ["ZQX [L4]"])):
        f = []
        L.build(t, "gp-update", to, audience=aud, flags=f)
        assert [x for x in f if "ZQX" in x] == want


def test_wl_flag_linked_medicines_direct():
    f = []
    md, dropped = L.build(_WL_PSY, "gp-update", "Podiatry", flags=f)
    assert f.count(_WL_MED_L5) == 3            # cut rule twice + linked-medicine check once (screen dedupes)
    g = []
    assert L.flag_linked_medicines(md, _WL_PSY, dropped, g) == md
    assert g == [_WL_MED_L5]
    assert L.flag_linked_medicines(md, _WL_PSY, dropped, None) == md
    h = []
    assert L.flag_linked_medicines(md, _WL_PSY, [], h) == md and h == []


def test_wl_drop_cut_change_reasons():
    nr = f"- Changed: x 1 mg.{L.NO_REASON_CHANGE} [L3]"
    cr = f"- Changed: y 2 mg.{L.CUT_CHANGE_REASON} [L4]"
    f = []
    out = L.drop_cut_change_reasons(nr + "\n" + cr, f)
    assert out == "- Changed: x 1 mg. [L3]\n- Changed: y 2 mg. [L4]"
    assert f == [L.NO_REASON_NOTICE.format(lid="L3"), L.MED_NOTICE.format(lid="L4")]
    assert L.drop_cut_change_reasons(nr + "\n" + cr, None) == out      # no flag list: no crash


def test_wl_printed_flags():
    hdr = "# Letter [flag: abbreviation, meaning not confirmed]"
    assert L.printed_flags([], hdr) == ["Flag: this letter has an open flag. Check it."]
    assert L.printed_flags(["X [L9]"], hdr) == [L.line_only_flag("X [L9]")]
    two = "- a [flag: abbreviation, meaning not confirmed] [L5]\n- b [flag: abbreviation, meaning not confirmed] [L5]"
    assert L.printed_flags([], two) == [L.OPEN_NOTICE.format(lid="L5")]
    multi = "- c [flag: abbreviation, meaning not confirmed] [L3, L4]"
    assert L.printed_flags(["Y [L3]"], multi) == [L.line_only_flag("Y [L3]")]


def test_wl_wording_initial_state_and_flag_format():
    h = L.HEADINGS["gp"]
    f = []
    assert L.wording(["- zorbaxa 10 mg [L1]"], "gp", flags=f) == ["- zorbaxa [flag: abbreviation, meaning not confirmed] 10 mg [L1]"]
    assert f == ["zorbaxa [L1]"]
    f = []
    L.wording(["- insulin 10 u daily [L1]"], "gp", flags=f)
    assert f == ["10 u [L1]"]
    f = []
    L.wording(["- insulin 10 u daily"], "gp", flags=f)
    assert f == ["10 u"]
    assert "grams per litre" not in L.wording(["- Hb 120 g/L [L1]"], "patient")[0]
    f = []
    out = L.wording(["## " + h["meds"], "- Zor 10 mg [L1]", "- Zorbaxa 10 mg [L2]"], "gp", flags=f)
    assert out[1] == "- Zor [flag: abbreviation, meaning not confirmed] 10 mg [L1]"
    assert f == ["Zor [L1]", "Zorbaxa (drug name, check spelling) [L2]"]


def test_wl_cli_requires_notes_type_and_to(tmp_path):
    n = tmp_path / "n.txt"
    n.write_text(_WL_PSY)
    r = subprocess.run(CLI + [str(n)], capture_output=True, text=True)
    assert r.returncode == 2 and "notes, --type and --to are required" in r.stderr


def test_wl_cli_out_makes_nested_folders(tmp_path):
    out = tmp_path / "a" / "b"
    r = subprocess.run(CLI + [str(TPL / "chest_pain.txt"), "--type", "referral", "--to", "Cardiology clinic",
                              "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "SYNTH-1001" in (out / "letter.md").read_text()


def test_wl_cli_template_result_without_date_has_no_letter_date(tmp_path):
    n = tmp_path / "t.txt"
    n.write_text((TPL / "chest_pain.txt").read_text().replace(
        "- LDL 3.2 mmol/L 1937-03-16\n", "- LDL 3.2 mmol/L 1937-03-16\n- Hb 120 g/L\n"))
    r = subprocess.run(CLI + [str(n), "--type", "referral", "--to", "Cardiology clinic"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "- Hb: 120 g/L [L17]" in r.stdout


def _wl_st(notes, to="Rheumatology", **k):
    e, _ = L.parse("Patient: SYNTH-5401\n" + notes)
    return L.cumsum(e, to, **k)


def test_wl_cumsum_first_date_and_defaults():
    st = _wl_st("1937-06-03 Clinic review.\n1937-06-10 Clinic review.\n")
    assert (st["first"], st["last"]) == ("1937-06-03", "1937-06-10")
    e, _ = L.parse("Patient: SYNTH-5401\n1937-06-03 Clinic review.\nMed: lithium 250 mg daily (for bipolar disorder)\n")
    a, b = L.cumsum(e), L.cumsum(e, "")
    assert a["meds"] == b["meds"] and a["dropped"] == b["dropped"] and a["dropped"]   # cut by default


def test_wl_cumsum_empty_body_and_followup_who_only():
    st = _wl_st("1937-06-03 Clinic review.\n.\nReview with the patient\n")
    assert all(b for _, b, _ in st["narrative"])
    assert [b for b, _ in st["followup"]] == ["Review with the patient"]
    assert [b for b, _ in _wl_st("1937-06-03 Clinic review.\nReview in 2 weeks\n")["followup"]] == ["Review in 2 weeks"]


def test_wl_cumsum_negation_pops_only_its_own_problem():
    st = _wl_st("1937-06-03 Clinic review.\nProblem: gout\nNo pneumonia\n")
    assert list(st["problems"]) == ["gout"] and not st["resolved"]
    st = _wl_st("1937-06-03 Clinic review.\nProblem: gout\nNo gout\n")
    assert not st["problems"] and list(st["resolved"]) == ["gout"]


def test_wl_cumsum_reason_vs_indication_and_inheritance():
    st = _wl_st("1937-06-03 Clinic review.\nMed: aspirin 100 mg daily (reason: stroke prevention)\n"
                "Med: allopurinol 100 mg daily (for gout)\n1937-06-10 Clinic review.\n"
                "Med: allopurinol 300 mg daily (for gout)\nMed: aspirin 150 mg daily\n")
    assert st["meds"]["aspirin"]["ind"] is None
    assert st["meds"]["allopurinol"]["ind"] == "gout"
    assert [c["reason"] for c in st["med_changes"]] == [None, None]
    st = _wl_st("1937-06-03 Clinic review.\nMed: allopurinol 100 mg daily (for gout)\n"
                "1937-06-10 Clinic review.\nMed: allopurinol 300 mg daily\n")
    assert st["meds"]["allopurinol"]["ind"] == "gout"


def test_wl_cumsum_cut_reason_and_cut_flag_only_for_own_line():
    base = "1937-06-03 Clinic review.\nMed: allopurinol 100 mg daily\n1937-06-10 Clinic review.\nMed: allopurinol 300 mg daily\n"
    # L5 is the changed medicine line, L3 the earlier one
    for dropped, reason, cut in (([], None, False),
                                 (["L5: indication for allopurinol cut (x, not relevant to y)"], "reason not included", True),
                                 (["L3: indication for allopurinol cut (x, not relevant to y)"], None, False),
                                 (["L5: reason cut"], "reason not included", False),
                                 (["L5: kept (x)"], None, False)):
        st = _wl_st(base, dropped=list(dropped))
        assert st["med_changes"][0]["reason"] == reason, dropped
        assert st["meds"]["allopurinol"]["cut"] is cut, dropped
    st = _wl_st("1937-06-03 Clinic review.\nMed: allopurinol 100 mg daily (for gout)\n",
                dropped=["L3: indication for allopurinol cut (x, not relevant to y)"])
    assert st["meds"]["allopurinol"]["cut"] is False          # an indication is shown: not marked cut


def test_wl_cumsum_stop_reason_only_for_own_cut():
    stop = "1937-06-03 Clinic review.\nStopped: ramipril\n"
    assert _wl_st(stop)["med_changes"][0]["reason"] is None
    assert _wl_st(stop, dropped=["L9: x cut (y)"])["med_changes"][0]["reason"] is None
    assert _wl_st(stop, dropped=["L3: x cut (y)"])["med_changes"][0]["reason"] == "reason not included"


# ---------- refusal lines: room rulings 14 Oct 2026 (Physician SAFETY.md, Educator WORDING.md) ----------
import fillscan  # noqa: E402

SIGNED_DIR = HERE.parent / "wording"
HOSTILE = ("Zebedee", "Quillfeather", "zorvadol", "Quixomab", "Snarkle", "77.7", "0412")
PV = L.BY_HAND_LINES["PV-LETTER-BY-HAND"]


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def _signed_in_both():
    """{id: signed text} for lines signed in both files (Physician safety, Educator wording), all rounds."""
    safety = (SIGNED_DIR / "letters_refusals_SAFETY.md").read_text()
    wording = (SIGNED_DIR / "letters_refusals_WORDING.md").read_text()
    ids = lambda s: {w for w in re.findall(r"\b[A-Za-z_]+\b", s) if w in ("copy_refusal", "INCIDENT")
                     or re.fullmatch(r"[A-Z][A-Z_]{3,}", w)}
    held = ids(" ".join(re.findall(r"^(?:Still held|Held)[^:]*: (.*)$", safety, re.M)))
    released = ids(" ".join(re.findall(r"^Signed: (.*)$", safety.split("## Holds released")[1], re.M))) \
        if "## Holds released" in safety else set()
    out = {}
    for line in wording.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) >= 3 and re.match(r"^[A-Z][A-Z_]+( \(|$)", cells[0]):
            out[cells[0].split(" (")[0]] = cells[1] if cells[2] == "unchanged" else cells[2]
    ends = re.findall(r'^- (?:Not a patient letter|Patient letter): "(.*)"$', wording, re.M)
    if len(ends) == 2:
        out["copy_refusal"] = tuple(ends)
    final = safety.split("## Final 5")[1] if "## Final 5" in safety else ""
    final_ids = {k for k in ("INCIDENT", "COMBO_REFUSED", "SENSITIVE_REFUSED") if k in final} | \
        {f"PRIVACY_BLOCKED[{w}]" for w in ("missing", "unset") if f"PRIVACY_BLOCKED {w}" in final}
    r3 = wording.split("## Round 3")[1] if "## Round 3" in wording else ""
    for m in re.finditer(r'^- ([A-Z_]+)(?: (missing|unset))?: "([^"]+)"(.*)$', r3, re.M):
        key = f"{m.group(1)}[{m.group(2)}]" if m.group(2) else m.group(1)
        confirmed = "needs Physician confirm" not in m.group(4) or re.search(
            rf"^- {re.escape(m.group(1))}\b.*\bSigned\.", safety, re.M)
        if key in final_ids and confirmed:
            out[key] = m.group(3)
    return {k: v for k, v in out.items() if k not in held or k in released}


def _code_text(k, t):
    return (L.copy_refusal("{where}", False), L.copy_refusal("{where}", True)) if k.startswith("copy_refusal") else t


def _sample(text):
    """A refusal line with its fill-ins set to fixed synthetic values."""
    return re.sub(r"\{[^}]*\}", "L3", text) if "{where}" not in text else L.copy_refusal("in the brief")


def test_refusal_codes_signed_in_both_files():
    """Plain failing test until both files cover every line; scripts/pre-push blocks a push on it."""
    signed = _signed_in_both()
    unsigned = []
    for k, t in refusalcodes.codes().items():
        key = "copy_refusal" if k.startswith("copy_refusal") else k
        want, have = signed.get(key), _code_text(k, t)
        if want is None or want != have:                         # byte-exact: whitespace and newlines count
            unsigned.append(key)
    assert not unsigned, "not signed in both files: " + "; ".join(unsigned)


def test_refusal_signed_lines_used_word_for_word():
    signed, code = _signed_in_both(), refusalcodes.codes()
    plain = {k: v for k, v in signed.items() if k != "copy_refusal"}
    assert len(plain) >= 42 and set(plain) <= set(code)   # 43 once Physician confirms SENSITIVE_REFUSED
    for k, t in plain.items():
        assert code[k] == t, k                                   # byte-exact
    assert signed["copy_refusal"] == (L.copy_refusal("{where}", False), L.copy_refusal("{where}", True))


def test_refusal_check_numbers_resolve_for_every_code():
    for c in _refusal_registry().values():
        assert L.refusal_check(_sample(c["text"])) == c["check"], c["key"]


# item 2: one test per line no test reached before
def test_unreached_check_shorthand_line():
    text = "Patient: SYNTH-4701\n1937-06-03 Clinic review.\nProblem: knee pain\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "GP")
    with pytest.raises(Refused) as e:
        check(md.replace("knee pain", "?knee pain"), parse(text)[1], "gp-update")
    assert L.CHECK_SHORTHAND.format(at="line L3") in str(e.value).splitlines()


def test_unreached_check_sentence_limit_line():
    text = "Patient: SYNTH-4701\n1937-06-03 Clinic review.\nProblem: knee pain\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "GP")
    long = " ".join(["knee pain"] * (L.SENTENCE_LIMIT // 2 + 1))
    with pytest.raises(Refused) as e:
        check(md.replace("- knee pain", "- " + long, 1), parse(text)[1], "gp-update")
    assert L.CHECK_SENTENCE_LIMIT.format(SENTENCE_LIMIT=L.SENTENCE_LIMIT, at="line L3") in str(e.value).splitlines()


def test_unreached_not_copy_arg_line(tmp_path):
    r = subprocess.run(CLI + [str(TPL / "chest_pain.txt"), "--type", "referral", "--to", "Cardiology clinic",
                              "--not-copy", "History", *READER, "--out", str(tmp_path / "o")], capture_output=True,
                       text=True)
    assert r.returncode == 2 and _clin(r) == L.NOT_COPY_ARG and r.stdout == ""
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize("prog", ["letters", "check_template"])
def test_unreached_keep_out_arg_line_both_sites(tmp_path, prog):
    exe = CLI if prog == "letters" else [sys.executable, str(HERE / "check_template.py")]
    extra = ["--type", "referral", "--to", "Cardiology clinic"] if prog == "letters" else []
    r = subprocess.run(exe + [str(TPL / "chest_pain.txt"), *extra, "--keep-out", "results"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and _clin(r) == L.KEEP_OUT_ARG


def test_unreached_action_missing_line():
    t = "SYNTH-9921 :: F :: 81\n1937-05-14 falls review.\nPlan: review in 2 weeks || GP\n"
    md = _letter([("Background", ["falls review [L2]"])])
    assert _ck(t, md) == [L.ACTION_MISSING.format(lid="L3")]


# item 3: fill-ins (Physician condition 1)
def test_fill_ins_static_scan_closed_set_only():
    bad, seen = fillscan.scan()
    assert bad == [] and len(seen) >= 45
    fields = {f for _, _, f, _ in seen}
    assert fields <= {"lid", "n", "at", "sec", "what", "words_total", "WORD_LIMIT", "SENTENCE_LIMIT", "where"}


def test_fill_ins_scan_catches_planted_note_text():
    src = (HERE / "letters.py").read_text()
    planted = src + '\n\ndef leak(line, body):\n    raise Refused(MED_MISSING.format(lid=body))\n' \
                    '\n\ndef leak2(what, lid):\n    return WRONG_SECTION.format(what=what, lid=lid)\n' \
                    '\n\ndef leak3(drug):\n    return f"Refused: {drug} is wrong."\n'
    bad, _ = fillscan.scan(planted)
    assert {(b[1], b[2]) for b in bad} == {("MED_MISSING", "lid"), ("WRONG_SECTION", "what"), ("leak3", "f-string")}


def _hostile_messages(tmp_path):
    msgs = []
    def grab(fn, *a, **k):
        with pytest.raises(Refused) as e:
            fn(*a, **k)
        msgs.append(str(e.value))
    grab(L.check_safe, "Patient: SYNTH-1 Zebedee Quillfeather\n1937-06-03 SYNTH-2 zorvadol 77.7 mg Snarkle syndrome\n")
    grab(L.check_safe, "Patient: SYNTH-1\n1999-01-01 Zebedee Quillfeather zorvadol 77.7 mg Snarkle syndrome\n")
    grab(L.check_safe, "Patient: Zebedee Quillfeather\n1937-06-03 zorvadol 77.7 mg Snarkle syndrome\n")
    grab(L.check_safe, "Patient: SYNTH-1\n1937-06-03 Zebedee Quillfeather 0412345678 zorvadol\n")
    grab(L.build, "Patient: SYNTH-1\n1937-06-03 Clinic review.\nStopped: zorvadol 77.7 mg reason: Snarkle syndrome, "
                  "Zebedee Quillfeather\n", "gp-update", "GP")
    grab(L.refuse_copies, "Patient: SYNTH-1\n1937-06-03 Clinic review.\nCopy to: Zebedee Quillfeather (zorvadol)\n")
    grab(L.refuse_copies, "x", None, "update to GP, cc Zebedee Quillfeather", True)
    grab(L.read_brief, "Zebedee Quillfeather zorvadol 77.7 Snarkle")
    grab(L.read_brief, "update to GP, include Snarkle syndrome and Quixomab")
    grab(L.from_template, "Patient: SYNTH-1\nDate: 1937-06-03\nZebedee Quillfeather: zorvadol 77.7 mg Snarkle\n")
    text = "Patient: SYNTH-4701\n1937-06-03 Clinic review.\nProblem: knee pain\nFollow-up: review in 2 weeks\n"
    md, _ = build(text, "gp-update", "GP")
    bad = md.replace("knee pain", "?Snarkle syndrome Zebedee Quillfeather zorvadol 77.7 mg Quixomab "
                     + "knee " * L.SENTENCE_LIMIT)
    grab(check, bad, parse(text)[1], "gp-update")
    notes = ("SYNTH-9921 :: F :: 81\nAllergy: Quixomab - Snarkle rash\n1937-05-14 Zebedee Quillfeather review.\n"
             "Med: zorvadol 77.7 mg daily\nPlan: Zebedee Quillfeather to review || GP\n")
    msgs += _ck(notes, _letter([("Background", ["Zebedee Quillfeather review [L3]"])]))
    msgs += _ck(notes, _letter([("Current medicines", ["Current medicines not documented."]),
                                ("Allergies", ["Allergies not documented."])]))
    for args in (["--keep-out", "zorvadol"], ["--not-copy", "Zebedee Quillfeather"]):
        r = subprocess.run(CLI + [str(TPL / "chest_pain.txt"), "--type", "referral", "--to", "Patient", *args],
                           capture_output=True, text=True)
        msgs.append(r.stderr + r.stdout)
    return msgs


def test_fill_ins_hostile_input_never_echoed(tmp_path):
    msgs = _hostile_messages(tmp_path)
    assert len(msgs) >= 16
    for m in msgs:
        for tok in HOSTILE:
            assert tok.lower() not in m.lower(), (tok, m)
        for line in m.strip().splitlines():
            if line not in (PV,) and not line.startswith("For a clinician to write"):
                assert L.refusal_check(line) is not None or any(
                    line in c["text"] for c in _refusal_registry().values()), line   # only listed refusal lines


# item 4: no refusal text in the letter, the patient-visible queue entry or any log
REFUSING_RUNS = [  # (notes edit, extra args): each refuses on the CLI
    (lambda t: t.replace("SYNTH-1001", "SYNTH-1001 SYNTH-1002"), []),
    (lambda t: t.replace("1937-03-02", "1999-03-02", 1), []),
    (lambda t: t + "Copy to: Dr A. Synth\n", []),
    (lambda t: t, ["--keep-out", "results"]),
    (lambda t: t, ["--not-copy", "History"]),
    (lambda t: t, ["--brief", "update to patient, cc GP"]),
]


@pytest.mark.parametrize("patient", [False, True])
@pytest.mark.parametrize("edit,extra", REFUSING_RUNS)
def test_no_refusal_text_reaches_letter_queue_or_log(tmp_path, edit, extra, patient):
    n = tmp_path / "notes.txt"
    n.write_text(edit((TPL / "chest_pain.txt").read_text()))
    out = tmp_path / "out"
    to = ["--to", "Patient" if patient or "--brief" in extra else "Cardiology clinic"]
    r = subprocess.run(CLI + [str(n), "--type", "gp-update", *to, *extra, *READER, "--out", str(out)],
                       capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 2, r.stderr
    assert not out.exists() and sorted(p.name for p in tmp_path.iterdir()) == ["notes.txt"]   # nothing written
    pat = patient or "--brief" in extra
    assert r.stdout == (PV + "\n" if pat else "")                       # the only patient-visible output
    for c in _refusal_registry().values():
        stem = _norm(_sample(c["text"]))[:30]
        assert stem not in r.stdout and stem not in PV
    assert "Refused" not in r.stdout and "real patient information" not in r.stdout


def test_built_letters_never_hold_refusal_text():
    stems = [_norm(c["text"]).split("{")[0][:25] for c in _refusal_registry().values()]
    for f in sorted(TPL.glob("*.txt")):
        for to, aud in (("GP", None), ("Patient", "patient")):
            md, _ = L.build_from_template(f.read_text(), "gp-update", to, audience=aud)
            assert "Refused" not in md and not any(s in md for s in stems if len(s) > 12), f


# item 5: Patient's fallback for every code
@pytest.mark.parametrize("key", sorted({c["key"] for c in tomllib.loads(
    (HERE / "data" / "refusal_lines.toml").read_text())["code"]}))
def test_patient_fallback_every_code(key, monkeypatch, capsys):
    c = _refusal_registry()[key]
    msg = _sample(c["text"])
    exc = L.ComboRefused if key == "COMBO_REFUSED" else Refused
    def boom(*a, **k):
        raise exc(msg)
    monkeypatch.setattr(L, "build", boom)
    rc = L.main([str(TPL / "chest_pain.txt"), "--type", "gp-update", "--to", "Patient"])
    out, err = capsys.readouterr()
    assert rc == 2
    assert out == PV + "\n"                                             # PV-LETTER-BY-HAND, nothing else
    hand = L.letter_by_hand(c["check"])["clinician"]
    assert hand["open"] is True and err.splitlines() == [msg.split("\n")[0], *msg.split("\n")[1:], hand["line"]]


# item 6: copy_refusal endings
def test_copy_refusal_endings_listed_and_never_reach_a_patient(tmp_path):
    assert L.copy_refusal("in the brief", False) == "Refused: copy request in the brief. " + L.COPY_TAIL
    assert L.copy_refusal("in the brief", True) == "Refused: copy request in the brief. " + L.PATIENT_COPY_TAIL
    assert L.COPY_TAIL == ("This tool can't send copies. Write a separate letter for each reader, "
                           "so nobody assumes someone was copied in when they weren't.")
    assert L.PATIENT_COPY_TAIL == ("A letter to the patient can't be copied to anyone else. "
                                   "Write a separate letter for each other reader.")
    for brief, patient in (("update to patient, cc GP", True), ("update to GP, cc cardiology", False)):
        out = tmp_path / brief.replace(" ", "_").replace(",", "")
        r = subprocess.run(CLI + [str(TPL / "chest_pain.txt"), "--brief", brief, *READER2, "--out", str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 2 and not out.exists()
        tail = L.PATIENT_COPY_TAIL if patient else L.COPY_TAIL
        assert tail in r.stderr and L.COPY_TAIL not in r.stdout and L.PATIENT_COPY_TAIL not in r.stdout
        assert r.stdout == (PV + "\n" if patient else "")


# item 7: INCIDENT never repeats what triggered it
@pytest.mark.parametrize("notes,reason", [
    ("Patient: Zebedee Quillfeather\n1937-06-03 Clinic review.\n", "no SYNTH- identifier"),
    ("Patient: SYNTH-1\n1937-06-03 Ring Zebedee on 0412345678 today\n", "phone- or card-number-like digits"),
    ("Patient: SYNTH-1\n1937-06-03 Card 4111 1111 1111 1111 Quillfeather.\n", "phone- or card-number-like digits"),
])
def test_incident_line_never_repeats_the_triggering_text(notes, reason):
    with pytest.raises(Refused) as e:
        L.check_safe(notes)
    assert str(e.value) == f"{INCIDENT} (reason: {reason})"
    for tok in re.findall(r"[A-Za-z]{4,}|\d{3,}", notes):
        if tok not in ("Patient", "SYNTH", "Clinic", "review", "1937", "Ring", "Card"):
            assert tok not in str(e.value), tok


# item 8: check_safe writes nothing
def test_check_safe_writes_nothing(monkeypatch, tmp_path):
    import builtins, os
    def no(*a, **k):
        raise AssertionError("check_safe wrote or opened something")
    monkeypatch.chdir(tmp_path)
    for target, name in ((builtins, "open"), (os, "open"), (pathlib.Path, "write_text"), (pathlib.Path, "write_bytes"),
                         (pathlib.Path, "mkdir"), (pathlib.Path, "touch"), (os, "makedirs")):
        monkeypatch.setattr(target, name, no)
    assert L.check_safe("Patient: SYNTH-1\n1937-06-03 Clinic review.\n") == "SYNTH-1"
    for bad in ("Patient: SYNTH-1 SYNTH-2\n", "Patient: SYNTH-1\n1999-01-01 x\n", "Patient: x\n",
                "Patient: SYNTH-1\n0412345678\n"):
        with pytest.raises(Refused):
            L.check_safe(bad)
    monkeypatch.undo()
    assert list(tmp_path.iterdir()) == []
    assert "writes the session" not in safetyscan.kinds_of("check_safe") if hasattr(safetyscan, "kinds_of") else True


# fails closed: what counts as a patient letter (Physician, 15 Oct 2026)
def _fc(tmp_path, *args):
    out = tmp_path / "o"
    n = tmp_path / "n.txt"
    n.write_text((TPL / "chest_pain.txt").read_text() + "Copy to: Dr A. Synth\n")   # every run refuses (copy line)
    r = subprocess.run(CLI + [str(n), *args, "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 2 and not out.exists()
    assert r.stdout == PV + "\n" and "For a clinician to write:" in r.stderr, (r.stdout, r.stderr)


@pytest.mark.parametrize("args", [["--type", "gp-update", "--to", "Patient"],
                                  ["--type", "gp-update", "--to", "GP", "--audience", "patient"]])
def test_fail_closed_to_or_audience_names_the_patient(tmp_path, args):
    _fc(tmp_path, *args)


@pytest.mark.parametrize("brief", ["update to GP; copied to patient", "update to GP, cc the patient",
                                   "referral to cardiology, copy to patient"])
def test_fail_closed_copy_recipient_without_id(tmp_path, brief):
    _fc(tmp_path, "--brief", brief, "--reader-id", "SYNTH-REG-0007")        # the copy recipient has no id


@pytest.mark.parametrize("brief", ["zzz qqq", "to whom it may concern"])
def test_fail_closed_brief_cannot_be_read(tmp_path, brief):
    _fc(tmp_path, "--brief", brief, "--to", "GP", "--type", "gp-update")


def test_pre_push_blocks_on_the_signed_test():
    hook = (HERE / "scripts" / "pre-push").read_text() + (HERE / "scripts" / "prepush.py").read_text()
    assert "scripts/prepush.py" in hook and "test_refusal_codes_signed_in_both_files" in hook and "return 1" in hook
    t = (HERE / "test_letters.py").read_text()
    i = t.index("def test_refusal_codes_signed_in_both_files")
    assert "xfail" not in t[t.rindex("\n\n\n", 0, i):i]                    # a plain test, never xfail
    assert len(re.findall(r"xfail\(raises=Refused, strict=True, reason=\"temporary combination", t)) == 4   # 4 sites = the 5 check 2 cases


# reader ids (Physician, 15 Oct 2026): by id only, never by name; grants from the cards grant data
CARD = HERE / "fixtures" / "cards" / "patient_card.toml"
GP_ID = "SYNTH-REG-0007"


def _rd(tmp_path, notes_extra, *args):
    tmp_path.mkdir(parents=True, exist_ok=True)
    n = tmp_path / "n.txt"
    n.write_text((TPL / "chest_pain.txt").read_text() + notes_extra)
    out = tmp_path / "o"
    r = subprocess.run(CLI + [str(n), "--type", "gp-update", "--to", "GP", *args, "--out", str(out)],
                       capture_output=True, text=True)
    return r, out


def _by_hand(r, out):
    assert r.returncode == 2 and not out.exists()
    assert r.stdout == PV + "\n" and "For a clinician to write:" in r.stderr, (r.stdout, r.stderr)


def test_reader_patient_id_takes_by_hand_route(tmp_path):
    _by_hand(*_rd(tmp_path, "Copy to: Dr A. Synth\n", "--reader-id", "SYNTH-1001"))
    _by_hand(*_rd(tmp_path / "c", "Copy to: Dr A. Synth\n", "--reader-id", "SYNTH-CARD-1001", "--card", str(CARD)))


def test_reader_guardian_id_takes_by_hand_route(tmp_path):
    _by_hand(*_rd(tmp_path, "Copy to: Dr A. Synth\n", "--reader-id", "SYNTH-AG-0003", "--card", str(CARD)))
    card = L.load_patient_card(CARD)
    assert L.acts_for_patient(card, "SYNTH-AG-0003") is True                # grant with rel = guardian
    assert L.acts_for_patient(card, "SYNTH-AG-0001") is True                # any grant ever held counts
    assert L.acts_for_patient(card, GP_ID) is False and L.acts_for_patient(None, "SYNTH-AG-0003") is False


@pytest.mark.parametrize("ids", [[], ["Dr A. Synth"], ["Dr A. Snyth"], ["SYNTH-REG-x"]])
def test_reader_name_without_id_takes_by_hand_route(tmp_path, ids):
    args = [x for i in ids for x in ("--reader-id", i)]
    _by_hand(*_rd(tmp_path, "Copy to: Dr A. Synth\n", *args))


def test_reader_gp_id_goes_through_normally(tmp_path):
    r, out = _rd(tmp_path, "", "--reader-id", GP_ID, "--card", str(CARD))
    assert r.returncode == 0 and (out / "letter.md").exists(), r.stderr
    r, out = _rd(tmp_path / "x", "Copy to: Dr A. Synth\n", "--reader-id", GP_ID, "--card", str(CARD))
    assert r.returncode == 2 and r.stdout == "" and "For a clinician to write:" not in r.stderr   # plain refusal


def test_gp_letter_saying_the_patient_presented_goes_through(tmp_path):
    n = tmp_path / "n.txt"
    n.write_text((TPL / "chest_pain.txt").read_text().replace(
        "History: hypertension,", "History: the patient presented with hypertension,"))
    out = tmp_path / "o"
    r = subprocess.run(CLI + [str(n), "--brief", "update to GP", "--reader-id", GP_ID, "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and (out / "letter.md").exists() and r.stdout == "", r.stderr
    n.write_text(n.read_text() + "Copy to: Dr A. Synth\n")
    r = subprocess.run(CLI + [str(n), "--brief", "update to GP", "--reader-id", GP_ID], capture_output=True, text=True)
    assert r.returncode == 2 and r.stdout == ""                             # refused, but not the by-hand route


def test_needs_hand_reasons():
    t, card = (TPL / "chest_pain.txt").read_text(), L.load_patient_card(CARD)
    nh = lambda ids, **k: L.needs_hand(k.get("to", "GP"), k.get("aud"), k.get("ok", True), k.get("n", 1), ids, t,
                                       k.get("card", card))
    assert nh([GP_ID]) is None
    assert nh([GP_ID], to="Patient") == "patient named" and nh([GP_ID], aud="patient") == "patient named"
    assert nh([GP_ID], ok=False) == "brief not read"
    assert nh([]) == "reader without id" and nh([GP_ID], n=2) == "reader without id"
    assert nh([GP_ID, "A. Synth"], n=2) == "reader without id"
    assert nh(["SYNTH-1001"]) == "patient" and nh(["SYNTH-CARD-1001"]) == "patient"
    assert nh(["SYNTH-AG-0003"]) == "acts for patient"
    assert nh(["SYNTH-AG-0001"]) == "acts for patient" and nh(["SYNTH-AG-0003"], card=None) == "not a reader id"
    assert nh(["SYNTH-2002"]) == "not a reader id"
    assert nh([GP_ID, "SYNTH-AG-0003"], n=2) == "acts for patient" and nh([GP_ID, "SYNTH-REG-0008"], n=2) is None
    assert L.brief_readers("update to GP") == 1 and L.brief_readers("update to GP, cc renal") == 2
    assert L.brief_readers(None) == 1


def test_reader_rules_match_no_names():
    src = (HERE / "letters.py").read_text()
    body = src[src.index("def needs_hand("):src.index("def brief_readers(")]
    assert "display_name" not in body and "person_name" not in body and "agent_name" not in body
    assert "PATIENT_BRIEF" not in src                                      # the word rule is gone
    assert L.READERS == {"normal": ["SYNTH-REG"]}


def test_reader_ended_line_copied_word_for_word():
    w = (SIGNED_DIR / "letters_refusals_WORDING.md").read_text()
    s = (SIGNED_DIR / "letters_refusals_SAFETY.md").read_text()
    assert re.search(r"^## CQ-LETTER-READER-ENDED signed for safety", s, re.M)
    assert re.findall(r'^CQ-LETTER-READER-ENDED: "(.*)"$', w, re.M)[-1] == L.READER_ENDED_LINE     # latest text
    assert f'"{L.READER_ENDED_LINE}"' in s.split("Physician safety re-sign")[-1]               # Physician re-sign


# ---------------------------------------------------------------- grant ids (ruling 15 Oct ~07:00)
GRANT4 = ('\n[[grant]]\ngrant_id = "SYNTH-GR-0004"\nagent_id = "SYNTH-AG-0003"\nagent_name = "Guardian"\n'
          'categories = ["general"]\ngranted = "1937-01-25"\nrel = "guardian"\nperson_name = "A. Guardian"\n')
EVT = '\n[[grant_event]]\ngrant_id = "{}"\nevent = "{}"\nat = "{}"\n'


def _gcard(tmp_path, *evs, regrant=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    t = CARD.read_text()
    if regrant:
        head, sep, tail = t.partition("\n[[grant_event]]")
        t = head + GRANT4 + sep + tail
    t += "".join(EVT.format(*e) for e in evs)
    if regrant:
        t += EVT.format("SYNTH-GR-0004", "granted", "1937-01-25")              # appended after the revocation
    p = tmp_path / "card.toml"
    p.write_text(t)
    return str(p)


def _g(tmp_path, card, *extra, now="1937-02-01"):
    args = ["--reader-id", "SYNTH-AG-0003", "--card", card, *extra] + (["--now", now] if now else [])
    return _rd(tmp_path / "run", "", *args)


def _ended(r, out):
    assert r.returncode == 2 and r.stdout == "" and not out.exists(), (r.stdout, r.stderr)
    assert L.READER_ENDED_LINE in r.stderr


def _normal(r, out):
    assert r.returncode == 0 and (out / "letter.md").exists(), r.stderr


def test_revoked_guardian_raises_reader_ended(tmp_path):
    c = _gcard(tmp_path, ("SYNTH-GR-0003", "revoked", "1937-01-20"))
    _ended(*_g(tmp_path, c, "--grant-id", "SYNTH-GR-0003"))


def test_grant_expiring_partway_through_a_trace(tmp_path):
    c = _gcard(tmp_path, ("SYNTH-GR-0003", "expired", "1937-02-10"))
    _normal(*_g(tmp_path / "a", c, "--grant-id", "SYNTH-GR-0003", now="1937-02-09"))
    _ended(*_g(tmp_path / "b", c, "--grant-id", "SYNTH-GR-0003", now="1937-02-10"))   # at the scenario time
    _ended(*_g(tmp_path / "c", c, "--grant-id", "SYNTH-GR-0003", now="1937-03-01"))


def test_revoke_regrant_then_old_grant_is_ended(tmp_path):
    c = _gcard(tmp_path, ("SYNTH-GR-0003", "revoked", "1937-01-20"), regrant=True)
    _ended(*_g(tmp_path / "a", c, "--grant-id", "SYNTH-GR-0003"))
    _ended(*_g(tmp_path / "b", c, "--grant-id", "SYNTH-GR-0003", "--grant-id", "SYNTH-GR-0004"))
    _by_hand(*_g(tmp_path / "c", c))                                          # same person, no grant id
    _normal(*_g(tmp_path / "d", c, "--grant-id", "SYNTH-GR-0004"))           # same person, current grant id


@pytest.mark.parametrize("extra,now", [
    (["--grant-id", "SYNTH-GR-0099"], "1937-02-01"),       # unknown grant id
    (["--grant-id", "SYNTH-GR-0001"], "1937-02-01"),       # someone else's grant
    (["--grant-id", "GR-0003"], "1937-02-01"),             # broken grant id
    (["--grant-id", "SYNTH-GR-0003"], None),               # no scenario time
    (["--grant-id", "SYNTH-GR-0003"], "2026-10-15"),       # wall-clock date, not the scenario clock
    (["--grant-id", "SYNTH-GR-0003"], "1937-01-04"),       # before the grant began
])
def test_broken_or_missing_grant_data_by_hand(tmp_path, extra, now):
    _by_hand(*_g(tmp_path, str(CARD), *extra, now=now))


def test_broken_card_history_by_hand(tmp_path):
    bad = _gcard(tmp_path, ("SYNTH-GR-0003", "paused", "1937-01-20"))
    _by_hand(*_g(tmp_path / "a", bad, "--grant-id", "SYNTH-GR-0003"))
    r, out = _rd(tmp_path / "b", "", "--reader-id", GP_ID, "--grant-id", "SYNTH-GR-0003", "--now", "1937-02-01")
    _by_hand(r, out)                                                          # grant id without a card


def test_current_guardian_view_shows_no_earlier_revocation(tmp_path):
    c = _gcard(tmp_path, ("SYNTH-GR-0003", "revoked", "1937-01-20"), regrant=True)
    r, out = _g(tmp_path, c, "--grant-id", "SYNTH-GR-0004")
    _normal(r, out)
    shown = r.stdout + r.stderr + (out / "letter.md").read_text()
    assert "1937-01-20" not in shown and "SYNTH-GR-0003" not in shown and "revok" not in shown.lower()


def test_patient_only_letter_is_not_by_hand(tmp_path):
    out = tmp_path / "o"
    r = subprocess.run(CLI + [str(TPL / "chest_pain.txt"), "--type", "gp-update", "--to", "Patient", "--audience",
                              "patient", "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == "" and PV not in r.stdout and (out / "letter.md").exists(), r.stderr


def test_reader_grants_reasons():
    card = L.load_patient_card(CARD)
    rg = lambda ids, gids, now="1937-02-01", c=card: L.reader_grants(c, ids, gids, now)
    assert rg([GP_ID], []) == (None, ())
    assert rg(["SYNTH-AG-0003"], []) == ("no grant id", ())
    assert rg(["SYNTH-AG-0003"], ["SYNTH-GR-0003"]) == ("current", ("SYNTH-AG-0003",))
    assert rg([GP_ID], ["SYNTH-GR-0003"]) == ("unknown grant id", ())
    assert rg(["SYNTH-AG-0003"], ["SYNTH-GR-0003"], c=None) == ("grant data unreadable", ())
    assert rg(["SYNTH-AG-0003"], ["SYNTH-GR-0003"], now="1937-13-01") == ("grant data unreadable", ())
    assert rg(["SYNTH-AG-0003", "SYNTH-AG-0001"], ["SYNTH-GR-0003"]) == ("no grant id", ())
    assert rg(["SYNTH-AG-0003", "SYNTH-AG-0001"], ["SYNTH-GR-0003", "SYNTH-GR-0001"])[0] == "current"
    assert rg([" SYNTH-AG-0003 "], [" SYNTH-GR-0003 "])[0] == "current"
    assert L.needs_hand("GP", None, True, 1, ["SYNTH-AG-0003"], "", card, ("SYNTH-AG-0003",)) is None


# ---------------------------------------------------------------- traces setting (room rulings 08:07-08:09)
@pytest.mark.needs_signed
def test_run_traces_not_run_line_copied_word_for_word():
    import paths
    rows = re.findall(r"^\| RUN-TRACES-NOT-RUN \| (?:[a-z]+ \| )?([^|]*?) \|$", paths.reused_signed().read_text(), re.M)
    assert rows == [L.RUN_TRACES_NOT_RUN]                         # one row, word for word, from its one source


@pytest.mark.needs_signed
def test_no_line_id_signed_in_more_than_one_file():
    import paths, signing
    assert signing.duplicates(paths.wording_dirs()) == {}       # one signed source per line id


@pytest.mark.parametrize("cmd", [["scripts/pre-push"], [sys.executable, "scripts/check14_derived.py"]])
def test_safety_bar_fails_when_traces_not_run(tmp_path, cmd):
    envv = {k: v for k, v in os.environ.items() if k not in ("LETTERS_TRUTH", "TRUTH_DIR")}
    envv["LETTERS_LOCAL"] = str(tmp_path / "absent.toml")
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, env=envv)
    assert r.returncode != 0 and L.RUN_TRACES_NOT_RUN in r.stdout, (r.stdout, r.stderr)


def test_missing_answer_file_fails_when_set(tmp_path):
    envv = dict(os.environ, LETTERS_TRUTH=str(tmp_path))
    r = subprocess.run([sys.executable, "scripts/check14_derived.py"], cwd=HERE, capture_output=True, text=True, env=envv)
    assert r.returncode != 0 and "answer file missing" in r.stderr


# ---------------------------------------------------------------- mutation hash (room ruling 15 Oct 08:23)
sys.path.insert(0, str(HERE / "scripts"))
import mutation_hash  # noqa: E402


def _mh_copy(tmp_path):
    import shutil
    for f in ("safety_functions.toml", "letters.py"):
        shutil.copy(HERE / f, tmp_path / f)
    mutation_hash.record(tmp_path)
    return tmp_path


def test_mutation_hash_unchanged_passes(tmp_path):
    root = _mh_copy(tmp_path)
    assert mutation_hash.fresh(root)
    p = root / "letters.py"
    p.write_text(p.read_text().replace("def needs_hand(", "# a comment\ndef needs_hand(", 1))
    assert mutation_hash.fresh(root)                                       # comments don't count


def test_mutation_hash_edited_function_body_fails(tmp_path):
    root = _mh_copy(tmp_path)
    p = root / "letters.py"
    t = p.read_text()
    assert 'return "no grant id", ()' in t
    p.write_text(t.replace('return "no grant id", ()', 'return "no grant", ()', 1))
    assert not mutation_hash.fresh(root)
    (root / mutation_hash.RECORD).unlink()
    assert not mutation_hash.fresh(root)


# ---------------------------------------------------------------- pairing (room ruling 15 Oct 09:51)
def _wording_table(wording):
    out = []
    for line in wording.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) >= 3 and re.match(r"^[A-Z][A-Z_]+( \(|$)", cells[0]):
            out.append(cells[0].split(" (")[0])
    return out


SAFETY_REGISTER, WORDING_REGISTER = "## Per-id safety register", "## Per-id wording register"


def _register(text, head):
    """The ids listed (one "- ID" bullet each) under a register heading, in order; None if the heading is absent."""
    if head not in text:
        return None
    sec = text.split(head, 1)[1].split("\n## ")[0]
    return re.findall(r"^- (\S+)\s*$", sec, re.M)


def _unpaired(safety, wording, keys):
    """Id-by-id match of Physician's and Educator's per-id registers against each other and the code's refusal ids.
    No count shortcut: every id must be named in both registers, once, and nothing extra."""
    s, w = _register(safety, SAFETY_REGISTER), _register(wording, WORDING_REGISTER)
    if s is None or w is None:
        return ["register missing"]
    bad = [f"repeated: {x}" for x in sorted({x for x in s + w if s.count(x) > 1 or w.count(x) > 1})]
    for name, have in (("safety", set(s)), ("wording", set(w))):
        bad += [f"not in {name} register: {x}" for x in sorted(set(keys) - have)]
        bad += [f"extra in {name} register: {x}" for x in sorted(have - set(keys))]
    return bad


def _refusal_keys():
    return {"copy_refusal" if k.startswith("copy_refusal") else k for k in refusalcodes.codes()}


def test_every_refusal_line_signed_in_both_paired_files():
    safety = (SIGNED_DIR / "letters_refusals_SAFETY.md").read_text()
    wording = (SIGNED_DIR / "letters_refusals_WORDING.md").read_text()
    keys = _refusal_keys()
    assert len(keys) == 44 and _unpaired(safety, wording, keys) == []


def test_line_in_only_one_register_fails(tmp_path):
    keys = _refusal_keys()
    for name in ("letters_refusals_SAFETY.md", "letters_refusals_WORDING.md"):
        texts = {n: (SIGNED_DIR / n).read_text() for n in ("letters_refusals_SAFETY.md", "letters_refusals_WORDING.md")}
        cut = tmp_path / name                                             # a temp copy; the signed file is untouched
        cut.write_text(texts[name].replace("\n- DATE_REFUSED\n", "\n", 1))
        texts[name] = cut.read_text()
        assert _unpaired(texts["letters_refusals_SAFETY.md"], texts["letters_refusals_WORDING.md"], keys) \
            == [f"not in {'safety' if 'SAFETY' in name else 'wording'} register: DATE_REFUSED"]
    s = (SIGNED_DIR / "letters_refusals_SAFETY.md").read_text()
    w = (SIGNED_DIR / "letters_refusals_WORDING.md").read_text()
    assert _unpaired(s.replace("\n- INCIDENT\n", "\n- INCIDENT\n- EXTRA_ID\n", 1), w, keys) == \
        ["extra in safety register: EXTRA_ID"]
    assert _unpaired(s, w.replace("- INCIDENT\n", "- INCIDENT\n- INCIDENT\n", 1), keys) == ["repeated: INCIDENT"]
    assert _unpaired(s.replace(SAFETY_REGISTER, "## x"), w, keys) == ["register missing"]


def test_signing_file_row_counts_match_committed_numbers():
    want = tomllib.loads((HERE / "data" / "signed_counts.toml").read_text())
    got = len(_wording_table((SIGNED_DIR / "letters_refusals_WORDING.md").read_text()))
    assert got == want["letters_refusals_WORDING_table"], f"row count read off the file: {got}"


# ---------------------------------------------------------------- signed sources (room ruling 15 Oct 10:18)
import ast as _ast  # noqa: E402
sys.path.insert(0, str(HERE))
import signing  # noqa: E402
import paths  # noqa: E402

# Constants copied from a signing file, and where each one's single signed source is.
SIGNED_LINE_NAMES = {"BY_HAND_LINES", "RUN_TRACES_NOT_RUN", "READER_ENDED_LINE", "COMBO_PACK_REFUSED", "DATE_REFUSED"}
SOURCE_REFS = {"SIGNED_DIR", "reused_signed", "_signed_in_both", "_register", "_signed_sources"}
CONTENT = re.compile(r"signed|exact|word_for_word|copied")


def _reaches(fn):
    return any((isinstance(n, _ast.Name) and n.id in SOURCE_REFS) or (isinstance(n, _ast.Attribute) and n.attr in SOURCE_REFS)
               for n in _ast.walk(fn))


def _compares_signed_constant(fn):
    for n in _ast.walk(fn):
        if isinstance(n, _ast.Compare) and any(isinstance(o, _ast.Eq) for o in n.ops):
            names = {x.attr for x in _ast.walk(n) if isinstance(x, _ast.Attribute)} | \
                    {x.id for x in _ast.walk(n) if isinstance(x, _ast.Name)}
            if names & SIGNED_LINE_NAMES:
                return True
    return False


# Behaviour checks that a refusal raised IS the constant (the constant's bytes are checked against the signing file by
# test_every_resolved_line_comes_from_its_current_single_signed_source), not checks of the wording itself.
BEHAVIOUR = {"test_c2_letters_refuses_to_run_without_a_signed_combination_list"}


def guard_problems(fns):
    """Signed-content tests (named as such, comparing a copied signed line) that never reach a signing file."""
    return [n.name for n in fns if CONTENT.search(n.name) and n.name not in BEHAVIOUR and _compares_signed_constant(n)
            and not _reaches(n)]


def test_signed_content_tests_take_expected_from_signed_source():
    fns = [n for n in _ast.parse((HERE / "test_letters.py").read_text()).body
           if isinstance(n, _ast.FunctionDef) and n.name.startswith("test_")]
    assert guard_problems(fns) == []


def test_guard_catches_a_test_checking_a_file_against_itself():
    bad = _ast.parse("def test_line_signed():\n    assert reg['DATE_REFUSED'] == L.DATE_REFUSED\n").body[0]
    good = _ast.parse("def test_line_signed():\n    assert _signed_in_both()['DATE_REFUSED'] == L.DATE_REFUSED\n").body[0]
    assert guard_problems([bad]) == ["test_line_signed"] and guard_problems([good]) == []


def _signed_sources():
    """{name: (code text, single signed source file name, signed bytes)} for every line letters resolves."""
    fo = paths.reused_signed()
    rows = signing.rows(fo.read_text())
    w = (SIGNED_DIR / "letters_refusals_WORDING.md").read_text()
    out = {k: (v, fo.name, rows.get(k)) for k, v in L.BY_HAND_LINES.items()}
    out["RUN-TRACES-NOT-RUN"] = (L.RUN_TRACES_NOT_RUN, fo.name, rows.get("RUN-TRACES-NOT-RUN"))
    out["CQ-LETTER-READER-ENDED"] = (L.READER_ENDED_LINE, "letters_refusals_WORDING.md",
                                     re.findall(r'^CQ-LETTER-READER-ENDED: "(.*)"$', w, re.M)[-1])
    signed = _signed_in_both()
    for k, t in refusalcodes.codes().items():
        if not k.startswith("copy_refusal"):
            out[k] = (t, "letters_refusals_WORDING.md", signed.get(k))
    return out


@pytest.mark.needs_signed
def test_every_resolved_line_comes_from_its_current_single_signed_source():
    where = signing.sources(paths.wording_dirs())
    stem = lambda name: signing.FILE.match(name).group(1)
    for k, (code, src, signed) in _signed_sources().items():
        assert code == signed, f"{k}: differs from {src}"                    # byte-exact
        if "-" in k:                                                         # hyphenated ids: one signing set only
            assert where.get(k) == [stem(src)], f"{k}: signed in {where.get(k)}"


def _clean_runs(tmp_path):
    c = _gcard(tmp_path / "card", ("SYNTH-GR-0003", "revoked", "1937-01-20"), regrant=True)
    runs = [_rd(tmp_path / "a", "Copy to: Dr A. Synth\n", "--reader-id", "SYNTH-1001"),                   # by hand
            _g(tmp_path / "b", c, "--grant-id", "SYNTH-GR-0003"),                                          # ended
            _g(tmp_path / "c", c, "--grant-id", "SYNTH-GR-0004"),                                          # normal
            _rd(tmp_path / "d", "Seen 1941-02-03.\n", "--reader-id", GP_ID),                               # refusal
            _rd(tmp_path / "e", "Copy to: Dr A. Synth\n", "--reader-id", GP_ID)]
    return [x for r, _ in runs for x in (r.stdout + r.stderr).splitlines() if x.strip()]


OPENINGS = ("Refused:", "For a clinician", "Your care team", "Safety traces not run")


@pytest.mark.needs_signed
def test_clean_run_every_emitted_signed_line_matches_its_source(tmp_path):
    pats = [signing.pattern(re.sub(r"\{[^}]*\}", "[x]", s)) for _, _, s in _signed_sources().values() if s]
    lines = [x for x in _clean_runs(tmp_path) if x.startswith(OPENINGS)]
    assert len(lines) >= 4
    for x in lines:
        assert any(p.match(x) for p in pats), f"emitted line not byte-exact to a signed line: {x!r}"
    assert not any(p.match(lines[0] + " ") for p in pats)                   # one extra byte fails


# ---------------------------------------------------------------- LT-PRIVACY-PT (room ruling 15 Oct 10:23)
def test_patient_privacy_line_is_the_signed_status_row():
    """PATIENT_PRIVACY is the signed LT-PRIVACY-PT row of letters_status_lines_SIGNED.md, byte-exact; the row count
    read off the file matches the committed number. Fails, never skips, if the row is missing or differs."""
    f = SIGNED_DIR / "letters_status_lines_SIGNED.md"
    t = f.read_text()
    rows = dict(re.findall(r"^\| (LT-[A-Z0-9-]+) \| [a-z]+ \| [^|]+ \| ([^|]*?) \|$", t, re.M))
    want = tomllib.loads((HERE / "data" / "signed_counts.toml").read_text())["letters_status_lines_SIGNED"]
    assert rows.get("LT-PRIVACY-PT") == L.PATIENT_PRIVACY                     # byte-exact
    assert len(rows) == want, f"row count read off the file: {len(rows)}"


# ---------------------------------------------------------------- stated line counts (room ruling 15 Oct 11:09)
import signed_header_count  # noqa: E402  scripts/signed_header_count.py, standalone (copy as-is to other repos)


def test_header_count_check_fails_on_a_wrong_header(tmp_path):
    good = (SIGNED_DIR / "letters_status_lines_SIGNED.md").read_text()
    assert signed_header_count.problems(good) == []                          # 12 lines stated, 12 rows
    bad = tmp_path / "letters_status_lines_SIGNED.md"                         # a temp copy; the signed file is untouched
    bad.write_text(good.replace("SIGNED by Physician, 12 lines", "SIGNED by Physician, 13 lines", 1))
    assert signed_header_count.problems(bad.read_text(), bad.name) == \
        ["letters_status_lines_SIGNED.md: header says 13 lines, 12 rows parsed"]
    r = subprocess.run([sys.executable, str(HERE / "scripts" / "signed_header_count.py"), str(bad)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "header says 13 lines, 12 rows parsed" in r.stdout
    s = (SIGNED_DIR / "letters_refusals_SAFETY.md").read_text()
    assert signed_header_count.problems(s) == []                              # register: 44 ids stated, 44 listed
    assert len(signed_header_count.problems(s.replace("Physician), 44 ids", "Physician), 45 ids", 1))) == 1


# ---------------------------------------------------------------- every signing file states its true count (15 Oct 11:16)
@pytest.mark.needs_signed
def test_every_signing_file_states_its_true_line_count():
    files = [f for d in paths.wording_dirs() for f in sorted(d.iterdir())
             if signing.FILE.match(f.name) and "DRAFT" not in f.name and "RETIRED" not in f.name]
    assert files and [p for f in files for p in signed_header_count.problems(f.read_text(), f.name)] == []
