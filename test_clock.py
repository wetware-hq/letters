"""One approved clock (clock.today), as figures 4112225. LETTERS_CLOCK=test gives the scenario clock (--now), real the
real clock; unset refuses with RF-CLOCK-UNSET (staff-only: stderr, never in a letter or patient line), nothing written.
No other letters code reads the box clock."""
import os, pathlib, re, subprocess, sys
import pytest
import clock

HERE = pathlib.Path(__file__).parent
BOX = re.compile(r"\b(datetime\.(now|utcnow|today)|date\.today|time\.(time|time_ns|localtime|gmtime|strftime|monotonic|perf_counter))\b")
CARD = '''[header]
card_id = "SYNTH-CARD-1300"
kind = "patient"
display_name = "C. Thirteen"
person_name = "C. Thirteen"
role = "patient"
scope = ["read", "check", "draft"]
issued = "1936-01-02"
expires = "1936-12-31"
episode_id = "SYNTH-EP-1300"

[[grant]]
grant_id = "SYNTH-GR-1301"
agent_id = "SYNTH-PERSON-G13"
agent_name = "Guardian"
categories = ["general"]
granted = "1936-01-02"
rel = "guardian"
person_name = "G. Thirteen"

[[grant_event]]
grant_id = "SYNTH-GR-1301"
event = "granted"
at = "1936-01-02"
'''


def _run(tmp_path, mode):
    c = tmp_path / "card.toml"
    c.write_text(CARD)
    n = tmp_path / "n.txt"
    n.write_text((HERE / "fixtures" / "template" / "chest_pain.txt").read_text())
    env = dict(os.environ)
    env.pop("LETTERS_CLOCK", None)
    if mode is not None:
        env["LETTERS_CLOCK"] = mode
    out = tmp_path / "o"
    r = subprocess.run([sys.executable, str(HERE / "letters.py"), str(n), "--type", "discharge", "--to", "SYNTH-PERSON-G13",
                        "--grant-id", "SYNTH-GR-1301", "--card", str(c), "--now", "1936-03-09", "--out", str(out)],
                       capture_output=True, text=True, env=env)
    return r, out


def test_test_mode_gives_the_scenario_clock(monkeypatch):
    monkeypatch.setenv("LETTERS_CLOCK", "test")
    assert clock.today("1936-03-09") == "1936-03-09"


@pytest.mark.parametrize("mode", [None, "", "TEST", "scenario"])
def test_unset_clock_refuses_staff_only_and_writes_nothing(tmp_path, mode):
    r, out = _run(tmp_path, mode)
    assert r.returncode == 2 and not out.exists()
    assert clock.RF_CLOCK_UNSET in r.stderr and clock.RF_CLOCK_UNSET not in r.stdout
    assert "Your care team" not in r.stdout + r.stderr                     # never a patient line


def test_unset_without_a_grant_check_needs_no_clock(monkeypatch):
    monkeypatch.delenv("LETTERS_CLOCK", raising=False)
    import letters as L
    assert L.reader_grants(None, [], [], None) == (None, ())


def test_test_mode_without_scenario_time_gives_none_so_grant_check_goes_by_hand(monkeypatch):
    monkeypatch.setenv("LETTERS_CLOCK", "test")
    monkeypatch.delenv("LETTERS_SCENARIO_CLOCK", raising=False)
    assert clock.today(None) is None
    monkeypatch.setenv("LETTERS_SCENARIO_CLOCK", "1936-03-09")
    assert clock.today(None) == "1936-03-09"


def test_real_mode_never_gives_a_1930s_date(monkeypatch):
    monkeypatch.setenv("LETTERS_CLOCK", "real")
    assert not clock.today("1936-03-09").startswith("193")


@pytest.mark.needs_signed
def test_clock_unset_text_byte_exact_from_single_signed_source():
    import paths, signing
    assert signing.rows(paths.reused_signed().read_text())["RF-CLOCK-UNSET"] == clock.RF_CLOCK_UNSET


def test_no_code_outside_clock_py_reads_the_box_clock():
    bad = []
    for p in HERE.rglob("*.py"):
        rel = p.relative_to(HERE).as_posix()
        if "__pycache__" in rel or rel == "clock.py" or p.name.startswith("test_") or rel.startswith((".venv/", "golden/")):
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if BOX.search(line.split("#")[0]):
                bad.append(f"{rel}:{n}")
    assert bad == [], bad
