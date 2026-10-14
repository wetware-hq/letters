# Rules: safety checks

The 19 safety checks in `docs/SAFETY_CHECKS.md`, each with its signer, class and the tests that pin it.
`python3 scripts/rulecheck.py` exits 0 only if every row lists at least one test and every listed test exists.

| # | Check | Signer | Class | Tests |
|---|---|---|---|---|
| 1 | Synthetic-data guard | Physician | safety | test_refuses_non_synthetic, test_fixtures_synthetic_1937 |
| 2 | Combination-medicine refusal | Physician | safety | test_combination_brand_refused, test_combination_brand_cli_writes_nothing, test_r12_letter_command_inline_and_file_brief_identical |
| 3 | Copy and recipient refusals | Physician | safety | test_patient_letter_copy_via_brief_refused, test_patient_letter_copy_via_template_refused, test_patient_letter_copy_via_notes_refused, test_gp_letter_copy_via_brief_refused |
| 4 | Template check | Physician | safety | test_r10_template_refusals_name_first_line, test_r10_template_free_text_notes_refused |
| 5 | Unknown Include: item | Physician | safety | test_include_line_keeps_item, test_r12_check_template_file_brief_matches_inline_parse |
| 6 | Number-without-unit drop and screen line | Physician | safety | test_result_without_unit_dropped_and_logged |
| 7 | Results line with no value (sensitive first, unreadable flag) | Physician | safety | test_r12_result_without_value_kept_word_for_word_and_cited, test_r12_sensitive_result_without_value_cut_in_gp_letter, test_r12_sensitive_result_without_value_kept_in_patient_letter, test_r12_unparseable_result_flagged |
| 8 | Sensitive cut at source | Physician | safety | test_sensitive_kept_only_when_brief_includes_it, test_r13_audit_leak_check_source_side, test_r11m_sensitive_result_cut_record_and_flag_on_screen |
| 9 | Line checker (numbers, dates, words, flags) | Physician | safety | test_planted_unsourced_sentence_refused, test_10u_insulin_flagged_and_checker_refuses_unflagged, test_checker_messages_line_only_no_letter_text |
| 10 | Required details (reactions, pending owners) | Physician | safety | test_missing_required_detail_refused_names_line_only, test_pending_owner_kept |
| 11 | Pending "none" refusal | Physician | safety | test_letter_saying_none_pending_while_pending_refused |
| 12 | Medicine, dose, maximum, allergy, result, action check | Physician | safety | test_r8_guard_dose_dropped_refuses, test_r8_guard_dose_changed_refuses, test_r8_guard_medicine_missing_entirely_refuses, test_r13_audit_dose_check_same_with_and_without_plain_words |
| 13 | Reverse check | Physician | safety | test_r10_reverse_check_refuses_with_letter_line, test_r10_reverse_check_names_from_brief_allowed_but_not_invented |
| 14 | Cut rule on the letter | Physician | safety | test_off_med_line_linked_sentence_left_out_and_flagged, test_sensitive_cut_everywhere_medicine_stays_and_flagged, test_r13_audit_leak_check_output_side_introduced_leak |
| 15 | Leak refusal | Physician | safety | test_r13_audit_leak_refusal_on_finished_letter, test_reg_h3_cut_diagnosis_on_med_line_never_leaks |
| 16 | Long sentence | Physician | safety | test_planted_long_sentence_flagged_not_refused |
| 17 | Empty allergies or medicines field, question flag | Physician | safety | test_r10_empty_allergies_and_medicines_non_patient, test_referral_without_question_flagged_and_final_refused |
| 18 | --final gate | Physician | safety | test_final_removes_tags_keeps_draft_mark_and_footer, test_default_cut_for_gp_and_flagged_blocks_final |
| 19 | Privacy line (explicit privacy field) | Physician | safety | test_c19_privacy_true_line_missing_blocks_final, test_c19_privacy_false_passes_unblocked, test_c19_privacy_field_absent_on_patient_letter_blocks_final |
