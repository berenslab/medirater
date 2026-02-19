from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.models import Asset
from app.schemas import BulkRecipeType


@dataclass
class GroupedCase:
    case_key: str
    stimulus_asset_ids: list[str] = field(default_factory=list)
    stimulus_labels: list[str] = field(default_factory=list)
    patch_asset_ids: list[str] = field(default_factory=list)


@dataclass
class GroupingResult:
    cases: list[GroupedCase]
    warnings: list[str]


@dataclass
class RegisteredBulkRecipe:
    recipe_type: BulkRecipeType
    title: str
    summary: str
    instructions: list[str]
    example_paths: list[str]
    config_keys: list[str]
    supports_patch_question_template: bool
    grouper: Callable[[list[Asset], dict[str, Any]], GroupingResult]


def asset_path(asset: Asset) -> str:
    return (asset.original_path or asset.file_name).strip().replace("\\", "/")


def sorted_assets_by_path(assets: list[Asset]) -> list[Asset]:
    return sorted(assets, key=lambda item: (asset_path(item), item.file_name, item.id))
