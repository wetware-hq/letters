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

---

## Engineering specification

Settings read: `LETTERS_TRUTH`/`TRUTH_DIR` (scenario answer files), `WORDING_DIRS`, `REUSED_SIGNED` (signed wording), `LETTERS_CARDS_DIR`, `LETTERS_CLOCK`, `LETTERS_SCENARIO_CLOCK`, `LETTERS_LOCAL`. If `LETTERS_LLM_PROVIDER` is set, letters exits: it makes no model calls. The ward runs letters as its CLI.

## Contract

| Slot | Direction | Type |
|---|---|---|
| **notes** | input | raw text, synthetic only (`SYNTH-` ids, 1937 dates) |
| **brief** | input | "<kind> to <reader>", e.g. "discharge to GP" or "update to patient"; optional `include: <item>` lines |
| **template** | input, optional | section layout; required sections are added back if missing |
| **brand** | input, optional | name, address, logo, fonts and colours; drafts show "DRAFT, unsigned" |
| **letter** | output | one Markdown file, ready to convert to PDF or HTML |
| **dropped** | output | line numbers shown on screen to the clinician only; never written to disk |

The output folder holds only `letter.md`. Identical input gives an identical letter.

## Rules

- **Sources.** Every sentence names the note lines it came from. A sentence the checker can't match is refused, never softened.
- **Uncertainty stays uncertain.** "?" and "possible" are never written as a diagnosis.
- **Question first.** A referral opens with what the writer is asking; without one, it fails.
- **Trends** are written as values with dates, never as a direction.
- **Labels** such as "non-compliant" become a plain description or are dropped. Write as if the patient will read it.
- **Abbreviations.** An abbreviation is spelled out only if it's on a short reviewed list of abbreviations with one meaning in every specialty. Every other abbreviation stays as written and is flagged for the signing clinician. `--final` won't run while a flag is open.
- **Style.** Sentences are short, and every number has a unit. A letter is about one page.
- **Copies.** letters doesn't send copies. A copy request in the brief, template or notes is refused, so nobody believes a reader was copied in when they weren't. Write a separate letter for each reader.
- **Footer.** Every letter ends with an unsigned clinician block and "Draft: check against the record before sending."
- **No model calls** in v0.1. An optional model rewrite may come later, off by default, and must pass the same checker.

## Brief

Write the brief as "<kind> to <reader>", for example "discharge to GP" or "update to patient". Any other brief is refused, and the brief is never repeated back:

> Refused: brief not understood. Write it as "<kind> to <reader>", for example "discharge to GP" or "update to patient".

"to patient" makes a patient letter, and every patient rule applies.

## Sensitive items

The sensitive items are HIV status, psychiatric history and PrEP. Their linked words, medicines and brands are listed in `linked_terms.toml`, version 2. The file is frozen and checksummed in `LINKED_TERMS.SHA256`. Its header reads "Version 2 signed off by the clinical reviewer, 13 Oct 2026. Frozen." Any change needs fresh sign-off.

- **What marks an item.** A refuse-level term, a linked medicine or a linked brand anywhere in the notes marks its item, even when no diagnosis is written. Words with more than one meaning are flag-only and mark nothing.
- **Default.** If the brief says nothing about a marked item, it is cut for every reader except the patient. That includes the GP.
- **Keeping an item.** `include: psychiatric history` keeps that item in a letter to someone other than the patient. Add it only once you've confirmed this reader needs it and, where required, the patient has agreed. letters shows that the line was there. It can't check consent; that stays with the signing clinician. On screen it prints "Included by the brief: psychiatric history." It names the item only.
- **Other lines.** A sentence holding a linked term for a cut item is left out and listed by line number: "Flag: a sentence on line L9 was left out to keep something private. Check it doesn't hold a safety fact the reader needs."
- **Medicine lines.** The drug, dose and frequency always stay. A brand becomes its generic. Only the part holding the term is left out: "Flag: part of the medicine entry on line L5 was left out to keep something private. The medicine and its dose are still listed. Check nothing the reader needs was lost." If the drug itself would disappear, the letter is refused.
- **A medicine that hints at a cut item:** "Flag: a medicine on line L7 may tell the reader about something that was cut from this letter. Keep it, or explain it to the reader."
- **A word with more than one meaning**, flagged only when its item was cut: "Flag: a word on line L6 may tell the reader about something that was cut from this letter. Keep it, or reword it."
- **Leftovers.** "Refused: line L9 still mentions something this letter was meant to cut. Nothing was written."
- **Patient letters** skip every cut rule and keep everything.

