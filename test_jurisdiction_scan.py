"""Jurisdiction term scan: no country, state, city, agency, policy number, emergency number, program name,
local job title or local currency in this repo's core (file contents, file names, and commit messages made after
the scan base). Local names and values belong in site-profile data. Each category fails on its own check id.
Signed/pinned ids that still carry a local word sit on the named exception list (ids only) until the next version."""
import pathlib, re, subprocess, tomllib

ROOT = pathlib.Path(__file__).resolve().parent
while not (ROOT / "jurisdiction_scan.toml").is_file():
    ROOT = ROOT.parent
CFG = tomllib.loads((ROOT / "jurisdiction_scan.toml").read_text(encoding="utf-8"))
CATS = {k: re.compile(v) for k, v in CFG["categories"].items()}
EXCEPT_IDS = [re.compile(x) for x in CFG["exceptions"]["ids"]]
TERM_EXC = [(re.compile(e["path"]), re.compile(r"(?<![\w-])" + re.escape(e["term"]) + r"(?![\w-])"), e)
            for e in CFG["exceptions"].get("term", [])]     # one entry per term: term, path, reason
SELF = {"jurisdiction_scan.toml", pathlib.Path(__file__).name}


def strip_ids(text):
    for e in EXCEPT_IDS:
        text = e.sub(" ", text)
    return text


def scan_text(text, path=None):
    """Check ids that fire on this text (exception ids, then this file's per-term exceptions, removed first)."""
    t = strip_ids(text)
    if path is not None:
        for pr, tr, _ in TERM_EXC:
            if pr.search(path):
                t = tr.sub(" ", t)
    return sorted(k for k, p in CATS.items() if p.search(t))


def tracked():
    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    files = [f for f in r.stdout.splitlines() if f] if r.returncode == 0 else \
        [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]
    files += [str(p.relative_to(ROOT)) for p in ROOT.rglob("live_*.json")      # live results, tracked or not
              if ".git" not in p.parts and str(p.relative_to(ROOT)) not in files]
    return [f for f in files if pathlib.Path(f).name not in SELF]


def scan_repo():
    hits = []
    for f in tracked():
        for k in scan_text(f):
            hits.append((f, 0, k))
        p = ROOT / f
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for i, line in enumerate(lines, 1):
            for k in scan_text(line, f):
                hits.append((f, i, k))
    return hits


def test_core_has_no_jurisdiction_terms():
    hits = scan_repo()
    assert hits == [], "jurisdiction terms in core (move to site profile): " + "; ".join(f"{k} {f}:{n}" for f, n, k in hits[:40])


def test_new_commit_messages_have_no_jurisdiction_terms():
    base = CFG.get("commit_base", "")
    r = subprocess.run(["git", "log", "--format=%H%x00%B%x01", f"{base}..HEAD"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        return  # not a git checkout (e.g. a packed copy): file scan still runs
    bad = []
    for rec in r.stdout.split("\x01"):
        if "\x00" in rec:
            sha, msg = rec.split("\x00", 1)
            ids = scan_text(msg)
            if ids:
                bad.append((sha.strip()[:7], ids))
    assert bad == [], f"jurisdiction terms in commit messages: {bad}"


def test_each_category_planted_term_fails_on_its_own_id(tmp_path):
    for k, planted in CFG["planted"].items():
        assert scan_text(f"x {planted} x") == [k], (k, planted)
        f = tmp_path / f"plant_{k.replace('/', '_')}.txt"
        f.write_text(f"notes: {planted}\n")
        assert scan_text(f.read_text()) == [k]
    assert set(CFG["planted"]) == set(CATS), "every category needs a planted term"


def test_planted_file_name_fails():
    for k, planted in CFG["planted"].items():
        assert k in scan_text(f"docs/{planted}.md")


def test_exception_list_is_ids_only_and_still_needed():
    for e in CFG["exceptions"]["ids"]:
        assert re.fullmatch(r"[A-Z0-9\-\[\]\*\+\\b]+", e), f"exception must be an id pattern: {e}"


def test_term_exceptions_are_one_term_each_with_a_reason():
    assert "paths" not in CFG["exceptions"] and "held_paths" not in CFG["exceptions"], "no folder-level exceptions"
    for _, _, e in TERM_EXC:
        assert set(e) == {"term", "path", "reason"} and e["reason"].strip() and e["term"].strip(), e
        assert re.fullmatch(r"\^[\w./\\-]+\$", e["path"]), f"exception must name one file exactly: {e}"
        assert not e["path"].endswith("/"), f"exception must name files, not a folder: {e}"


def test_planted_term_in_excepted_file_but_other_term_still_fails():
    for pr, tr, e in TERM_EXC:
        other = "Sydney" if e["term"] != "Sydney" else "Melbourne"
        path = e["path"].strip("^$").replace("\\", "")
        assert scan_text(f"{e['term']} {other}", path) == ["J-CITY"], e


def test_prompt_leak_groups_each_have_a_planted_term():
    groups = {k for k in CATS if k.startswith("SCAN-PROMPT-LEAK/")}
    assert {"names", "persona", "paths", "tokens", "people", "email", "directive"} == {g.split("/")[1] for g in groups}
    for g in groups:
        assert scan_text(CFG["planted"][g]) == [g]


def test_prompt_leak_extra_planted_terms():
    for t, g in [("BI" "O Engineer", "names"), ("Masterchief", "names"), ("Grok Bot", "names"), ("Captain Cook", "names"),
                 ("You are", "persona"), ("YOU ARE A helper", "persona"), ("subagent", "persona"), ("executor", "persona"),
                 ("/" "workspace/tmp/x", "paths"), ("WARD_MODEL_API_KEY", "tokens"), ("MODAL", "tokens"),
                 ("Mathew", "people"), ("Chu", "people"), ("SYSTEM DIRECTIVE", "directive")]:
        assert scan_text(t) == [f"SCAN-PROMPT-LEAK/{g}"], t
    assert scan_text("hellowetware@gmail.com") == []
    assert scan_text("Physician, 2026-10-16") == [] and scan_text("Clinical lead, 2026-10-17") == []


def test_untracked_live_json_is_scanned(tmp_path, monkeypatch):
    import shutil
    (ROOT / "live_planted_scan_test.json").write_text('{"note": "signed in the room"}')
    try:
        assert any(f == "live_planted_scan_test.json" for f, _, _ in scan_repo())
    finally:
        (ROOT / "live_planted_scan_test.json").unlink()


def test_english_reach_and_numbers_are_not_flagged():
    assert scan_text("within reach of the bed; 80,000 tokens; SYNTH-REC-0001; TEST-000") == []
