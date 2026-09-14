"""
Deterministic exercise ordering within a session phase.

After the LLM generates a day plan, apply reorder_phase() to redistribute
exercises within each phase so that no two consecutive exercises share the
same rotation group (primary_target_area or movement_family). This prevents
the compounding-fatigue problem where the LLM stacks several chest/push
exercises back-to-back.

Algorithm: greedy interleaver with one-slot cooldown exclusion.
  - Bucket exercises by rotation-group key (primary_target_area, movement_family).
  - At each step, pick from the largest bucket that is NOT in the cooldown set.
  - After placing from bucket X, add X to cooldown and release the previously
    cooled bucket.
  - If all remaining buckets are on cooldown (dominant group, nothing else left),
    force-release from the largest cooled bucket — unavoidable consecutive pair,
    best possible outcome (graceful degradation, no crash).

Phase boundaries are strictly preserved: the list is split by 'category' field
(warmup / main / cooldown), each segment is shuffled independently, then the
segments are reassembled in warmup → main → cooldown order. No exercise ever
crosses a phase boundary.

Only the main phase is reordered. Warmup and cooldown segments are short (2-4
and 1-4 exercises respectively) and are returned in their original LLM order.
"""

import logging
from collections import deque
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Movement-family mapping
# Maps individual movement_patterns tokens → a coarser family label.
# Two exercises in the same family are treated as a rotation conflict even if
# their primary_target_area differs (e.g. chest+triceps are both push_family).
# ---------------------------------------------------------------------------

_PATTERN_TO_FAMILY: dict[str, str] = {
    # Push
    "push": "push_family",
    "horizontal_push": "push_family",
    "vertical_push": "push_family",
    "push_up": "push_family",
    "dip": "push_family",
    # Pull
    "pull": "pull_family",
    "horizontal_pull": "pull_family",
    "vertical_pull": "pull_family",
    "curl": "pull_family",
    # Lower body
    "squat": "lower_family",
    "lunge": "lower_family",
    "hip_hinge": "lower_family",
    "lower_body_push": "lower_family",
    "bridge": "lower_family",
    "calf_raise": "lower_family",
    "hip_extension": "lower_family",
    "step_up": "lower_family",
    # Core / stability
    "anti_extension": "core_family",
    "anti_rotation": "core_family",
    "core_stability": "core_family",
    "plank": "core_family",
    "rollout": "core_family",
    "isometric": "core_family",
    "rotation": "core_family",
    "lateral_stability": "core_family",
    # Cardio / plyometric
    "jump": "cardio_family",
    "plyometric": "cardio_family",
    "explosive": "cardio_family",
    "locomotion": "cardio_family",
    "cardio": "cardio_family",
}

_PHASE_ORDER = ["warmup", "main", "cooldown"]


def _movement_family(movement_patterns: list[str]) -> str:
    """Derive the coarsest movement family from a list of pattern tokens.

    Returns the family of the first pattern that matches the mapping, or
    'other_family' if none match. Uses the first match so that more specific
    tokens (e.g. 'horizontal_push') take precedence when listed first.
    """
    for pattern in movement_patterns:
        family = _PATTERN_TO_FAMILY.get(pattern)
        if family:
            return family
    return "other_family"


def _rotation_key(
    ex: dict,
    metadata_lookup: Callable[[str], Optional[dict]],
) -> tuple[str, str]:
    """Return (primary_target_area, movement_family) for a plan exercise dict.

    Looks up the exercise in the library via metadata_lookup(name).
    Falls back to (ex['focus'], 'unknown') when the name is not in the library
    (e.g. custom exercises added outside the standard library).
    """
    name = ex.get("name", "")
    meta = metadata_lookup(name) if name else None
    if meta:
        primary = meta.get("primary_target_area") or ex.get("focus", "unknown")
        family = _movement_family(meta.get("movement_patterns") or [])
    else:
        primary = ex.get("focus", "unknown")
        family = "unknown"
    return (primary, family)


