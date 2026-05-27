#!/usr/bin/env python3
"""
Fluent DB Update Script
Updates all 6 learning databases from a session report.

Three modes:

  --checkpoint   Merge a partial delta into a per-session staging file. No DBs written.
                 Call this after every meaningful interaction (graded answer, finished
                 block). Crash-safe: partials survive across restarts.

  --commit       Merge any final metadata (duration_minutes, breakthroughs, notes)
                 with the staged partial, then run the canonical update flow ONCE.
                 Deletes the partial on success. Call this exactly once at session end.

  (no flag)      Legacy single-shot mode. Reads a full session payload from stdin
                 and runs the canonical update flow. Kept for backward compat —
                 prefer --checkpoint + --commit for any new skill.

Usage:
    # Checkpoint after a graded answer:
    python3 .claude/hooks/update-db.py --checkpoint <<'EOF'
    { "session_id": "session-007", "date": "2026-05-27",
      "review_results": [{"item_id": "vocab_mercado", "quality": 5}] }
    EOF

    # Final commit at session end:
    python3 .claude/hooks/update-db.py --commit <<'EOF'
    { "session_id": "session-007", "date": "2026-05-27",
      "duration_minutes": 28, "command_used": "/fluent-chain",
      "session_notes": "Chain session — all 5 blocks completed." }
    EOF

See docs/DB_SCRIPTS.md for the full input schema. Partials live in
<DATA_DIR>/sessions/<session_id>.partial.json.

Exit codes: 0=success, 1=validation error, 2=blocking/data error
"""
import argparse
import copy
import json
import os
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fluent_paths import ensure_data_dir, ensure_backups_dir  # noqa: E402

DATA_DIR = ensure_data_dir()
BACKUP_DIR = ensure_backups_dir()
PARTIALS_DIR = DATA_DIR / "sessions"
PARTIALS_DIR.mkdir(parents=True, exist_ok=True)

# --- Utility functions ---

