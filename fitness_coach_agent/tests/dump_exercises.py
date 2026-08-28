"""
Script: dump_exercises.py
Location: fitness_coach_agent/tests/dump_exercises.py

Purpose:
--------
Extracts name, type, primary target area, secondary areas, equipment, movement patterns,
tags, difficulty, baseline, and full description for all exercises in `data/workouts.json`.

This allows reviewing each exercise to determine which ones are correctly
classified as 'strength' and which should be reclassified into warmup, mobility,
stretch, cardio, or cooldown for V9.6 reclassification.

Usage:
------
    python fitness_coach_agent/tests/dump_exercises.py [--format console|markdown|json] [--save]
"""

import argparse
import json
import os
import sys

# Ensure root fitness_coach_agent directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "workouts.json"
)

def load_exercises():
    if not os.path.exists(DATA_PATH):
        raise FileNotFoundError(f"Workouts dataset not found at {DATA_PATH}")
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "exercises" in data:
        return data["exercises"]
    return data

def format_exercise_markdown(idx, ex):
    name = ex.get("name", "Unknown")
    ex_id = ex.get("id", "N/A")
    current_type = ex.get("type", "N/A")
    primary = ex.get("primary_target_area", "N/A")
    secondary = ", ".join(ex.get("secondary_target_areas", [])) or "None"
    equipment = ", ".join(ex.get("equipment", [])) or "None"
    patterns = ", ".join(ex.get("movement_patterns", [])) or "None"
    tags = ", ".join(ex.get("tags", [])) or "None"
    difficulty = ex.get("difficulty", "N/A")
    description = ex.get("description", "").strip() or "No description available."
    baseline = ex.get("baseline", {})
    baseline_str = f"{baseline.get('sets', '?')} sets x {baseline.get('reps', '?')} {baseline.get('unit', '')} (rest {baseline.get('rest_seconds', '?')}s)" if baseline else "None"

    return (
        f"### {idx}. {name} (ID: `{ex_id}`)\n"
        f"- **Current Type:** `{current_type}`\n"
        f"- **Primary Target Area:** {primary}\n"
        f"- **Secondary Areas:** {secondary}\n"
        f"- **Equipment:** {equipment}\n"
        f"- **Movement Patterns:** {patterns}\n"
        f"- **Tags:** {tags}\n"
        f"- **Difficulty:** {difficulty}\n"
        f"- **Baseline:** {baseline_str}\n"
        f"- **Description:** {description}\n"
    )

def format_exercise_console(idx, ex):
    name = ex.get("name", "Unknown")
    ex_id = ex.get("id", "N/A")
    current_type = ex.get("type", "N/A")
    primary = ex.get("primary_target_area", "N/A")
    secondary = ", ".join(ex.get("secondary_target_areas", [])) or "None"
    equipment = ", ".join(ex.get("equipment", [])) or "None"
    patterns = ", ".join(ex.get("movement_patterns", [])) or "None"
    tags = ", ".join(ex.get("tags", [])) or "None"
    difficulty = ex.get("difficulty", "N/A")
    description = ex.get("description", "").strip() or "No description available."
    baseline = ex.get("baseline", {})
    baseline_str = f"{baseline.get('sets', '?')} sets x {baseline.get('reps', '?')} {baseline.get('unit', '')} (rest {baseline.get('rest_seconds', '?')}s)" if baseline else "None"

    return (
        f"[{idx:02d}/89] {name} (ID: {ex_id})\n"
        f"  Type: {current_type} | Target: {primary} (Secondary: {secondary})\n"
        f"  Equipment: {equipment} | Patterns: {patterns} | Difficulty: {difficulty}\n"
        f"  Tags: {tags}\n"
        f"  Baseline: {baseline_str}\n"
        f"  Description: {description}\n"
        f"{'-'*75}"
    )

def main():
    parser = argparse.ArgumentParser(description="Dump exercise library info for V9.6 reclassification analysis.")
    parser.add_argument("--format", choices=["console", "markdown", "json"], default="console", help="Output format (console, markdown, json)")
    parser.add_argument("--save", action="store_true", help="Save output to file (docs/exercise_reclassification_list.md or .json)")
    args = parser.parse_args()

    exercises = load_exercises()
    print(f"Loaded {len(exercises)} exercises from {DATA_PATH}\n")

    if args.format == "json":
        output_str = json.dumps(exercises, indent=2)
    elif args.format == "markdown":
        lines = [
            "# Exercise Library for V9.6 Reclassification Analysis",
            f"Total Exercises: {len(exercises)}",
            "",
            "Review each exercise description and metadata to reclassify into: `strength`, `warmup`, `stretch`, `mobility`, `cardio`, or `cooldown`.",
            "",
            "---",
            ""
        ]
        for idx, ex in enumerate(exercises, 1):
            lines.append(format_exercise_markdown(idx, ex))
        output_str = "\n".join(lines)
    else:
        lines = [format_exercise_console(idx, ex) for idx, ex in enumerate(exercises, 1)]
        output_str = "\n".join(lines)

    if args.save:
        out_filename = "exercise_reclassification_list.md" if args.format == "markdown" else ("exercises_dump.json" if args.format == "json" else "exercises_dump.txt")
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.normpath(os.path.join(out_dir, out_filename))
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(output_str)
        print(f"Successfully saved output to {out_path}\n")
    
    print(output_str)

if __name__ == "__main__":
    main()
