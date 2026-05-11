from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from app.models import Asset
from app.services.bulk_recipes.base import (
    GroupedCase,
    GroupingResult,
    RegisteredBulkRecipe,
    asset_path,
    sorted_assets_by_path,
)

ROLES = ("stage1", "stage2", "icd")


def group_assets(assets: list[Asset], recipe_config: dict) -> GroupingResult:
    stage1_folder = str(recipe_config.get("stage1_folder", "stage1")).strip() or "stage1"
    stage2_folder = str(recipe_config.get("stage2_folder", "stage2")).strip() or "stage2"
    icd_folder = str(recipe_config.get("icd_folder", "icd")).strip() or "icd"
    strict = bool(recipe_config.get("strict", True))

    role_by_folder = {
        stage1_folder: "stage1",
        stage2_folder: "stage2",
        icd_folder: "icd",
    }

    grouped: dict[str, dict[str, str]] = defaultdict(dict)
    warnings: list[str] = []

    for asset in sorted_assets_by_path(assets):
        path = PurePosixPath(asset_path(asset))
        parent = path.parent.name
        role = role_by_folder.get(parent)
        if not role:
            warnings.append(
                f"Skipped {path.as_posix()}: parent folder must be "
                f"'{stage1_folder}', '{stage2_folder}', or '{icd_folder}'"
            )
            continue
        case_key = path.stem
        if not case_key:
            warnings.append(f"Skipped {path.as_posix()}: empty filename stem")
            continue
        if role in grouped[case_key]:
            warnings.append(
                f"Skipped duplicate {role} file for case '{case_key}': {path.name}"
            )
            continue
        grouped[case_key][role] = asset.id

    cases: list[GroupedCase] = []
    for case_key in sorted(grouped):
        roles = grouped[case_key]
        missing = [role for role in ROLES if role not in roles]
        if missing and strict:
            warnings.append(
                f"Skipped case '{case_key}': missing role file(s) "
                f"{', '.join(missing)} (strict mode)"
            )
            continue
        stimulus_asset_ids: list[str] = []
        stimulus_labels: list[str] = []
        for role in ROLES:
            asset_id = roles.get(role)
            if asset_id:
                stimulus_asset_ids.append(asset_id)
                stimulus_labels.append(role)
        cases.append(
            GroupedCase(
                case_key=case_key,
                stimulus_asset_ids=stimulus_asset_ids,
                stimulus_labels=stimulus_labels,
            )
        )

    return GroupingResult(cases=cases, warnings=warnings)


RECIPE = RegisteredBulkRecipe(
    recipe_type="text_two_stage_review",
    title="Two-Stage Text Review with ICD-11 Pick",
    summary="Per-case Stage 1 + Stage 2 text reports plus ICD-11 candidate codes; baked review form.",
    instructions=[
        "Upload three sibling folders paired by filename stem: stage1/, stage2/, icd/.",
        "stage1/<case>.txt and stage2/<case>.txt are narrative clinical summaries.",
        "icd/<case>.txt holds AI-generated ICD-11 candidate codes, one per line.",
        "One review form per case is auto-generated; no question templates to author.",
    ],
    example_paths=[
        "stage1/d1_1-158.txt",
        "stage2/d1_1-158.txt",
        "icd/d1_1-158.txt",
    ],
    config_keys=["stage1_folder", "stage2_folder", "icd_folder", "strict"],
    supports_patch_question_template=False,
    grouper=group_assets,
    allows_case_question_templates=False,
    catalog_order=50,
)
