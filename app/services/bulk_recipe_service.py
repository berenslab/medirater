from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.models import Asset
from app.schemas import BulkRecipeType


@dataclass
class GroupedCase:
    case_key: str
    stimulus_asset_ids: list[str] = field(default_factory=list)
    patch_asset_ids: list[str] = field(default_factory=list)


@dataclass
class GroupingResult:
    cases: list[GroupedCase]
    warnings: list[str]


def _asset_path(asset: Asset) -> str:
    return (asset.original_path or asset.file_name).strip().replace("\\", "/")


def _sorted_assets(assets: list[Asset]) -> list[Asset]:
    return sorted(assets, key=lambda item: (_asset_path(item), item.file_name, item.id))


def _group_single_per_file(assets: list[Asset]) -> GroupingResult:
    cases: list[GroupedCase] = []
    for index, asset in enumerate(_sorted_assets(assets), start=1):
        cases.append(
            GroupedCase(
                case_key=f"case-{index:04d}",
                stimulus_asset_ids=[asset.id],
            )
        )
    return GroupingResult(cases=cases, warnings=[])


def _group_triplet_by_suffix(assets: list[Asset], recipe_config: dict) -> GroupingResult:
    suffixes = recipe_config.get("suffixes", ["a", "b", "c"])
    normalized_suffixes = [str(item).lower() for item in suffixes if str(item).strip()]
    if not normalized_suffixes:
        normalized_suffixes = ["a", "b", "c"]

    strict = bool(recipe_config.get("strict", True))

    grouped: dict[str, dict[str, str]] = defaultdict(dict)
    warnings: list[str] = []
    pattern = re.compile(r"^(?P<base>.+?)(?P<suffix>[A-Za-z])$")
    for asset in _sorted_assets(assets):
        stem = PurePosixPath(asset.file_name).stem
        match = pattern.match(stem)
        if not match:
            warnings.append(f"Skipped {asset.file_name}: does not match suffix pattern")
            continue
        suffix = match.group("suffix").lower()
        if suffix not in normalized_suffixes:
            warnings.append(f"Skipped {asset.file_name}: suffix '{suffix}' is not in allowed suffixes")
            continue
        base = match.group("base")
        grouped[base][suffix] = asset.id

    cases: list[GroupedCase] = []
    for base in sorted(grouped):
        by_suffix = grouped[base]
        missing = [suffix for suffix in normalized_suffixes if suffix not in by_suffix]
        if missing and strict:
            warnings.append(
                f"Skipped group '{base}': missing suffixes {', '.join(missing)} (strict mode)"
            )
            continue
        stimulus_asset_ids = [by_suffix[suffix] for suffix in normalized_suffixes if suffix in by_suffix]
        if not stimulus_asset_ids:
            continue
        cases.append(GroupedCase(case_key=base, stimulus_asset_ids=stimulus_asset_ids))

    return GroupingResult(cases=cases, warnings=warnings)


def _group_paired_by_filename(assets: list[Asset], recipe_config: dict) -> GroupingResult:
    left_folder = str(recipe_config.get("left_folder", "no_support"))
    right_folder = str(recipe_config.get("right_folder", "with_support"))
    strict = bool(recipe_config.get("strict", True))

    grouped: dict[str, dict[str, str]] = defaultdict(dict)
    warnings: list[str] = []
    for asset in _sorted_assets(assets):
        path = PurePosixPath(_asset_path(asset))
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


def _group_case_with_patches(assets: list[Asset], recipe_config: dict) -> GroupingResult:
    img_folder = str(recipe_config.get("img_folder", "img"))
    patch_folder = str(recipe_config.get("patch_folder", "patch"))
    strict = bool(recipe_config.get("strict", True))

    grouped: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(
        lambda: {"img": [], "patch": []}
    )
    warnings: list[str] = []

    for asset in _sorted_assets(assets):
        path = PurePosixPath(_asset_path(asset))
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


def group_assets_for_recipe(
    *,
    recipe_type: BulkRecipeType,
    assets: list[Asset],
    recipe_config: dict,
) -> GroupingResult:
    if recipe_type == BulkRecipeType.SINGLE_PER_FILE:
        return _group_single_per_file(assets)
    if recipe_type == BulkRecipeType.TRIPLET_BY_SUFFIX:
        return _group_triplet_by_suffix(assets, recipe_config)
    if recipe_type == BulkRecipeType.PAIRED_BY_FILENAME:
        return _group_paired_by_filename(assets, recipe_config)
    if recipe_type == BulkRecipeType.CASE_WITH_PATCHES:
        return _group_case_with_patches(assets, recipe_config)

    return GroupingResult(cases=[], warnings=[f"Unsupported recipe type: {recipe_type}"])