def _conflicts_with(
    key1: tuple[str, str],
    key2: Optional[tuple[str, str]],
) -> bool:
    """Return True if key1 and key2 represent exercises that should not be adjacent.

    Two exercises conflict if they share:
    - the same primary_target_area, OR
    - the same movement_family — UNLESS the family is 'other_family' (unclassified
      patterns). Using 'other_family' as a conflict key would create spurious blocks
      between unrelated movements that simply happen to lack a recognized pattern.

    This OR-logic is necessary because chest (primary=chest) and triceps (primary=
    triceps) exercises both belong to push_family and cause the same compounding
    fatigue, even though they have different primary_target_area labels.
    """
    if key2 is None:
        return False
    p1, f1 = key1
    p2, f2 = key2
    if p1 == p2:
        return True
    if f1 != "other_family" and f1 == f2:
        return True
    return False


def _interleave(
    exercises: list[dict],
    metadata_lookup: Callable[[str], Optional[dict]],
) -> list[dict]:
    """Reorder a flat list of exercises using the cluster-based greedy interleaver.

    To prevent cross-key conflicts (e.g. biceps/pull and back/pull sharing pull_family)
    from being pushed together when other non-conflicting exercises exist, exercises
    are pre-merged into conflict clusters (connected components over rotation keys
    using _conflicts_with as the edge predicate).

    The frequency-based greedy interleaving logic operates on clusters:
    - Avoids selecting the active cooldown cluster whenever eligible alternative clusters exist.
    - Selects the largest eligible cluster (ties broken deterministically by cluster key).
    - Inside the chosen cluster, pops from the largest sub-bucket whose key does not conflict
      with the previous exercise's key if possible.
    - If the pool is genuinely homogeneous (one cluster dominates), degrades gracefully via
      the force-release fallback path.

    Args:
        exercises: list of ExercisePlanItem dicts (already within one phase).
        metadata_lookup: callable that accepts an exercise name and returns the
            library dict (or None). Injected so this module has no import-time
            DB/file side-effects and stays independently testable.

    Returns:
        Reordered list — same exercises, different order.
    """
    if len(exercises) <= 1:
        return list(exercises)

    # 1. Map each exercise to its rotation key and group identity
    ex_keys = [_rotation_key(ex, metadata_lookup) for ex in exercises]
    unique_keys = list(set(ex_keys))

    # 2. Compute connected components (clusters) of keys using _conflicts_with as edge predicate
    parent = {k: k for k in unique_keys}

    def find(k: tuple[str, str]) -> tuple[str, str]:
        if parent[k] != k:
            parent[k] = find(parent[k])
        return parent[k]

    def union(k1: tuple[str, str], k2: tuple[str, str]) -> None:
        r1, r2 = find(k1), find(k2)
        if r1 != r2:
            if r1 < r2:
                parent[r2] = r1
            else:
                parent[r1] = r2

    for i in range(len(unique_keys)):
        for j in range(i + 1, len(unique_keys)):
            k1, k2 = unique_keys[i], unique_keys[j]
            if _conflicts_with(k1, k2):
                union(k1, k2)

    # 3. Group exercises by cluster ID (root key) and sub-bucket (rotation_key, group_identity)
    clusters: dict[tuple[str, str], dict[tuple[tuple[str, str], str], deque[dict]]] = {}
    for ex in exercises:
        k = _rotation_key(ex, metadata_lookup)
        group_id = ex.get("set_group_id") or ex.get("name", "unknown")
        sub_k = (k, group_id)
        root = find(k)
        if root not in clusters:
            clusters[root] = {}
        if sub_k not in clusters[root]:
            clusters[root][sub_k] = deque()
        clusters[root][sub_k].append(ex)

    def cluster_size(root_key: tuple[str, str]) -> int:
        return sum(len(q) for q in clusters[root_key].values())

    unique_groups = {ex.get("set_group_id") or ex.get("name") for ex in exercises}

    if len(clusters) == 1 and len(unique_groups) == 1:
        # All exercises belong to a single conflict cluster and share the exact same set group — nothing can be separated.
        logger.debug(
            "reorder_phase: all %d exercises belong to single conflict cluster %s and group %s, returning as-is.",
            len(exercises),
            next(iter(clusters)),
            next(iter(unique_groups)),
        )
        return list(exercises)

    result: list[dict] = []
    cooldown_cluster: Optional[tuple[str, str]] = None
    cooldown_key: Optional[tuple[str, str]] = None
    cooldown_group_id: Optional[str] = None

    while clusters:
        active_clusters = [c for c in clusters if cluster_size(c) > 0]
        if not active_clusters:
            break

        eligible_clusters = [c for c in active_clusters if c != cooldown_cluster]

        if eligible_clusters:
            chosen_cluster = max(eligible_clusters, key=lambda c: (cluster_size(c), c))
        else:
            # Forced: all remaining exercises belong to cooldown_cluster — unavoidable consecutive pair.
            chosen_cluster = max(active_clusters, key=lambda c: (cluster_size(c), c))
            logger.debug(
                "reorder_phase: force-releasing conflicting cluster %s — "
                "unavoidable consecutive conflict (pool too homogeneous).",
                chosen_cluster,
            )

        sub_buckets = clusters[chosen_cluster]

        # Prefer sub-buckets whose rotation key doesn't conflict AND group_id isn't on cooldown
        eligible_subs = [
            sk for sk, q in sub_buckets.items()
            if len(q) > 0 and not _conflicts_with(sk[0], cooldown_key) and sk[1] != cooldown_group_id
        ]

        if not eligible_subs:
            # Secondary option: allow same rotation key, but different group_id
            eligible_subs = [
                sk for sk, q in sub_buckets.items()
                if len(q) > 0 and sk[1] != cooldown_group_id
            ]

        if not eligible_subs:
            # Fallback: allow non-conflicting key regardless of group_id
            eligible_subs = [
                sk for sk, q in sub_buckets.items()
                if len(q) > 0 and not _conflicts_with(sk[0], cooldown_key)
            ]

        if eligible_subs:
            chosen_key = max(eligible_subs, key=lambda sk: (len(sub_buckets[sk]), sk))
        else:
            active_subs = [sk for sk, q in sub_buckets.items() if len(q) > 0]
            chosen_key = max(active_subs, key=lambda sk: (len(sub_buckets[sk]), sk))

        ex = sub_buckets[chosen_key].popleft()
        result.append(ex)

        cooldown_cluster = chosen_cluster
        cooldown_key = chosen_key[0]
        cooldown_group_id = chosen_key[1]

        if len(sub_buckets[chosen_key]) == 0:
            del sub_buckets[chosen_key]
        if cluster_size(chosen_cluster) == 0:
            del clusters[chosen_cluster]

    return result


