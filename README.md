[![build status](https://github.com/vielhuber/lamp/actions/workflows/ci.yml/badge.svg)](https://github.com/vielhuber/lamp/actions)
[![docker build](https://img.shields.io/badge/Docker-Package-blue)](https://github.com/vielhuber/lamp/pkgs/container/lamp)
[![last commit](https://img.shields.io/github/last-commit/vielhuber/lamp)](https://github.com/vielhuber/lamp/commits)

# 🛠️ lamp 🛠️

a portable development machine in docker: apache, php, mysql, postgresql, redis, node, python, ruby, rust and the usual tooling. projects get isolated checkouts and databases, are served through an existing cloudflare tunnel behind cloudflare access, and are managed by one cli that also works over ssh or from an agent harness.

<details>

<summary><strong>requirements</strong></summary>

- linux or wsl2 on amd64
- [docker](https://docs.docker.com/engine/install/ubuntu/) `>= 20.10.0` with the compose plugin
- `bash`, `git`, `python3`, `flock`, `sha256sum`, `readlink` on the host
- `/dev/net/tun` on the host (vpn support)
- a cloudflare account with the dns zone of `<domain>` and [cloudflared](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/) on the host; tunnel and access are set up during installation

</details>

<details>

<summary><strong>installation</strong></summary>

<blockquote>

<details>

<summary>1. pull image</summary>

- `docker pull ghcr.io/vielhuber/lamp:latest`

</details>

<details>

<summary>2. configure instance</summary>

- `mkdir lamp && cd lamp`
- `docker run --rm -v "$PWD:/install" ghcr.io/vielhuber/lamp:latest init` (copies `lamp` and `docker/docker-compose.yml` out of the image)
- `./lamp setup` (creates `.data` with a commented `config.yaml`, an empty `environments.yaml`, example files for syncdb, build scripts and the access service token, and `docker/docker-compose.override.yml` with the projects mount; never overwrites)
- optional: change the host side of the projects mount in `docker/docker-compose.override.yml`; the container side stays `/var/www`
- set `domain` in `.data/config.yaml`
- put ssh keys into `.data/ssh/`, syncdb profiles into `.data/syncdb/`, build scripts into `.data/build/`
- optional: `sudo ln -s "$(pwd -P)/lamp" /usr/local/bin/lamp`

</details>

<details>

<summary>3. cloudflare tunnel</summary>

- reuse an existing locally managed tunnel or create one with `cloudflared tunnel create lamp`; only its credentials json is needed, no `cert.pem` and no api token for the tunnel itself
- once: proxied wildcard cname `*.<domain>` → `<TUNNEL_UUID>.cfargotunnel.com`, or `cloudflared tunnel route dns <TUNNEL> '*.<domain>'`
- `install -m 600 ~/.cloudflared/<TUNNEL_UUID>.json .data/cloudflare/cloudflared-credentials.json`
- `sudo systemctl disable --now cloudflared` on the host; never run the same tunnel from two machines

</details>

<details>

<summary>4. cloudflare access</summary>

- zero trust › `Access controls › Applications › Add an application › Self-hosted`: name `lamp`, public hostname `*.<domain>`, all paths
- policy 1, developer login: `Allow`, `Include › Emails` with your cloudflare login email, `Require › Login Methods › Cloudflare`; the cloudflare identity provider under `Integrations › Identity providers` must have `Restrict to account members` enabled
- policy 2, harness: `Access controls › Service credentials › Service Tokens › Create` a token, then `Service Auth`, `Include › Service Token` selecting it; never `Bypass` or `Allow › Everyone` on this application
- `cp .data/cloudflare/cloudflare-service-token.yaml.example .data/cloudflare/cloudflare-service-token.yaml` and fill in `CF-Access-Client-Id` and `CF-Access-Client-Secret` of that token (mode 600)
- management token: [my profile › api tokens](https://dash.cloudflare.com/profile/api-tokens) › `Create Custom Token` › `Account › Access: Apps and Policies › Edit`, scoped to the one account, with an expiry
- `touch .data/cloudflare/cloudflare-api-token && chmod 600 .data/cloudflare/cloudflare-api-token && nano .data/cloudflare/cloudflare-api-token`; paste only the token on one line, no `Bearer`, no quotes, never into a shell command or chat
- without these two files and the wildcard application `add`, `start` and `restart` refuse to serve environments

</details>

</blockquote>

</details>

<details>

<summary><strong>start</strong></summary>

- `./lamp start`
- `./lamp add --git git@github.com:<owner>/<project>.git --subdomain project`
- open `https://project.<domain>`

</details>

<details>

<summary><strong>update</strong></summary>

- `./lamp stop`
- `docker pull ghcr.io/vielhuber/lamp:latest`
- `docker run --rm -v "$PWD:/install" ghcr.io/vielhuber/lamp:latest init` (refreshes `lamp` and `docker/docker-compose.yml`, never `.data`)
- `./lamp start`

</details>

<details>

<summary><strong>commands</strong></summary>

| command                                                                 | effect                                                                              |
| ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `./lamp setup`                                                          | create `.data` with commented presets, example files and the compose override; keeps existing files |
| `./lamp start`                                                          | start container, wait for health, apply `.data/environments.yaml`                   |
| `./lamp restart`                                                        | validate yaml, stop, start, apply                                                   |
| `./lamp stop`                                                           | stop container, keep all data                                                       |
| `./lamp status [--json]`                                                | container state, health, ports, supervised services                                 |
| `./lamp version [--json]`                                               | host checkout version and container image id                                        |
| `./lamp docker-build`                                                   | rebuild the image without layer cache, keep volumes (requires stopped container)    |
| `./lamp build <id>`                                                     | rerun the configured build of one environment inside the running container          |
| `./lamp docker-reset`                                                   | **delete all compose volumes**, then rebuild the image (requires stopped container) |
| `./lamp add [--git <url>] [--id <12-hex>] [--branch <b>] [--base-branch <b>] [--php <v>] [--vpn <name>] [--build "<cmd>"] [--subdomain <label>] [--directory <name>] [--alias <suffix>]… [--webroot <dir>] [--proxy-port <port>] [--proxy-exclude </path>] [--visibility private\|public]` | create environment, returns json |
| `./lamp remove <id>`                                                    | remove environment, owned databases, runtime data; dynamic project directory only   |
| `./lamp list [--search <term>]`                                         | all environments as json; `--search` filters case-insensitively over all values     |
| `./lamp show <id>`                                                      | one environment as json                                                             |
| `./lamp branch <id> <branch> [--base <b>] [--operation switch\|rename]` | switch or create branch without rebuild                                             |
| `./lamp exec "<command>"`                                               | run a command in the container                                                      |
| `./lamp exec --environment <id> -- <cmd>`                               | run in the project directory with its php, setup variables and authenticated `curl` |
| `./lamp curl <id> -- <curl args>`                                       | curl the environment's exact https origin with the access service token             |
| `./lamp access <id>`                                                    | origin and access headers as json; secret, for trusted integrations only            |
| `./lamp ssh`                                                            | interactive root shell in the container                                             |

</details>

<details>

<summary><strong>notes</strong></summary>

<blockquote>

<details>

<summary>development installation</summary>

<blockquote>

<details>

<summary>1. pull repository</summary>

- `git clone git@github.com:vielhuber/lamp.git && cd lamp`

</details>

<details>

<summary>2. configure instance</summary>

- same as [installation](#installation) steps 2 to 4, minus the `init` step

</details>

<details>

<summary>3. build image</summary>

- `./lamp docker-build`
- `./lamp start`

</details>

<details>

<summary>update</summary>

- `./lamp stop`
- `git pull`
- `./lamp docker-build`
- `./lamp start`

</details>

<details>

<summary>docker-reset</summary>

- `./lamp stop`
- `./lamp docker-reset` (deletes databases, environment metadata and every other compose volume; `.data` and `/var/www` survive)
- `./lamp start` (re-registers all environments from `.data/environments.yaml`, including database imports and builds)

</details>

<details>

<summary>github action</summary>

- `.github/workflows/ci.yml` builds `docker/Dockerfile` and pushes `ghcr.io/vielhuber/lamp:latest`
- triggers: manual `workflow_dispatch` or a pushed tag matching `v*` / `[0-9]*`
- release: `git tag 1.2.7 && git push origin 1.2.7`
- the image build is large; the job timeout is six hours

</details>

</blockquote>

</details>

<details>

<summary>security model</summary>

- trusted development machine, not a sandbox: builds and php-fpm run as root, all environments share the container, php workers and redis
- database accounts are scoped per environment, but any build or php application can reach the whole container, other environments and mounted credentials
- every environment is routed through the tunnel; cloudflare access decides who gets in
- the host `/var/www` is bind-mounted read/write: changes inside the container are changes on the host

</details>

<details>

<summary>data layout</summary>

| path                      | contents                                                                          | survives `docker-reset` |
| ------------------------- | --------------------------------------------------------------------------------- | ---------------- |
| `.data/config.yaml`       | `domain`, optional `git`, `apache`, `postfix`, `vpn` (mode 600)                   | yes              |
| `docker/docker-compose.override.yml` | host-specific: projects mount, extra mounts and ports (gitignored)    | yes              |
| `.data/environments.yaml` | desired environments (mode 600)                                                   | yes              |
| `.data/build/*.sh`        | shared repository build scripts (mode 600)                                        | yes              |
| `.data/syncdb/*.json`     | original syncdb profiles (mode 600)                                               | yes              |
| `.data/ssh/`              | container `/root/.ssh` (keys, config, known_hosts)                                | yes              |
| `.data/cloudflare/`       | tunnel credentials, access service token, management api token                    | yes              |
| `.data/cliproxyapi/`      | cliproxyapi config, oauth auth files, logs                                        | yes              |
| `.data/ca/`               | local ca for direct-origin https                                                  | yes              |
| `.data/vpn/`              | openvpn / wireguard profiles                                                      | yes              |
| `.logs/`                  | host-side command logs                                                            | yes              |
| `/var/www`                | project checkouts; host directory set in `docker/docker-compose.override.yml`     | yes              |
| compose volumes           | mysql, postgresql, redis, apache sites, mail, certificates, `/var/lib/lamp` state | **no**           |

- `.data` is mounted at `/etc/lamp`, `.data/ssh` at `/root/.ssh`, `.data/cliproxyapi` at `/var/lib/lamp/cliproxyapi`
- `.data` and `.logs` are excluded from git and from the image
- back up `.data` and database-consistent dumps separately; `docker-reset` is not an update that preserves environments
- initial mysql root / postgres password: `/var/lib/lamp/secrets/database-password` in the state volume, generated on first start; set your own before the first database initialization with `docker compose -f docker/docker-compose.yml run --rm --no-deps app bash -c 'umask 077; mkdir -p /var/lib/lamp/secrets; read -rsp "password: " p; printf "%s\n" "$p" > /var/lib/lamp/secrets/database-password'`

</details>

<details>

<summary>configuration</summary>

- `./lamp setup` writes `.data/config.yaml` (mode 600) with `domain` set and every optional section as a commented example

| key                     | default               | effect                                                                                       |
| ----------------------- | --------------------- | -------------------------------------------------------------------------------------------- |
| `domain`                | required              | base domain of every environment; a change reapplies all environments on `start` / `restart` |
| `git.name`, `git.email` | unset                 | global git identity inside the container for commits made through `exec` or `ssh`            |
| `apache.admin`          | `webmaster@localhost` | `ServerAdmin` of the shared apache configuration                                             |
| `postfix.hostname`      | `lamp.localdomain`    | `myhostname` and `/etc/mailname` of the container's postfix                                  |
| `postfix.relayhost`     | empty (direct delivery) | postfix `relayhost`, e.g. `[smtp.example.com]:587`; credentials in `/etc/postfix/sasl_passwd` through a mount in the compose override |
| `vpn`                   | disabled              | see [vpn](#vpn)                                                                              |

- all values are applied on every container start, so a pulled image and a locally built image behave the same; edit the file and run `./lamp restart`
- invalid or unknown keys stop the start before any environment is touched

</details>

<details>

<summary>environments</summary>

- `.data/environments.yaml` is a list of the static environments, without ids; `start` / `restart` reconcile it, `add --subdomain` appends to it, `remove` deletes from it
- an entry and a running environment are the same when every value matches; changing any value in the file removes the old environment (owned databases included, static directories stay) and provisions a new one
- dynamic environments (`add` without `--subdomain`) live in the runtime state only, are never written to the file and are never touched by `start` / `restart`; remove them with `lamp remove <id>`
- ids are runtime identifiers reported by `add`, `show` and `list`; `add --id` reuses one and is idempotent: identical settings return the existing environment, different settings fail
- a file from the previous id-keyed format is converted on first use; its dynamic entries are dropped from the file, the environments themselves stay

- `./lamp setup` writes an empty `.data/environments.yaml` with a commented example entry; the first `add` or reconciliation rewrites the file without comments

| key             | default | meaning                                                                                        |
| --------------- | ------- | ---------------------------------------------------------------------------------------------- |
| `git`           | null    | ssh `git@host:path` or credential-free https url; null for an empty environment                |
| `branch`        | null    | null selects the repository default branch                                                     |
| `subdomain`     | required | one lowercase label or a list; first label is the primary host and the static project path    |
| `aliases`       | omitted | suffixes: `<primary>-<suffix>.<domain>` share the same checkout and databases                  |
| `directory`     | omitted | folder name under `/var/www` when it differs from the first subdomain, e.g. `aistats` for `ai` |
| `webroot`       | null    | directory relative to the checkout; null picks `public/` or `web/` with `index.php`, else root |
| `php`           | omitted | explicit version; omitted reads the repository root `.phprc`, else `8.5`                       |
| `vpn`           | null    | required tunnel name from `config.yaml`                                                        |
| `proxy_port`    | null    | forward the vhost to `http://127.0.0.1:<port>/` (`ProxyPreserveHost On`)                       |
| `proxy_exclude` | null    | one path prefix that stays on php, e.g. `/admin`                                               |
| `visibility`    | private | `public` adds a cloudflare access bypass for exactly this environment's hostnames              |
| `build`         | omitted | inline build; omitted uses `.data/build/<host>-<owner>-<repo>.sh` if present; `':'` for no-op  |

- project path: `/var/www/<directory or first subdomain>` for static environments, `/var/www/_environments/<id>` for dynamic ones; the same path on host and container
- existing directories are adopted without clone, pull, checkout or chown; missing directories are cloned
- `remove` deletes the project directory only for dynamic environments; static directories always stay
- hostnames: `<subdomain-or-id>.<domain>`; every hostname must be unique; `phpmyadmin` is reserved; no nested subdomains
- reconciliation: entries without a matching environment are provisioned, including database initialization, imports and the build; environments without a matching entry are removed; a changed build script reruns the build of every environment using it; `[]` removes all static environments, an empty file is invalid
- `lamp branch <id> <branch>` switches the checkout and updates the entry's `branch` in the file, so the environment keeps matching
- failed environments keep status `failed` and are not served; fix the yaml and `restart`, or `build <id>`, or `remove`
- `docker-reset` deletes the runtime state: static environments are re-registered from the file on the next `start`, dynamic environments are gone and their directories under `/var/www/_environments/` become orphans
- `show`, `list`, `add`, `build <id>` return json without passwords or build commands

</details>

<details>

<summary>build scripts</summary>

- `.data/build/<host>-<repository path with / replaced by ->.sh`, e.g. `github.com-owner-project.sh` for `git@github.com:owner/project.git` and `https://github.com/owner/project.git`
- sourced by bash with `set -e` in the checkout, the selected php first on `PATH`, node lts, the variables below and a `syncdb` function
- runs on first registration (also for adopted directories), when settings change, when the script content hash changes, and on `./lamp build <id>`
- complete output goes to `/var/lib/lamp/environments/<id>/build.log` (mode 600); the path is printed at start and in the failure message
- no build runs without a script or `build` setting
- `./lamp setup` writes the example `.data/build/github.com-owner-project.sh`: syncdb import, `.env` created from an embedded heredoc with `APP_URL` and `DB_*` rewritten from the setup variables, then composer and npm; copy it per project and keep only the steps the project has

| variable                                    | value                                                         |
| ------------------------------------------- | ------------------------------------------------------------- |
| `LAMP_ID`                                   | environment id                                                |
| `LAMP_URL`, `APP_URL`                       | `https://<hostname>`                                          |
| `LAMP_PROJECT_DIR`                          | checkout path                                                 |
| `LAMP_DATA_DIR`                             | persistent per-environment data directory                     |
| `DB_CONNECTION`                             | `mysql`; a successful sqlite `syncdb` switches it to `sqlite` |
| `DB_HOST`, `DB_PORT`                        | `localhost`, `3306`                                           |
| `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` | `lamp_<id>` / generated password, or the sqlite file path     |
| `PGHOST`, `PGPORT`                          | `localhost`, `5432`                                           |
| `PGDATABASE`, `PGUSER`, `PGPASSWORD`        | `lamp_<id>` / generated password                              |

- the variables are not injected into php-fpm; the build must write them into the project's own configuration (`.env`, `wp-config.php`, …)
- later shells: `./lamp ssh`, `source /var/lib/lamp/environments/<id>/setup.env`, `cd "$LAMP_PROJECT_DIR"`
- node: lts and current are installed; `source /root/.nvm/nvm.sh && nvm use` for a project `.nvmrc` (the version must be installed by the build)
- python 3 with pip/venv (`python` is python 3); `python3.12` additionally at `/opt/python3.12`; no python 2, no node 10–16
- ruby 3.1.2 via rvm, rust, go, gettext, pandoc, xvfb, wkhtmltopdf, imagemagick, ghostscript, tesseract (deu), ffmpeg, image optimizers, wp-cli, gh, svn

</details>

<details>

<summary>php</summary>

- all web requests use php-fpm; no `mod_php`
- selection: explicit `php` → repository root `.phprc` (one installed version, e.g. `8.3`) → `8.5`; invalid `.phprc` fails the setup
- default cli php in a plain shell is 8.1; `exec --environment` and builds use the environment's version
- shared `/etc/php/custom.ini` (linked into every version's cli and fpm): 4096M memory, 4800s execution time, 800M uploads, opcache with 2s revalidation, apcu, `variables_order = EGPCS`, xdebug 3 (xdebug 2 for 5.6–7.1) in `debug,profile` mode with trigger start, port 9003, profiles in `/tmp/xdebug`
- xdebug client host defaults to `localhost` inside the container; set `xdebug.client_host` for an ide on the host (`host.docker.internal` on docker desktop)
- `uopz` is installed but disabled; jit is disabled
- managed vhosts deny `.phps`, `.phtml`, `.phar` and dotfiles; requests with `Accept: text/event-stream` use a flushing fpm worker without gzip (sse / `mcp-server.php`)
- applications that force https in `.htaccess` must honor `X-Forwarded-Proto: https`

</details>

<details>

<summary>syncdb</summary>

- original profiles in `.data/syncdb/<profile>.json` (mode 600), visible in the container at `/etc/lamp/syncdb/`
- `syncdb <profile>` inside a build copies the profile, replaces its complete `target` with the environment's isolated mysql database or sqlite file, imports with php 8.5 in a temporary directory, deletes the copy; `source` and `replace` rules stay unchanged
- mysql and sqlite only, no postgresql; imports use the scoped environment account, syncdb rewrites object definers to it
- a successful import exports `DB_CONNECTION` and `DB_DATABASE` into the running build and `setup.env`; do not call it in a subshell
- every executed `syncdb` imports again; an unchanged start does not run the build at all
- syncdb `>= 2.1.3` resets object definers to the importing account and remaps `ALTER DATABASE` charset statements in routine dumps to the target database

</details>

<details>

<summary>cloudflare</summary>

<blockquote>

<details>

<summary>tunnel</summary>

- setup: [installation › 3. cloudflare tunnel](#installation)
- supervisor runs `cloudflared` inside the container; the wildcard ingress forwards `*.<domain>` to apache's internal listener `127.0.0.1:8081`, everything else gets 404
- without the credentials file `start` works but `add` and configured environments are refused
- `domain` changes in `config.yaml` reapply all environments on the next `start` / `restart`

</details>

<details>

<summary>access</summary>

- setup: [installation › 4. cloudflare access](#installation); the wildcard application, its two policies, the service token and the management token are mandatory
- customers: a separate application per exact hostname with an `allow` policy for their exact emails via one-time pin; revoke sessions when withdrawing access
- session tip: global session one month, application session 24 hours, developer policy `same as application`
- `visibility: public` creates `lamp-public:<domain>:<id>` with a `bypass › everyone` policy for exactly the environment's hostnames; private removes it; the wildcard stays untouched
- reserve the name prefix `lamp-public:`; lamp refuses to overwrite foreign applications on its hostnames
- on any access api or protection failure the affected vhosts are disabled and the command fails; retry with `start` / `restart` after fixing

</details>

<details>

<summary>cache</summary>

- cloudflare caches static assets (`.js`, `.css`, images, fonts) of every proxied hostname by default, also behind access; after a rebuild the tunnel can still deliver the previous bundle
- switch it off once for the whole zone: dashboard › `<domain>` › `Caching › Cache Rules › Create rule`, name `lamp: bypass cache`, `When incoming requests match › All incoming requests` (or `Hostname › wildcard › *.<domain>` if the zone also serves other sites), `Cache eligibility › Bypass cache`, deploy
- the same rule via api: ruleset phase `http_request_cache_settings`, action `set_cache_settings` with `"cache": false`; needs a token with `Zone › Cache Rules › Edit`, which the management token does not have
- verify with `./lamp curl <id> -- -sSI https://<hostname>/_build/app.js` (any static file): `cf-cache-status: DYNAMIC` on every request, never `HIT`
- `Caching › Configuration › Development Mode` expires after three hours and is no replacement

</details>

<details>

<summary>management api token</summary>

- created during [installation › 4. cloudflare access](#installation); needed for `add`, `remove`, `start`, `restart` (protection check and bypass management), also for private environments
- rotate it before it expires; the tunnel credentials, the service token and this token are three different credentials

</details>

<details>

<summary>service token (harness)</summary>

- created during [installation › 4. cloudflare access](#installation)
- `./lamp curl <id> -- -fsS https://<hostname>/` and the `curl` wrapper inside `exec --environment` send the headers only to that environment's exact https origin, never follow redirects with credentials, and refuse unsupported options
- `./lamp access <id>` prints origin and headers as json for trusted integrations; keep it out of visible tool calls and logs
- public environments send no token; their own application logins still apply

</details>

<details>

<summary>verify before sharing</summary>

- anonymous browser gets the access login, not content
- your cloudflare login works, an unapproved account is denied, otp is not accepted on the developer policy
- valid service token works, missing or invalid tokens do not (assets and api calls, not only the login page)
- a customer reaches only their project
- the direct origin is not reachable from the internet

</details>

</blockquote>

</details>

<details>

<summary>automation</summary>

- `lamp status --json` / `lamp version --json` return `root`, `state`, `healthy`, `api` (cli contract number, currently `1`)
- `lamp add --id <id> --git <url> --branch <b> --base-branch main` creates or returns the environment; missing remote branches start from `--base-branch`
- `lamp exec --environment <id> -- bash -c 'npm test'` runs in the checkout with the right php and database variables (use `bash -c`, not a login shell)
- `lamp branch <id> <branch> --base main` switches without rebuild; dirty checkouts are refused, ignored files are never overwritten
- `lamp build <id>` reruns the project build on demand; `lamp list --search <term>` finds environments by any value
- keep environments until their files and databases have been reviewed; archiving a chat does not require `remove`
- run `lamp` on the docker host, directly or over ssh (`/usr/local/bin/lamp` for restricted paths); the harness needs no docker inside its own container

</details>

<details>

<summary>local access</summary>

- http `127.0.0.1:18080`, https `127.0.0.1:8443` (direct origin, bypasses cloudflare; for diagnostics only)
- trust `.data/ca/certificate.crt` and add `127.0.0.1 <hostname>` to the browser machine's hosts file; remove the override for normal access through cloudflare
- phpmyadmin: `https://phpmyadmin.<domain>:8443` with automatic admin login; local ca, hosts entry, no tunnel vhost
- environment certificates last one year and are renewed on container start when fewer than 30 days remain
- no database, redis, ssh or docker ports are published; `docker/docker-compose.override.yml` (gitignored) is the place for extra ports and read-only bind mounts (`create_host_path: false`)

</details>

<details>

<summary>cliproxyapi</summary>

- started by supervisor when `.data/cliproxyapi/config.yaml` exists; use `auth-dir: /var/lib/lamp/cliproxyapi/auth`
- provider logins are documented in the [aistats readme](https://github.com/vielhuber/aistats); run them inside `./lamp ssh`; antigravity's callback port `51121` is not published
- expose through a project: `subdomain: ai`, `directory: aistats`, `proxy_port: 8317`, `proxy_exclude: /admin`; optional host access via `127.0.0.1:8317:8317` in the compose override
- stop any host cliproxyapi unit before running the container instance against the same oauth accounts

</details>

<details>

<summary>vpn</summary>

- the `vpn` section of `.data/config.yaml` lists tunnels with `name`, `type` (`openvpn` or `wireguard`), `config`, optional `username` / `password`, `routes` and `hosts`; `./lamp setup` leaves a commented example in the file

- profiles in `.data/vpn/` (mode 600); names: lowercase, max twelve characters, letter first
- `./lamp restart` applies changes; `./lamp exec 'supervisorctl start|stop|status vpn-office'` controls a tunnel
- `vpn: office` on an environment starts it before clone, import and build; `add --vpn office` likewise
- split tunnel only: server-pushed routes and dns are ignored, default-route redirects are rejected, use `routes` and `hosts`
- routes and hosts affect the whole container; a running client process does not prove the remote service is reachable
- retry an import without restart: `./lamp exec 'python3 /opt/lamp/control.py reconcile'`

</details>

<details>

<summary>ssh</summary>

- `.data/ssh/` is the container's `/root/.ssh`: `id_rsa` (default identity and git signing key), `config`, `known_hosts`; directory 700, files 600
- verify unknown hosts interactively with `./lamp ssh` before a non-interactive `add`
- git hooks from `docker/git-hooks/` are installed globally via `core.hooksPath`; repository-local hook paths override them
- `./lamp ssh` is `docker exec`; no ssh server port is published

</details>

<details>

<summary>logs and debugging</summary>

- `.logs/<command>-<timestamp>-<random>.log` for `docker-build`, `docker-reset`, `start`, `restart` (ansi stripped, exit code included, container output since the start request appended)
- `./lamp exec 'supervisorctl status'`; php errors in `/var/log/php-error.log`; xdebug profiles in `/tmp/xdebug` (`?XDEBUG_PROFILE=1` or `XDEBUG_PROFILE=1 php …`)
- vpn client logs in `/var/log/supervisor/vpn-<name>.log`
- project build logs in `/var/lib/lamp/environments/<id>/build.log`
- shutdown waits up to seven minutes for ordered service groups; postgresql uses fast shutdown

</details>

<details>

<summary>verification</summary>

- `bash -n lamp docker/docker-build.sh docker/docker-entrypoint.sh docker/docker-start.sh docker/scripts/healthcheck.sh`
- `docker compose -f docker/docker-compose.yml config --quiet`
- `python3 -m unittest discover -s docker/tests`
- the container copies `docker/scripts/` to `/opt/lamp/` at image build time; changed scripts need `./lamp docker-build` or a `docker compose cp` into the running container

</details>

</blockquote>

</details>
