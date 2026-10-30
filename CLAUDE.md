# Databricks PoC repo

Public GitHub repo of small, self-contained Databricks learning PoCs
(Lakeflow Designer, Databricks Apps, Lakebase, others). Azure workspace.

## Hard rules
- NEVER read, print or commit ~/.databrickscfg, ~/.databricks/, .env files or tokens.
- NEVER hardcode workspace hosts, tenant/subscription IDs, warehouse IDs,
  email addresses or storage account names. See "Configuration rules".
- Authenticate with the CLI profile (`-p personal`); no PATs. Do not set
  `workspace.host` or tokens in bundles.
- Synthetic or public datasets only. No employer or client data, and no data
  files in the repo.
- No notebook outputs in commits. Use source-format notebooks (.py / .sql).
- Ask before running `git push`, `databricks bundle deploy` or
  `databricks bundle destroy`. Never use `--no-verify`.

## Structure
- One folder per PoC, each with a README.md (see "Per-PoC README checklist").
- Shared helpers go in common/, only if used by 2+ PoCs.

## Before every commit
- Run `gitleaks git --staged --redact` and grep the diff for
  "azuredatabricks", "@", and "adb-".
- Keep commit messages short and descriptive.


## Working with Declarative Automation Bundles (DABs)

### Layout
- Each PoC is its own bundle (own `databricks.yml` in its folder), with
  resources split into `**/resources/*.yml` and pulled in via `include:`.
- `bundle.name` must be unique per PoC (e.g. `poc01_hello`) so deployed
  state and workspace folders never collide.
- Source code lives beside the bundle (`src/`, `app/`, `notebooks/`);
  YAML only describes resources and wiring.
- Do not create a shared root bundle unless I ask.

### Configuration rules
- Define every environment-specific value as a `variables:` entry with a
  harmless placeholder default (`catalog`, `schema`, `warehouse_id`, etc.).
  Reference them as `${var.catalog}`. Real values come from `--var`,
  `BUNDLE_VAR_<name>` environment variables, the gitignored
  `databricks.local.yml`, or a target-level override that is itself
  placeholder-only.
- Targets: a single `dev` target with `mode: development` and `default: true`.
  Dev mode prefixes resource names with the user and pauses schedules, which is
  what I want for experiments. Do not add a `prod` target.
- Prefer serverless compute for jobs and pipelines. Do not define always-on
  clusters.
- Each PoC uses its own schema (e.g. `${var.catalog}.poc01_hello`) so it
  can be dropped cleanly.

### Workflow (in this order)
1. Edit YAML and source files.
2. `databricks bundle validate -t dev -p personal`. Fix all errors and warnings.
3. Show me the diff and the planned changes. If the installed CLI has
   `databricks bundle plan`, use it. Otherwise explain what the deploy will create.
4. **Ask before** `databricks bundle deploy`. Never deploy, run or destroy unprompted.
5. After deploy, `databricks bundle summary` to list what exists.
6. `databricks bundle run <resource_key>` only when I ask.
7. When a PoC is finished, offer `databricks bundle destroy` and ask first.
   Apps and Lakebase instances can keep costing money while idle.

### Keeping the bundle as the source of truth
- Don't edit bundle-managed resources in the workspace UI, because the next
  deploy will overwrite the change or fight it.
- If something was built in the UI first (Lakeflow Designer work, a dashboard,
  an existing job), pull it into code with `databricks bundle generate`
  (for supported resource types) and commit the result. Otherwise use
  `databricks bundle deployment bind` to adopt it. Check the current docs
  for which types are supported.
- Anything that cannot be expressed in a bundle goes in the PoC's README as
  manual steps, with screenshots stored without workspace URLs or names.
- Check the CLI's `--help` and the current docs before using a resource type
  or command you are not certain exists in the installed version
  (`databricks -v`). Don't guess YAML keys.

### Resource-specific notes
- **Apps:** define under `resources.apps`, with the code in `app/` and an
  `app.yaml` for the start command. Apps run as their own service principal,
  so grant it access to any tables or warehouses it needs through
  `resources`. Do not give it more than it needs.
- **Jobs / pipelines:** use `source`-format notebooks or plain Python/SQL
  files.
- **Lakebase and other new features:** treat the YAML schema as changing.
  Validate early and keep the PoC minimal.
- **Dashboards / Lakeflow Designer:** may be UI-first. Document what was built
  in the README and generate or bind where possible.

### Per-PoC README checklist
Goal, what was built, variables needed (names only, no values), deploy
commands, what I learned, how to destroy it.
