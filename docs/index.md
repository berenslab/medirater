# Medirater Docs

`medirater` is a FastAPI app for medical-data evaluation workflows with passkey auth, role-based access control, questionnaire design, assignment, and response export.

## Documentation

- [Install and run locally](install.md)
- [Deployment guide](deployment.md)
- [Raspberry Pi deployment guide](pi_deployment.md)
- [API overview](api.md)
- [Recipe extension guide](recipes.md)
- [Bulk recipe preview/apply payload notes](bulk_recipe_spec.md)

## Core concepts

- `superadmin` controls global settings and all resources.
- `admin` designs and manages owned questionnaires.
- `user` answers assigned published questionnaire versions.
- Bulk questionnaire authoring is recipe-based and extensible.

## Source layout

- Backend: `app/`
- Templates: `templates/`
- Recipes (backend): `app/services/bulk_recipes/`
- Recipes (frontend templates): `templates/recipes/`
