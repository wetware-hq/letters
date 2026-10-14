"""Side-by-side run of check 14: old (scripts/check14_old.py) vs new (letters.py) on every call.

Used as a pytest plugin:  pytest -p scripts.check14_sbs   (records to $SBS_OUT, default /tmp/check14_sbs.jsonl)
and by derive_cases() for inputs derived from the scenario truth files (read only)."""
import copy, json, os, pathlib
import letters as L

ROOT = pathlib.Path(__file__).resolve().parent.parent
exec(compile((ROOT / "scripts" / "check14_old.py").read_text(), "check14_old.py", "exec"), L.__dict__)
OUT = os.environ.get("SBS_OUT", "/tmp/check14_sbs.jsonl")
NEW = {"apply_cut_rule": L.apply_cut_rule, "flag_cuts": L.flag_cuts}
OLD = {"apply_cut_rule": L.apply_cut_rule_old, "flag_cuts": L.flag_cuts_old}
STATE = ("_SENS_CUTS", "_KEPT_OUT")


def _run(fn, args, kw):
    a, k = copy.deepcopy(args), copy.deepcopy(kw)
    before = {s: list(getattr(L, s)) for s in STATE}
    try:
        r = ("ok", fn(*a, **k))
    except Exception as e:                               # Refused or a crash: both compared by type and text
        r = ("raise", f"{type(e).__name__}: {e}")
    after = {s: list(getattr(L, s)) for s in STATE}
    for s in STATE:                                      # restore so the other version starts from the same state
        getattr(L, s)[:] = before[s]
    flags = a[3] if len(a) > 3 else k.get("flags")
    flags = flags if flags is not None else []
    return {"result": r, "flags": flags, "state": after}


def compare(name, args, kw, where=""):
    o, n = _run(OLD[name], args, kw), _run(NEW[name], args, kw)
    rec = {"fn": name, "where": where, "patient": bool(L._PATIENT_LETTER), "same": o == n}
    if not rec["same"]:
        rec["old"], rec["new"] = o, n
    with open(OUT, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec


def _wrap(name):
    def w(*args, **kw):
        compare(name, args, kw, os.environ.get("PYTEST_CURRENT_TEST", ""))
        return NEW[name](*args, **kw)
    return w


def pytest_configure(config):
    L.apply_cut_rule = _wrap("apply_cut_rule")
    L.flag_cuts = _wrap("flag_cuts")
