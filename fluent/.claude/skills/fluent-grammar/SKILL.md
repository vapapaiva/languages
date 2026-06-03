---
name: fluent-grammar
description: Run a targeted grammar drill session — explains one grammar rule, then drills it with batched fill-in/error-correction exercises and one integration task. Triggered only when the learner types /fluent-grammar. Proactively selects the next rule to teach based on error patterns in mistakes-db, SM-2 due grammar items, CEFR curriculum progression, and skills not yet introduced. Updates mastery-db and spaced-repetition at session end.
allowed-tools: Read, Write, Bash
disable-model-invocation: true
---

# Grammar Drill Session

## Overview

Explicit grammar instruction backed by data. Each session targets one rule: explain it clearly, drill simple forms as a batch, then apply it in one real-sentence task. The rule is chosen automatically from three sources in priority order — learner's own error patterns, SM-2-due grammar items, and the level-appropriate curriculum. The learner never has to guess what to study next.

## When to Use

Trigger only when the learner types `/fluent-grammar`. Gated with `disable-model-invocation: true` — the proactive curriculum algorithm mutates `spaced-repetition.json` and `mastery-db.json` and must not fire on an ambiguous prompt.

Skip if the learner is below A1 mastery level 1 — they need a basic word bank first (`/fluent-vocab`).

## Instructions

### 1. Load context

```bash
python3 "${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR:-.}}/.claude/hooks/read-db.py"
```

Need all 6 DBs. If any are missing, direct the learner to `/fluent-setup` and stop.

Key fields:
- `learner_profile.current_level` — CEFR level (A1 / A2 / B1 / B2)
- `learner_profile.target_language` — language being learned
- `mistakes_db.error_patterns` filtered by `category` in `["grammar", "articles", "prepositions"]`
- `spaced_repetition.items` filtered by `type == "grammar_rule"` and `due_date <= today`
- `mastery_db.skills_mastery.grammar`
- `computed.next_session_id`

---

### 2. Select the rule to teach (proactive algorithm)

Run these three checks in order and pick the **highest-priority rule** found.

#### Check A — Error clusters (highest priority)

Group `mistakes_db.error_patterns` by grammar topic. A "cluster" is 2+ distinct error-pattern IDs that share the same underlying rule.

Spanish clustering map:

| Grammar topic | Matching pattern IDs (substrings) |
|---|---|
| `reflexive_verbs` | `reflexive`, `se_rompio`, `lavarse`, `ponerse` |
| `personal_a` | `personal_a` |
| `ser_estar` | `ser_`, `estar_`, `es_sobre` |
| `gender_agreement` | `gender`, `noun_gender`, `cerrados`, `mucho_calor`, `ruta_cercano`, `hora_esta` |
| `llevar_duration` | `llevar` |
| `subjunctive_present` | `subjuntivo`, `subjunctive`, `cuando_subj`, `hacer_que_subj`, `vengas` |
| `articles` | `article`, `del_contraction`, `mucho_del` |
| `adjective_adverb` | `cerca_adj`, `malo_mal` |
| `verb_tense_future` | `querré`, `tendré` |
| `relative_clauses` | `lo_que`, `relative` |

Score each topic: `(number_of_matching_patterns × 2) + sum(frequency of each pattern)`. Pick the **highest score**.

If the top topic's score is ≥ 4, this is the rule to teach — skip checks B and C.

#### Check B — SM-2 due grammar rules

From `spaced_repetition.items`, filter `type == "grammar_rule"` and `due_date <= today`. Sort by `priority` (critical → high → medium) then by `due_date` ascending. Pick the first item.

If any item is due, its `content` field names the rule to re-drill.

#### Check C — Curriculum progression (fallback)

Look up the learner's CEFR level in the curriculum map (§3 below). Find the first topic that has no corresponding item in `spaced_repetition.items` (i.e., has never been introduced). That is the next rule to teach.

#### Offer choice

After selecting, present the recommendation but let the learner override:

