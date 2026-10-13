# Word lists

Used by the abbreviation step: any lowercase word not in these lists is flagged for the signing clinician.

`SHA256SUMS` records the checksum of each list; a test fails if a list changes without updating it.

| File | Source | Licence |
| --- | --- | --- |
| `english.txt` | SCOWL 2020.12.07 (Kevin Atkinson, wordlist.aspell.net), `final/` english, american, british and australian `words` and `contractions` lists up to size 60, lowercase entries only, merged and sorted | SCOWL permissive licence; full notice in `SCOWL-COPYRIGHT` |
| `medical.txt` | written for this project | repo licence |
| `../shorthand.txt` | written for this project; prescribing shorthand that is always flagged, reviewed by clinician | repo licence |
