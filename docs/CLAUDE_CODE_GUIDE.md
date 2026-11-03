# Claude Code guide — Bakehouse Databricks PoC

This guide gives you context and guardrails, not a procedure. It adds to the project's own `CLAUDE.md`; where they conflict, the guardrails below win for this project. The *what* is in [`SPEC.md`](SPEC.md), and [`BUILD_PLAN.md`](BUILD_PLAN.md) describes the outcomes, the key decisions and the known traps. For the *how*, use your own knowledge, skills and established setups for Databricks, Terraform, Azure, GitHub Actions, Python packaging, Dash and Docker. Where your experience points to a better approach than the spec suggests, propose it. Make the change once Tim agrees, and record it in `docs/decisions.md`.

## What this project is

- **Scenario:** a personal, low-cost learning PoC on Azure Databricks. Bakehouse franchise analytics on `samples.bakehouse`, with a simulator that releases one virtual day of data at a time.
- **Pipeline:** a Python ETL from bronze to silver to gold.
- **Analyst work:** two Lakeflow Designer visual data preps and four notebooks.
- **App:** a Dash app that maintains 10 business parameters. It has two switchable backends, Lakebase and Delta, and run buttons that trigger the ETL.
- **Packaging and delivery:** one reusable package, `bakehouse_core`, with one bundle (`dev`, `test` and `prod` targets in one workspace) and Terraform for the foundation.
- **Audience:** the repo is **public** and shared with colleagues, so quality and clarity matter as much as working code.
- **Who pays:** it runs on **Tim's own money**, so cost discipline is a design requirement, not an afterthought.

## Guardrails (non-negotiable)

These are the only hard rules. Everything else is your judgement.

1. **Nothing identifying in tracked files.**
   - Workspace hosts and URLs, tenant, subscription, account and workspace IDs, emails, keys, tokens and secrets live only in gitignored files (`.env.local`, `infra/*.tfvars`, `infra/backend.hcl`) or in GitHub Environment secrets.
   - Commit `*.example` templates instead.
   - Keep gitleaks running on commits and in CI.
2. **Cost.**
   - Use serverless compute only. Don't create all-purpose clusters or classic warehouses, and don't run project jobs on the existing cluster.
   - Everything that bills while it runs honours the 1-minute idle default (`idle_stop_minutes`, spec §6.4).
   - Leave dev apps stopped when you're done, and keep dev backfills small.
   - If a change would add a standing cost, say so before making it.
3. **Ask Tim first** before:
   - anything that deploys to or runs in `test` or `prod`
   - `terraform apply` or `destroy`, or any `import`
   - deleting data or resources outside `dev`
   - force-pushing, or changing repo settings or secrets
4. **Reuse existing names.** The subscription, resource group, workspace, existing cluster and GitHub repo already exist. Discover their names and reuse them; never create replacements.
5. **Lakeflow Designer files** (`*.designer.ipynb`) are edited only on the Designer canvas by Tim. You reference them; you don't write them.

## Known facts

| Item | Value |
|---|---|
| Machine | Mac mini, Apple silicon (M1), Docker Desktop |
| Databricks CLI | 1.23 |
| Existing | Azure resource group, Databricks workspace (Premium, Unity Catalog), one all-purpose cluster, GitHub repo, gitleaks on the Mac |
| Sample data range | `samples.bakehouse.sales_transactions` covers 1–17 May 2024 (17 days) |
| CI auth | Service principal OAuth secret held in GitHub Environments (`test`, `prod`) |
| Run identity | `dev` runs as Tim. `test` and `prod` deploy as, and run as, their service principal. |

## Working style

- Work in sensible, reviewable increments on branches with PRs. The phases in `BUILD_PLAN.md` are a suggested order, not a script. Merge, split or reorder them where it makes sense, and say why.
- Prefer the idiomatic, current way of doing things over anything in the spec that looks dated. Confirm anything marked Beta or uncertain (spec §8.3) against current docs or `databricks bundle schema`. Record what you learn in `docs/decisions.md`.
- Keep `bakehouse_core` the single home for logic used by the jobs, app and tests. Entry points and notebooks stay thin.
- Tests should run locally without cloud access by default. Mark anything that needs Spark or Databricks so it can be skipped.
- Keep `SPEC.md` accurate. If the implementation departs from it with Tim's agreement, update it in the same PR.
- At the end of each piece of work, summarise: what changed, what it costs to run, anything still running, and what Tim needs to do.
