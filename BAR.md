# letters — the bar

What a green means in letters, and what it does not. A green here is not the same assurance as a green in a sibling repo; read this before quoting one.

**Not a medical device; not approved for real patient data.** Every letter is an unsigned draft; the signing clinician is responsible for checking it against the record.

## The bar

```bash
python -m venv .venv && source .venv/bin/activate
pytest -q                       # the suite
python scripts/rulecheck.py     # every rule has a test; no prose outside WORDING.md
python scripts/mutate.py        # mutation gate over safety_functions.toml
```

`pytest` green plus `rulecheck.py` exit 0 is letters' bar; `mutate.py` is the mutation gate. Before pushing, maintainers run `scripts/prepush.py` (the `scripts/pre-push` hook) and the held-out safety traces (see below).

## Narrower green on this checkout

**The safety traces do not run here.** Without the answer-file setting (`LETTERS_TRUTH`/`TRUTH_DIR`), the run prints `Safety traces not run: the held-out answer files aren't on this computer`, and conftest deselects the trace tests under `RUN-TRACES-NOT-RUN`. Such a run **never passes the safety bar**. On this box 7 trace tests are deselected and 5 are xfailed; the ~1674 that pass are the unit and checker tests only. Read a green here as "the code checks behaved on synthetic fixtures", not as a scored safety result.

## What a green covers

- **Source tracing** — every sentence names the note lines it came from; a sentence the checker can't match is refused, never softened (`test_letters`).
- **Negation and uncertainty** — "stopped", "ruled out", "no chest pain" are negations; "?"/"possible" are never written as a diagnosis.
- **Never-cut items** — allergies with reaction, full current medication list with the reason for each change, pending results with owner, and follow-up actions; missing ones become "not documented", and a reaction/owner the letter would drop is refused.
- **Sensitive items** — HIV status, psychiatric history and PrEP; linked words/medicines/brands from `linked_terms.toml` (version 2, frozen and checksummed in `LINKED_TERMS.SHA256`, checked by `test_linked_terms`); default-cut for non-patient readers, `include:` keeps an item, a drug that would disappear refuses the letter.
- **Copies, abbreviations, questions, drug names** — copy requests refused; abbreviations expanded only from the reviewed single-meaning list, else flagged; a referral with no question flags and blocks `--final`; unknown drug names flagged (`test_drug_names`).
- **Template parser & parity** — `from_template` is the one parser, and `check_template` runs the same copy/send/recipient checks in the same order.
- **Reproducibility** — identical input gives an identical letter.
- **Clock discipline** — `test_clock`.
- **Jurisdiction scan** — `test_jurisdiction_scan.py` fails on any planted term in each category below, each on its own id.

The scan's category ids are not copied here: duplicating the registry would drift from it, and can trip the scan on an id that itself contains a scanned term. The authoritative list is the `[categories]` table in `jurisdiction_scan.toml`, and `test_bar.py` checks this bar points to it.

## What a green does NOT cover

- **The held-out safety traces** — see above; they need the answer files and a version-checked external runner, and are reported as counts, never rates.
- **Live-model behaviour.** v0.1 makes no model calls (setting `LETTERS_LLM_PROVIDER` makes letters exit); S05 and S07 were checked with a scripted stand-in. The optional model rewrite is **unbuilt** and would have to pass the same checker.
- **The mutation sweep and pre-push unless run.** `mutate.py`, `prepush.py` and the `pre-push` hook are separate gates; a `pytest` green alone does not include them.
- **Real data or clinical validity.** A green is a code check on synthetic fixtures; every letter is an unsigned draft and letters is not a medical device.

## Name guard

`test_bar.py` checks this bar names `jurisdiction_scan.toml` as the authoritative scan registry, rather than copying the category ids (a copy would drift from the registry and can trip the scan itself). The registry's own test (`test_each_category_planted_term_fails_on_its_own_id`) holds every category to one declared, planted id, so no scan check runs under a name the registry does not name.
