from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.models import Asset

RECIPE_TYPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


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
    recipe_type: str
    title: str
    summary: str
    instructions: list[str]
    example_paths: list[str]
    config_keys: list[str]
    supports_patch_question_template: bool
    grouper: Callable[[list[Asset], dict[str, Any]], GroupingResult]
    forced_case_question_type: str | None = None
    allows_case_question_templates: bool = True
    requires_patch_question_template: bool = False
    catalog_order: int = 1000


def normalize_recipe_type(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip().lower()
    if not value:
        return None
    if not RECIPE_TYPE_PATTERN.fullmatch(value):
        return None
    return value


def asset_path(asset: Asset) -> str:
    return (asset.original_path or asset.file_name).strip().replace("\\", "/")


def sorted_assets_by_path(assets: list[Asset]) -> list[Asset]:
    return sorted(assets, key=lambda item: (asset_path(item), item.file_name, item.id))
