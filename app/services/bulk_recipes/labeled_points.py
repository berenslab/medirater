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
    recipe_type="labeled_points",
    title="Labeled Points Annotation",
    summary="One image per case with point annotations grouped by label.",
    instructions=[
        "Upload one image file per case.",
        "Use question template choices to define annotation labels (label|value).",
        "Use annotation question type to collect x/y coordinates grouped by label value.",
    ],
    example_paths=[
        "annotate/OCT/0001.png",
        "annotate/OCT/0002.png",
    ],
    config_keys=[],
    supports_patch_question_template=False,
    grouper=group_assets,
    forced_case_question_type="annotation",
    catalog_order=30,
)
