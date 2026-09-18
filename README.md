[![build status](https://github.com/vielhuber/lamp/actions/workflows/ci.yml/badge.svg)](https://github.com/vielhuber/lamp/actions)
[![docker build](https://img.shields.io/badge/Docker-Package-blue)](https://github.com/vielhuber/lamp/pkgs/container/lamp)
[![last commit](https://img.shields.io/github/last-commit/vielhuber/lamp)](https://github.com/vielhuber/lamp/commits)

# 🛠️ lamp 🛠️

a portable development machine in docker: apache, php, mysql, postgresql, redis, node, python, ruby, rust and the usual tooling. projects get isolated checkouts and databases, are served through an existing cloudflare tunnel behind cloudflare access, and are managed by one cli that also works over ssh or from an agent harness.

<details>

<summary><strong>requirements</strong></summary>

- [docker](https://docs.docker.com/engine/install/ubuntu/) `>= 20.10.0`

</details>

<details>

<summary><strong>installation</strong></summary>

<blockquote>

<details>

<summary>1. pull image</summary>

- `mkdir lamp`
- `cd lamp`
- `docker pull ghcr.io/vielhuber/lamp:latest`

</details>

<details>

<summary>2. configure instance</summary>

- `docker run --rm -v "$PWD:/install" ghcr.io/vielhuber/lamp:latest init`
- `sudo ln -s "$(pwd -P)/lamp" /usr/local/bin/lamp`
- `./lamp docker-setup`
- config `.data/*.yaml` and `docker/docker-compose.override.yml`

</details>

<details>

<summary>3. cloudflare</summary>

- [my profile › api tokens](https://dash.cloudflare.com/profile/api-tokens) › create custom token:
    - `Account › Cloudflare Tunnel › Edit`
    - `Account › Access: Apps and Policies › Edit`
    - `Account › Access: Service Tokens › Edit`
    - `Zone › Zone › Read`
    - `Zone › DNS › Edit`
    - `Zone › Cache Rules › Edit`
- set `cloudflare.token` and `cloudflare.email` in `.data/config/settings.yaml`
- `./lamp cloudflare-setup`

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
- `docker run --rm -v "$PWD:/install" ghcr.io/vielhuber/lamp:latest init`
- `./lamp start`

</details>

<details>

<summary><strong>commands</strong></summary>

| command                                                                            | effect                                                                                                                                                                   |
| ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `./lamp start`                                                                     | start container, wait for health, apply `.data/config/env.yaml`                                                                                                          |
| `./lamp add [options]`                                                             | create environment, returns json; all options below are optional; `--subdomain` records a static entry in `.data/config/env.yaml`, without it the environment is dynamic |
| `./lamp stop`                                                                      | stop container, keep all data                                                                                                                                            |
| `./lamp restart`                                                                   | validate yaml, stop, start, apply                                                                                                                                        |
| `./lamp status [--json]`                                                           | container state, health, ports, supervised services                                                                                                                      |
| `./lamp version [--json]`                                                          | host checkout version and container image id                                                                                                                             |
| `./lamp build <id\|subdomain>`                                                     | rerun the configured build of one environment inside the running container                                                                                               |
| `./lamp syncdb <profile>`                                                          | run a `.data/syncdb` profile unchanged inside the container; its target must point at the container databases                                                            |
| `./lamp list [--search <term>]`                                                    | all environments as json; `--search` filters case-insensitively over all values                                                                                          |
| `./lamp show <id\|subdomain>`                                                      | one environment as json                                                                                                                                                  |
| `./lamp branch <id\|subdomain> <branch> [--base <b>] [--operation switch\|rename]` | switch or create branch without rebuild                                                                                                                                  |
| `./lamp exec [<id\|subdomain>] "<command>"`                                        | with an id: in the project directory with its php, setup variables and authenticated `curl`; without: as root in the container                                           |
| `./lamp curl <id\|subdomain> -- <curl args>`                                       | curl the environment's exact https origin with the access service token                                                                                                  |
| `./lamp access <id\|subdomain>`                                                    | origin and access headers as json; secret, for trusted integrations only                                                                                                 |
| `./lamp remove <id\|subdomain>`                                                    | remove environment, owned databases, runtime data; dynamic project directory only                                                                                        |
| `./lamp ssh [<id\|subdomain>]`                                                     | interactive root shell in the container; with an environment: in its project directory with its variables, `git status` first                                            |
| `./lamp cloudflare-setup`                                                          | create or verify tunnel, wildcard dns, access application, service token and cache rule; prints `ok`, `created`, `updated`, `rotated` or `recreated` per item            |
| `./lamp docker-build`                                                              | rebuild the image without layer cache, keep volumes (requires stopped container)                                                                                         |
| `./lamp docker-setup`                                                              | create `.data` with commented presets, example files and the compose override; keeps existing files                                                                      |
| `./lamp docker-reset`                                                              | **delete all compose volumes**, then rebuild the image (requires stopped container)                                                                                      |

```bash
./lamp add \
    --git <url> \
    --id <12-hex> \
    --branch <b> \
    --base-branch <b> \
    --php <v> \
    --vpn <name> \
    --build "<cmd>" \
    --subdomain <label> \
    --directory <name> \
    --db-name <name> \
    --db-engine mysql|postgres|sqlite \
    --alias <suffix> \
    --webroot <dir> \
    --proxy-port <port> \
    --proxy-exclude </path> \
    --visibility private|public
```

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
- `./lamp start` (re-registers all environments from `.data/config/env.yaml`, including database imports and builds)

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

| path                                 | contents                                                                          | survives `docker-reset` |
| ------------------------------------ | --------------------------------------------------------------------------------- | ----------------------- |
| `.data/config/settings.yaml`         | `domain`, optional `git`, `apache`, `postfix`, `vpn` (mode 600)                   | yes                     |
| `docker/docker-compose.override.yml` | host-specific: projects mount, extra mounts and ports (gitignored)                | yes                     |
| `.data/config/env.yaml`              | desired environments (mode 600)                                                   | yes                     |
| `.data/build/*.sh`                   | shared repository build scripts (mode 600)                                        | yes                     |
| `.data/syncdb/*.json`                | original syncdb profiles (mode 600)                                               | yes                     |
| `.data/ssh/`                         | container `/root/.ssh` (keys, config, known_hosts)                                | yes                     |
| `.data/cloudflare/`                  | tunnel credentials and access service token, written by `cloudflare-setup`        | yes                     |
| `.data/vpn/`                         | openvpn / wireguard profiles                                                      | yes                     |
| `.logs/`                             | host-side command logs                                                            | yes                     |
| `/var/www`                           | project checkouts; host directory set in `docker/docker-compose.override.yml`     | yes                     |
| compose volumes                      | mysql, postgresql, redis, apache sites, mail, certificates, `/var/lib/lamp` state | **no**                  |

- `.data` is mounted at `/etc/lamp`, `.data/ssh` at `/root/.ssh`
- `.data` and `.logs` are excluded from git and from the image
- back up `.data` and database-consistent dumps separately; `docker-reset` is not an update that preserves environments
- initial mysql root / postgres password: `/var/lib/lamp/secrets/database-password` in the state volume, generated on first start; set your own before the first database initialization with `docker compose -f docker/docker-compose.yml run --rm --no-deps app bash -c 'umask 077; mkdir -p /var/lib/lamp/secrets; read -rsp "password: " p; printf "%s\n" "$p" > /var/lib/lamp/secrets/database-password'`

</details>

<details>

<summary>configuration</summary>

- `./lamp docker-setup` writes `.data/config/settings.yaml` (mode 600) with `domain` set and every optional section as a commented example

| key                                    | default                             | effect                                                                                                                                                                          |
| -------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `domain`                               | required                            | base domain of every environment; a change reapplies all environments on `start` / `restart`                                                                                    |
| `git.name`, `git.email`                | unset                               | global git identity inside the container for commits made through `exec` or `ssh`                                                                                               |
| `apache.admin`                         | `webmaster@localhost`               | `ServerAdmin` of the shared apache configuration                                                                                                                                |
| `postfix.hostname`                     | `lamp.localdomain`                  | `myhostname` and `/etc/mailname` of the container's postfix                                                                                                                     |
| `postfix.relayhost`                    | empty (direct delivery)             | postfix `relayhost`, e.g. `[smtp.example.com]:587`                                                                                                                              |
| `postfix.username`, `postfix.password` | unset                               | smtp auth for the relay; written to `/etc/postfix/sasl_passwd` (mode 600) on every start                                                                                        |
| `cloudflare.token`, `cloudflare.email` | unset                               | api token used by `cloudflare-setup`, `add`, `remove`, `start`, `restart`, and the login email allowed by access                                                                |
| `database.password`                    | generated once                      | password of mysql `root` and postgres `postgres`, applied on `start` / `restart` to the servers, `/var/lib/lamp/secrets/database-password`, the vhost variables and `setup.env` |
| `composer.github`                      | unset                               | github token written to composer's global `auth.json` on `start` / `restart` for private packages and the api rate limit                                                        |
| `php.xdebug`                           | true (`docker-setup` preset: false) | false removes the xdebug module from every php version on `start` / `restart`, about 15 percent faster requests, no debugging or profiling                                      |
| `vpn`                                  | disabled                            | see [vpn](#vpn)                                                                                                                                                                 |

- all values are applied on every container start, so a pulled image and a locally built image behave the same; edit the file and run `./lamp restart`
- invalid or unknown keys stop the start before any environment is touched

</details>

<details>

<summary>environments</summary>

- `.data/config/env.yaml` is a list of the static environments, without ids; `start` / `restart` reconcile it, `add --subdomain` appends to it, `remove` deletes from it
- an entry and a running environment are the same when every value matches; changing any value in the file removes the old environment and provisions a new one; static directories and fixed databases stay
- dynamic environments (`add` without `--subdomain`) live in the runtime state only, are never written to the file and are never touched by `start` / `restart`; remove them with `lamp remove <id>`
- ids are runtime identifiers reported by `add`, `show` and `list`; `add --id` reuses one and is idempotent: identical settings return the existing environment, different settings fail; every command that takes an id also accepts a subdomain label of a static environment
- a file from the previous id-keyed format is converted on first use; its dynamic entries are dropped from the file, the environments themselves stay

- `./lamp docker-setup` writes `.data/config/env.yaml` with a commented example entry and the phpmyadmin entry below; the first `add` or reconciliation rewrites the file without comments, one blank line between entries
- phpmyadmin: `https://github.com/phpmyadmin/phpmyadmin.git` on branch `STABLE` as subdomain `phpmyadmin`, private, so cloudflare access protects it like every other environment; its build script `.data/build/github.com-phpmyadmin-phpmyadmin.sh` runs composer and yarn and writes `config.inc.php` with automatic root login from the container's database password; delete the entry if you do not want it

| key             | default         | meaning                                                                                                       |
| --------------- | --------------- | ------------------------------------------------------------------------------------------------------------- |
| `git`           | null            | ssh `git@host:path` or credential-free https url; null for an empty environment                               |
| `branch`        | null            | null selects the repository default branch                                                                    |
| `subdomain`     | required        | one lowercase label or a list; first label is the primary host and the static project path                    |
| `aliases`       | omitted         | suffixes: `<primary>-<suffix>.<domain>` share the same checkout and databases                                 |
| `directory`     | first subdomain | folder under `/var/www`; written explicitly, may be nested like `tourconcept/new`                             |
| `db_name`       | null            | fixed database of a static environment; null creates nothing                                                  |
| `db_engine`     | null            | `mysql`, `postgres` or `sqlite`; required with `db_name`; only mysql and sqlite can be imported with `syncdb` |
| `webroot`       | null            | directory relative to the checkout; null picks `public/` or `web/` with `index.php`, else root                |
| `php`           | omitted         | explicit version; omitted reads the repository root `.phprc`, else `8.5`                                      |
| `vpn`           | null            | required tunnel name from `settings.yaml`                                                                     |
| `proxy_port`    | null            | forward the vhost to `http://127.0.0.1:<port>/` (`ProxyPreserveHost On`)                                      |
| `proxy_exclude` | null            | one path prefix that stays on php, e.g. `/admin`                                                              |
| `visibility`    | private         | `public` adds a cloudflare access bypass for exactly this environment's hostnames                             |
| `build`         | omitted         | inline build; omitted uses `.data/build/<host>-<owner>-<repo>.sh` if present; `':'` for no-op                 |

- project path: `/var/www/<directory or first subdomain>` for static environments, `/var/www/_environments/<id>` for dynamic ones; the same path on host and container
- existing directories are adopted without clone, pull, checkout, chown or build; missing directories are cloned and built
- `remove` deletes the project directory only for dynamic environments; static directories always stay
- databases: a static environment gets exactly one fixed database `db_name` on `db_engine`, created when missing and never dropped or altered by lamp, reachable as `root` (mysql) or `postgres` (postgres) with the password from `/var/lib/lamp/secrets/database-password`; a `sqlite` database is the file `/var/lib/lamp/environments/<id>/data/<db_name>.sqlite`; dynamic environments get isolated `lamp_<id>` databases on mysql and postgresql with their own account, dropped on `remove`
- mysql and postgresql are published on `127.0.0.1:3306` and `127.0.0.1:5432` of the host for database tools; the old host services must be stopped
- hostnames: `<subdomain-or-id>.<domain>`; every hostname must be unique; no nested subdomains
- reconciliation: entries without a matching environment are provisioned, including database initialization, imports and the build; environments without a matching entry are removed; a changed build script reruns the build of every cloned environment using it; `[]` removes all static environments, an empty file is invalid; one pass does the access checks once at start and end, one apache reload and one php-fpm restart per version at the end
- `lamp branch <id> <branch>` switches the checkout and updates the entry's `branch` in the file, so the environment keeps matching
- failed environments keep status `failed` and are not served; fix the yaml and `restart`, or `build <id>`, or `remove`
- `docker-reset` deletes the runtime state: static environments are re-registered from the file on the next `start`, dynamic environments are gone and their directories under `/var/www/_environments/` become orphans
- `show`, `list`, `add`, `build <id>` return json without passwords or build commands

</details>

<details>

<summary>build scripts</summary>

- `.data/build/<host>-<repository path with / replaced by ->.sh`, e.g. `github.com-owner-project.sh` for `git@github.com:owner/project.git` and `https://github.com/owner/project.git`
- sourced by bash with `set -e` in the checkout, the selected php first on `PATH`, node lts, the variables below and a `syncdb` function
- runs when lamp clones the project, when a cloned project's settings or script change, and on `./lamp build <id>`; adopted directories are only built by `./lamp build <id>`
- complete output goes to `/var/lib/lamp/environments/<id>/build.log` (mode 600); the path is printed at start and in the failure message
- no build runs without a script or `build` setting
- `./lamp docker-setup` writes the example `.data/build/github.com-owner-project.sh`: syncdb import, `.env` created from an embedded heredoc with `APP_URL` and `DB_*` rewritten from the setup variables, then composer and npm; copy it per project and keep only the steps the project has

| variable                                    | value                                                                                                                                                   |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LAMP_ID`                                   | environment id                                                                                                                                          |
| `LAMP_URL`, `APP_URL`                       | `https://<hostname>`                                                                                                                                    |
| `LAMP_PROJECT_DIR`                          | checkout path                                                                                                                                           |
| `LAMP_DATA_DIR`                             | persistent per-environment data directory                                                                                                               |
| `DB_CONNECTION`                             | static: `mysql`, `pgsql` or `sqlite` from `db_engine`, unset without database; dynamic: `mysql`, a sqlite `syncdb` switches it                          |
| `DB_HOST`, `DB_PORT`                        | `localhost`, `3306` (mysql) or `5432` (postgres)                                                                                                        |
| `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` | static: `db_name` with `root` / `postgres` and the container database password, or the sqlite file path; dynamic: `lamp_<id>` with a generated password |
| `PGHOST`, `PGPORT`                          | `localhost`, `5432`; static only with `db_engine: postgres`                                                                                             |
| `PGDATABASE`, `PGUSER`, `PGPASSWORD`        | static: `db_name` / `postgres`; dynamic: `lamp_<id>` / generated password                                                                               |

- the same variables are set in the environment's vhost (`SetEnv`) and reach php-fpm as fastcgi parameters: `getenv('DB_DATABASE')` or `$_SERVER['DB_DATABASE']` in php, laravel's `env()` prefers them over `.env`; cli runs get them through `exec <id>` and `build`
- wordpress: `define('DB_NAME', getenv('DB_DATABASE'))`, `define('DB_USER', getenv('DB_USERNAME'))`, `define('DB_PASSWORD', getenv('DB_PASSWORD'))`, `define('DB_HOST', getenv('DB_HOST'))` in the local branch of `wp-config.php`, no generated files needed
- services: a build may write supervisor `[program:…]` sections to `$LAMP_DATA_DIR/supervisor.conf` (unique program names, absolute paths); lamp loads the file after every build and on container start, restarts changed programs and stops them when the environment is removed; combine with `proxy_port` to route the vhost to the service
- later shells: `./lamp ssh`, `source /var/lib/lamp/environments/<id>/setup.env`, `cd "$LAMP_PROJECT_DIR"`
- node: lts and current are installed; `source /root/.nvm/nvm.sh && nvm use` for a project `.nvmrc` (the version must be installed by the build)
- python 3 with pip/venv (`python` is python 3); `python3.12` additionally at `/opt/python3.12`; no python 2, no node 10–16
- ruby 3.1.2 via rvm, rust, go, gettext, pandoc, xvfb, wkhtmltopdf, imagemagick, ghostscript, tesseract (deu), ffmpeg, image optimizers, wp-cli, gh, svn
- google chrome for puppeteer, `critical` and headless tests: `PUPPETEER_EXECUTABLE_PATH=/usr/bin/google-chrome` and `PUPPETEER_SKIP_DOWNLOAD=true` are set, so projects use the system browser instead of downloading one

</details>

<details>

<summary>php</summary>

- all web requests use php-fpm; no `mod_php`
- selection: explicit `php` → repository root `.phprc` (one installed version, e.g. `8.3`) → `8.5`; invalid `.phprc` fails the setup
- default cli php in a plain shell is 8.1; `exec <id>` and builds use the environment's version
- shared `/etc/php/custom.ini` (linked into every version's cli and fpm): 4096M memory, 4800s execution time, 800M uploads, opcache with 2s revalidation, apcu, `variables_order = EGPCS`, xdebug 3 (xdebug 2 for 5.6–7.1) in `debug,profile` mode with trigger start, port 9003, profiles in `/tmp/xdebug`
- xdebug is loaded in trigger mode for every php version: nothing happens until a request carries a trigger; the [xdebug helper](https://chromewebstore.google.com/detail/xdebug-helper/eadndfjplgieldjbigjakmdgkmoaaaoc) browser extension sets it per site (`Debug` → `XDEBUG_SESSION` cookie, `Profile` → `XDEBUG_PROFILE` cookie, `Trace` → `XDEBUG_TRACE` cookie, `Disable` → none), on the command line `XDEBUG_TRIGGER=1 php …` or `XDEBUG_PROFILE=1 php …` inside `exec`
- step debugging connects to `host.docker.internal:9003`, so the ide on the docker host listens on 9003 and maps `/var/www` to the same path on the host; profiles land in `/tmp/xdebug`
- `php.xdebug: false` in `settings.yaml` removes the module for every version and gains about 15 percent per request; triggers then do nothing
- `uopz` is installed but disabled; jit is disabled
- managed vhosts deny `.phps`, `.phtml`, `.phar` and dotfiles; requests with `Accept: text/event-stream` use a flushing fpm worker without gzip (sse / `mcp-server.php`)
- apache terminates tls itself with the let's encrypt certificate, so `%{HTTPS}`, `$_SERVER['HTTPS']` and https redirects in `.htaccess` behave as in production

</details>

<details>

<summary>syncdb</summary>

- original profiles in `.data/syncdb/<profile>.json` (mode 600), visible in the container at `/etc/lamp/syncdb/`
- `./lamp syncdb <profile>` runs the profile exactly as written: target `localhost:3306`, user `root` and `database.password`, database `db_name` of the static environment
- `syncdb <profile>` inside a build or `exec <id>` copies the profile, replaces its complete `target` with the environment's database (static: the fixed `db_name` as root, or the sqlite file; dynamic: the isolated `lamp_<id>` or sqlite file), imports with php 8.5 in a temporary directory, deletes the copy; `source` and `replace` rules stay unchanged; the profile engine must match `db_engine`
- mysql and sqlite only, no postgresql; imports use the scoped environment account, syncdb rewrites object definers to it
- a successful import exports `DB_CONNECTION` and `DB_DATABASE` into the running build and `setup.env`; do not call it in a subshell; postgres has no import, use `psql` with the `PG*` variables
- every executed `syncdb` imports again; an unchanged start does not run the build at all
- syncdb `>= 2.1.3` resets object definers to the importing account and remaps `ALTER DATABASE` charset statements in routine dumps to the target database

</details>

<details>

<summary>cloudflare</summary>

<blockquote>

<details>

<summary>tunnel</summary>

- `cloudflare-setup` creates the locally managed tunnel `<domain>`, writes `.data/cloudflare/cloudflared-credentials.json` and points the proxied cname `*.<domain>` at it; a tunnel of that name without local credentials is deleted and recreated
- supervisor runs `cloudflared` inside the container; the wildcard ingress forwards `*.<domain>` to apache's `*:443` with the let's encrypt certificate (origin name `<domain>`), everything else gets 404; no container port is published
- certificate: on every `start` / `restart` lamp requests or renews a let's encrypt certificate for `<domain>` and `*.<domain>` through the cloudflare dns challenge with `cloudflare.token` (stored in the `certificates` volume, renewed daily by cron); a domain change requests a new one
- every environment hostname resolves to `127.0.0.1` inside the container (`/etc/hosts`), so builds, `critical`, headless browsers and `curl` inside the container reach the local origin directly with a valid certificate, without cloudflare access
- without the credentials file `start` works but `add` and configured environments are refused
- `domain` changes in `settings.yaml` reapply all environments on the next `start` / `restart`; run `cloudflare-setup` again for the new zone
- never run the same tunnel from two machines

</details>

<details>

<summary>access</summary>

- `cloudflare-setup` creates the self-hosted application `lamp <domain>` for `*.<domain>` (found by its wildcard destination, so several lamp instances with different domains share one account) with policy `developer` (allow `cloudflare.email`) and policy `harness` (service token `lamp`), and writes `.data/cloudflare/cloudflare-service-token.yaml`; a rerun restores these settings if they were changed
- customers: a separate application per exact hostname with an `allow` policy for their exact emails via one-time pin; revoke sessions when withdrawing access
- session tip: global session one month, application session 24 hours, developer policy `same as application`
- `visibility: public` creates `lamp-public:<domain>:<id>` with a `bypass › everyone` policy for exactly the environment's hostnames; private removes it; the wildcard stays untouched
- reserve the name prefix `lamp-public:`; lamp refuses to overwrite foreign applications on its hostnames
- on any access api or protection failure the affected vhosts are disabled and the command fails; retry with `start` / `restart` after fixing

</details>

<details>

<summary>cache</summary>

- cloudflare caches static assets (`.js`, `.css`, images, fonts) of every proxied hostname by default, also behind access; after a rebuild the tunnel can still deliver the previous bundle
- `cloudflare-setup` creates the cache rule `lamp: bypass cache` for every host ending in `.<domain>`; other cache rules of the zone are kept
- verify with `./lamp curl <id> -- -sSI https://<hostname>/_build/app.js` (any static file): `cf-cache-status: DYNAMIC` on every request, never `HIT`
- `Caching › Configuration › Development Mode` expires after three hours and is no replacement

</details>

<details>

<summary>api token</summary>

- `cloudflare.token` in `settings.yaml`; needed for `cloudflare-setup` and for `add`, `remove`, `start`, `restart` (protection check and bypass management), also for private environments
- rotate it before it expires; the tunnel credentials, the service token and this token are three different credentials

</details>

<details>

<summary>service token (harness)</summary>

- created by `cloudflare-setup` as `.data/cloudflare/cloudflare-service-token.yaml`; if the file is lost, a rerun rotates the secret
- `./lamp curl <id> -- -fsS https://<hostname>/` and the `curl` wrapper inside `exec <id>` send the headers only to that environment's exact https origin, never follow redirects with credentials, and refuse unsupported options
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
- `lamp exec <id> "npm test"` runs the command in the checkout with the right php and database variables
- `lamp branch <id> <branch> --base main` switches without rebuild; dirty checkouts are refused, ignored files are never overwritten
- `lamp build <id>` reruns the project build on demand; `lamp list --search <term>` finds environments by any value
- keep environments until their files and databases have been reviewed; archiving a chat does not require `remove`
- run `lamp` on the docker host, directly or over ssh (`/usr/local/bin/lamp` for restricted paths); the harness needs no docker inside its own container

</details>

<details>

<summary>vpn</summary>

- the `vpn` section of `.data/config/settings.yaml` lists tunnels with `name`, `type` (`openvpn` or `wireguard`), `config`, optional `username` / `password`, `routes` and `hosts`; `./lamp docker-setup` leaves a commented example in the file

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
- `./lamp ssh` is `docker exec`; no ssh server port is published; `./lamp ssh <id|subdomain>` sources the environment's `setup.env`, changes into the project and runs `git status` before the shell

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
