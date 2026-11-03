# Requirements for az-inf

Changes this project needs in the Terraform project at `/Users/timda/HOME/GIT/az-inf`. Tim makes and applies them. Names are config values there, not hardcoded.

## 1. `bakehouse_dev` catalog on the existing Databricks ADLS account

**Why a new container.** The `learning` catalog's storage root is the root of the existing Unity Catalog container, and the access connector's role is scoped to that container only. A second catalog nested under it would probably overlap that managed location. A separate container also lets the catalog be torn down cleanly.

| Layer | Change |
|---|---|
| `50-databricks-storage` | Add a container for the catalog (e.g. `bakehouse-dev`) to the layer config and `storage.tf`. Add a `Storage Blob Data Contributor` role assignment for the existing access connector, scoped to the new container. |
| `52-databricks-cluster` | Add a `databricks_external_location` on the new container, reusing the existing storage credential. Add a `databricks_catalog` `bakehouse_dev` with that location as `storage_root`. Add a `databricks_grants` block for `ALL_PRIVILEGES`, following the `learning` pattern. |

**Before the first apply**
- No `bakehouse_dev` catalog exists in the workspace (checked 2026-11-03), so there is nothing to drop or import. If one is created by hand before the apply, drop it first.
- Layer `50` must be applied before `52`.

**Follow-up found on the first job run (2026-11-03): add `Storage Blob Delegator`**
- The simulator job failed with `Request for user delegation key is not authorized` (SQLSTATE 42501) as soon as it touched the new catalog's storage.
- Likely cause (not yet confirmed): the connector's `Storage Blob Data Contributor` role is scoped to a single container, but generating a user delegation key is an account-level action. The built-in `Storage Blob Delegator` role covers it.
- Change in layer `50-databricks-storage`: add a `Storage Blob Delegator` role assignment for the access connector, **scoped to the storage account** (not a container). One assignment covers every container, so it also covers `learning`.
- Role assignments can take a few minutes to take effect after the apply.

**Not needed**
- The read-only `samples.bakehouse` sample data needs nothing.
- A separate resource-group budget. The existing £20 budget already covers the Databricks resource groups.
- The `learning` catalog stays as it is.

## 2. Not in az-inf (recorded so they aren't forgotten)

- Databricks service principals, serverless usage policy and account budget (spec §6.1, §6.3). They are account-level, which the workspace-only Databricks provider in `52` can't manage. Deferred because the PoC is `dev`-only for now.
- Azure tags and tag policy on the existing resource groups. To decide later.
- GitHub Environments and secrets. Done by hand or with `gh`.
