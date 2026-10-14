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
Sources (read-only, the room's signed files): letters_refusals_SAFETY.md (Physician) and letters_refusals_WORDING.md (Educator).
Registry: data/refusal_lines.toml (44 codes).

- Signed in both files and used word for word: 38 ids (KEEP_OUT_ARG covers 2 sites) plus copy_refusal (both endings).
  Round 2 (15 Oct) released CHECK_SAFE_MULTI_ID, CHECK_DOSE_UNIT and copy_refusal ("This tool can't send copies.").
- Not yet signed in both files: INCIDENT (both reasons signed; the fixed text waits for Educator after the Engineer quote),
  COMBO_REFUSED, PRIVACY_BLOCKED[missing], PRIVACY_BLOCKED[unset], SENSITIVE_REFUSED (signed under earlier rulings;
  to be copied into both files once Engineer quotes them).
- test_refusal_codes_signed_in_both_files is a plain test and fails until both files cover every line.
  scripts/pre-push (installed as .git/hooks/pre-push) blocks a push while it fails. The branch wip/letters merges into
  main only once it passes.

### Patient letters fail closed (Physician, 15 Oct 2026)
A request counts as a patient letter if --to or --audience names the patient, if the patient appears anywhere in the
brief (as a reader or a copy recipient), or if the brief can't be read. Any refusal on such a request prints
PV-LETTER-BY-HAND (stdout, the only patient-visible output) and opens CQ-LETTER-BY-HAND for the clinician with the
check number from the registry. The refusal line goes to stderr only. Nothing is written to disk.

### Conditions that can never be true
None found. Every refusal line has a test that triggers its condition.
