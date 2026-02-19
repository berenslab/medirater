from __future__ import annotations

from app.schemas import BulkRecipeType
from app.services.bulk_recipes.base import RegisteredBulkRecipe
from app.services.bulk_recipes.case_with_patches import RECIPE as CASE_WITH_PATCHES_RECIPE
from app.services.bulk_recipes.indexed_suffix_sets import RECIPE as INDEXED_SUFFIX_SETS_RECIPE
from app.services.bulk_recipes.paired_by_filename import RECIPE as PAIRED_BY_FILENAME_RECIPE
from app.services.bulk_recipes.single_per_file import RECIPE as SINGLE_PER_FILE_RECIPE

_RECIPES: list[RegisteredBulkRecipe] = [
    INDEXED_SUFFIX_SETS_RECIPE,
    SINGLE_PER_FILE_RECIPE,
    PAIRED_BY_FILENAME_RECIPE,
    CASE_WITH_PATCHES_RECIPE,
]

_RECIPE_BY_TYPE: dict[BulkRecipeType, RegisteredBulkRecipe] = {
    recipe.recipe_type: recipe for recipe in _RECIPES
}


def list_registered_bulk_recipes() -> list[RegisteredBulkRecipe]:
    return list(_RECIPES)


def get_registered_bulk_recipe(recipe_type: BulkRecipeType) -> RegisteredBulkRecipe | None:
    return _RECIPE_BY_TYPE.get(recipe_type)
