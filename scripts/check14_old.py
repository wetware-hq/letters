"""Check 14 BEFORE simplification (round 13 step 5), kept for the side-by-side run. Executed inside the letters
module namespace by scripts/check14_sbs.py; never imported by letters.py."""

def flag_cuts_old(md, dropped, flags, title, text=None):
    """Every cut of a sensitive item carries a flag that blocks --final (line ids only). A result line cut as
    sensitive gets a cut record and its own flag, both on screen only; --keep-out L<n> clears that one flag."""
    lids = list(dict.fromkeys(i for d in dropped if any(c in d for c in SENSITIVE)
                              for i in re.findall(r"\b[LR]\d+\b", d.split(":")[0])))
    if text is not None and lids:
        src = _source_lines(text)
        results = [l for l in lids if src.get(l, (None,))[0] == "result"]
        lids = [l for l in lids if l not in results]
        for l in results:
            _SENS_CUTS.append(l)
            if l in _KEEP_OUT:
                _KEPT_OUT.append(l)
            elif flags is not None:
                flags.append(SENS_RESULT_NOTICE.format(lid=l))
    if not lids:
        return md
    if flags is not None:
        flags.extend(CUT_NOTICE.format(lid=l) for l in lids)
    return md          # round 11 freeze: the cut flag is on screen only and blocks --final; never in letter.md


def apply_cut_rule_old(md, text, dropped, flags, title=None):
    """For a cut item (never in patient letters):
    - medication line: a listed brand becomes its generic; a reason naming the cut item becomes 'reason not included';
      other linked words on that line are left out; the medicine is flagged.
    - any other line: every sentence holding a linked word (term, flag-only term, medicine or brand) is left out, flagged.
    Refused only for a listed brand with no generic in the map."""
    if flags is not None:                                 # H22: a reason cut from a medicine line: screen flag only
        for d in dropped:
            m = re.match(r"^([LR]\d+(?:,[LR]\d+)*): indication for .* cut \(", d)
            if m:
                flags.append(MED_NOTICE.format(lid=m.group(1).split(",")[-1]))
    items = _cut_items(dropped)
    if not items:
        return md
    brands = _brand_map(items)
    generics = [m for v in items.values() for m in v.get("medicines", [])]
    hard = [t for v in items.values() for t in v.get("terms", [])]
    words = hard + [t for v in items.values() for t in v.get("flag_terms", [])]
    linked = sorted(set(words + generics + list(brands)), key=len, reverse=True)
    rx = lambda ws: re.compile("|".join(term_pattern(w) for w in ws), re.I)
    any_linked = rx(linked)
    any_word = rx(sorted(set(hard), key=len, reverse=True)) if hard else None      # left out even on a medication line
    gen_rx = rx(sorted(set(generics + list(brands.values())), key=len, reverse=True)) if generics or brands else None
    src = _source_lines(text)
    lines, left_out = md.split("\n"), []
    def note(ids, pat):
        lid = next((i for i in ids if i in src and pat.search(src[i][1])), ids[0])
        left_out.append(lid)
    for n, line in list(_body_lines(md)):
        ids = _line_ids(line)
        text_part, id_part = split_ids(line)
        lead = re.match(r"^(- |\d+\. |\| )?", text_part).group(0)
        body = text_part[len(lead):]
        if any(i in src and src[i][0] in MED_TAGS for i in ids):
            if not any_linked.search(FLAG_ANY.sub("", body)) and "reason not included" not in body:
                continue
            for b, g in brands.items():
                if re.search(rf"(?<![A-Za-z0-9]){re.escape(b)}(?![A-Za-z0-9])", body, re.I):
                    if not g:
                        lid = next((i for i in ids if i in src and re.search(re.escape(b), src[i][1], re.I)), ids[0])
                        raise Refused(LINKED_REFUSED.format(lid=lid))
                    body = re.sub(rf"(?<![A-Za-z0-9]){re.escape(b)}(?![A-Za-z0-9]){DRUG_FLAG_RX}", g, body, flags=re.I)
            # the medicine points to the cut item, so its stated reason is dropped entirely, with no marker (H22)
            body = re.sub(r"\s*\(for [^()]*\)", "", body)
            # a kept stop or change reason still goes through the cut rules (round 10)
            body = re.sub(r"Reason for change: ([^\[]*?)\.(?=\s|$)",
                          lambda m: "Reason for change: reason not included." if any_linked.search(m.group(1))
                          else m.group(0), body)
            if any_word:                                 # other linked words on the medication line: only that part goes
                bare = lambda p: FLAG_ANY.sub("", p)          # hard terms never include a bare generic
                parts = re.split(r",\s*(?![^\[]*\])(?![^()]*\))", body)       # not inside a flag or bracket
                first, rest = parts[0], parts[1:]
                kept = [p for p in rest if not (any_word.search(bare(p)) and p.strip() != "reason not included")]
                orig_first = first
                if any_word.search(bare(first)):
                    if not any_word.search(first):           # a flag sits inside the term: match on plain text
                        first = bare(first)
                    drug_word = FLAG_ANY.sub("", first).split()[0] if FLAG_ANY.sub("", first).split() else ""
                    first = re.sub(r"\s*\([^()]*\)", lambda m: "" if any_word.search(bare(m.group(0))) else m.group(0), first)
                    first = re.sub(r"\s+", " ", any_word.sub(lambda m: m.group(0) if gen_rx and gen_rx.fullmatch(m.group(0))
                                                            else "", first)).strip()
                    if not drug_word or not re.search(rf"(?<![A-Za-z0-9]){re.escape(drug_word)}(?![A-Za-z0-9])", first):
                        lid = next((i for i in ids if i in src and src[i][0] in MED_TAGS), ids[0])
                        raise Refused(LINKED_REFUSED.format(lid=lid))   # the medicine itself would disappear
                if len(kept) < len(rest) or first != orig_first:
                    body = ", ".join([first] + [p.strip() for p in kept])
                    lid = next((i for i in ids if i in src and src[i][0] in MED_TAGS), ids[0])
                    body = f"{body} {MED_PART_FLAG}"
                    if flags is not None:
                        flags.append(MED_PART_NOTICE.format(lid=lid))
            if flags is not None:                         # H22: the medicine flag is on screen only
                flags.append(MED_NOTICE.format(lid=next((i for i in ids if i in src and src[i][0] in MED_TAGS), ids[0])))
            lines[n] = lead + body + id_part
            continue
        if all(src.get(i, (None,))[0] == "allergy" for i in ids):   # allergies are safety facts: never left out
            if any_linked.search(FLAG_ANY.sub("", body)) and MED_FLAG not in body:
                lines[n] = lead + body + f" {MED_FLAG}" + id_part
                if flags is not None:
                    flags.append(MED_NOTICE.format(lid=ids[0]))
            continue
        sents = re.split(r"(?<=[.!?])\s+", body)
        keep = [x for x in sents if not any_linked.search(FLAG_ANY.sub("", x))]
        if len(keep) < len(sents):
            note(ids, any_linked)
            lines[n] = (lead + " ".join(keep) + id_part) if keep else None
    if left_out:
        for lid in dict.fromkeys(left_out):
            if flags is not None:
                flags.append(LEFT_OUT_NOTICE.format(lid=lid))
        head = f"**Letter:** {title}" if title else None
        out = [l for l in lines if l is not None]
        i = next((k for k, l in enumerate(out) if head and l.startswith(head)), 0)
        out[i] = f"{out[i]} {LEFT_OUT_FLAG}"
        return "\n".join(out)
    return "\n".join(lines)


