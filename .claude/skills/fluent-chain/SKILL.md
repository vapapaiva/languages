---
name: fluent-chain
description: Structured full-skill chain session that covers all 5 skills in fixed order — Review → Vocab → Writing → Speaking → Reading. Each block runs for a fixed slice of the learner's daily_study_minutes. Use when the learner wants systematic coverage of every skill rather than an adaptive mix. Triggered only when the learner types /fluent-chain.
allowed-tools: Read, Write, Bash
disable-model-invocation: true
---

# Full-Skill Chain Session

## Overview

Runs all 5 skills back-to-back in a predictable sequence. Good for learners who want to know exactly what's coming and ensure no skill gets neglected. Unlike `/fluent-learn` (adaptive mix driven by weak spots), this is a structured workout — every session hits every skill.

## When to Use

Only when the learner types `/fluent-chain`. Gated — this is a full interactive session that writes to all 6 databases.

## Instructions

### 1. Load context

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-.}}/.claude/hooks/read-db.py"
```

If any DB is missing → direct to `/fluent-setup` and stop.

**Check `computed.orphaned_partials[]`.** If any partial belongs to a previous date, surface it before opening a new `session_id`:

> Found an unfinished session from {date} ({command_used}) with {N} graded items and {M} errors. Commit it to your stats now? (Y/n)

If yes → call `update-db.py --commit` with that `session_id` + minimal payload (date + a `duration_minutes` estimate). If no → delete the partial. Either way, then open the new `session_id`.

### 2. Calculate time slices

Read `learner_profile.daily_study_minutes` (default 30). Distribute:

| Block | Slice | Min time | Max time |
|-------|-------|----------|----------|
| 🔄 Review   | 20% | 5 min  | 10 min |
| 📖 Vocab    | 20% | 5 min  | 10 min |
| ✍️ Writing  | 25% | 6 min  | 12 min |
| 🗣️ Speaking | 20% | 5 min  | 10 min |
| 👀 Reading  | 15% | 4 min  | 8 min  |

For 30 min → Review 6 · Vocab 6 · Writing 8 · Speaking 6 · Reading 4.
Round to whole minutes; give leftover to Writing.

### 3. Greet + show chain

```markdown
# 🔗 ¡Cadena completa, {name}!

**Hoy's chain — {total} min:**
🔄 Review    {X} min  — {N} items due
📖 Vocab     {X} min  — {N} new words
✍️ Writing   {X} min  — sentences + text
🗣️ Speaking  {X} min  — dialogue / questions
👀 Reading   {X} min  — text + comprehension

**Streak:** 🔥 {N} days

¿Listo? Empezamos con Review.
```

### 4. Run each block in sequence

Announce each transition clearly:

```markdown
---
## 🔄 Block 1 — Review ({X} min)
---
```

**Durability — checkpoint after every meaningful interaction.** See `fluent-db-updater` for full semantics. The rule for this skill:

- After **each block** finishes → `update-db.py --checkpoint` with the running `skill_scores` for every skill touched so far, plus that block's `review_results[]` / `new_vocabulary[]` / `errors[]`.
- After **each non-trivial mid-block interaction** that produces durable state (a graded answer, a learner-confirmed new word) → also checkpoint. Cheap, idempotent, crash-safe.
- The vocab-review batch in Block 1 is a single interaction — one checkpoint covers all N items.

Do NOT call `--commit` between blocks. Only ONE `--commit` at session end (step 7).

#### Block 1 — Review

- Pull `computed.due_review_items` from read-db output (these are item IDs — look up full records in `spaced_repetition.items`).
- If 0 due → skip block, note it, add its time to Vocab.
- Cap at `daily_limits.review_items_per_day` (default 20).
- **Batch all atomic exercises into one numbered list** (vocab recognition + cloze + single-sentence error correction + multiple choice). The learner answers all in one message. See `fluent-review` SKILL for the canonical batch format and the atomic-vs-constructed rule.
- Only break out a non-atomic item (rare — e.g. a pattern that needs a multi-line discourse scenario) as a separate one-at-a-time prompt after the batch.
- After the learner answers, produce a single combined feedback table (one ✅/❌ + correction + score per row) — invoke `fluent-feedback-formatter` once per batch.
- Score every item with SM-2 via `fluent-sm2-calculator`. Stage one `review_results[]` entry per item.
- **Checkpoint after the batch** (one write covers all N items).

Vocabulary batch template:

```markdown
## 📖 Vocabulary Review — {N} items

