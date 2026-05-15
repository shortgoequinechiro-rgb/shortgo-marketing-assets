# Short Go Post — Self-QC Checklist

> The agent runs this checklist on every rendered post before sending it to Charles for approval. Each check returns PASS, FAIL, or WARN. Any FAIL aborts the post and escalates to Charles via iMessage with the failure reason. WARN is logged but doesn't block.

> Used by the Sunday scheduled task after `compose_post.py` renders the PNG and BEFORE `send_for_approval.py` is called.

---

## How to run

1. Use the Read tool to view the rendered PNG (Claude can see images natively).
2. Re-read `business-context.md` if not already loaded.
3. Walk every check below in order. For each: state PASS / FAIL / WARN and a one-line reason.
4. If any check returns FAIL, do NOT proceed to `send_for_approval.py`. Instead, send Charles an iMessage:
   > "Post {post_id} failed QC: {check name} — {reason}. Post skipped this week."
5. If all checks pass (WARN is okay), proceed.

---

## Visual checks (look at the rendered PNG)

### Check 1 — Cornermark legibility
- Top-left text "SHORT GO EQUINE CHIROPRACTIC" + "DFW · NORTH TEXAS · MONTANA" must read clearly against whatever the background is doing at that corner.
- FAIL: cornermark is blending into the background or partially obscured by a bright/busy area.
- The top dark gradient should be doing the work. If it isn't, the agent should re-render with a different background.

### Check 2 — Service tag legibility
- Top-right "MOBILE · cAVCA-CERTIFIED" + "WE COME TO YOUR BARN" must read clearly.
- Same standard as Check 1.

### Check 3 — Hook text readability
- The big serif headline must be sharp, fully on-canvas (not cut off at edges), and stand out against the bottom-half of the background.
- FAIL: any word is truncated, illegible, or lost in busy texture.

### Check 4 — Body bullets / explainer readability
- The 3 symptom lines (educational template) or where/when lines (CTA template) must each be a single, complete line.
- FAIL: any line wraps, overflows, or overlaps with the headline.

### Check 5 — Contact block clarity
- The gold phone number `(406) 799-3369` must read clean.
- The web URL + CTA line below it must read clean.
- FAIL: phone digits look mushy or any character is unreadable.

### Check 6 — Background appropriate for the message
- An aggressive direct-CTA post (e.g., "BOOKING DFW SATURDAY") works best against a high-energy background (sunrise, dynamic light).
- A philosophical / quote post works best against atmospheric or texture backgrounds.
- An educational post works against arena interiors or barn aisles.
- WARN if the background feels mismatched (e.g., quote post over a high-contrast roping-arena pipe panel). Still passes.

### Check 7 — No AI-generated bodies/faces/hands accidentally present
- Scan the background image carefully. If the AI slipped a horse silhouette, a person's hand, a face in the dust beams, anything anatomical — FAIL.
- This is the locked rule. No anatomy ever in the library backgrounds.

---

## Text checks (compare image text against the post spec)

### Check 8 — Spelling
- Re-read every word of the headline, body lines, explainer, and contact block in the image.
- Compare letter-for-letter against the PostPlan spec.
- FAIL: any spelling drift between spec and rendered image.

### Check 9 — Caption ends with a locked CTA
- The IG/FB caption (separate from on-image text) must end with one of:
  - A "DM 'BOOK'" variant (e.g., "DM 'BOOK' to grab a spot")
  - A "Book now" variant (e.g., "Book now at shortgochiro.com")
- FAIL: caption ends with "learn more," "check us out," "link in bio" (without "book"), or any non-booking CTA.

### Check 10 — TX or MT geographic cue present
- Caption must include at least one Texas or Montana place name OR reference (e.g., "Coppell," "Frisco," "Great Falls," "Havre," "DFW," "North Texas," "Montana").
- FAIL: post is geographically generic and could be set anywhere in the country.

### Check 11 — Voice match
- Caption uses rider language: "short go," "clicking," "off behind," "stopping crooked," "lead change," "drifting" — or synonymous performance-horse vocabulary.
- Caption does NOT use clinical-textbook language: "biomechanical asymmetry," "subluxation," "musculoskeletal," "spinal manipulation."
- FAIL: caption reads like a medical brochure.

### Check 12 — Attribution rule
- Any reference to performing the adjustment / clinical work attributes to **Dr. Leo** or "our chiropractor" or "we" — never to Charles.
- FAIL: caption attributes hands-on work to Charles.

### Check 13 — No fabricated claims
- No "guaranteed results," "cures," "100% success rate," "proven to," or similar absolutist claims.
- FAIL on any of these.

### Check 14 — Length sanity check
- Caption is between 30 and 250 words. Under 30 feels thin; over 250 gets truncated in IG feed.
- WARN if outside 50–180 (sweet spot). FAIL if outside 30–250.

---

## Spec-vs-render parity checks

### Check 15 — post_type matches template used
- educational → 2-line hook + 3 bullets + explainer
- cta → 2-line hook + 1-2 location lines + 1-line explainer
- quote → 2-3 line italic-feel serif quote + attribution
- FAIL: rendered template doesn't match the declared post_type.

### Check 16 — Scheduled date is in the future
- `scheduled_at` is after current time.
- FAIL: scheduled in the past.

### Check 17 — Scheduled date is during business hours for the audience
- Best windows for performance-horse owners on IG: 6–8am or 7–9pm Central, weekdays. Weekend mornings also strong.
- WARN: scheduled outside these windows. Doesn't block — agent can override for specific occasions.

---

## Output format

The agent records its QC pass in a JSON object like:

```json
{
  "post_id": "2026-05-19-edu-001",
  "rendered_at": "2026-05-17T04:34:00-05:00",
  "checks": {
    "1_cornermark": "PASS",
    "2_service_tags": "PASS",
    "3_hook_readability": "PASS",
    "4_body_readability": "PASS",
    "5_contact_block": "PASS",
    "6_background_match": "PASS",
    "7_no_anatomy": "PASS",
    "8_spelling": "PASS",
    "9_cta_locked": "PASS",
    "10_tx_mt_cue": "PASS",
    "11_voice_match": "PASS",
    "12_attribution": "PASS",
    "13_no_fabrication": "PASS",
    "14_length": "WARN: caption is 220 words, near upper limit",
    "15_template_parity": "PASS",
    "16_future_date": "PASS",
    "17_optimal_window": "WARN: scheduled at 2pm CDT, not in 6-8am/7-9pm sweet spot"
  },
  "overall": "PASS (2 warnings)",
  "ready_for_approval": true
}
```

Save this to `~/short-go-agent/pending-approval/{post_id}.qc.json` alongside the rendered PNG.

If any check returns FAIL, set `ready_for_approval: false` and the agent skips `send_for_approval.py` for that post.
