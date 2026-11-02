# Decisions

Departures from `SPEC.md` and `CLAUDE_CODE_GUIDE.md`, and choices made along the way. Newest last.

## 2026-11-02: alignment with the repo's CLAUDE.md

- **One large PoC, one bundle.** The root `CLAUDE.md` was written for many small PoCs. It now describes one bundle named `bakehouse` with the spec's layout.
- **`CLAUDE.md` wins over the guide.** The docs were written outside this repo, so conflicts are resolved in `CLAUDE.md`.
- **Targets.** `dev` is the default and the focus. `test` and `prod` stay defined but minimal until needed.
- **Tim deploys and pushes.** Claude doesn't run `git push`, `bundle deploy`/`run`/`destroy` or `terraform apply`/`destroy`/`import`. It supplies the commands.
- **Infrastructure lives in az-inf, not `infra/`.** This repo has no Terraform. az-inf already provides the workspace, ADLS storage, access connector, Unity Catalog storage credential and external location, and a `learning` catalog. Needed changes are written up in `docs/az-inf-requirements.md`. SPEC §5.1 and §6.5 and BUILD_PLAN workstream 2 still describe `infra/` and need updating.
- **Catalog.** `bakehouse_dev` gets its own container on the existing az-inf ADLS account. The `learning` catalog is left alone and there is no separate resource-group budget. Details in `docs/az-inf-requirements.md`.
- **Python versions (docs checked Oct 2026).** Serverless environment version 5 uses Python 3.12.3. Databricks Apps that use pip use Python 3.11, and only uv-based apps can choose a version. Decision: because the app uses uv, the app can match the jobs. Use Python 3.12 everywhere (`.python-version`, `requires-python`, Docker `python:3.12-slim` as in SPEC §7). The binding constraint is the serverless runtime, not Apps. uv downloads the interpreter, so no brew install is needed. The app directory must not contain a `requirements.txt`.
- **GitHub Environments and secrets** are set up by hand or with `gh`, because az-inf has no GitHub provider.
- **Python tooling.** uv, as in SPEC §5.2, so the app isn't tied to Python 3.11. This overrides the global venv + `requirements.txt` rule.
- **Designer notebooks.** `.gitignore` ignores `*.ipynb` except `*.designer.ipynb`. To be reviewed once the first Designer file is committed.
- **CI.** Deploy-on-merge workflows use a `paths` filter.