def load_json(path: Path) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(path: Path, data: dict):
    tmp_path = path.with_suffix('.json.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.rename(str(tmp_path), str(path))


def parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def date_str(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def tomorrow(today_str: str) -> str:
    return date_str(parse_date(today_str) + timedelta(days=1))


def yesterday(today_str: str) -> str:
    return date_str(parse_date(today_str) - timedelta(days=1))


def date_plus_days(today_str: str, days: int) -> str:
    return date_str(parse_date(today_str) + timedelta(days=days))


def get_week_start(today_str: str) -> str:
    d = parse_date(today_str)
    monday = d - timedelta(days=d.weekday())
    return date_str(monday)


def backup_all(tag: str):
    backup_path = BACKUP_DIR / tag
    backup_path.mkdir(parents=True, exist_ok=True)
    for f in DATA_DIR.glob("*.json"):
        shutil.copy2(f, backup_path / f.name)


# --- SM-2 Algorithm ---

def calculate_sm2(item: dict, quality: int) -> dict:
    """Classic SM-2. Uses ceil() for interval growth (standard)."""
    import math
    ef = item.get("easiness_factor", 2.5)
    interval = item.get("interval_days", 1)
    reps = item.get("repetitions", 0)

    if quality >= 3:
        if reps == 0:
            interval = 1
        elif reps == 1:
            interval = 6
        else:
            interval = int(math.ceil(interval * ef))
        reps += 1
    else:
        reps = 0
        interval = 1

    ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ef = max(1.3, ef)

    return {
        "easiness_factor": round(ef, 2),
        "interval_days": interval,
        "repetitions": reps,
    }


# --- Updater functions ---
# Each mutates in place. Confidence in learner-profile is 0-100 int.
# Session-log preserves the existing rich schema (skills_practiced array,
# score_breakdown, topics_covered, breakthroughs, focus_next_session,
# achievements_earned). Spaced-repetition preserves consecutive_*,
# mastery_level, total_reviews, priority, content, answer, category,
# difficulty fields on existing items.

def update_learner_profile(profile: dict, session: dict):
    today = session["date"]
    last = profile.get("last_updated", "")

    if last == today:
        pass
    elif last == yesterday(today):
        profile["current_streak_days"] = profile.get("current_streak_days", 0) + 1
    else:
        profile["current_streak_days"] = 1

    profile["last_updated"] = today
    profile["total_sessions"] = profile.get("total_sessions", 0) + 1
    profile["total_study_minutes"] = profile.get("total_study_minutes", 0) + session.get("duration_minutes", 0)

    for skill, scores in session.get("skill_scores", {}).items():
        skills = profile.setdefault("skills", {})
        s = skills.setdefault(skill, {
            "current_level": 0, "confidence": 0,
            "last_practiced": None, "total_practice_time": 0,
        })
        s["last_practiced"] = today
        s["total_practice_time"] = s.get("total_practice_time", 0) + scores.get("time_minutes", 0)
        if scores.get("exercises", 0) > 0:
            # confidence is 0-100 int; EWMA against session accuracy (0-100)
            new_acc_pct = (scores["correct"] / scores["exercises"]) * 100
            old_conf = s.get("confidence", 0)
            s["confidence"] = round(old_conf * 0.7 + new_acc_pct * 0.3)
        s["current_level"] = max(s.get("current_level", 0), 1)

    if session.get("focus_areas"):
        profile["focus_areas"] = session["focus_areas"]

    for ms in session.get("milestones", []):
        slug = re.sub(r'[^a-z0-9]+', '_', ms[:30].lower()).strip('_')
        profile.setdefault("achievements", []).append({
            "id": f"session_{session['session_id']}_{slug}",
            "name": ms,
            "earned_date": today,
            "description": ms,
        })


def update_progress_db(progress: dict, session: dict):
    today = session["date"]
    skill_scores = session.get("skill_scores", {})
    total_ex = sum(s.get("exercises", 0) for s in skill_scores.values())
    total_cor = sum(s.get("correct", 0) for s in skill_scores.values())
    accuracy = round(total_cor / total_ex, 3) if total_ex > 0 else 0.0

    stats = progress.setdefault("overall_stats", {
        "total_sessions": 0, "total_exercises": 0, "total_correct": 0,
        "total_incorrect": 0, "accuracy_rate": 0.0,
        "total_study_minutes": 0, "average_session_duration": 0,
    })
    stats["total_sessions"] = stats.get("total_sessions", 0) + 1
    stats["total_exercises"] = stats.get("total_exercises", 0) + total_ex
    stats["total_correct"] = stats.get("total_correct", 0) + total_cor
    stats["total_incorrect"] = stats.get("total_incorrect", 0) + (total_ex - total_cor)
    stats["accuracy_rate"] = round(stats["total_correct"] / stats["total_exercises"], 3) if stats["total_exercises"] > 0 else 0.0
    stats["total_study_minutes"] = stats.get("total_study_minutes", 0) + session.get("duration_minutes", 0)
    stats["average_session_duration"] = round(stats["total_study_minutes"] / stats["total_sessions"])

    trend = progress.setdefault("accuracy_trend", [])
    # Dedup same-day entries: replace if present, else append
    existing = next((t for t in trend if t.get("date") == today), None)
    if existing is not None:
        existing["accuracy"] = accuracy
        existing["exercises"] = existing.get("exercises", 0) + total_ex
    else:
        trend.append({"date": today, "accuracy": accuracy, "exercises": total_ex})

    for skill, scores in skill_scores.items():
        sp = progress.setdefault("skill_progress", {}).setdefault(skill, {
            "sessions": 0, "accuracy": 0.0, "last_practiced": None,
            "exercises_completed": 0, "correct_count": 0, "incorrect_count": 0,
        })
        old_sessions = sp.get("sessions", 0)
        sp["sessions"] = old_sessions + 1
        new_acc = scores["correct"] / scores["exercises"] if scores.get("exercises", 0) > 0 else 0.0
        sp["accuracy"] = round(
            (sp.get("accuracy", 0.0) * old_sessions + new_acc) / sp["sessions"], 3
        )
        sp["last_practiced"] = today
        sp["exercises_completed"] = sp.get("exercises_completed", 0) + scores.get("exercises", 0)
        sp["correct_count"] = sp.get("correct_count", 0) + scores.get("correct", 0)
        sp["incorrect_count"] = sp.get("incorrect_count", 0) + (scores.get("exercises", 0) - scores.get("correct", 0))

    week_start = get_week_start(today)
    weekly = progress.setdefault("weekly_summary", [])
    week_entry = next((w for w in weekly if w.get("week_start") == week_start), None)
    if week_entry is None:
        week_entry = {"week_start": week_start, "sessions": 0, "total_minutes": 0, "accuracy": 0.0}
        weekly.append(week_entry)

    old_s = week_entry.get("sessions", 0)
    week_entry["sessions"] = old_s + 1
    week_entry["total_minutes"] = week_entry.get("total_minutes", 0) + session.get("duration_minutes", 0)
    week_entry["accuracy"] = round(
        (week_entry.get("accuracy", 0.0) * old_s + accuracy) / week_entry["sessions"], 3
    )

    progress.setdefault("metadata", {})["last_updated"] = today


def update_mistakes_db(mistakes: dict, session: dict):
    today = session["date"]
    patterns = mistakes.setdefault("error_patterns", {})

    for error in session.get("errors", []):
        pid = error["pattern_id"]

        if pid in patterns:
            pat = patterns[pid]
            pat["frequency"] = pat.get("frequency", 0) + 1
            pat["last_seen"] = today
            pat["last_occurred"] = today  # legacy alias kept
            pat["next_review"] = tomorrow(today)
            pat["consecutive_incorrect"] = pat.get("consecutive_incorrect", 0) + 1
            pat["consecutive_correct"] = 0
            pat.setdefault("examples", []).append({
                "incorrect": error.get("your_answer", ""),
                "correct": error.get("correct_answer", ""),
                "context": error.get("context", ""),
                "date": today,
            })
            pat["examples"] = pat["examples"][-5:]
            if error.get("notes"):
                pat["notes"] = error["notes"]
        else:
            patterns[pid] = {
                "category": error.get("category", "other"),
                "subcategory": error.get("subcategory", ""),
                "description": error.get("description", ""),
                "severity": error.get("severity", "minor"),
                "frequency": 1,
                "mastery_level": 0,
                "difficulty_score": error.get("difficulty_score", 0.5),
                "last_seen": today,
                "last_occurred": today,
                "next_review": tomorrow(today),
                "consecutive_correct": 0,
                "consecutive_incorrect": 1,
                "examples": [{
                    "incorrect": error.get("your_answer", ""),
                    "correct": error.get("correct_answer", ""),
                    "context": error.get("context", ""),
                    "date": today,
                }],
                "notes": error.get("notes", ""),
            }

    mistakes.setdefault("metadata", {})["last_updated"] = today
    mistakes["metadata"]["total_patterns_tracked"] = len(patterns)


def update_mastery_db(mastery: dict, session: dict, progress: dict):
    today = session["date"]

    for skill, scores in session.get("skill_scores", {}).items():
        s = mastery.setdefault("skills", {}).setdefault(skill, {
            "mastery_level": 0, "confidence_score": 0.0,
            "total_practice_time": 0, "last_practiced": None,
            "practice_count": 0, "avg_accuracy": 0.0,
        })
        s["last_practiced"] = today
        s["total_practice_time"] = s.get("total_practice_time", 0) + scores.get("time_minutes", 0)
        s["practice_count"] = s.get("practice_count", 0) + scores.get("exercises", 0)

        sp = progress.get("skill_progress", {}).get(skill, {})
        acc = sp.get("accuracy", 0)
        sessions = sp.get("sessions", 0)
        s["confidence_score"] = round(acc, 3)
        s["avg_accuracy"] = round(acc, 3)

        if sessions == 0:
            s["mastery_level"] = 0
        elif sessions < 3 or acc < 0.5:
            s["mastery_level"] = max(s.get("mastery_level", 0), 1)
        elif sessions < 5 or acc < 0.65:
            s["mastery_level"] = max(s.get("mastery_level", 0), 2)
        elif sessions < 10 or acc < 0.8:
            s["mastery_level"] = max(s.get("mastery_level", 0), 3)
        elif sessions < 20 or acc < 0.9:
            s["mastery_level"] = max(s.get("mastery_level", 0), 4)
        else:
            s["mastery_level"] = 5

    mastery.setdefault("metadata", {})["last_updated"] = today


def update_spaced_repetition(sr: dict, session: dict):
    today = session["date"]
    items = sr.setdefault("items", {})

    for review in session.get("review_results", []):
        item_id = review["item_id"]
        quality = review["quality"]
        if item_id in items:
            item = items[item_id]
            result = calculate_sm2(item, quality)
            item["easiness_factor"] = result["easiness_factor"]
            item["interval_days"] = result["interval_days"]
            item["repetitions"] = result["repetitions"]
            item["due_date"] = date_plus_days(today, result["interval_days"])
            item["last_reviewed"] = today
            item["last_quality"] = quality
            item["total_reviews"] = item.get("total_reviews", 0) + 1
            if quality >= 3:
                item["consecutive_correct"] = item.get("consecutive_correct", 0) + 1
                item["consecutive_incorrect"] = 0
            else:
                item["consecutive_incorrect"] = item.get("consecutive_incorrect", 0) + 1
                item["consecutive_correct"] = 0
            # Mastery: rough map from repetitions and quality (clamped 0..5)
            current = item.get("mastery_level", 0)
            if item["repetitions"] >= 5 and item["consecutive_correct"] >= 3:
                item["mastery_level"] = min(5, max(current, 3))
            elif item["repetitions"] >= 2 and item["consecutive_correct"] >= 1 and quality >= 4:
                item["mastery_level"] = min(5, current + 1)
            # priority heuristic
            if item.get("consecutive_incorrect", 0) >= 2:
                item["priority"] = "high"
            elif item.get("mastery_level", 0) >= 3:
                item["priority"] = "low"
            else:
                item["priority"] = item.get("priority", "medium")
            item.setdefault("review_history", []).append({
                "date": today,
                "quality": quality,
                "score": review.get("score", 0),
            })

    for vocab in session.get("new_vocabulary", []):
        item_id = vocab["item_id"]
        if item_id not in items:
            items[item_id] = {
                "id": item_id,
                "type": vocab.get("item_type", "vocabulary"),
                "content": vocab.get("content", ""),
                "answer": vocab.get("answer", ""),
                "category": vocab.get("category", ""),
                "difficulty": vocab.get("difficulty", ""),
                "created_date": today,
                "due_date": tomorrow(today),
                "interval_days": 1,
                "repetitions": 0,
                "easiness_factor": 2.5,
                "consecutive_correct": 0,
                "consecutive_incorrect": 0,
                "last_reviewed": today,
                "last_quality": vocab.get("initial_quality", 3),
                "mastery_level": 0,
                "total_reviews": 0,
                "priority": vocab.get("priority", "medium"),
            }

    for error in session.get("errors", []):
        item_id = error["pattern_id"]
        if item_id not in items:
            items[item_id] = {
                "id": item_id,
                "type": "error_pattern",
                "content": error.get("your_answer", ""),
                "answer": error.get("correct_answer", ""),
                "category": error.get("category", ""),
                "difficulty": "",
                "created_date": today,
                "due_date": tomorrow(today),
                "interval_days": 1,
                "repetitions": 0,
                "easiness_factor": 2.5,
                "consecutive_correct": 0,
                "consecutive_incorrect": 1,
                "last_reviewed": today,
                "last_quality": 2,
                "mastery_level": 0,
                "total_reviews": 0,
                "priority": "high",
            }

    # Rebuild review queue
    sr["review_queue"] = {"today": [], "tomorrow": [], "this_week": [], "later": []}
    tom = tomorrow(today)
    week_end = date_plus_days(today, 7)
    for item_id, item in items.items():
        due = item.get("due_date", today)
        if due <= today:
            sr["review_queue"]["today"].append(item_id)
        elif due == tom:
            sr["review_queue"]["tomorrow"].append(item_id)
        elif due <= week_end:
            sr["review_queue"]["this_week"].append(item_id)
        else:
            sr["review_queue"]["later"].append(item_id)

    sr.setdefault("metadata", {})["last_updated"] = today
    sr["metadata"]["total_items_tracked"] = len(items)


def update_session_log(log: dict, session: dict, streak: int):
    """Matches existing schema: skills_practiced (array), score_breakdown,
    topics_covered, breakthroughs, focus_next_session, achievements_earned."""
    today = session["date"]
    skill_scores = session.get("skill_scores", {})
    total_ex = sum(s.get("exercises", 0) for s in skill_scores.values())
    total_cor = sum(s.get("correct", 0) for s in skill_scores.values())

    score_breakdown = {
        skill: round(s["correct"] / s["exercises"], 3) if s.get("exercises", 0) > 0 else 0.0
        for skill, s in skill_scores.items()
    }

    entry = {
        "session_id": session["session_id"],
        "date": today,
        "duration_minutes": session.get("duration_minutes", 0),
        "skills_practiced": session.get("skills_practiced", list(skill_scores.keys())),
        "command_used": session.get("command_used", "/fluent-learn"),
        "exercises_completed": total_ex,
        "accuracy": round(total_cor / total_ex, 3) if total_ex > 0 else 0.0,
        "score_breakdown": score_breakdown,
        "topics_covered": session.get("topics_covered", []),
        "breakthroughs": session.get("breakthroughs", []),
        "focus_next_session": session.get("focus_next_session", session.get("focus_areas", [])),
        "notes": session.get("session_notes", ""),
        "achievements_earned": session.get("achievements_earned", []),
        "streak_day": streak,
    }
    if session.get("exam_focus"):
        entry["exam_focus"] = session["exam_focus"]
    if session.get("critical_errors_identified"):
        entry["critical_errors_identified"] = session["critical_errors_identified"]

    log.setdefault("sessions", []).append(entry)

    for ms in session.get("milestones", []):
        log.setdefault("milestones", []).append({
            "date": today,
            "milestone": ms,
            "session_id": session["session_id"],
        })

    log.setdefault("metadata", {})["total_sessions"] = len(log["sessions"])


# --- Partial (checkpoint) staging ---
# Partial file shape:
#   {
#     "session_id": "...",
#     "date": "YYYY-MM-DD",
#     "command_used": "...",
#     "started_at": "<ISO>",
#     "last_checkpoint_at": "<ISO>",
#     "duration_minutes": int,
#     "skills_practiced": [...],          # set-union across checkpoints
#     "skill_scores": { skill: {...} },   # per-skill REPLACE (agent maintains running totals)
#     "review_results_by_id": { item_id: {...} },   # last-write-wins
#     "new_vocabulary_by_id": { item_id: {...} },   # last-write-wins
#     "errors_by_id": { pattern_id: {...} },        # last-write-wins
#     "topics_covered": [...],            # set-union
#     "breakthroughs": [...],             # set-union
#     "focus_next_session": [...],        # set-union
#     "achievements_earned": [...],
#     "milestones": [...],
#     "session_notes": "...",
#     "exam_focus": ...,
#     "critical_errors_identified": [...]
#   }


def partial_path(session_id: str) -> Path:
    return PARTIALS_DIR / f"{session_id}.partial.json"


def load_partial(session_id: str):
    """Return the partial dict, or None if no partial exists for this session_id."""
    p = partial_path(session_id)
    if not p.exists():
        return None
    with open(p, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_partial(partial: dict):
    save_json(partial_path(partial["session_id"]), partial)


def delete_partial(session_id: str):
    p = partial_path(session_id)
    if p.exists():
        p.unlink()


def empty_partial(session_id: str, date: str) -> dict:
    now_iso = datetime.now().isoformat(timespec="seconds")
    return {
        "session_id": session_id,
        "date": date,
        "started_at": now_iso,
        "last_checkpoint_at": now_iso,
        "duration_minutes": 0,
        "command_used": "",
        "skills_practiced": [],
        "skill_scores": {},
        "review_results_by_id": {},
        "new_vocabulary_by_id": {},
        "errors_by_id": {},
        "topics_covered": [],
        "breakthroughs": [],
        "focus_next_session": [],
        "achievements_earned": [],
        "milestones": [],
        "session_notes": "",
    }


def _set_union(dst: list, src) -> list:
    """Append-only set semantics, order-preserving."""
    if not src:
        return dst
    if not isinstance(src, list):
        src = [src]
    seen = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in dst}
    for x in src:
        key = json.dumps(x, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            dst.append(x)
            seen.add(key)
    return dst


def merge_delta_into_partial(partial: dict, delta: dict) -> dict:
    """Merge a checkpoint delta into the cumulative partial state.

    Semantics:
      - review_results: list of {item_id, quality, ...} → merged into dict, last-write-wins by item_id
      - new_vocabulary: list of {item_id, ...} → merged into dict, last-write-wins by item_id
      - errors: list of {pattern_id, ...} → merged into dict, last-write-wins by pattern_id
      - skill_scores: dict of {skill: {...}} → per-skill REPLACE (agent maintains running totals)
      - skills_practiced, topics_covered, breakthroughs, focus_next_session,
        achievements_earned, milestones: set-union
      - duration_minutes, command_used, session_notes, exam_focus,
        critical_errors_identified: last-write-wins (replace)
      - date: never overwritten once set (locked at first checkpoint)
    """
    for rr in delta.get("review_results", []) or []:
        if "item_id" not in rr:
            continue
        partial["review_results_by_id"][rr["item_id"]] = rr

    for nv in delta.get("new_vocabulary", []) or []:
        if "item_id" not in nv:
            continue
        partial["new_vocabulary_by_id"][nv["item_id"]] = nv

    for err in delta.get("errors", []) or []:
        if "pattern_id" not in err:
            continue
        partial["errors_by_id"][err["pattern_id"]] = err

    if "skill_scores" in delta:
        for skill, scores in delta["skill_scores"].items():
            partial["skill_scores"][skill] = scores

    for k in ("skills_practiced", "topics_covered", "breakthroughs",
              "focus_next_session", "achievements_earned", "milestones"):
        if k in delta:
            partial.setdefault(k, [])
            _set_union(partial[k], delta[k])

    for k in ("duration_minutes", "command_used", "session_notes",
              "exam_focus", "critical_errors_identified"):
        if k in delta and delta[k] not in (None, ""):
            partial[k] = delta[k]

    partial["last_checkpoint_at"] = datetime.now().isoformat(timespec="seconds")
    return partial


def partial_to_session_payload(partial: dict) -> dict:
    """Flatten the partial (dict-keyed) into the array-shaped payload that the
    main update flow expects."""
    return {
        "session_id": partial["session_id"],
        "date": partial["date"],
        "duration_minutes": partial.get("duration_minutes", 0),
        "command_used": partial.get("command_used", ""),
        "skills_practiced": partial.get("skills_practiced", []),
        "skill_scores": partial.get("skill_scores", {}),
        "review_results": list(partial.get("review_results_by_id", {}).values()),
        "new_vocabulary": list(partial.get("new_vocabulary_by_id", {}).values()),
        "errors": list(partial.get("errors_by_id", {}).values()),
        "topics_covered": partial.get("topics_covered", []),
        "breakthroughs": partial.get("breakthroughs", []),
        "focus_next_session": partial.get("focus_next_session", []),
        "achievements_earned": partial.get("achievements_earned", []),
        "milestones": partial.get("milestones", []),
        "session_notes": partial.get("session_notes", ""),
        "exam_focus": partial.get("exam_focus"),
        "critical_errors_identified": partial.get("critical_errors_identified", []),
    }


def run_checkpoint(delta: dict):
    """--checkpoint: merge delta into partial, write, exit."""
    for field in ("session_id", "date"):
        if field not in delta:
            print(f"[Fluent] Error: Missing required field '{field}' in checkpoint delta",
                  file=sys.stderr)
            sys.exit(1)

    sid = delta["session_id"]
    partial = load_partial(sid) or empty_partial(sid, delta["date"])
    # date locks at first checkpoint, but allow update if missing
    if not partial.get("date"):
        partial["date"] = delta["date"]

    merge_delta_into_partial(partial, delta)
    save_partial(partial)

    rr = len(partial["review_results_by_id"])
    nv = len(partial["new_vocabulary_by_id"])
    er = len(partial["errors_by_id"])
    print(f"[Fluent] 💾 Checkpoint saved → {sid}.partial.json "
          f"(review_results={rr}, new_vocab={nv}, errors={er})")
    sys.exit(0)


def run_commit(stdin_payload: dict):
    """--commit: merge final metadata, run main flow ONCE, delete partial."""
    for field in ("session_id", "date"):
        if field not in stdin_payload:
            print(f"[Fluent] Error: Missing required field '{field}' in commit payload",
                  file=sys.stderr)
            sys.exit(1)

    sid = stdin_payload["session_id"]
    partial = load_partial(sid)
    if partial is None:
        print(f"[Fluent] ⚠️  No partial found for {sid} — treating commit payload as full session.",
              file=sys.stderr)
        partial = empty_partial(sid, stdin_payload["date"])

    # Fold the final stdin payload (duration, notes, breakthroughs, etc.) in as a last delta
    merge_delta_into_partial(partial, stdin_payload)
    session = partial_to_session_payload(partial)
    run_main_flow(session)
    delete_partial(sid)


# --- Main ---

def run_main_flow(session: dict):
    """The canonical update flow — must run exactly once per session."""
    session.setdefault("duration_minutes", 0)

    files = {
        "profile": DATA_DIR / "learner-profile.json",
        "progress": DATA_DIR / "progress-db.json",
        "mistakes": DATA_DIR / "mistakes-db.json",
        "mastery": DATA_DIR / "mastery-db.json",
        "sr": DATA_DIR / "spaced-repetition.json",
        "log": DATA_DIR / "session-log.json",
    }

    try:
        originals = {k: load_json(p) for k, p in files.items()}
    except Exception as e:
        print(f"[Fluent] Error loading databases: {e}", file=sys.stderr)
        sys.exit(2)

    # Work on deep copies so a mid-run exception leaves disk untouched.
    data = {k: copy.deepcopy(v) for k, v in originals.items()}

    try:
        update_learner_profile(data["profile"], session)
        update_progress_db(data["progress"], session)
        update_mistakes_db(data["mistakes"], session)
        update_mastery_db(data["mastery"], session, data["progress"])
        update_spaced_repetition(data["sr"], session)
        streak = data["profile"].get("current_streak_days", 0)
        update_session_log(data["log"], session, streak)
    except Exception as e:
        import traceback
        print(f"[Fluent] Error updating databases: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)

    # Backup originals BEFORE writing new state.
    backup_all(f"pre-update-{session['session_id']}")

    try:
        for k, p in files.items():
            save_json(p, data[k])
    except Exception as e:
        print(f"[Fluent] Error saving databases: {e}", file=sys.stderr)
        sys.exit(2)

    # Summary
    stats = data["progress"]["overall_stats"]
    sr_tomorrow = len(data["sr"]["review_queue"].get("tomorrow", []))
    skill_scores = session.get("skill_scores", {})
    total_ex = sum(s.get("exercises", 0) for s in skill_scores.values())
    total_cor = sum(s.get("correct", 0) for s in skill_scores.values())

    print(f"[Fluent] ✅ Updated 6 databases for session {session['session_id']}")
    print(f"[Fluent] 🔥 Streak: {streak} days | Sessions: {stats['total_sessions']} | Minutes: {stats['total_study_minutes']}")
    if total_ex > 0:
        print(f"[Fluent] 📊 This session: {total_cor}/{total_ex} correct ({round(total_cor/total_ex*100)}%)")
    else:
        print("[Fluent] 📊 No exercises recorded")
    print(f"[Fluent] 📈 Overall accuracy: {stats['accuracy_rate']*100:.0f}% ({stats['total_exercises']} exercises)")
    print(f"[Fluent] 🧠 SR: {data['sr']['metadata']['total_items_tracked']} items tracked, {sr_tomorrow} due tomorrow")
    print(f"[Fluent] 📝 Errors tracked: {data['mistakes']['metadata']['total_patterns_tracked']} patterns")


def main():
    parser = argparse.ArgumentParser(add_help=True, description="Fluent DB updater")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--checkpoint", action="store_true",
                       help="Merge a delta into the per-session partial. No DB writes.")
    group.add_argument("--commit", action="store_true",
                       help="Merge final metadata + commit partial to all 6 DBs. Once per session.")
    args = parser.parse_args()

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"[Fluent] Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    if args.checkpoint:
        run_checkpoint(payload)
        return

    if args.commit:
        run_commit(payload)
        sys.exit(0)

    # Legacy single-shot mode — kept for backward compat.
    for field in ("session_id", "date"):
        if field not in payload:
            print(f"[Fluent] Error: Missing required field '{field}'", file=sys.stderr)
            sys.exit(1)
    print("[Fluent] ⚠️  Legacy single-shot mode. Prefer --checkpoint + --commit "
          "for crash-safe per-interaction writes.", file=sys.stderr)
    run_main_flow(payload)
    sys.exit(0)


if __name__ == "__main__":
    main()
