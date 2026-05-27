---
name: fluent-db-updater
description: Persist learner data to the 6 Fluent databases via .claude/hooks/update-db.py. Two modes — `--checkpoint` writes a delta to a per-session partial after every meaningful interaction (graded answer, finished block) and is crash-safe; `--commit` finalises the session and writes to all 6 DBs exactly once. Use checkpoint after every graded interaction in fluent-writing, fluent-vocab, fluent-speaking, fluent-reading, fluent-review, fluent-learn, fluent-chain — and commit at session end.
---

# DB Updater — Checkpoint + Commit

## Why two modes

The 6 learner databases (`learner-profile`, `progress`, `mistakes`, `mastery`, `spaced-repetition`, `session-log`) carry counters that double-count if mutated more than once per session: `total_sessions`, `current_streak_days`, `accuracy_trend`, SM-2 intervals on `spaced-repetition`, `frequency` on error patterns, the `session-log` entry itself.

But waiting until session end to write is fragile — a crash, a context blowup, or simply the agent forgetting the final updater call (our actual bug today: `last_reviewed=2026-05-24` after two completed sessions) silently throws away the data.

**Solution:** durable per-interaction staging file (`<DATA_DIR>/sessions/<session_id>.partial.json`) + a single end-of-session commit that runs the counters once.

```
practice exercise → fluent-feedback-formatter → --checkpoint  (1..N times)
                                                    ↓
                                            partial.json on disk
                                                    ↓
                  end of session → --commit → all 6 DBs updated once
```

## When to call which

**`--checkpoint`** — after every **meaningful interaction**:
- A graded answer (with `review_results[]`, `errors[]`, or running `skill_scores`)
- A finished block inside a chain session
- A new vocabulary item introduced (`new_vocabulary[]`)
- Any state the learner would lose if the session crashed *right now*

**`--commit`** — exactly **once**, at session end:
- Folds in final-only fields: `duration_minutes`, `session_notes`, `breakthroughs`, `focus_next_session`, `achievements_earned`, `milestones`
- Runs the canonical update flow → bumps `total_sessions`, updates `streak`, applies SM-2, rebuilds the review queue, appends one `session-log` entry
- Deletes the partial file on success

**Never** call `--commit` more than once per `session_id`. **Never** hand-edit the partial — let the script merge it.

## Instructions

### 1. Get a session_id at session start

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-.}}/.claude/hooks/read-db.py"
```

Use `computed.next_session_id` (format `session-NNN`). Also check `computed.orphaned_partials[]` — if any belong to a previous date, offer the learner commit-or-discard before starting a new session_id (the partial holds real graded data that hasn't reached the DBs yet).

### 2. Checkpoint after every meaningful interaction

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-.}}/.claude/hooks/update-db.py" --checkpoint <<'EOF'
{
  "session_id": "session-007",
  "date": "2026-05-27",
  "command_used": "/fluent-chain",
  "skills_practiced": ["vocabulary"],
  "skill_scores": {
    "vocabulary": {"exercises": 15, "correct": 13, "time_minutes": 6}
  },
  "review_results": [
    {"item_id": "vocab_mercado", "quality": 5, "score": 10},
    {"item_id": "vocab_cercano", "quality": 2, "score": 4}
  ],
  "errors": [
    {"pattern_id": "err_cerca_adj", "category": "grammar",
     "your_answer": "рядом", "correct_answer": "близлежащий (прил.)",
     "context": "cercano = adjective; cerca = adverb", "severity": "moderate"}
  ]
}
EOF
```

**Required:** `session_id`, `date`.

**Optional (omit to skip):** `review_results[]`, `new_vocabulary[]`, `errors[]`, `skill_scores{}`, `skills_practiced[]`, `topics_covered[]`, `breakthroughs[]`, `focus_next_session[]`, `session_notes`, etc.

### 3. Merge semantics inside the partial

The script reads the existing partial (or creates an empty one) and applies the delta with these rules:

| Field | Semantics |
|---|---|
| `review_results[]` | dict-merge by `item_id` — **last-write-wins** (re-grading the same item overwrites cleanly) |
| `new_vocabulary[]` | dict-merge by `item_id` — last-write-wins |
| `errors[]` | dict-merge by `pattern_id` — last-write-wins |
| `skill_scores{}` | per-skill **REPLACE** — agent maintains running totals across checkpoints |
| `skills_practiced[]`, `topics_covered[]`, `breakthroughs[]`, `focus_next_session[]`, `achievements_earned[]`, `milestones[]` | **set-union**, order-preserving |
| `duration_minutes`, `command_used`, `session_notes`, `exam_focus`, `critical_errors_identified` | **last-write-wins** |
| `date` | locked at first checkpoint |

Practical consequences:
- Sending the same checkpoint twice is a no-op. Safe to retry.
- To revise a learner's score after they corrected themselves: re-checkpoint with the same `item_id` and the new `quality`. The earlier value is overwritten.
- `skill_scores` should be the **running total so far this session** — the agent tracks this in memory.