```markdown
# 🧠 Grammar Session

Hola {name}! Today's grammar focus — selected based on your recent sessions:

**Recommended:** {rule_name} — {one-line why: "you've made this mistake 3× recently" / "due for review" / "next in your A2 curriculum"}

Or pick any topic:
{list 3-4 alternatives from the curriculum at the learner's level}

**Type a number to pick, or press Enter for the recommendation:**
```

---

### 3. Grammar curriculum map

Use this map for Check C and for populating the choice list. Topics are ordered by pedagogical dependency — don't introduce a topic if its prerequisites haven't been taught.

#### Spanish A1 (prerequisite baseline)
1. `present_tense_regular` — -ar/-er/-ir conjugation (hablar, comer, vivir)
2. `ser_basic` — ser for identity, profession, origin
3. `articles_basic` — el/la/los/las, un/una/unos/unas
4. `gender_nouns` — masculine/feminine nouns, -o/-a pattern
5. `subject_pronouns` — yo/tú/él/ella/usted/nosotros/vosotros/ellos/ustedes
6. `negation_no` — no + verb; nunca, nada, nadie

#### Spanish A2 (learner's current level)
7. `present_tense_irregular` — tener, hacer, ir, poder, querer, venir, salir, poner, saber, conocer
8. `estar_uses` — estar for location, health, state (vs ser)
9. `reflexive_verbs` — me/te/se/nos/os/se + reflexive verbs; reflexive meaning change (romper vs romperse)
10. `personal_a` — a before animate direct objects (ver **a** alguien, conocer **a** María)
11. `gender_agreement` — adjective-noun agreement in gender and number (including irregular: el agua fría)
12. `adjective_placement` — adjective after noun (default); before noun changes meaning (grande/gran, mismo, propio)
13. `hay_que_tener_que` — impersonal obligation (hay que + inf) vs personal (tener que + inf)
14. `preterite_regular` — -é/-aste/-ó, -í/-iste/-ió for completed past actions
15. `preterite_irregular` — ser/ir (fui), tener (tuve), hacer (hice), poder (pude), estar (estuve)
16. `llevar_duration` — llevar + gerund for ongoing duration (llevo 2 horas **esperando**, NOT "hace 2 horas estoy")
17. `del_al_contractions` — de + el = del; a + el = al (never de el / a el)
18. `adjective_adverb_distinction` — adjective modifies noun (cercano); adverb modifies verb (cerca)
19. `verb_infinitive_construction` — quiero/puedo/debo/voy a + infinitive (no double verb)
20. `direct_object_pronouns` — lo/la/los/las placement and agreement

#### Spanish B1
21. `preterite_vs_imperfect` — completed action (pretérito) vs ongoing/habitual/background (imperfecto)
22. `future_tense` — -é/-ás/-á/-emos/-éis/-án; irregular stems (tendré, haré, podré, vendré)
23. `conditional_tense` — -ía/-ías/-ía/-íamos/-íais/-ían; same irregular stems as future
24. `subjunctive_present` — querer que, esperar que, es importante que + subj; -e/-es/-e/-emos/-éis/-en for -ar
25. `subjunctive_adverbial` — cuando + subj (future reference); para que, aunque + subj
26. `indirect_object_pronouns` — le/les; leísmo; le → se before lo/la
27. `relative_clauses` — que (people/things), quien (people after prep), donde (places), lo que (what/that which)
28. `formal_commands` — usted: use present subjunctive form (hable, coma, escriba)
29. `impersonal_se` — se vende, se habla español, se puede + inf

#### Spanish B2
30. `past_subjunctive` — -ara/-ase forms; after si (hypotheticals), ojalá, como si
31. `compound_tenses` — haber + participio: he comido, había llegado, habré terminado
32. `hypothetical_conditionals` — si + imperfecto subj → condicional (si tuviera…, haría)
33. `passive_voice` — ser + participio + por; passive se

---

### 4. Opening

```markdown
# 🧠 Gramática: {rule_display_name}

**Rule:** {rule_display_name}
**Why now:** {one of: "You've made this mistake {N}× across {M} sessions" / "Due for review — last drilled {X} days ago" / "Next in your A2 curriculum"}
**Level:** {CEFR}
**Duration:** ~10 min

---
```

---

### 5. Explain the rule

Write the explanation in the **learner's native language** (Russian for Artem). Structure:

