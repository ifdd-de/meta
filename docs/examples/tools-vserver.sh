#!/usr/bin/env bash
# Example: run the stages prod, int and dev from a tools vServer over ssh.
#
# Copy it into the private directory installations/ of the tools vServer, next
# to the descriptions (installations/<stage>/installation.yaml and the files they
# name), and adapt the settings below. ifdd-meta runs on each server with the
# description of its stage (docs/multi-server.md); this script only calls it:
#
#   tools-vserver.sh [--dry-run] setup  [STAGE...]  first installation: prod, then int, dev
#   tools-vserver.sh [--dry-run] update [STAGE...]  a new release: dev, then int, then prod
#   tools-vserver.sh [--dry-run] status [STAGE...]  the containers of every stage
#
# --dry-run prints the commands instead of running them.
#
# ssh never forwards the agent (no -A): a server never gets a key it could use
# elsewhere. Client secrets go from the Keycloak of prod through a pipe straight
# into a file (mode 0600) on the other server, never into a file here. Logs stay
# on the servers (work/logs/); the console shows the progress lines of ifdd-meta
# with the stage in front.
set -euo pipefail

# --- Settings (invented example values) ----------------------------------------
# ssh destination of each stage: the operating user (umask 0007) on its server.
declare -A SERVER=(
    [prod]=ifdd@prod.cache.example.org
    [int]=ifdd@int.cache.example.org
    [dev]=ifdd@dev.cache.example.org
)
# Realm of each stage on the Keycloak of prod (realm and extra_realms there).
declare -A REALM=([prod]=ifdd-prod [int]=ifdd-int [dev]=ifdd-dev)
# The stage that runs Keycloak and the static services.
KEYCLOAK_STAGE=prod
# Order of a first installation (Keycloak first) and of updates (dev first).
SETUP_ORDER=(prod int dev)
UPDATE_ORDER=(dev int prod)
# How ifdd-meta runs on the servers, and where the descriptions are (relative
# to the home directory of the operating user, as here).
IFDD_META='uv run --project ~/ifdd-meta ifdd-meta'
REMOTE_DIR=installations
# No agent, no other forwarding, no password prompt in the middle of a run.
SSH_OPTIONS=(-o BatchMode=yes -o ForwardAgent=no -o ClearAllForwardings=yes)
# ---------------------------------------------------------------------------------

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DRY_RUN=no

usage() {
    printf 'usage: %s [--dry-run] setup|update|status [STAGE...]\n' "$0" >&2
    exit 2
}

# One command in the directory of the stage on its server; output with prefix.
on_stage() {
    local stage=$1 command="cd $REMOTE_DIR/$1 && $2"
    if [[ $DRY_RUN == yes ]]; then
        printf "+ ssh %s %s '%s'\n" "${SSH_OPTIONS[*]}" "${SERVER[$stage]}" "$command"
        return
    fi
    # shellcheck disable=SC2029  # the command is meant to be built here
    ssh "${SSH_OPTIONS[@]}" "${SERVER[$stage]}" "$command" 2>&1 | sed -u "s/^/[$stage] /"
}

# The description of a stage and the shared CA certificates to its server; never
# .env files, client secrets or what ifdd-meta made there (work/, state/).
sync_stage() {
    local stage=$1
    local rsync=(rsync -a --exclude .env --exclude keycloak-secrets.env
        --exclude work/ --exclude state/ -e "ssh ${SSH_OPTIONS[*]}")
    if [[ $DRY_RUN == yes ]]; then
        printf '+ %s %s %s %s\n' "${rsync[*]}" "certs" "$stage" "${SERVER[$stage]}:$REMOTE_DIR/"
        return
    fi
    (cd "$HERE" && "${rsync[@]}" certs "$stage" "${SERVER[$stage]}:$REMOTE_DIR/")
}

# The client secrets of the realm of a stage, from the Keycloak of prod.
secrets_to() {
    local stage=$1
    local source="cd $REMOTE_DIR/$KEYCLOAK_STAGE && $IFDD_META --settings ifdd-meta.yaml keycloak secrets --realm ${REALM[$stage]}"
    local target="cd $REMOTE_DIR/$stage && umask 077 && cat > keycloak-secrets.env"
    if [[ $DRY_RUN == yes ]]; then
        printf "+ ssh %s %s '%s' | ssh %s %s '%s'\n" \
            "${SSH_OPTIONS[*]}" "${SERVER[$KEYCLOAK_STAGE]}" "$source" \
            "${SSH_OPTIONS[*]}" "${SERVER[$stage]}" "$target"
        return
    fi
    # shellcheck disable=SC2029  # the commands are meant to be built here
    ssh "${SSH_OPTIONS[@]}" "${SERVER[$KEYCLOAK_STAGE]}" "$source" |
        ssh "${SSH_OPTIONS[@]}" "${SERVER[$stage]}" "$target"
}

meta() {
    printf '%s --settings ifdd-meta.yaml %s' "$IFDD_META" "$*"
}

compile_stage() {
    on_stage "$1" "$IFDD_META compile installation.yaml && $(meta config check)"
}

setup() {
    local stage
    for stage in "$@"; do
        sync_stage "$stage"
        compile_stage "$stage"
        if [[ $stage != "$KEYCLOAK_STAGE" ]]; then
            secrets_to "$stage"
        fi
        on_stage "$stage" "$(meta create) && $(meta build) && $(meta up)"
    done
}

update() {
    local stage
    for stage in "$@"; do
        sync_stage "$stage"
        compile_stage "$stage"
        on_stage "$stage" "$(meta update) && $(meta build) && $(meta up)"
    done
}

status() {
    local stage
    for stage in "$@"; do
        on_stage "$stage" "$(meta status)"
    done
}

main() {
    if [[ ${1:-} == --dry-run ]]; then
        DRY_RUN=yes
        shift
    fi
    [[ $# -ge 1 ]] || usage
    local command=$1
    shift
    local stage
    for stage in "$@"; do
        [[ -n ${SERVER[$stage]:-} ]] || { printf 'unknown stage: %s\n' "$stage" >&2; exit 2; }
    done
    local stages=("$@")
    case $command in
        setup)
            [[ $# -gt 0 ]] || stages=("${SETUP_ORDER[@]}")
            setup "${stages[@]}"
            ;;
        update)
            [[ $# -gt 0 ]] || stages=("${UPDATE_ORDER[@]}")
            update "${stages[@]}"
            ;;
        status)
            [[ $# -gt 0 ]] || stages=("${SETUP_ORDER[@]}")
            status "${stages[@]}"
            ;;
        *) usage ;;
    esac
}

main "$@"