def reorder_phase(
    exercises: list[dict],
    *,
    metadata_lookup: Optional[Callable[[str], Optional[dict]]] = None,
) -> list[dict]:
    """Reorder a day's exercises within the main phase to avoid consecutive
    same-group pairs.

    Phase boundaries are strictly preserved:
      - All warmup exercises remain first, in their original relative order.
      - All main exercises are reordered by the cooldown-exclusion interleaver.
      - All cooldown exercises remain last, in their original relative order.
      - No exercise is moved across a phase boundary.

    Args:
        exercises: Full list of ExercisePlanItem dicts for a day (all phases).
        metadata_lookup: Optional callable(name) -> library dict | None.
            Defaults to the standard get_exercise_metadata from workout_library.
            Pass a custom function in tests to avoid needing the real library file.

    Returns:
        Reordered list — same exercises, same counts per phase, different main-
        phase internal order.
    """
    if not exercises:
        return []

    # Default to the real library lookup when not injected.
    if metadata_lookup is None:
        from tools.workout_library import get_exercise_metadata
        metadata_lookup = get_exercise_metadata

    # Partition by phase, preserving original ordering within each partition.
    phases: dict[str, list[dict]] = {phase: [] for phase in _PHASE_ORDER}
    unknown_phase: list[dict] = []

    for ex in exercises:
        cat = ex.get("category", "")
        if cat in phases:
            phases[cat].append(ex)
        else:
            unknown_phase.append(ex)
            logger.warning(
                "reorder_phase: exercise '%s' has unknown category '%s', "
                "appending at end unchanged.",
                ex.get("name", "?"),
                cat,
            )

    # Only reorder the main phase; warmup and cooldown stay as-is.
    main_reordered = _interleave(phases["main"], metadata_lookup)

    consecutive_conflicts = sum(
        1
        for i in range(1, len(main_reordered))
        if _rotation_key(main_reordered[i], metadata_lookup)
        == _rotation_key(main_reordered[i - 1], metadata_lookup)
    )
    if consecutive_conflicts:
        logger.info(
            "reorder_phase: %d unavoidable consecutive same-group pair(s) remain "
            "after reordering (pool too homogeneous to fully separate).",
            consecutive_conflicts,
        )

    return phases["warmup"] + main_reordered + phases["cooldown"] + unknown_phase