```markdown
## Правило: {rule_display_name}

**Суть:**
{1-3 sentence plain explanation of what the rule does and WHY it exists — not just how}

**Формула / структура:**
{formula or pattern, e.g.: SUBJECT + llevar + DURATION + gerund}

**Когда применять:**
- ✅ {correct use case 1 + example in target language + translation}
- ✅ {correct use case 2}
- ❌ {common mistake + why it's wrong}

**Запомни:**
{1 memorable contrast pair or mnemonic}

---
```

Keep the explanation short — the learning happens in the drills. The rule statement should fit in ~6 lines.

If this is a **review** (Check B hit), skip the full explanation and just show:

```markdown
## Повторение: {rule_display_name}

Помнишь это правило? Быстрый напоминатель:
{2-sentence summary}

Сразу к упражнениям:
```

---

### 6. Drill sequence

Three stages per session. Never skip stage 1 — it establishes the correct form before harder tasks.

#### Stage 1 — Recognition / correction batch (all at once)

Present **all simple exercises in one message**. Each exercise is a single-answer item: fill in one word, fix one error, or choose between two forms. Number them and ask for all answers at once.

```markdown
## Упражнения — Часть 1

Исправь или заполни пропуск:

**1.** _{wrong sentence or sentence with blank}_
**2.** _{wrong sentence or sentence with blank}_
...
**N.** _{wrong sentence or sentence with blank}_
```

Generate 6-10 items. Vary the surface form so the learner can't pattern-match mechanically: mix "fix the error", "fill the blank", "choose A or B", and "true/false about the rule". Prioritize sentences that echo real mistakes from `mistakes_db` for this rule — make the learner face the exact traps they've fallen into.

After the learner answers: give batch feedback (see §7), then move to Stage 2.

#### Stage 2 — Production (one at a time)

Present 2-3 exercises that require producing a full sentence. These test transfer — can the learner apply the rule in their own output?

```markdown
## Упражнение {N} — Составь фразу

{prompt in Russian explaining what to express}

**Напиши по-испански:**
```

Wait for the answer, give immediate feedback via `fluent-feedback-formatter`, then present the next one. Do NOT batch these — each answer needs its own feedback before the learner can calibrate.

#### Stage 3 — Integration task

One short real-world task that forces the rule in a natural context (not an isolated drill):

- A 2-3 sentence mini-story with blanks
- A short dialogue to complete
- Translate a paragraph that's dense with the rule
- Fix a paragraph with 3-4 embedded errors (all of the same rule)

```markdown
## Интеграция

{task description}

{text/prompt}
```

Wait for the full answer, give detailed feedback. If score < 7, offer a rewrite:

```markdown
Хочешь переписать с учётом исправлений? Напиши "да" или переходи дальше.
```

---

### 7. Feedback

For Stage 1 (batch): give one numbered block per answer, terse. Use `fluent-feedback-formatter` pattern without full header overhead:

```markdown
**1.** ✅ — верно.
**2.** ❌ — "llamó" → **"se llamó"** (reflexive глагол, нужен se). Действие произошло с субъектом, не над объектом.
**3.** ✅
...
**Batch score: {X}/{N}** — {encouragement}
```

For Stage 2 and 3 (individual): use full `fluent-feedback-formatter` template with score/10 and severity tag.

After every mistake that maps to a known error pattern ID in `mistakes_db`, call it out:

```markdown
💡 Это та же ловушка что в прошлых сессиях (*personal_a*). Запомни: {one-line rule}.
```

---

### 8. Progress pulse (mid-session, after Stage 1)

After Stage 1 feedback, show a one-line pulse before Stage 2:

```markdown
**Stage 1:** {X}/{N} верно ({percent}%) — {emoji: ✅ if ≥70%, 🟡 if 50-69%, 🔴 if <50%}
{if <50%: "Давай чуть проще — в Stage 2 начнём с более простых форм."}
{if ≥90%: "Отлично — в Stage 2 усложним формулировки."}
```

