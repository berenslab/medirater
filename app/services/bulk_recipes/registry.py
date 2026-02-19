from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from pkgutil import iter_modules

from app.services.bulk_recipes.base import RegisteredBulkRecipe, normalize_recipe_type


@lru_cache(maxsize=1)
def _recipes() -> tuple[RegisteredBulkRecipe, ...]:
    package_name = __name__.rsplit(".", 1)[0]
    package = import_module(package_name)
    package_path = getattr(package, "__path__", None)
    if not package_path:
        return ()

    recipes: list[RegisteredBulkRecipe] = []
    seen_types: dict[str, str] = {}

    for module_info in sorted(iter_modules(package_path), key=lambda item: item.name):
        module_name = module_info.name
        if module_name in {"base", "registry"} or module_name.startswith("_"):
            continue

        module = import_module(f"{package_name}.{module_name}")
        recipe = getattr(module, "RECIPE", None)
        if recipe is None:
            continue
        if not isinstance(recipe, RegisteredBulkRecipe):
            raise RuntimeError(
                f"{package_name}.{module_name}.RECIPE must be a RegisteredBulkRecipe instance"
            )

        normalized_type = normalize_recipe_type(recipe.recipe_type)
        if not normalized_type:
            raise RuntimeError(
                f"{package_name}.{module_name}.RECIPE has invalid recipe_type '{recipe.recipe_type}'"
            )

        if normalized_type in seen_types:
            raise RuntimeError(
                f"Duplicate recipe_type '{normalized_type}' in {seen_types[normalized_type]} and "
                f"{package_name}.{module_name}"
            )

        recipe.recipe_type = normalized_type
        seen_types[normalized_type] = f"{package_name}.{module_name}"
        recipes.append(recipe)

    recipes.sort(key=lambda item: (item.catalog_order, item.recipe_type))
    return tuple(recipes)


@lru_cache(maxsize=1)
def _recipe_by_type() -> dict[str, RegisteredBulkRecipe]:
    return {recipe.recipe_type: recipe for recipe in _recipes()}


def list_registered_bulk_recipes() -> list[RegisteredBulkRecipe]:
    return list(_recipes())


def get_registered_bulk_recipe(recipe_type: str) -> RegisteredBulkRecipe | None:
    normalized = normalize_recipe_type(recipe_type)
    if not normalized:
        return None
    return _recipe_by_type().get(normalized)
