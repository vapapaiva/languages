---
name: fluent-review
description: Run today's spaced-repetition review queue — items scheduled by SM-2 that need reinforcement before the learner forgets them. Triggered only when the learner types /fluent-review. Pulls due items from spaced-repetition.review_queue.today, generates a targeted exercise for each, evaluates the response, updates SM-2 parameters, and reshelves items into the correct future queue.
allowed-tools: Read, Write, Bash
disable-model-invocation: true
---

# Spaced-Repetition Review Session

## Overview

Replay items the learner learned before, timed so they hit just before the forgetting curve drops them. This is the single most effective session type — the system depends on it running daily. Items the learner gets right get pushed further into the future; items they miss come back tomorrow.

## When to Use

Trigger this skill only when the learner types `/fluent-review`. The skill is gated with `disable-model-invocation: true` — mutating SM-2 state from a misread prompt would cascade through every future session.

Skip this skill when the queue is empty — suggest `/fluent-vocab` or `/fluent-learn` instead.

## Instructions

### 1. Load review queue

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-.}}/.claude/hooks/read-db.py"
```

Read `spaced-repetition.review_queue.today` and `daily_limits.review_items_per_day`. Sort items by `priority` (critical → high → medium → low). Cap at the daily limit (usually 20).

**Check `computed.orphaned_partials[]`** — if a prior session was interrupted before commit, offer the learner commit-or-discard. (See `fluent-db-updater` for details.)

If the queue is empty:

```markdown
🎉 No reviews due today! Your spaced repetition is up to date.

Want to practice something new? Try:
- `/fluent-learn` — adaptive mixed practice
- `/fluent-vocab` — learn new words
- `/fluent-progress` — see your stats
```

### 2. Opening

```markdown
# 🔄 Today's Spaced Repetition Review

Hallo {name}! Time to review items your brain is about to forget. This keeps everything fresh. 🧠

**Items Due Today:** {count}
**Estimated Time:** ~{minutes} min

Why review? Spaced repetition prevents forgetting, moves items into long-term memory, and builds automaticity.

**Ready? Let's start!** 💪
```

### 3. Generate exercises — BATCH vocabulary, one-at-a-time for the rest

Each item has:

```json
{
  "item_id": "...",
  "item_type": "error_pattern | vocabulary | grammar_rule",
  "easiness_factor": 2.5,
  "interval_days": 6,
  "repetitions": 2,
  "due_date": "YYYY-MM-DD",
  "priority": "critical | high | medium | low",
  "content": "...",
  "answer": "..."
}
```

**Batch atomic exercises. One-at-a-time only for multi-sentence work.**

An exercise is *atomic* when the answer is a single word, a single short phrase, a letter choice, or one sentence-level correction. Atomic exercises are fast — batching them is dramatically less friction than ping-ponging one Q one A. The reverse — putting an atomic Q in its own message — is the friction the learner complained about.

**Batch (one numbered list, learner answers all in one message):**
- vocabulary recognition (target → native) or production (native → target)
- cloze (fill one word into a short sentence)
- multiple choice on a grammar/usage point (a/b/c/d)
- single-sentence error correction
- short phrase translation

**One-at-a-time:**
- free writing (≥ 3 sentences)
- speaking turn (open-ended response)
- reading comprehension on a longer text
- role-play turns
- anything requiring a constructed multi-sentence answer

For this skill (`/fluent-review`), almost every review item is atomic — vocabulary recall, single-sentence error correction, one-word cloze for a grammar rule. **Present the whole capped queue as one batched list**, in this order:

1. Vocabulary items first (target → native).
2. Error-pattern + grammar-rule items next, each as a one-line scenario or cloze inside the same numbered list.

Only break out into one-at-a-time mode if the rule genuinely needs a multi-sentence scenario the learner has to construct (rare).

After the learner answers, produce a **single combined feedback table** (one ✅/❌ + correction + score per row) — invoke `fluent-feedback-formatter` once per batch.

#### Batched review format

```markdown
## 🔄 Review — {N} items

Answer all in one message. Format: number + answer, one per line.

1. {content_1}                                              ← vocab recognition
2. {content_2}                                              ← vocab recognition
...
{K}. "____ verano" (this/that summer, masculine)            ← cloze for err_ese_este_gender
{K+1}. Correct: "Llevo 2 horas estoy cerca del centro."     ← error correction
{N}. el mercado ___ (nearby — fill the adjective)           ← cloze for err_cerca_adj

