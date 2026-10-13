# letters

**Overview.** letters is a local tool that turns a dump of raw medical notes, plus a short brief such as "referral to cardiology", into a plain, professional Markdown letter written for that reader. It works in memory, keeps nothing, and writes only sentences it can trace back to a line in the notes. Agent rules are in [AGENTS.md](AGENTS.md).

> **Not approved for real patient data.** Deleting the name isn't enough: notes mention family, addresses and rare details that still identify someone, so never paste real notes. Every letter is an unsigned draft. The clinician who signs it is responsible for checking it against the record. This is a research prototype tested on synthetic data only. It is not a medical device and not approved by any regulator for clinical use. Privacy, ethics and regulatory review have not been done.

---

## Clinical abstract

Letters are where care gets handed over, and where it most often goes wrong. A referral arrives without a question, a discharge letter forgets a pending result, an allergy loses its reaction, or a "?PE" turns into a diagnosis. Labels such as "non-compliant" are copied forward, and details the reader never needed reach them anyway.

letters reads the notes once. It keeps the latest state of every problem, medicine, result and plan, and it understands that "stopped", "ruled out" and "no chest pain" are negations. It filters what this reader needs, groups it into sections, and writes the letter in plain English. A plain checker then refuses any sentence whose numbers, medicines, dates or diagnoses aren't in its source lines. What was cut is shown to the clinician once and never saved.

Some things are never cut:

| Item | If missing from the notes |
|---|---|
| allergies, each with its reaction | "not documented" or "reaction not documented" |
| the full current medication list, and the reason for each change | "not documented" |
| pending results and who follows them up | "not documented" |
| follow-up actions, with who and by when | "not documented" |

## Contract

| Slot | Direction | Type |
|---|---|---|
| **notes** | input | raw text, synthetic only (`SYNTH-` ids, 1937 dates) |
| **brief** | input | letter type and reader, e.g. referral to cardiology |
| **template** | input, optional | section layout; required sections are added back if missing |
| **brand** | input, optional | name, address, logo, fonts and colours; drafts show "DRAFT, unsigned" |
| **letter** | output | one Markdown file, ready to convert to PDF or HTML |
| **dropped** | output | shown on screen to the clinician only; never written to disk |

The output folder holds only the letter. Identical input gives an identical letter.

## Rules

- **Sources.** Every sentence names the note lines it came from. A sentence the checker can't match is refused, never softened.
- **Uncertainty stays uncertain.** "?" and "possible" are never written as a diagnosis.
- **Question first.** A referral opens with what the writer is asking; without one, it fails.
- **Trends** are written as values with dates, never as a direction.
- **Sensitive details** the reader doesn't need are cut. A medicine stays on the list, but its reason is cut when it's sensitive.
- **Labels** such as "non-compliant" become a plain description or are dropped. Write as if the patient will read it.
- **Style.** Abbreviations are spelled out on first use, sentences are short, and every number has a unit. A letter is about one page.
- **Footer.** Every letter ends with an unsigned clinician block and "Draft: check against the record before sending."
- **No model calls** in v0.1. An optional model rewrite may come later, off by default, and must pass the same checker.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pytest -q
python letters.py fixtures/chest_pain.txt --brief "referral to cardiology"
python letters.py fixtures/podiatry_referral.txt --brief "referral to podiatry" \
  --template templates/minimal.md --brand brands/example-clinic --out out/
python letters.py fixtures/chest_pain.txt --brief "referral to cardiology" --final
python letters.py --heldout ../heldout-notes   # folder outside this repo; prints counts only
```

`--final` removes source tags only. The draft mark stays until the signing clinician removes it.

## Evaluation

Regression tests plant each failure: an unsourced fact, a stopped medicine, a negation, "?PE", a missing allergy, a pending result, a label, a sensitive detail in an unrelated referral, and a referral with no question. Held-out notes, made up by someone who didn't write the rules, are kept outside this repository. No failure rate is reported until they have been run.

## Roadmap

1. **Notes to draft letter, with template and brand packs** (v0.1, this repo). Only a made-up example brand ships.
2. Held-out runs with a reported failure rate.
3. Any path to real data, as a separate review.

## License

MIT.
