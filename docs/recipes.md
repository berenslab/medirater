# Recipe Extension Guide

Bulk authoring is recipe-driven and dynamically discovered from `app/services/bulk_recipes/*.py`.

## Existing recipes

- `indexed_suffix_sets`: numbered files with optional suffix (`1.png`, `1a.png`, `1b.png`, ...)
- `single_per_file`: one file per case
- `paired_by_filename`: two folders paired by identical filenames
- `case_with_patches`: case folder with main images and patch images

## Add a new recipe (add-file-only contract)

Add exactly these new files:

- `app/services/bulk_recipes/<recipe_type>.py`
- `templates/recipes/<recipe_type>/design.html`
- `templates/recipes/<recipe_type>/answer.html`

No central registry edits are required.

## Backend recipe contract

In `app/services/bulk_recipes/<recipe_type>.py`, export `RECIPE` as `RegisteredBulkRecipe`:

```python
from app.services.bulk_recipes.base import RegisteredBulkRecipe, GroupingResult, GroupedCase


def group_assets(assets, recipe_config) -> GroupingResult:
    # map assets -> grouped cases
    return GroupingResult(cases=[GroupedCase(case_key="case-0001", stimulus_asset_ids=[])], warnings=[])


RECIPE = RegisteredBulkRecipe(
    recipe_type="your_recipe_type",
    title="Your Recipe Title",
    summary="One-line summary",
    instructions=["How to prepare files"],
    example_paths=["example/path/file.png"],
    config_keys=["your_config_key"],
    supports_patch_question_template=False,
    grouper=group_assets,
)
```

Notes:

- `recipe_type` must match `^[a-z0-9][a-z0-9_-]{0,63}$`.
- The design endpoint uses `templates/recipes/<recipe_type>/design.html`.
- The answer page uses `templates/recipes/<recipe_type>/answer.html` when questions carry that `recipe_type`.
- Missing recipe templates fail explicitly.

## Design template responsibilities

`templates/recipes/<recipe_type>/design.html` should render the recipe-specific controls for:

- recipe config fields (`recipe_config`)
- question templates (`question_templates`)
- optional `patch_question_template`

The admin shell loads this fragment from:

- `GET /api/admin/questionnaires/bulk-recipes/{recipe_type}/design`

## Answer template responsibilities

`templates/recipes/<recipe_type>/answer.html` controls rendering for answering cases/prompts of that recipe.

For recipe-specific behavior, implement it only inside that recipe's `answer.html`.

## Testing a new recipe

1. Upload assets for a questionnaire draft.
2. Select your recipe in questionnaire design.
3. Preview generation.
4. Apply generation.
5. Publish.
6. Assign to a user and test `/answer/{questionnaire_version_id}`.
