# Exercise Plan Generation — Quality Analysis

**Scope:** Why daily/monthly exercise plans come out small, repetitive, and poorly matched to the user's goal and profile, and what to change.

**Status:** Investigation / recommendation. No code changed.

---

## Executive summary

The problems the user reports (6–8 exercises per day, narrow variety, monthly-goal exercises missing, and arbitrary intensity) are **not** random LLM behavior — they are the deterministic output of two concrete bugs plus two data gaps:

1. **The LLM is only ever shown 5 exercises.** `search_workout_library` caps results at `matches[:5]` (`tools/workout_library.py:191`), and `generate_today_plan` builds its pool from exactly two such calls that return the *same* first 5 entries from the file (`tools/plans.py:454-456`). Today that pool is literally `Dead Bug, Band Assisted Wheel Rollerout, Russian Twist, Reverse Crunch, Dumbbell Side Bend` — **five abs exercises**. The LLM cannot invent exercises (schema + prompt forbid it), so 7–20-exercise days are impossible to build from 5 candidates → it repeats the same exercises, which is also the "push-up for 3 rounds with nothing in between" feeling.
2. **The pool is never filtered by the user's goal, week focus, equipment, or difficulty.** `generate_today_plan` calls `search_workout_library.invoke({})` with *no* filters, so it ignores the goal's `volume_targets` entirely. The monthly-goal exercises are not even passed into the backfill prompt, so the model has no reason to include them.
3. **The library has only `strength` exercises** — no stretch/mobility/cardio/cooldown records (verified: `type` ∈ `{"strength"}` across all 89). Yet the prompt *requires* warm-up and cool-down phases, so the model is forced to label strength movements as "warmup"/"cooldown", producing the same exercise across phases.
4. **Intensity (sets/reps) is written by the LLM from nothing.** The search output doesn't include the per-exercise `baseline` (sets/reps/rest), and `ExercisePlanItem` accepts any free-form `sets`/`reps`. No user capacity data (baseline, experience) or anthropometrics (age/height/weight/BMI) is fed into daily generation at all.

---

## How generation works today

```
generate_today_plan (tools/plans.py:371)
 ├─ requires confirmed month goal  (tools/month_plans.py)
 ├─ ensures week theme path + week block structure (non-LLM)
 ├─ past_plans_context = get_past_plans()
 ├─ backlog_items      = get_backlog()
 ├─ knowledge_context  = search_fitness_knowledge_base(week_focus + goal)
 ├─ main_pool  = search_workout_library()                      ← 5 rows, no filters
 ├─ short_pool = search_workout_library(max_duration_minutes=6) ← same 5 rows again
 └─ generate_backfill_days(..., goal_description, week_focus, backlog, past, knowledge, available_exercises=main_pool+short_pool)
      └─ BACKFILL_DAY_PLAN_PROMPT → structured LLM call → 4 days × 7-20 exercises
```

---

## Root causes, mapped to each reported symptom

### 1. Number of exercises is too small (6–8)

**Primary cause — `matches[:5]` cap + duplicate pools.**

- `tools/workout_library.py:191` — `for w in matches[:5]` truncates every search to 5 results.
- `tools/plans.py:454-455`:
  ```python
  main_pool = search_workout_library.invoke({})
  short_pool = search_workout_library.invoke({"max_duration_minutes": 6})
  ```
  `max_duration_minutes=6` is a no-op here: the estimator (`tools/workout_library.py:47`) puts *every* library entry under 6 min, so both calls return the **same 5** rows. The dedupe that the code *assumes* never actually shrinks anything, and the LLM sees 5 unique exercises, not 10.

- The 5 returned are deterministic and alphabetical-by-area: the file lists `abs` exercises first, so every backfill day is built from **only abs exercises**. The 7–20 count is enforced by `BackfillDayPlanInput` (`tools/plans.py:250`), so the model hits the **7 minimum** by duplicating the 5 candidates across warmup/main/cooldown.

**Secondary cause — every day gets the same pool.** No randomization or round-robin exists, so all 4 backfill days look identical, compounding the "few + repetitive" feel.

### 2. Type variety is limited; monthly-goal exercises don't appear

