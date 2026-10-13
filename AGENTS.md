---
schema: wetware.agent.v1
resource: letters
version: 0.1.0
capabilities: [draft]
inputs:
  notes: {type: text, constraint: "synthetic only: SYNTH- ids, 1937 dates"}
  brief: {type: text, example: "referral to cardiology"}
  template: {type: file, optional: true}
  brand: {type: directory, optional: true, constraint: "example brand only in repo"}
outputs:
  letter: {type: markdown, constraint: "unsigned draft"}
  dropped: {type: screen, constraint: "never written to disk"}
---

# Agent contract

Normative rules for agents that operate or modify letters. Wording uses RFC 2119: **MUST**, **MUST NOT**, **SHOULD**. Not approved for real patient data.

## Data handling

1. Real notes **MUST NOT** enter agent context. Removing a name does not make a note synthetic.
2. If a user pastes real notes, the agent **MUST** stop, **MUST NOT** repeat or process them, and **MUST** tell the user to report a privacy incident.
3. The dropped list **MUST NOT** be written to disk, bundled into the letter, or kept after the run.
4. The output folder **MUST** hold only the letter.

## Pipeline

1. Read: number every note line (L1, L2…); refuse non-synthetic input.
2. Record: keep the latest state per problem, medicine, result, allergy and plan, with source lines; handle negation and reversal; keep "?" and "possible" as uncertain.
3. Filter by letter type and reader; every cut goes to the dropped list with a reason.
4. Group into sections; a referral **MUST** open with its question.
5. Write from templates in plain style; add back any required section a template lacks.
6. Check: every sentence **MUST** cite source lines; refuse any number, medicine, date or diagnosis not in them, and any added certainty.

## Never cut

Allergies with reaction; full current medication list; reason for each change; pending results with owner; follow-up actions with owner and date. Missing items **MUST** read "not documented" (allergy reaction: "reaction not documented").

## Writing

- Abbreviations **MUST** stay as written and be flagged, unless listed in `single_meaning.toml` (each entry signed off by the physician; patient letters use its signed plain-words version). Anything that may be an abbreviation **MUST** be flagged: any case variant of a listed entry, any word of two or more letters in capitals or with an inner capital, any entry in `shorthand.txt` (clinician-reviewed), the single letter u/U, and any word not in the committed English or medical word lists (checksummed; drug names included). A clinician closes a flag with `--resolve "ABBR=meaning"`, which becomes a cited source. `--final` **MUST** refuse while any flag is open, and **MUST** strip source tags only.
- Trends **MUST** be values with dates, never a direction.
- A sensitive indication **MUST** be cut when the reader doesn't need it; the medicine stays.
- Stigmatising labels **MUST** be rewritten neutrally or dropped.
- A copy request (brief, template slot, or cc line in notes) **MUST** be refused with exit code 2 and nothing written; it **MUST NOT** be dropped silently. Letters to the patient **MUST** end with: "This letter includes private details about your health. It's yours to share or keep private."
- Every letter **MUST** end with an unsigned clinician block and "Draft: check against the record before sending."
- Branded drafts **MUST** show "DRAFT, unsigned" beside the logo.

## Changing code

- `pytest -q` **MUST** pass before commit.
- Every rule **MUST** have a test that plants its failure.
- Fixtures and held-out notes **MUST** use made-up people.
- Model wording, if added, **MUST** be off by default, **MUST** pass step 6, and **MUST NOT** grade its own output.

## Out of scope

- Real notes, signing, sending, or storing letters
- Hosted services or vendor APIs
- Clinical advice
