from __future__ import annotations

from app.models import Asset
from app.services.bulk_recipes.base import (
    GroupedCase,
    GroupingResult,
    RegisteredBulkRecipe,
    sorted_assets_by_path,
)


def group_assets(assets: list[Asset], recipe_config: dict) -> GroupingResult:
    del recipe_config
    cases: list[GroupedCase] = []
    for index, asset in enumerate(sorted_assets_by_path(assets), start=1):
        cases.append(
            GroupedCase(
                case_key=f"case-{index:04d}",
                stimulus_asset_ids=[asset.id],
            )
        )
    return GroupingResult(cases=cases, warnings=[])


RECIPE = RegisteredBulkRecipe(
    recipe_type="single_per_file",
    title="Single Image Per Case",
    summary="Each uploaded image becomes one case.",
    instructions=[
        "Use this when there is no set grouping pattern and each file is its own case.",
        "Filenames can be arbitrary.",
    ],
    example_paths=[
        "Fundus/Task3/1.png",
        "ERM-OCT/0004210405-...-Volume-24.png",
    ],
    config_keys=[],
    supports_patch_question_template=False,
    grouper=group_assets,
    catalog_order=20,
)
