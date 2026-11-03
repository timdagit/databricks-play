#!/usr/bin/env bash
# One-off, after the first deploy: let the app's own service principal manage (stop) the app,
# which the idle stop needs. The principal only exists once the app has been created, so the
# bundle cannot grant this itself (a resource cannot refer to its own outputs).
#
#   bash scripts/grant_app_self_manage.sh [app-name]
#
# Uses the `personal` CLI profile, or $DATABRICKS_PROFILE. Adds a permission and leaves the
# existing ones as they are, so it is safe to run again.
set -euo pipefail

APP="${1:-bakehouse-param-admin-dev}"
PROFILE="${DATABRICKS_PROFILE:-personal}"

SP="$(databricks apps get "$APP" -p "$PROFILE" -o json | jq -r '.service_principal_client_id // empty')"
if [ -z "$SP" ]; then
  echo "No service principal found for app $APP. Has it been deployed?" >&2
  exit 1
fi

databricks apps update-permissions "$APP" -p "$PROFILE" \
  --json "{\"access_control_list\":[{\"service_principal_name\":\"$SP\",\"permission_level\":\"CAN_MANAGE\"}]}" \
  >/dev/null

echo "Granted CAN_MANAGE on $APP to its own service principal."
