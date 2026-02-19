from __future__ import annotations

from app.models import Asset
from app.services.bulk_recipes import (
    GroupedCase,
    GroupingResult,
    get_registered_bulk_recipe,
    list_registered_bulk_recipes,
)


def group_assets_for_recipe(
    *,
    recipe_type: str,
    assets: list[Asset],
    recipe_config: dict,
) -> GroupingResult:
    recipe = get_registered_bulk_recipe(recipe_type)
    if recipe is None:
        return GroupingResult(cases=[], warnings=[f"Unsupported recipe type: {recipe_type}"])
    return recipe.grouper(assets, recipe_config)


def list_bulk_recipes():
    return list_registered_bulk_recipes()


__all__ = ["GroupedCase", "GroupingResult", "group_assets_for_recipe", "list_bulk_recipes"]
