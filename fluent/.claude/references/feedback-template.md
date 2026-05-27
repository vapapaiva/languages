# Feedback Template

Canonical per-answer feedback format used by every Fluent practice skill. Referenced by the `fluent-feedback-formatter` skill.

## Standard Template

```markdown
{✅ or ❌} {one-line encouragement or gentle correction}

**Corrections:**
- ❌ "{wrong_part}" → **"{correct_part}"** ({category} — {brief_why})
- ✅ "{correct_part}" — {specific_praise}

**Correct version:**
"{full_correct_sentence}"

**Score: {X}/10** {emoji} {short_comment}

---
```

Skip the ❌ block if the answer is fully correct. Skip the ✅ block only if truly nothing was right.

## Severity markers

| Symbol | Severity | Meaning | Example |
|--------|----------|---------|---------|
| 🔴 | Critical | Breaks communication or exam-blocker | Formal/informal mix in formal email; wrong subordinate-clause word order |
| 🟡 | Moderate | Noticeable but understandable | Preposition error, missing article |
| 🟢 | Minor | Low priority | Spelling, punctuation, accent marks |

A single answer may contain multiple errors of different severity — tag each.

## Category labels

These feed `mistakes-db.json`:

- `grammar` — word order, conjugation, clause structure
- `formal_informal` — u/je, uw/jouw, register mismatch
- `vocabulary` — wrong word, English mixing, register-wrong synonym
- `spelling` — minor
- `prepositions` — om/op/in/bij/naar/etc.
- `articles` — de/het, definite/indefinite
- `missing` — omitted greeting, closing, required word

## Tone rules

- Encourage before correcting — open with a ✅ or a warm ❌, not a bare "Wrong."
- Explain why, not just what — "Ik schrijf je" → "Ik schrijf u" (formal_informal — business emails require u)
- Name the pattern so the learner generalizes
- Celebrate progress — "You didn't miss this last time"
- Emojis on (learner default: `use_emojis: true`)

## Examples

### Mostly correct

> ✅ Nice — past tense is solid.
>
> **Corrections:**
> - 🟢 "gestern" → **"hier"** (vocabulary — small slip, you used the German word)
> - ✅ "Ik ben gegaan" — perfect auxiliary + participle
>
> **Correct version:**
> "Ik ben hier naar de markt gegaan."
>
> **Score: 9/10** 🎯 One minor swap — don't sweat it.

### Critical error

> ❌ Close, but one pattern is costing you points on the exam.
>
> **Corrections:**
> - 🔴 "Ik schrijf je omdat ik heb een vraag" → **"Ik schrijf u omdat ik een vraag heb"** (formal_informal + grammar — formal register needs "u", and "omdat" pushes the verb to the end)
> - ✅ "Ik schrijf" — correct opening verb
>
> **Correct version:**
> "Ik schrijf u omdat ik een vraag heb."
>
> **Score: 5/10** 💪 Two patterns to drill — both fixable.

## Score → SM-2 Quality

`quality = floor(score / 2)`. See `sm2-worked-examples.md`.
