"""Side-by-side check 14 run on letter inputs derived from the scenario truth files (read only; the letters
build steps of S02, S03b, S05 and S12), crossed with every linked-terms category, every letter type and audience."""
import itertools, json, os, sys, tomllib, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("SBS_OUT", "/tmp/check14_derived.jsonl")
import letters as L
from scripts import check14_sbs as S
S.pytest_configure(None)

TRUTH = pathlib.Path(os.environ["TRUTH_DIR"])        # the scenario truth folder, read only
BASE = {   # from each scenario's expected_outputs; synthetic, 1930s dates
    "S02": "Patient: SYNTH-0201\n1937-08-01 ED triage.\nProblem: chest pain, resolved\nFollow-up: book follow-up\n",
    "S03b": "Patient: SYNTH-0301\n1937-08-10 Medicine reconciliation.\nMed: metformin 500 mg twice daily\nPlan: send list || pharmacist || today\n",
    "S05": "Patient: SYNTH-0501\n1937-08-12 Clinic review.\nProblem: low mood screen\nPMHx: mh_q2 Yes\nFollow-up: review in 2 weeks\n",
    "S12": "Patient: SYNTH-1201\n1939-06-05 Discharge.\nMed: paracetamol 1 g oral four times a day for 5 days\nFollow-up: family doctor review in 1 week 1939-06-12\n",
}
SCEN = {"S02": ("discharge", "gp"), "S03b": ("gp-update", "gp"), "S05": ("gp-update", "gp"), "S12": ("discharge", "patient")}
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
            t0, m0 = v["terms"][0], (v.get("medicines") or ["aspirin"])[0]
            yield f"{cat}/medpart", f"Med: {m0} 100 mg daily, {t0} clinic, with food\nAllergy: {t0} trial drug - rash\n"
            yield f"{cat}/result", f"Result: {t0} negative 1937-08-01\n"


n = 0
for scen, (ltype, aud) in SCEN.items():
    for (label, extra), lt, au in itertools.product(list(injections()), L.TITLES, ("gp", "specialist", "patient")):
        for inc in [None] + ([[label.split("/")[0]]] if "/" in label else []):
            text = BASE[scen] + extra
            try:
                L.build(text, lt, "GP" if au == "gp" else ("Cardiology" if au == "specialist" else "patient"),
                        audience=au, flags=[], include=inc, privacy=True if au == "patient" else None)
            except Exception:
                pass                                       # a refusal before or after check 14 is fine; check 14 calls are compared
            n += 1
print("builds", n)
