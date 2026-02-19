from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath

from app.models import Asset
from app.schemas import BulkRecipeType
from app.services.bulk_recipes.base import (
    GroupedCase,
    GroupingResult,
    RegisteredBulkRecipe,
    asset_path,
    sorted_assets_by_path,
)


def group_assets(assets: list[Asset], recipe_config: dict) -> GroupingResult:
    images_per_case_raw = recipe_config.get("images_per_case", 1)
    try:
        images_per_case = int(images_per_case_raw)
    except (TypeError, ValueError):
        images_per_case = 1
    if images_per_case < 1:
        images_per_case = 1

    warnings: list[str] = []
    grouped: dict[int, list[tuple[str, str, str]]] = defaultdict(list)
    seen_suffix_by_index: dict[int, set[str]] = defaultdict(set)
    pattern = re.compile(r"^(?P<index>\d+)(?P<suffix>[A-Za-z]?)$")

    custom_labels_raw = recipe_config.get("stimulus_slot_labels", [])
    custom_labels: list[str] = []
    if isinstance(custom_labels_raw, list):
        custom_labels = [str(item).strip() for item in custom_labels_raw if str(item).strip()]
    elif isinstance(custom_labels_raw, str):
        custom_labels = [item.strip() for item in custom_labels_raw.split("|") if item.strip()]
    if custom_labels and len(custom_labels) != images_per_case:
        warnings.append("Ignored stimulus_slot_labels: label count must equal images_per_case")
        custom_labels = []

    for asset in sorted_assets_by_path(assets):
        path = PurePosixPath(asset_path(asset))
        stem = path.stem
        match = pattern.match(stem)
        if not match:
            warnings.append(
                f"Skipped {path.name}: filename stem must be numeric with optional suffix (e.g. 1, 1a, 2b)"
            )
            continue

        case_index = int(match.group("index"))
        if case_index < 1:
            warnings.append(f"Skipped {path.name}: index must start from 1")
            continue

        suffix = match.group("suffix").lower()
        if suffix in seen_suffix_by_index[case_index]:
            warnings.append(f"Skipped {path.name}: duplicate suffix '{suffix or '(none)'}' for case {case_index}")
            continue
        seen_suffix_by_index[case_index].add(suffix)
        grouped[case_index].append((suffix, path.name, asset.id))

    if not grouped:
        return GroupingResult(cases=[], warnings=warnings)

    existing_indices = sorted(grouped.keys())
    max_index = existing_indices[-1]
    missing_indices = [index for index in range(1, max_index + 1) if index not in grouped]
    if missing_indices:
        warnings.append(f"Missing case indices: {', '.join(str(index) for index in missing_indices)}")

    def suffix_sort_key(value: str) -> tuple[int, str]:
        # Keep plain numeric names (e.g. 1.png) ahead of suffixed files (e.g. 1a.png).
        return (0, "") if value == "" else (1, value)

    cases: list[GroupedCase] = []
    for case_index in existing_indices:
        entries = sorted(grouped[case_index], key=lambda item: (suffix_sort_key(item[0]), item[1]))
        if len(entries) != images_per_case:
            warnings.append(f"Skipped case {case_index}: expected {images_per_case} image(s), found {len(entries)}")
            continue

        stimulus_labels = list(custom_labels)
        if not stimulus_labels:
            stimulus_labels = []
            for slot_index, (suffix, _, _) in enumerate(entries, start=1):
                if suffix:
                    stimulus_labels.append(suffix)
                elif images_per_case > 1:
                    stimulus_labels.append(str(slot_index))
                else:
                    stimulus_labels.append("")

        cases.append(
            GroupedCase(
                case_key=f"case-{case_index:04d}",
                stimulus_asset_ids=[asset_id for _, _, asset_id in entries],
                stimulus_labels=stimulus_labels,
            )
        )

    return GroupingResult(cases=cases, warnings=warnings)


RECIPE = RegisteredBulkRecipe(
    recipe_type=BulkRecipeType.INDEXED_SUFFIX_SETS,
    title="Numbered Image Sets",
    summary="Group files by case index with optional suffix letters (e.g. 1a, 1b, 1c).",
    instructions=[
        "Use filenames with consecutive numbering starting from 1.",
        "For multi-image cases, use suffix letters per slot (e.g. 1a/1b/1c, 2a/2b/2c).",
        "Optional slot labels can override default a/b/c style labels.",
    ],
    example_paths=[
        "OCT/Task1/1a.png",
        "OCT/Task1/1b.png",
        "OCT/Task1/1c.png",
        "OCT/Task1/2a.png",
    ],
    config_keys=["images_per_case", "stimulus_slot_labels"],
    supports_patch_question_template=False,
    grouper=group_assets,
)