Adapt Stage 2 difficulty based on Stage 1 accuracy:
- **<50%** → produce only the key form (no full sentence required); add scaffolding (offer the verb stem)
- **50-70%** → produce a short sentence, no scaffolding
- **>70%** → produce a complete sentence with an additional constraint (add a time expression, make it negative, use the formal register)

---

### 9. Session summary

```markdown
## 📊 Грамматика: Итоги

**Правило:** {rule_display_name}
**Stage 1 (батч):** {X}/{N} верно
**Stage 2 (продукция):** {X}/{N} верно
**Stage 3 (интеграция):** {score}/10

**Результат:** {mastery assessment}
- ✅ Усвоено: {what the learner got consistently right}
- 🔁 Требует повторения: {what to drill again — linked to a specific exercise type}

**Следующее повторение:** {next_review_date from SM-2}

**Следующее правило (рекомендация):**
→ {next_rule_name} — {why: prerequisite, same error cluster, or next curriculum step}

---
{target-language encouragement + rule summary in one sentence}
```

---

### 10. DB update

Use the `fluent-db-updater` skill's checkpoint + commit workflow.

**Checkpoint after Stage 1 batch:**
```bash
python3 ".claude/hooks/update-db.py" --checkpoint <<'EOF'
{
  "session_id": "{session_id}",
  "date": "{date}",
  "command_used": "/fluent-grammar",
  "skills_practiced": ["grammar"],
  "skill_scores": {
    "grammar": {"exercises": {N}, "correct": {correct}, "time_minutes": {t}}
  },
  "review_results": [
    {"item_id": "{grammar_rule_id}", "quality": {0-5}}
  ]
}
EOF
```

**Checkpoint after each Stage 2 answer** — update `skill_scores` with running total.

**Commit at session end:**
```bash
python3 ".claude/hooks/update-db.py" --commit <<'EOF'
{
  "session_id": "{session_id}",
  "date": "{date}",
  "duration_minutes": {t},
  "command_used": "/fluent-grammar",
  "breakthroughs": ["{rule} — first clean pass" if stage1 ≥ 90%],
  "focus_next_session": ["{next_rule}", "{any recurring mistake}"],
  "session_notes": "Grammar: {rule_id}. Stage1: {X}/{N}. Stage2: {X}/{N}. Stage3: {score}/10."
}
EOF
```

**If introducing a rule for the first time** (Check C hit): add it to SR via `new_vocabulary[]` so it enters the review queue:

```json
{
  "item_id": "rule_{topic_id}",
  "item_type": "grammar_rule",
  "content": "{rule_display_name}",
  "answer": "{one-line summary of the rule}",
  "category": "grammar",
  "difficulty": "{easy|medium|hard based on CEFR distance}",
  "initial_quality": "{3 if stage1 ≥70%, 2 if 50-69%, 1 if <50%}",
  "priority": "{high if from error cluster, medium otherwise}"
}
```

**Quality mapping for review_results:**
- Stage 1 batch score ≥ 90% → `quality: 5`
- 70-89% → `quality: 4`
- 50-69% → `quality: 3`
- 30-49% → `quality: 2`
- <30% → `quality: 1`

Also add one `errors[]` entry per **new** mistake type uncovered in Stage 2/3 that isn't already in `mistakes_db`.

Save the full session as `/results/fluent-grammar-session-{NNN}.md` with: rule taught, explanation text, all exercises, all answers, all corrections, and final scores. The `fluent-session-analyzer` parses this format.

---

## Examples

### Example 1 — proactive selection via error cluster (personal_a)

Artem has `err_personal_a` (freq=1) and `err_personal_a_recurring` (freq=2) in `mistakes_db`. Cluster score = 2 patterns × 2 + 3 total freq = 7. Check A fires.