**Cause A — pool ignores goal & week focus.** `generate_today_plan` passes only `goal_description` (a text string) and `week_focus` into the prompt (`tools/plans.py:465-466`). The goal's **structured** `volume_targets` — the exact exercises the user agreed to do monthly — are never passed, and never added to the pool. So `Push-Ups` in your monthly goal can't appear in a day plan built from the 5 abs-only rows.

**Cause B — no phase-appropriate exercise types exist.** All 89 records are `type: "strength"`. The prompt requires warmup (mobility/activation) and cooldown (stretch/breathing) phases (`agent/prompts.py:57-60`), but no such exercises exist in the library. The model must fill those slots from strength entries, so a single movement shows up in multiple phases (the UI even documents this hack: `app/components/workout_session.py:177-180` uses "Dead Bug" twice because "the rebuilt library has no true stretch/mobility/cooldown-type entries yet"). Only 16 records even carry a `stretch`/`mobility`/`cardio` *tag*, and tags aren't surfaced as a filterable category.

**Cause C — `category` is free choice, not data.** `category` (`warmup/main/cooldown`) is chosen by the LLM with zero input from the record, so there is nothing stopping it from labeling a strength movement as "warmup".

### 3. Intensity (sets/reps) is unreliable

**Cause — no grounding data in the prompt, no validation in the schema.**

- The formatted search result shows name, areas, equipment, difficulty, and `~X min` — but **not** the `baseline` (sets/reps/rest) that the estimator already computes from (`tools/workout_library.py:191-200`).
- `ExercisePlanItem.sets/reps` are unconstrained (`tools/plans.py:84-87`) — no range check per difficulty, no link to `calculate_volume_target`.
- The `calculate_volume_target` tool (`tools/month_plans.py:130`) computes grounded month targets from a user baseline + experience level, but that path is only used at goal setup; the numbers never flow into day plans. Day-level intensity is therefore whatever the LLM writes with no reference point.

The user's hunch ("intensity is OK for the ones chosen") is partially right — but only because the model's arbitrary picks happen to sit in sane ranges. It will not survive fixing issues 1–2 without also grounding intensity.

### 4. Age / height / weight / BMI are not used

**Cause — the data is never captured, and no pipeline consumes it.**

- User accounts store only `email` + `password_hash` (`auth/user_store.py:61-67`).
- `body_weight` may exist as a free-form metric via `log_metric` (`tools/metrics.py`), but no code reads it for plan generation.
- `calculate_volume_target` uses only `experience_level` (LLM-guessed) + optional `baseline_value` (`utils/baseline_targets.py:39-43`); anthropometrics never factor into intensity factor, rep selection, or exercise difficulty choice.
- The Streamlit app has no profile/onboarding screen collecting age/height/weight/sex/activity level (only login/register in `app/components/auth_ui.py`).

---

## Additional contributing findings

- **`data/workout_updated.json` is empty (0 bytes)** — a stale/leftover file that shadows the real `workouts.json` in a few directory listings; no code loads it, but it should be removed to avoid confusion.
- **No day-over-day diversity.** The 4-day backfill uses the same static pool and no "don't repeat yesterday's exercises" constraint.
- **The "3 rounds sequential" behavior** is straight-set execution in the guided session UI (`app/components/workout_session.py:297-369`): each exercise's sets run back-to-back. Normal for straight sets, but feels monotonous when the pool is 5 abs exercises and the same movement is reused across phases. The fix is a wider pool + explicit superset/alternation guidance, not a UI change.
- **`matches[:5]` is explicitly called "out of scope"** in the V9.5 scope doc (`docs/V9.5 ... .md:112-115`), so this cap was a deliberate-but-now-bottlenecked design decision.

---

## Recommended changes (prioritized)

### P1 — Fix the exercise pool (unblocks everything else)

