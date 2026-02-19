from __future__ import annotations

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
    left_folder = str(recipe_config.get("left_folder", "no_support"))
    right_folder = str(recipe_config.get("right_folder", "with_support"))
    strict = bool(recipe_config.get("strict", True))

    grouped: dict[str, dict[str, str]] = defaultdict(dict)
    warnings: list[str] = []
    for asset in sorted_assets_by_path(assets):
        path = PurePosixPath(asset_path(asset))
        parent = path.parent.name
        if parent == left_folder:
            grouped[path.name]["left"] = asset.id
        elif parent == right_folder:
            grouped[path.name]["right"] = asset.id
        else:
            warnings.append(
                f"Skipped {path.as_posix()}: parent folder must be '{left_folder}' or '{right_folder}'"
            )

    cases: list[GroupedCase] = []
    for filename in sorted(grouped):
        pair = grouped[filename]
        missing_sides = [side for side in ("left", "right") if side not in pair]
        if missing_sides and strict:
            warnings.append(
                f"Skipped pair '{filename}': missing {', '.join(missing_sides)} side(s) (strict mode)"
            )
            continue
        stimulus_asset_ids = [pair[side] for side in ("left", "right") if side in pair]
        if not stimulus_asset_ids:
            continue
        cases.append(GroupedCase(case_key=PurePosixPath(filename).stem, stimulus_asset_ids=stimulus_asset_ids))

    return GroupingResult(cases=cases, warnings=warnings)


RECIPE = RegisteredBulkRecipe(
    recipe_type=BulkRecipeType.PAIRED_BY_FILENAME,
    title="Paired Comparison Folders",
    summary="Pair same filename from two sibling folders (e.g. no_support vs with_support).",
    instructions=[
        "Prepare two folders where matching filenames represent one case.",
        "Each case gets two stimulus images (left then right folder order).",
        "Use strict=false only when partial pairs should be kept.",
    ],
    example_paths=[
        "BagNetFP/no_support/24939_left.png",
        "BagNetFP/with_support/24939_left.png",
    ],
    config_keys=["left_folder", "right_folder", "strict"],
    supports_patch_question_template=False,
    grouper=group_assets,
)
