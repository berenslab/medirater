# Bulk Recipe Spec (Draft)

This spec defines the explicit recipe-based bulk authoring flow for questionnaire draft versions.

## Flow

1. Upload assets to `POST /api/admin/assets/upload` with optional relative `paths`.
2. Create/select draft questionnaire version.
3. Call `POST /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/preview`.
4. Review generated cases/questions in frontend.
5. Persist using one of:
   - `POST /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/apply` for server-generated output.
   - `POST /api/admin/questionnaires/{questionnaire_id}/versions/{version_id}/bulk-recipes/apply-preview` for edited preview payload.
6. Edit manually as needed and publish.

## Recipe Types

## `single_per_file`

- Each asset becomes one case with one or more generated questions.

## `triplet_by_suffix`

- Groups files by stem suffix (default `a,b,c`), e.g. `1a.png,1b.png,1c.png`.
- `recipe_config`:
  - `suffixes: string[]` (optional)
  - `strict: bool` (optional, default `true`)

## `paired_by_filename`

- Pairs files by identical filename from two parent folders.
- `recipe_config`:
  - `left_folder: string` (default `no_support`)
  - `right_folder: string` (default `with_support`)
  - `strict: bool` (default `true`)

## `case_with_patches`

- Uses path-based case grouping:
  - main images under `<case>/img/*`
  - patch images under `<case>/patch/*`
- `recipe_config`:
  - `img_folder: string` (default `img`)
  - `patch_folder: string` (default `patch`)
  - `strict: bool` (default `true`)

## Prompt Template Variables

Supported placeholders in `prompt_template`:

- `{case_index}`
- `{case_total}`
- `{case_key}`
- `{stimulus_count}`
- `{patch_total}`
- `{patch_index}` (patch template only)
- `{patch_asset_id}` (patch template only)
