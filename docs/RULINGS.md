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

### Lift conditions (all needed; lifting is out of scope for this release)
1. A combination table in the locale pack, checked against each country's product information.
2. A test that the letter names every part of each combination.
3. The refusal tests kept alongside.

Until lifted, the 5 strict xfail tests that rely on these brands stay as they are.