```markdown
# 🧠 Gramática: Personal "a"

**Правило:** Personal "a"
**Почему сейчас:** Ты допускал эту ошибку 3 раза в разных сессиях
**Уровень:** A2
**Время:** ~10 мин

---

## Правило: Personal "a"

**Суть:**
В испанском перед одушевлённым прямым дополнением (человек, питомец) ставится предлог *a*. Это не перевод — в русском аналога нет. Это просто испанский способ показать, что объект — живой.

**Формула:**
ГЛАГОЛ + **a** + [одушевлённое дополнение]

**Когда применять:**
- ✅ *Veo **a** mi madre* (Я вижу свою маму)
- ✅ *Llaman **a** alguien* (Они зовут кого-то)
- ✅ *¿Conoces **a** Juan?* (Ты знаешь Хуана?)
- ❌ *Veo mi madre* — пропущена personal *a*; звучит как «смотрю на маму» (неодушевлённо)
- ❌ *Busco **a** trabajo* — работа неодушевлённая, *a* не нужна

**Запомни:**
Человек → *a*. Предмет → без *a*.

---

## Упражнения — Часть 1

Исправь или заполни пропуск (ответь на все сразу):

**1.** Necesito llamar ___ mi jefe mañana.
**2.** ¿Conoces ___ esta canción? (эта песня)
**3.** Voy a visitar ___ mis padres este fin de semana.
**4.** El médico está esperando ___ el paciente.
**5.** Busco ___ un apartamento en el centro.
**6.** ¿Ves ___ Pedro por aquí?
**7.** Tengo que llevar ___ el perro al veterinario.
**8.** Quiero conocer ___ tu ciudad natal. (родной город)
```

After learner answers:

```markdown
**1.** ✅ — *llamar **a** mi jefe* верно.
**2.** ❌ — "esta canción" неодушевлённое → *a* не нужна: *¿Conoces esta canción?*
**3.** ✅
**4.** ✅
**5.** ❌ — "apartamento" неодушевлённое → без *a*: *Busco un apartamento.*
**6.** ✅
**7.** ✅ — животные тоже одушевлённые, *a* нужна.
**8.** ❌ — "ciudad natal" неодушевлённое → *Quiero conocer tu ciudad natal.*

**Stage 1: 5/8 (62%)** 🟡 — Логику ты понял, застреваешь только на неодушевлённых. В Stage 2 сосредоточимся на различии.
```

---

### Example 2 — proactive recommendation after session

```markdown
## Следующее правило (рекомендация):

→ **Gender agreement** (согласование рода) — у тебя 4 паттерна ошибок связанных с родом существительных и прилагательных: *noun_gender_mente*, *noun_gender_ruta*, *noun_gender_hora_esta*, *noun_gender_agreement_recurring*. Логично добить это сейчас.
```

---

### Example 3 — curriculum check (no clusters, no due items)

Artem is A2. All A1 topics are in SR. Check A: no cluster score ≥ 4. Check B: no grammar_rule items due. Check C: `present_tense_irregular` not yet in `spaced_repetition.items` → introduce it.

```markdown
**Recommended:** Irregulares en presente (tener, hacer, ir...) — следующий пункт в программе A2, все A1-правила уже усвоены.
```

---

## Critical Rules

- **Never auto-invoke.** Gated; must fire only on `/fluent-grammar`.
- **One rule per session.** Depth over breadth — the learner should finish knowing this rule cold, not half-knowing four.
- **Run the proactive algorithm every time** — never assume what rule is next without checking the data.
- **Explain in the learner's native language.** Grammar explanation in Russian; exercises in Spanish + feedback in Russian.
- **Stage 1 is always a batch.** Never drip-feed fill-in-the-blank items one by one.
- **Stage 2 waits for each answer** before the next. Production exercises need calibrated feedback to be useful.
- **Call out the connection to mistakes_db** whenever a drill exercise matches a known error pattern. The learner should see "this is that mistake you keep making."
- **Always add the rule to spaced_repetition** on first introduction — the skill is useless if the rule is never reviewed.
- **Save results to `/results/`** — the `fluent-session-analyzer` and the learner's own review depend on it.
- **Use `fluent-feedback-formatter`** for Stage 2/3 individual answers.
- **Checkpoint after Stage 1 and after each Stage 2 answer** — don't wait until session end to persist data.

## Why This Skill Exists

Grammar mistakes compound. An unresolved reflexive-verb gap causes errors in every writing session, every speaking session, every review. Addressing it explicitly — explain once, drill to near-mastery, schedule review — removes a recurring drag on the whole system. This skill makes grammar a first-class practice mode instead of a side-effect of other sessions.
