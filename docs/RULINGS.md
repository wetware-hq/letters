# letters rulings (copied from signed sources; the signed source wins if they ever differ)

## Check 2: combination-medicine refusal (safety, signed by Physician)
Source: Physician, room message 14 Oct 2026, 21:22 (UTC+11), scope signed by Physician and Director. First added 13 Oct 2026.

- Any note line naming a listed combination medicine is refused, in every letter type and for every reader,
  including patient letters. The refusal gives the line number only, never the medicine:
  "Refused: line L<n> has a combination medicine that letters can't fully list yet. Nothing was written."
- No letter is made and the medicine appears in no output.
- The patient sees PV-LETTER-BY-HAND only, with no medicine, condition or check details. A CQ-LETTER-BY-HAND
  clinician entry stays open until a letter is written by hand (wording: forms_orders_lines_SIGNED.md, round 12).
- The list lives in the locale pack (locale/<pack>/combination.toml) with a source and a reviewer sign-off.
  letters refuses to run if the list, its source or its sign-off is missing.

### Signed refusal lines
- COMBO_PACK_REFUSED, the missing-pack line, was awaiting sign-off. Physician (safety) and Educator (wording) signed it
  on 14 Oct 2026; it is also used when the list lacks a source or sign-off. It stays clinician-only.

### Lift conditions (all needed; lifting is out of scope for this release)
1. A combination table in the locale pack, checked against each country's product information.
2. A test that the letter names every part of each combination.
3. The refusal tests kept alongside.

Until lifted, the 5 strict xfail tests that rely on these brands stay as they are.


## Refusal lines (room rulings, 14-15 Oct 2026)
Sources (read-only, the signed files): letters_refusals_SAFETY.md (Physician) and letters_refusals_WORDING.md (Educator).
Registry: data/refusal_lines.toml (44 codes).

- Signed in both files and used word for word: 38 ids (KEEP_OUT_ARG covers 2 sites) plus copy_refusal (both endings).
  Round 2 (15 Oct) released CHECK_SAFE_MULTI_ID, CHECK_DOSE_UNIT and copy_refusal ("This tool can't send copies.").
- Round 3 / final 5 (15 Oct): INCIDENT, COMBO_REFUSED ("this tool"), both PRIVACY_BLOCKED lines signed in both files.
- Not yet signed in both files: SENSITIVE_REFUSED. Educator's round 3 text (adds "Remove the item from the brief, or
  use one from the list.") is marked "needs Physician confirm"; Physician's file signs only the earlier text.
- test_refusal_codes_signed_in_both_files is a plain test and fails until both files cover every line.
  scripts/pre-push (installed as .git/hooks/pre-push) blocks a push while it fails. The branch wip/letters merges into
  main only once it passes.

### Patient letters fail closed (Physician, 15 Oct 2026; reader ids replace the word rule)
On a refusal the letter takes the by-hand route (PV-LETTER-BY-HAND on stdout, the only patient-visible output, and
CQ-LETTER-BY-HAND open for the clinician) when: --to or --audience names the patient; any reader or copy recipient's
id (--reader-id) is the patient's (notes id or card id) or holds a grant to act for them in the cards grant data
(--card; a grant with a rel); any reader has no id or an id that is not a reader kind (data/readers.toml); or the
brief can't be read. No name is ever matched. The refusal line goes to stderr only; nothing is written to disk.
cards' grant rels are parent, guardian and sdm; cards has no "carer" rel, so a carer can only count once cards adds it.

### Conditions that can never be true
None found. Every refusal line has a test that triggers its condition.