## Reasons for medicines

- "reason not included" means the notes gave a reason, and it was cut on purpose.
- "reason not documented" means the notes gave no reason.

A medicine never gets both. letters never fills in a reason it can't see.

## Other checks

- **Drug names.** A drug name not in the word lists is flagged: "Flag: a drug name on line L3 may be misspelled. Check it." A listed brand with no generic is refused only when its item was cut.
- **Allergies.** "reaction not documented" is used only when nothing follows the drug name. Otherwise: "Flag: the allergy on line L4 has extra wording. Check whether it describes the reaction." A reaction or owner the letter would leave out is refused: "Refused: line L12 has an allergy reaction or a follow-up owner that this letter leaves out. Nothing was written."
- **Pending tests.** A test is pending only if no later line gives its result. Pending tests always go in the pending section with their owner. "Refused: line L11 has a test still pending, but this letter says none are. Nothing was written." Wording around the owner is flagged: "Flag: the pending test on line L8 has follow-up wording. Check who follows it up."
- **Referral questions.** "Flag: this referral has no clear question. Add one before it's signed." "Please answer the question below" appears only when there is a question.
- **Long sentences.** "Flag: sentence at L12 is over 25 words. Split it, or keep it as written."

## Messages

Every message gives line numbers only, never note text. The dropped list prints as "Cut from this letter: L3, L9, L14." Every flag blocks `--final`.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pytest -q
python letters.py fixtures/template/chest_pain.txt --brief "referral to cardiology"
python letters.py fixtures/template/podiatry_referral.txt --brief "referral to podiatry" \
  --template templates/minimal.md --brand brands/example-clinic --out out/
python letters.py fixtures/template/pneumonia_admission.txt --brief "discharge to GP" \
  --resolve "SOB=shortness of breath" --resolve "GP=general practitioner" \
  --final   # every flag resolved, so --final runs (it refuses while any flag is open)
```

`--final` removes source tags only. The draft mark stays until the signing clinician removes it.

## Evaluation

Regression tests plant each failure: an unsourced fact, a stopped medicine, a negation, "?PE", a missing allergy, a pending result, a label, a sensitive detail in an unrelated referral, and a referral with no question. Held-out cases use invented people, are written by someone who didn't write the rules, and are kept outside this repository. Results are reported as counts, such as "2 of 12 failed", never as rates. Held-out runs go through a version-checked runner outside the repo. It refuses a stale commit or uncommitted changes and logs the commit next to each output.

## Roadmap

1. **Notes to draft letter, with template and brand packs** (v0.1, this repo). Only a made-up example brand ships.
2. Held-out runs reported as counts.
3. Any path to real data, as a separate review.

## License

MIT.

## Notes template

`letters` reads notes only in this fixed template. Fields start a line, in this order, and every heading must be there:

```
Patient: SYNTH-0000
Date: 1937-01-01
Recipients: <exactly one reader>
Question: <free text, or none>
Problems:
- <one per line>
History: <free text>
Medicines:
- drug dose frequency [route] [max X in 24 hours | max X per <period>] [for N days] [reason: <why>] [; stopped|ceased|withheld <date> - <stop reason>]
Allergies:
- allergen: reaction      (or '- allergen', or the single line 'No known allergies')
Results:
- test value unit date
Pending:
- test, sent <date>, owner
Actions:
- action, owner
```

A list may be empty: the letter says "none recorded". An empty Allergies or Medicines field is also flagged and blocks `--final`.
Anything else is refused, naming the first line that doesn't fit.

`--not-copy L<n>` is a recorded override, not a way around the copy check. When a line is refused because it looks like
a copy request but isn't one, rerun with `--not-copy L<n>` (one line per flag). It prints
"Line L<n> marked as not a copy request" beside the cut list and still never sends a copy. It can't be used on a line
that names someone to send the letter to. Don't edit clinical notes to get past a check.

## Checking notes against the template

`python -m check_template <notes> [--brief brief.txt] [--not-copy L<n>]` (run from this folder) checks notes without writing a letter. It is not a second implementation: it runs the same copy, send and recipient checks and the same template parser (`from_template`) that every letter uses, in the same order. Exit 0 prints "Template check passed."; exit 2 prints the signed refusal with the line number. Nothing is written.

- Not for use with real patients.
- S05 and S07 were checked with a scripted stand-in, not a real AI model. The scripted runs show the checks catch the mistakes those two scenarios are for. They don't show how a real model behaves. Those tests are still to come.
