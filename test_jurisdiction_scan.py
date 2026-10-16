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
EXCEPT_PATHS = [re.compile(x) for x in CFG["exceptions"].get("paths", []) + CFG["exceptions"].get("held_paths", [])]
SELF = {"jurisdiction_scan.toml", pathlib.Path(__file__).name}


def strip_ids(text):
    for e in EXCEPT_IDS:
        text = e.sub(" ", text)
    return text


def scan_text(text):
    """Check ids that fire on this text (exception ids removed first)."""
    t = strip_ids(text)
    return sorted(k for k, p in CATS.items() if p.search(t))


def tracked():
    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    files = [f for f in r.stdout.splitlines() if f] if r.returncode == 0 else \
        [str(p.relative_to(ROOT)) for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]
    return [f for f in files if pathlib.Path(f).name not in SELF and not any(e.search(f) for e in EXCEPT_PATHS)]


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
            for k in scan_text(line):
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
        f = tmp_path / f"plant_{k}.txt"
        f.write_text(f"notes: {planted}\n")
        assert scan_text(f.read_text()) == [k]
    assert set(CFG["planted"]) == set(CATS), "every category needs a planted term"


def test_planted_file_name_fails():
    for k, planted in CFG["planted"].items():
        assert k in scan_text(f"docs/{planted}.md")


def test_exception_list_is_ids_only_and_still_needed():
    for e in CFG["exceptions"]["ids"]:
        assert re.fullmatch(r"[A-Z0-9\-\[\]\*\+\\b]+", e), f"exception must be an id pattern: {e}"


def test_english_reach_and_numbers_are_not_flagged():
    assert scan_text("within reach of the bed; 80,000 tokens; SYNTH-REC-0001; TEST-000") == []
