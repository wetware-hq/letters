# Safety checks (audit, round 13)

Classes:
- **FACT** checks (doses, dates, values, refusals) must read the **source note line**.
- **OUTPUT** checks (sensitive leak, readability, long sentence) must read the **finished letter**.
- The **sensitive leak** check runs on **both**.

Plain words (signed patient wording for words, units and test names) are added only while the letter is
written. FACT checks take their facts from the source lines, so plain words never change a FACT result.

| # | Check (function) | Reads now | Class | Status |
|---|---|---|---|---|
| 1 | Synthetic-data guard (`check_safe`) | source notes | FACT (refusal) | correct |
| 2 | Combination-medicine refusal (`refuse_combination_brands`) | source notes | FACT (refusal) | correct |
| 3 | Copy and recipient refusals (`refuse_copies`, `pre_template_checks`) | source notes, brief, template | FACT (refusal) | correct |
| 4 | Template check (`from_template`) | source notes | FACT (refusal) | correct |
| 5 | Unknown Include: item (`read_brief`, `build`) | brief | FACT (refusal) | correct |
| 6 | Number-without-unit drop and screen line (`cumsum`) | source line | FACT | correct |
| 7 | Results line with no value: sensitive first, unreadable flag (`cumsum`) | source line | FACT + leak (source side) | correct |
| 8 | Sensitive cut at source (`clean_text`, `mark_linked_items`, `sensitive_result_lines`) | source lines | leak, source side | correct |
| 9 | Line checker (`check`): every number, date and word in a letter line must be in its cited source lines; shorthand and dose-unit flags present | finished letter, compared with cited source lines; signed wording mapped back to the source token first | FACT (values, dates) + OUTPUT (flags) | correct |
| 10 | Required details (`refuse_missing_required`): allergy reactions, pending owners | source lines (what is required), looked up in the letter | FACT | correct |
| 11 | Pending "none" refusal | source pending entries | FACT | correct |
| 12 | Medicine, dose, frequency, maximum, course, allergy, result, action (`verify_letter`) | own reader over the source notes (never the parser); each fact looked up in the finished letter | FACT | correct |
| 13 | Reverse check (`reverse_check`): every date, number, name and owner in the letter is in the notes | finished letter, compared with the notes | FACT | correct |
| 14 | Cut rule on the letter (`apply_cut_rule`, `drop_cut_change_reasons`, `flag_cuts`, `flag_linked_medicines`) | finished letter, terms from `linked_terms.toml` | leak, output side | correct |
| 15 | Leak refusal (`verify_letter` cut words, `LEAK_REFUSED`) | finished letter, cut items from the notes | leak, output side | correct |
| 16 | Long sentence (`flag_long_sentences`) | finished letter | OUTPUT (readability) | correct; counting of plain-word brackets is unchanged (round 14 item) |
| 17 | Empty allergies or medicines field, question flag | finished letter + template fields | OUTPUT | correct |
| 18 | `--final` gate (`finalise`) | build result (letter + every flag) | OUTPUT | correct |

Result of the audit: no check was misclassified, so no check was moved. The tests below pin the two properties
the classes depend on:
- `test_r13_audit_dose_check_same_with_and_without_plain_words`
- `test_r13_audit_leak_check_source_side`
- `test_r13_audit_leak_check_output_side_introduced_leak`
- `test_r13_audit_leak_refusal_on_finished_letter`