**Type your answers (in target language or native, as appropriate):**
```

Annotate each line with the bare exercise — no need to label item_type to the learner, just give them the prompt. After they answer, grade in a **single combined table**, one row per item with ✅/❌, correction, and a 0-10 score. Stage one `review_results[]` entry per item.

For variety, you may rotate the *whole vocab portion* between recognition (target → native) and production (native → target) across sessions — but never mix modes inside a single column.

#### When NOT to batch

If a review item legitimately needs a multi-sentence scenario the learner has to construct (e.g. drilling a discourse-level pattern like "complete this formal email opener with 3 lines"), break it out as a separate one-at-a-time prompt AFTER the atomic batch. This should be rare — most review items are atomic.

### 4. Evaluate + checkpoint after each interaction

Use the `fluent-feedback-formatter` skill for per-answer feedback.

Then **immediately checkpoint** the result so the SM-2 update is durable on disk. Do NOT hand-edit `spaced-repetition.json`. Call `update-db.py --checkpoint` with the new `review_results[]` entries:

- **After the vocab batch:** one checkpoint with N entries (one per item in the batch) + the running `skill_scores`.
- **After each error_pattern / grammar_rule item:** one checkpoint with that single entry + updated running `skill_scores`.

```bash
python3 .claude/hooks/update-db.py --checkpoint <<'EOF'
{
  "session_id": "session-008", "date": "2026-05-27",
  "command_used": "/fluent-review",
  "skill_scores": {"vocabulary": {"exercises": 14, "correct": 13, "time_minutes": 6}},
  "review_results": [{ "item_id": "vocab_huis", "quality": 4 }, ...]
}
EOF
```

The SM-2 math (see `fluent-sm2-calculator`) runs at **commit** time, not checkpoint time — so re-checkpointing the same item with a corrected `quality` is safe. Mapping: `quality = floor(score / 2)`.

### 5. Progress pulse every 5 items

```markdown
## Progress Update

**Reviewed:** {N}/{total}
**Accuracy:** {percent}%
**Time Remaining:** ~{min} min

Keep going! 💪
```

### 6. Session summary

```markdown
## 🎉 Review Session Complete!

**Reviewed:** {count}
**Accuracy:** {percent}%
**Time:** {min} min

### Breakdown

**Mastered (no mistakes):** {count} — won't appear again for a while 🎉
**Good (minor slips):** {count} — next in {X} days
**Need more practice:** {count} — tomorrow again

### Next Review Schedule
- Tomorrow: {count}
- This week: {count}
- Next week: {count}

**Streak:** 🔥 {X} {day/days} 🔥

**Tip:** {one line of advice based on accuracy}

{target-language well done}! 🌟
```

### 7. Final commit

By this point every `review_results[]` entry is already in the partial via checkpoints. The final step is a single `--commit` with the session-final fields only:

```bash
python3 .claude/hooks/update-db.py --commit <<'EOF'
{
  "session_id": "session-008", "date": "2026-05-27",
  "duration_minutes": 12,
  "command_used": "/fluent-review",
  "focus_next_session": ["Drill cercano vs cerca"]
}
EOF
```

The script merges the partial + this payload, runs the canonical flow ONCE (streak, SM-2 on every staged review_result, mastery update, session-log entry), deletes the partial.

Save exchange to `/results/fluent-review-session-{NNN}.md` for later analysis.

## Examples

### Example 1 — vocabulary review with wrong answer

> ## Review 3/12 — 🔴
>
> **Type:** vocabulary
> **Last reviewed:** 6 days ago
> **Current mastery:** ⭐⭐⭐☆☆
>
> **Dutch:** het raam
>
> **What does it mean in English?**

Learner: "the door"

> ❌ Close — those are both openings in a wall, but not the same.
>
> **Corrections:**
> - 🟡 "the door" → **"the window"** (vocabulary — `het raam` is window; `de deur` is door)
>
> **Correct version:**
> "het raam" = the window.
>
> **Score: 3/10** 💪 Easy to mix — we'll review this again soon.
>
> (Logged: `review_results[]` item quality=1 → `interval_days=1, repetitions=0`, stays in today's queue.)

### Example 2 — correct answer with mastery bump

> ## Review 7/12 — 🟡
>
> **Type:** grammar_rule
> **Last reviewed:** 14 days ago
> **Current mastery:** ⭐⭐⭐⭐☆
>
> **Complete:** "Ik schrijf u omdat ik ____ kan komen." (reason: can't come)

Learner: "niet"

> ✅ Perfect — omdat-clause word order locked in.
>
> **Answer:** "Ik schrijf u omdat ik niet kan komen."
>
> **Score: 10/10** 🎯
>
> (Logged: quality=5 → `interval_days = round(14 * EF)`, queue: `later`. `consecutive_correct` = 5, mastery → 5 ⭐⭐⭐⭐⭐.)

## Critical Rules

- **Daily.** The whole system assumes the learner runs `/fluent-review` every day. Missing a day breaks the intended spacing.
- **Never auto-invoke.** Gated; must fire only on explicit `/fluent-review`. Long interactive + SM-2 mutation.
- **Vocabulary in batches, the rest one-at-a-time.** Vocab recognition is just recall — batching is dramatically faster and the learner asked for it. Error patterns and grammar rules need scenarios → still one at a time.
- **Checkpoint after every graded interaction, commit once at end.** If the session crashes between checkpoint and commit, the partial survives — next session start surfaces it via `orphaned_partials` and offers to commit.
- **Let the learner struggle.** If they don't remember, that's useful data (quality 0-2). The algorithm needs honest signals.
- **Never hand-edit `spaced-repetition.json`.** Queue is rebuilt on every `update-db.py` call.

## What the Schedule Means

Tell the learner if they ask:

- 1 day — new or struggling items
- 2-3 days — learning, building strength
- 1 week — getting comfortable
- 2+ weeks — strong, maintenance only
- 1+ month — mastered, long-term memory
