#!/bin/bash
# Shell of the integration run: every step runs with umask 0007, like the
# operating user of the stages (files 0660, directories 0770, nothing for
# "other"). Containers that run as another user than the runner (Keycloak,
# nginx, Grafana) can then read only what ifdd-meta makes readable itself.
#
#   defaults.run.shell: bash tools/ci/umask0007.sh {0}
umask 0007
exec bash --noprofile --norc -eo pipefail "$1"
