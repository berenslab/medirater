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
    img_folder = str(recipe_config.get("img_folder", "img"))
    patch_folder = str(recipe_config.get("patch_folder", "patch"))
    strict = bool(recipe_config.get("strict", True))

    grouped: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(lambda: {"img": [], "patch": []})
    warnings: list[str] = []

    for asset in sorted_assets_by_path(assets):
        path = PurePosixPath(asset_path(asset))
        parts = path.parts

        if img_folder in parts:
            idx = parts.index(img_folder)
            if idx == 0:
                warnings.append(f"Skipped {path.as_posix()}: missing case prefix before '{img_folder}'")
                continue
            case_key = "/".join(parts[:idx])
            grouped[case_key]["img"].append((path.name, asset.id))
        elif patch_folder in parts:
            idx = parts.index(patch_folder)
            if idx == 0:
                warnings.append(f"Skipped {path.as_posix()}: missing case prefix before '{patch_folder}'")
                continue
            case_key = "/".join(parts[:idx])
            grouped[case_key]["patch"].append((path.name, asset.id))
        else:
            warnings.append(
                f"Skipped {path.as_posix()}: path must include '{img_folder}' or '{patch_folder}' folder"
            )

    cases: list[GroupedCase] = []
    for case_key in sorted(grouped):
        entry = grouped[case_key]
        images = [asset_id for _, asset_id in sorted(entry["img"], key=lambda item: item[0])]
        patches = [asset_id for _, asset_id in sorted(entry["patch"], key=lambda item: item[0])]
        if not images:
            warnings.append(f"Skipped case '{case_key}': no image files under '{img_folder}'")
            continue
        if strict and not patches:
            warnings.append(f"Skipped case '{case_key}': no patch files under '{patch_folder}' (strict mode)")
            continue
        cases.append(GroupedCase(case_key=case_key, stimulus_asset_ids=images, patch_asset_ids=patches))

    return GroupingResult(cases=cases, warnings=warnings)


RECIPE = RegisteredBulkRecipe(
    recipe_type=BulkRecipeType.CASE_WITH_PATCHES,
    title="Case Folder With Patches",
    summary="Use case folders containing main images and patch images for per-patch prompts.",
    instructions=[
        "Each case folder should include one subfolder for main images and one for patch images.",
        "Set img_folder and patch_folder names if different from defaults.",
        "Enable patch question template to generate one prompt per patch image.",
    ],
    example_paths=[
        "ERM_Macula/0004210405-...-Volume-13/img/main.png",
        "ERM_Macula/0004210405-...-Volume-13/patch/p_1.png",
    ],
    config_keys=["img_folder", "patch_folder", "strict"],
    supports_patch_question_template=True,
    grouper=group_assets,
)
