# Bakehouse Databricks PoC

One large, public PoC on Azure Databricks. It replaces the earlier idea of
many small PoCs. The spec is `docs/SPEC.md` and the build order is
`docs/BUILD_PLAN.md`. Guardrails, known facts and working style come from the
guide imported here:

@docs/CLAUDE_CODE_GUIDE.md

Where this file and the guide or spec differ, this file wins, because the docs
were written outside this repo. Known differences: Terraform lives in az-inf,
not `infra/`; Python uses venv + `requirements.txt`, not uv; I never push or
deploy. Record each departure in `docs/decisions.md`.

## Hard rules
- NEVER read, print or commit ~/.databrickscfg, ~/.databricks/, .env files,
  tfvars or tokens. When discovery produces identifying values (hosts, tenant,
  subscription, workspace or account IDs, emails), write them straight to a
  gitignored file and do not echo them in my output.
- NEVER hardcode workspace hosts, tenant/subscription IDs, warehouse IDs,
  email addresses or storage account names. See "Configuration rules".
- Locally, authenticate with the CLI profile (`-p personal`); no PATs. CI uses
  service-principal OAuth from environment variables. Do not set
  `workspace.host` or tokens in bundle files.
- Synthetic or public data only. `samples.bakehouse` is Databricks' public
  sample. No employer or client data, and no data files in the repo (seed data
  is `.sql`).
- Notebooks are source-format `.py` / `.sql` with no outputs. The only
  `.ipynb` files allowed are `*.designer.ipynb`. Tim edits those on the
  Designer canvas; I never write them. If one contains a workspace URL or ID,
  tell Tim rather than editing it.
- Tim runs, not me: `git push`, `databricks bundle deploy` / `run` / `destroy`,
  `terraform apply` / `destroy` / `import`, and anything that changes GitHub
  settings or secrets. I give the exact command. Never use `--no-verify`.
- Commands that reach Azure, Databricks or GitHub may be blocked by my
  sandbox. Ask Tim to run them (`! <command>`) and paste the output. Do not
  work around a block.

## Where things live
- Two bundles (spec §9.3). `dab-data/` holds the schemas and volumes and is
  deployed to every environment, dev included. The compute bundle (jobs, app,
  Lakebase, Designer tasks) is still at the repo root as `bundle.name:
  bakehouse` and moves to `dab-compute/` when sandbox is built. Dev compute is
  hand-built from the workspace Git folder and runs code from source
  (`jobs/run_from_src.py`); sandbox and above run the wheel.
- **Infrastructure is all in az-inf** (`/Users/timda/HOME/GIT/az-inf`, with
  its own CLAUDE.md and skill). Do not create Terraform in this repo. az-inf
  already provides the workspace, ADLS storage, access connector, Unity
  Catalog storage credential and external location, and a `learning` catalog.
  Reuse them before proposing anything new. Leave the `learning` catalog
  alone: this project gets its own `bakehouse_dev` catalog on the az-inf ADLS
  account (see `docs/az-inf-requirements.md`). When this project needs an
  az-inf change, write it up for Tim (layer, resources, variables, manual
  steps) in `docs/az-inf-requirements.md`. I don't edit az-inf from here
  unless asked.
- GitHub Environments and their secrets are not Terraform-managed. Give Tim
  the `gh` commands or UI steps.
- Reusable logic lives in `src/bakehouse_core`. Jobs, notebooks and the app
  stay thin.

## Python
- Use uv, not pip or `python -m venv`. This overrides the global venv +
  `requirements.txt` rule. uv still keeps the environment in `./.venv`
  (ignored by git) and creates it itself. Dependencies live in
  `pyproject.toml` and the committed `uv.lock`; there is no
  `requirements.txt`. Commands: `uv sync` (create/update `.venv`),
  `uv add <pkg>` / `uv remove <pkg>` (edit `pyproject.toml` and the lock),
  `uv run <cmd>` (run inside the env), `uv lock` (refresh the lock).
- Tim is new to uv. When I first use a uv command or concept, say in one line
  how it differs from venv + pip.
- Python version: pinned in `.python-version` and `requires-python`. uv
  downloads the interpreter itself. Serverless environment version 5 runs
  3.12.3 and the jobs run `bakehouse_core`, so the project uses 3.12. uv-based
  Databricks Apps can choose any version, but the app shares the package, so
  it matches. Re-check against the docs in workstream 0 before pinning.
- Databricks Apps: the app directory uses `pyproject.toml` + `uv.lock` and
  must NOT contain a `requirements.txt` (pip takes precedence if one exists).
  How the app gets `bakehouse_core` (a path outside the app folder isn't
  deployed) is a workstream 8 question: likely the built wheel in a volume.
- Wheel build: bundle `artifacts` runs `uv build --wheel`.

## Declarative Automation Bundles (DABs)

### Configuration rules
- Every environment-specific value is a `variables:` entry with a harmless
  placeholder default (`catalog`, `warehouse_id`, etc.), referenced as
  `${var.catalog}`. Real values come from `--var`, `BUNDLE_VAR_<name>`, the
  gitignored `databricks.local.yml`, or a placeholder-only target override.
- Targets: `dev` is the default (`mode: development`) and the one that
  matters. Keep `test` and `prod` minimal and valid per the spec, but don't
  invest in them until Tim asks.
- In dev mode, check what actually gets prefixed (schemas, apps, workspace
  paths) and set `presets` deliberately so names match the spec. Record the
  result in `docs/decisions.md`.
- Serverless only. Never define always-on clusters or classic warehouses.

### Workflow
1. Edit YAML and source files.
2. `databricks bundle validate -t dev -p personal`. If the sandbox blocks it,
   ask Tim to run it. Fix all errors and warnings.
3. Show Tim the diff and planned changes (`databricks bundle plan` if the CLI
   has it, otherwise a description of what the deploy creates).
4. Tim deploys. Afterwards he can run `databricks bundle summary` to list what
   exists.
5. When work is finished, remind Tim what keeps costing money (apps,
   Lakebase) and offer the `bundle destroy` command.

### Keeping the bundle as the source of truth
- Don't edit bundle-managed resources in the workspace UI. The next deploy
  would overwrite the change or fight it.
- If something was built in the UI first (Designer, a dashboard), pull it into
  code with `databricks bundle generate` where supported, or adopt it with
  `databricks bundle deployment bind`. Anything that can't be expressed in the
  bundle goes in the README as manual steps, with screenshots stripped of
  workspace URLs and names.
- Check `--help`, `databricks bundle schema` and the current docs before using
  a resource type or field I'm not certain exists in the installed CLI. Don't
  guess YAML keys.

### Resource notes
- **Apps:** under `resources.apps`, code in `apps/param_admin/` with an
  `app.yaml`. The app runs as its own service principal, so grant it only what
  it needs through `resources`.
- **Lakebase and other new features:** the YAML schema is changing. Validate
  early and keep it minimal.
- **Designer:** UI-first. Wire the committed `*.designer.ipynb` files in as
  `notebook_task`s and document what was built.

## Docs
- Root `README.md`: goal, what was built, variable names (no values), deploy
  commands, what was learned, how to destroy it.
- Keep `docs/SPEC.md` accurate when the implementation departs from it with
  Tim's agreement, in the same PR.

## Before every commit
- The pre-commit hook runs gitleaks, and CI runs it again on push. Don't
  bypass either.
- Also grep the staged diff for "azuredatabricks", "adb-" and "@". Doc text
  that names these patterns is an expected hit.
- Follow the global commit conventions. Keep messages short and descriptive.
- Workflows that deploy or run on merge use a `paths` filter, so docs-only
  changes don't trigger them.