Answer all in one message. Just write the number + meaning, one per line.

1. {content_1}
2. {content_2}
...
{N}. {content_N}
```

#### Block 2 — Vocab

- Introduce 3–5 new words relevant to the learner's goal (`learning_goal`) and current level.
- For each word: show in context sentence first, then drill recognition → production.
- One exercise at a time. Use `fluent-feedback-formatter` after each answer.
- Stage `new_vocabulary[]` for DB update.

#### Block 3 — Writing

- 1 short exercise (sentence or paragraph) targeting the top weak grammar pattern from `mistakes_db.error_patterns` (sort by frequency desc).
- 1 freer writing task (3–5 sentences) on a topic relevant to `learning_goal`.
- Use `fluent-feedback-formatter`. Stage `errors[]` for any new mistakes.

#### Block 4 — Speaking

- 3–4 questions to answer in Spanish (typed). Mix personal questions with situational prompts.
- If `learning_goal` = `living_in_country` → favour practical scenarios (shops, neighbours, appointments).
- Use `fluent-feedback-formatter`. One question at a time.

#### Block 5 — Reading

- Present a short text (6–10 sentences) at the learner's current level + 1.
- **Batch** 3 comprehension questions in one numbered list (1 factual, 1 vocabulary-in-context, 1 inference). Learner answers all in one message. Use `fluent-feedback-formatter`'s batched table (§1b). See `fluent-reading` SKILL for the canonical format.

### 5. Transition announcements

Between blocks:

```markdown
---
✅ Block {N} done! ({correct}/{total}, {accuracy}%)
Moving to Block {N+1} — {name} ({X} min)
---
```

### 6. Session end

```markdown
## 🎉 ¡Cadena completa!

| Block     | Score       |
|-----------|-------------|
| 🔄 Review  | {X}/{Y} ✅  |
| 📖 Vocab   | {X}/{Y} ✅  |
| ✍️ Writing | {X}/{Y} ✅  |
| 🗣️ Speaking| {X}/{Y} ✅  |
| 👀 Reading | {X}/{Y} ✅  |

**Total: {correct}/{total} ({accuracy}%)**
**Streak: 🔥 {N} days**
**New words added to SR: {N}**

**Focus next time:**
- {top pattern from today}

¡Hasta mañana! 👏
```

### 7. Final commit

All graded data is already on disk (the per-block checkpoints did the work). At the very end, fold in session-final fields with **one** `--commit`:

```bash
python3 .claude/hooks/update-db.py --commit <<'EOF'
{
  "session_id": "session-{NNN}", "date": "{today}",
  "duration_minutes": {total_minutes},
  "command_used": "/fluent-chain",
  "breakthroughs": [...],
  "focus_next_session": [...],
  "session_notes": "Chain session — {summary of what happened}"
}
EOF
```

The script merges the partial + this payload → runs the canonical update flow ONCE → deletes the partial.

Save exchange to `/results/fluent-chain-session-{NNN}.md`.

## Adaptive difficulty within blocks

Same rules as `/fluent-learn`:
- Rolling accuracy < 50% → easier exercises, more scaffolding
- 50–70% → hold
- > 70% → raise difficulty

## Critical Rules

- **Batch atomic exercises; one-at-a-time only for constructed/multi-sentence work.** Atomic = single word, short phrase, letter choice, one-sentence correction. Batch those into one numbered list. One-at-a-time only for free writing, speaking turns, reading text, role-play — anything where the learner constructs a multi-sentence answer. Block 1 review is almost entirely atomic → one batch. Block 2 new-vocab introduction is per-word (each needs a context sentence + drill). Block 3 writing, Block 4 speaking, Block 5 reading — constructed → one at a time per question.
- **Never skip the feedback step** — use `fluent-feedback-formatter` on every answer.
- **Announce every block transition** — the learner needs to know where they are in the chain.
- **If a block is skipped** (e.g. 0 reviews due) → say so explicitly and redistribute time.
- **Never auto-invoke** — full session + DB writes, must be explicit `/fluent-chain`.
- **Always save to `/results/`** — chain sessions are longer; the session file is valuable for `/fluent-session-analyzer`.
- **Checkpoint after every block, commit once at the end.** A crashed chain session leaves graded data safely in the partial; next session's startup check offers to commit it.
