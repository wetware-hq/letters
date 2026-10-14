"""Side-by-side check 14 run on letter inputs derived from the scenario truth files (read only; the letters
build steps of S02, S03b, S05 and S12), crossed with every linked-terms category, every letter type and audience."""
import itertools, json, os, sys, tomllib, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("SBS_OUT", "/tmp/check14_derived.jsonl")
import letters as L
from scripts import check14_sbs as S
S.pytest_configure(None)

TRUTH = pathlib.Path(os.environ["TRUTH_DIR"])        # the scenario truth folder, read only
CASES = tomllib.loads((pathlib.Path(__file__).resolve().parent.parent / "data" / "check14_derived_cases.toml")
                     .read_text(encoding="utf-8"))           # inputs are data, not code
BASE = {k: v["note"] for k, v in CASES["scenarios"].items()}
SCEN = {k: (v["type"], v["audience"]) for k, v in CASES["scenarios"].items()}
for k in SCEN:                                            # confirm the scenario really has that letters build step
    t = tomllib.loads(next(TRUTH.glob(k + "-*/truth.toml")).read_text())
    assert any("letters: build" in c for c in t["minimum_call_path"]), k


def injections():
    yield "none", ""
    for cat, v in L.load_linked().items():
        for w in v.get("terms", [])[:4] + v.get("flag_terms", [])[:3]:
            yield f"{cat}/term", f"PMHx: {w}\nPlan: discuss {w} at review. Keep walking.\n"
        for m in v.get("medicines", [])[:2]:
            yield f"{cat}/med", f"Med: {m} 100 mg daily\nPlan: continue {m}\n"
        for b in list(v.get("brands", {}))[:2]:
            yield f"{cat}/brand", f"Med: {b} 1 tablet daily (for {cat})\n"
        if v.get("terms"):
            t0, m0 = v["terms"][0], (v.get("medicines") or [CASES["fallback_medicine"]])[0]
            yield f"{cat}/medpart", f"Med: {m0} 100 mg daily, {t0} clinic, with food\nAllergy: {t0} trial drug - rash\n"
            yield f"{cat}/result", f"Result: {t0} negative 1937-08-01\n"


n = 0
for scen, (ltype, aud) in SCEN.items():
    for (label, extra), lt, au in itertools.product(list(injections()), L.TITLES, ("gp", "specialist", "patient")):
        for inc in [None] + ([[label.split("/")[0]]] if "/" in label else []):
            text = BASE[scen] + extra
            try:
                L.build(text, lt, CASES["to"][au],
                        audience=au, flags=[], include=inc, privacy=True if au == "patient" else None)
            except Exception:
                pass                                       # a refusal before or after check 14 is fine; check 14 calls are compared
            n += 1
print("builds", n)