### 4. Commit once at session end

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-.}}/.claude/hooks/update-db.py" --commit <<'EOF'
{
  "session_id": "session-007",
  "date": "2026-05-27",
  "duration_minutes": 28,
  "command_used": "/fluent-chain",
  "breakthroughs": ["Held vocab batch recall above 90%"],
  "focus_next_session": ["Drill cercano vs cerca", "Llevo + tiempo construction"],
  "session_notes": "Chain session — all 5 blocks completed."
}
EOF
```

The commit payload only needs **session-final** fields. All graded data already lives in the partial. The script:

1. Loads the partial.
2. Merges the commit payload as a final delta (same merge rules).
3. Flattens dict-keyed fields back to arrays.
4. Runs the canonical update flow ONCE: streak, SM-2, mastery, session-log entry, accuracy.
5. Deletes the partial.

### 5. Field notes

- `errors[]` — one entry per distinct mistake. The script's `frequency` bump still happens, but only at commit, so it's bumped exactly once even if the learner made the same mistake three times in the session.
- `new_vocabulary[]` — items the learner met for the first time. Fill every field; incomplete entries yield incomplete SR records.
- `review_results[]` — items already in the queue. The script runs SM-2 at commit. Mapping: `quality = floor(score / 2)` (see `fluent-sm2-calculator`).
- `skill_scores[].correct` counts correct exercises, not a percentage. Accuracy is derived at commit.
- `confidence` in `learner-profile.skills` is 0–100 integer; `accuracy` in `progress-db` is 0.0–1.0 float. The script handles the conversion.

### 6. Recovery — orphaned partials

If the agent or session crashes between checkpoints and commit, the partial survives. On next session start, `read-db.py` reports it in `computed.orphaned_partials[]`:

```json
{
  "session_id": "session-006",
  "date": "2026-05-26",
  "command_used": "/fluent-chain",
  "review_results_count": 12,
  "errors_count": 3,
  "is_stale": true,
  "path": ".../sessions/session-006.partial.json"
}
```

Skill behaviour when a partial is detected:
- If `is_stale` (date < today): tell the learner ("Found an unfinished session from {date} with {N} graded items — should I commit it to your stats?"). Default to YES.
- Otherwise it's the current day's session — keep using the same `session_id` and append more checkpoints.

To commit an orphan, call `--commit` with that `session_id` and a minimal payload (date + duration estimate; the rest is already in the partial). To discard, delete the file.

### 7. Legacy single-shot mode

Calling `update-db.py` with NO flag still works for backward compat: it expects a full session payload on stdin and runs the canonical flow once. The script prints a deprecation warning to stderr. **Do not use this in new skills.**

## Examples

### Example 1 — /fluent-review with vocab batch + one grammar item

```bash
# After grading the vocab batch (15 items at once):
python3 .claude/hooks/update-db.py --checkpoint <<'EOF'
{
  "session_id": "session-008", "date": "2026-05-27",
  "command_used": "/fluent-review",
  "skills_practiced": ["vocabulary"],
  "skill_scores": {"vocabulary": {"exercises": 15, "correct": 14, "time_minutes": 7}},
  "review_results": [
    {"item_id": "vocab_mercado", "quality": 5}, ...
  ]
}
EOF

# After grading the grammar item (one at a time):
python3 .claude/hooks/update-db.py --checkpoint <<'EOF'
{
  "session_id": "session-008", "date": "2026-05-27",
  "skills_practiced": ["vocabulary", "grammar"],
  "skill_scores": {
    "vocabulary": {"exercises": 15, "correct": 14, "time_minutes": 7},
    "grammar": {"exercises": 1, "correct": 1, "time_minutes": 2}
  },
  "review_results": [{"item_id": "err_llevar_double_verb", "quality": 4}]
}
EOF

# At session end:
python3 .claude/hooks/update-db.py --commit <<'EOF'
{
  "session_id": "session-008", "date": "2026-05-27",
  "duration_minutes": 10,
  "focus_next_session": ["Drill cercano vs cerca"]
}
EOF
```

### Example 2 — /fluent-vocab introducing 3 new words

Checkpoint after each word's drill is complete:

```bash
python3 .claude/hooks/update-db.py --checkpoint <<'EOF'
{
  "session_id": "session-009", "date": "2026-05-27",
  "command_used": "/fluent-vocab",
  "skills_practiced": ["vocabulary"],
  "skill_scores": {"vocabulary": {"exercises": 1, "correct": 1, "time_minutes": 2}},
  "new_vocabulary": [
    {"item_id": "vocab_estanteria", "item_type": "vocabulary",
     "content": "la estantería", "answer": "стеллаж/полка",
     "category": "household", "difficulty": "easy", "initial_quality": 4, "priority": "medium"}
  ]
}
EOF
# ... repeat for words 2 and 3, then --commit
```

## Critical Rules

- **Checkpoint after every meaningful interaction.** Never let an answered exercise sit in agent memory only.
- **One `--commit` per session.** Multiple commits double-count `total_sessions`, append duplicate session-log rows, and re-run SM-2.
- **`skill_scores` is a running total**, not a per-interaction delta. The agent tracks the running total and re-sends the absolute value each checkpoint.
- **Same `item_id` overwrites** in review_results / new_vocabulary / errors. That's the feature, not a bug — it makes corrections clean.
- **Never hand-edit the partial.** Always go through `--checkpoint`.
- **Never hand-edit `spaced-repetition.review_queue`.** It's regenerated from scratch at commit.
- **Backups are automatic.** Written to `.backups/pre-update-<session_id>/` before any DB write.
- **Exit code 1 = validation error, no files touched.** Exit code 2 = I/O failure, no DB files touched (the partial may already exist from earlier checkpoints).

## Why This Matters

The whole spaced-repetition system depends on `review_results[]` reaching the DB. Today (2026-05-27) we discovered 13 of 14 vocab items with `last_reviewed=2026-05-24` even though sessions ran on the 25th and 26th — those sessions ended without calling the updater. With the checkpoint pattern, each graded item is durable on disk the moment it's graded. The session can crash, the context can blow up, the agent can forget to commit — and the data survives until the next session, which surfaces the partial via `orphaned_partials` and offers to commit it.
