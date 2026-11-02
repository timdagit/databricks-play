# Decisions

Departures from `SPEC.md` and `CLAUDE_CODE_GUIDE.md`, and choices made along the way. Newest last.

## 2026-11-02: alignment with the repo's CLAUDE.md

- **One large PoC, one bundle.** The root `CLAUDE.md` was written for many small PoCs. It now describes one bundle named `bakehouse` with the spec's layout.
- **`CLAUDE.md` wins over the guide.** The docs were written outside this repo, so conflicts are resolved in `CLAUDE.md`.
- **Targets.** `dev` is the default and the focus. `test` and `prod` stay defined but minimal until needed.
- **Tim deploys and pushes.** Claude doesn't run `git push`, `bundle deploy`/`run`/`destroy` or `terraform apply`/`destroy`/`import`. It supplies the commands.
- **Infrastructure lives in az-inf, not `infra/`.** This repo has no Terraform. az-inf already provides the workspace, ADLS storage, access connector, Unity Catalog storage credential and external location, and a `learning` catalog. Needed changes are written up in `docs/az-inf-requirements.md`. SPEC §5.1 and §6.5 and BUILD_PLAN workstream 2 still describe `infra/` and need updating.
- **Catalog.** `bakehouse_dev` gets its own container on the existing az-inf ADLS account. The `learning` catalog is left alone and there is no separate resource-group budget. Details in `docs/az-inf-requirements.md`.
- **Python versions (docs checked Oct 2026).** Serverless environment version 5 uses Python 3.12.3. Databricks Apps that use pip use Python 3.11, and only uv-based apps can choose a version. Decision: `.venv` on 3.12, `requires-python >=3.11`, CI on 3.11 and 3.12, app Docker image `python:3.11-slim`. SPEC §7 says `python:3.12-slim` and needs updating. Local Python 3.12 is not installed yet.
- **GitHub Environments and secrets** are set up by hand or with `gh`, because az-inf has no GitHub provider.
- **Python tooling.** venv + `requirements.txt` instead of uv. `pyproject.toml` stays for building the wheel. The Python version is pinned in workstream 0.
- **Designer notebooks.** `.gitignore` ignores `*.ipynb` except `*.designer.ipynb`. To be reviewed once the first Designer file is committed.
- **CI.** Deploy-on-merge workflows use a `paths` filter.
