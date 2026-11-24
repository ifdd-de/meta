# Several servers: stages prod, int and dev

One installation per server, each with a description of its own; one of them
runs Keycloak and the static services (schema, type, unit, model) for all. The
example has three stages on three servers and a fourth machine, the tools
vServer, that runs them over ssh. All names are invented (`example.org`); the
complete descriptions are in [`examples/stages/`](examples/stages/), the
script in [`examples/tools-vserver.sh`](examples/tools-vserver.sh).

| Service | prod | int | dev |
|---|---|---|---|
| proxy, be, ui, ts, ts-api, ct, wf, lab | local | local | local |
| keycloak (the realms of all stages), schema, type, unit, model | local | on prod (`external: true`) | on prod (`external: true`) |

## Layout on the tools vServer

```text
installations/            private: never in a public repository
  tools-vserver.sh        copy of examples/tools-vserver.sh, adapted
  certs/                  company-root-ca.crt, company-sub-ca.crt, web-proxy-ca.crt
  prod/
    installation.yaml
    realms/values_ifdd-demo.toml, values_ifdd-dev.toml, values_ifdd-int.toml
    theme/theme.properties, ifdd.css, logo.svg
  int/installation.yaml
  dev/installation.yaml
```

The script copies `certs/` and the directory of a stage to its server
(`rsync`, never `.env`, `keycloak-secrets.env`, `work/` or `state/`); there
`ifdd-meta compile` writes the settings, the variables and `hosts.txt` next to
the description, and the instances go to `work/`. Each server keeps its own
`.env` next to `ifdd-meta.yaml` by hand, for example with the token of the Git
server of wf (`IFDD_META_WF_GIT_TOKEN`).

## The descriptions

- **Hosts:** every service with a host names it in its block; there is no
  `hosts.suffix`. The external services of int and dev carry the same hosts as
  on prod. The proxy block names the host of the Traefik dashboard; it gets a
  line in `hosts.txt` (a DNS record) but no network alias.
- **Realms:** prod has the realm `ifdd-prod` and the further realms
  `ifdd-demo`, `ifdd-dev` and `ifdd-int` (`extra_realms`), each with its values
  file in `files:` (a copy of the instance's `keycloak/realm/values.toml` with
  its own `[realm] name`, client ids and URLs). int and dev use their realm on
  prod (`realm: ifdd-int`, `realm: ifdd-dev`).
- **Certificates:** `tls: own`. The certificate of each proxy (into
  `proxy/certs/` by hand) must cover every host of its `hosts.txt`, the proxy
  host included. `ca_certs` lists the company CA, its sub CA and the CA of the
  TLS-inspecting web proxy: every service that trusts CAs gets them; ct and
  oauth2-proxy (the login gate of wf, which calls the realm on prod) get them
  as one bundle.
- **`protected: true`:** a stage is only updated. `down --purge` and `rebuild`
  refuse; to tear a stage down after all, remove the key and compile again.
- **Login theme:** `files:` copies the company theme over the files of the
  template theme (`keycloak/themes/ifdd/login/`); those files go onto the skip
  list and no longer receive template changes.

## Operating user and file modes

The operating user of the stages has umask `0007` and a uid and gid other than
1000. Containers that run as other users read their files through bind mounts:
Keycloak the realm import files and the theme, nginx (schema, type, unit, model)
its configuration and content, Grafana its provisioning and certificates.

- Every `ifdd-meta` command runs with umask `0022`, whatever the umask of the
  shell: rendered files are `0644`, directories `0755`. Secrets get `0600`
  explicitly (the hooks of the templates and ifdd-meta).
- `files:` copies get `0644`, or `0600` when the target looks like a secret
  (below `secrets/`, `.env`, `*.key`, `*.pem`); copies of `ca_certs` and the CA
  bundles get `0644`.
- The content clones of schema, type, unit and model (`IFDD_*_CONTENT_DIR`) are
  yours: clone them with umask `0022` or make them readable afterwards
  (`chmod -R o+rX <clone>`), otherwise nginx answers 403.

## First installation

`tools-vserver.sh setup` does, in this order:

1. **prod:** copy the description, `compile`, `config check`, `create`,
   `build`, `up`. `create` copies the values files and runs
   `realm/build_realm.py`; Keycloak imports all four realms at its first start.
2. **int, then dev:** copy the description, `compile`, `config check`; the
   client secrets of the realm of the stage go from prod straight into
   `keycloak-secrets.env` (mode 0600) of the stage:

   ```bash
   ssh -o BatchMode=yes -o ForwardAgent=no ifdd@prod.cache.example.org \
       'cd installations/prod && ifdd-meta --settings ifdd-meta.yaml keycloak secrets --realm ifdd-dev' \
     | ssh -o BatchMode=yes -o ForwardAgent=no ifdd@dev.cache.example.org \
       'cd installations/dev && umask 077 && cat > keycloak-secrets.env'
   ```

   then `create`, `build`, `up`.

ssh never forwards the agent (`-A`): a server never gets a key it could use
elsewhere. The secrets pass the tools vServer only in the pipe. The logs stay on
the servers (`work/logs/`); the console shows the progress lines of ifdd-meta
with the stage in front. `--dry-run` prints the commands instead of running them.

## Updates

`tools-vserver.sh update` updates dev first, then int, then prod: copy the
description, `compile`, `config check`, `update`, `build`, `up`. Each stage
names its template references in its description, so a release can go to dev
before the others. `update` merges the template changes into the instances
(`cruft update`) and lists new settings of the `*.dist` files; add those by hand.
It does not copy the `files:` again (they may be changed on the server).

**Never `rebuild` or `down --purge` on a stage:** both delete the instances and
the database of Keycloak, with every user and realm change made since. With
`protected: true` ifdd-meta refuses them.

## Users

On prod, for the realm of any stage:

```bash
ifdd-meta --settings ifdd-meta.yaml keycloak add-user jdoe --password-file jdoe.password \
    --realm ifdd-dev --group ifdd_nodes_readers --grafana-role editor
```

`--grafana-role admin|editor|viewer` grants the realm role that ts maps to the
Grafana role (`ifdd_ts_role_admin`, `…_editor`, `…_viewer` of the variables).

## A further realm later

Add it to `extra_realms` and `files` of prod, `update` keycloak (it lists the
new secrets of `dotenv.dist`: add them to `.env` with random values), copy the
values file into `keycloak/realm/`, run
`python3 realm/build_realm.py --values realm/values_<x>.toml`, commit, restart
Keycloak; then the description and `setup` of the new stage.

## Backups of Keycloak

The backup service of keycloak writes database dumps to `keycloak/backups/`;
`./adm_backup_restore.sh` restores one. `down --purge` (where allowed) keeps
the directory in the persist directory (`state/keycloak/backups/`, mode 0700),
adds to what is there and never deletes; the next `create` puts the dumps back.

## What is tested

The descriptions of `examples/stages/` compile in the unit tests, with
stand-ins for the certificates, values files and theme. The script is checked
by shellcheck and run in the unit tests with stand-ins for `ssh` and `rsync`
(order of the stages, secrets only in the pipe, no agent forwarding, no secret
on the console). It has not run against real servers in the CI.