1. **Raise / parameterize the result cap** in `search_workout_library` (`tools/workout_library.py:191`). Add a `limit` argument (default ~5 for chat use, but pass e.g. `limit=20-30` from `generate_today_plan`), or add a dedicated `get_exercise_catalog(...)` bulk tool returning a compact catalog (name, type, primary area, difficulty, equipment, est duration, baseline, tags) for plan-building.
2. **Rebuild the pool properly in `generate_today_plan`** (`tools/plans.py:454-456`): one call with the week/goal filters and a large limit, plus a `random.sample` / per-area round-robin so days vary. Never build it from two identical 5-row calls.
3. **Filter the pool by user constraints**: week `focus_area`, the goal's `volume_targets` exercise names (always included), equipment, `avoid_if_injured`, and difficulty matched to experience.

### P2 — Ground daily exercises in the monthly goal

4. **Pass `volume_targets` (exercise + unit + month_target) into `generate_backfill_days`** and the `BACKFILL_DAY_PLAN_PROMPT` (`agent/plan_generation.py:45-56`, `agent/prompts.py:172-183`). Instruct the model to feature those exercises and split each month_target across the week's sessions (the remaining-volume math already exists in `tools/week_plans.py:_calculate_week_targets` — reuse it for the day prompt).
5. **Enforce presence**: add a validation that goal exercises appear in the plan (or an explicit "why excluded" note), mirroring the existing exercise-name validator (`tools/plans.py:125-134`).

### P3 — Ground intensity

6. **Surface `baseline` in search output** (`tools/workout_library.py:191-200`): include `baseline sets x reps, rest Xs` so the LLM writes sets/reps consistent with the record.
7. **Feed experience level / baseline into day generation**: pull the confirmed goal's baseline and the `calculate_volume_target` result into the prompt; tell the model the user's experience level so rep ranges scale (e.g. beginner 8–12, intermediate 10–15, advanced 12–20 depending on goal).
8. **Add schema validation** to `ExercisePlanItem` (`tools/plans.py:78-134`): rep-range sanity per `difficulty`, require `sets`/`reps` on `main`, and optionally a `target_quantity`/`unit` when the exercise has a goal volume.

### P4 — Fix the exercise-type gap (data-level)

9. **Add stretch / mobility / cardio / cooldown records** to `data/workouts.json` (the `exercises-dataset` used in V9.5 has 1,324 entries including stretching and cardio categories), or at minimum tag existing records with an `exercise_type` field (`warmup`, `main`, `cooldown`). Surface this field in the catalog and let the prompt assign `category` from it rather than free choice.
10. **Add variety guards**: no exercise in more than one phase per day, cap repetitions of the same exercise, and prefer alternate (push/pull, upper/lower) superset pairing to break the "3 rounds straight" monotony.

### P5 — User profile & anthropometrics

11. **Add a profile schema** (onboarding in the app + stored on the user doc, `auth/user_store.py`): age, sex, height, weight, activity level, experience level, equipment list. Compute `BMI` and classify (underweight/normal/overweight/obese).
12. **Wire BMI + age into intensity**:
    - Map BMI/age/activity → the `INTENSITY_FACTOR` in `utils/baseline_targets.py` instead of the LLM-guessed `experience_level` alone.
    - Use BMI/weight to gate high-impact exercises (e.g. plyometrics, burpees) toward lower-impact alternatives for obese/high-BMI users and older ages.
    - Use age to set default rest times and caution flags (e.g. `avoid_if_injured` for knee/back loading).
13. **Feed the profile into day generation** (same channel as P2/P3): include it in `generate_today_plan`'s prompt assembly so intensity and exercise choice reflect the user's stats.

### P3+ — Cleanup

14. Delete empty `data/workout_updated.json`.
15. Fix the duplicate-pool construction comment and consider a small unit test that asserts `generate_today_plan`'s pool is >5 unique exercises and contains the goal's `volume_targets`.

---

## Expected impact

| Fix | Effect |
|---|---|
| Larger, filtered, deduplicated pool | 7–20 real exercises/day; proper warmup/main/cooldown distribution; varied days |
| Pass goal `volume_targets` into day prompt | Monthly-goal exercises reliably appear and get daily volume |
| Surface baseline + experience in prompt, validate schema | Reps/sets track the user's real capacity and difficulty |
| Add stretch/cardio/cooldown types | Phase-appropriate exercises, no more "Dead Bug twice" workaround |
| Profile + BMI → intensity/impact gating | Plans adapt to age, weight, BMI, activity — closes the final gap |
