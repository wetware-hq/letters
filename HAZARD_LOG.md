# Hazard log (letters)

Created in round 13; earlier hazard entries live in the project docs.

## H-R13-BP: blood pressure reading split by the renderer (round 13, fixed)

- **Defect:** a result written "blood pressure 150/90 mmHg" was rendered as "150 /90 on <date> (mmHg)". The
  reading was split, the unit moved away from the values, and a reader could take "150" alone as the value.
- **Fix:** the result parser keeps a slash reading as one value ("150/90"), so the letter prints
  "150/90 mmHg" exactly as written, in clinician and patient letters.
- **Test:** `test_r13_blood_pressure_kept_together_as_written`.
