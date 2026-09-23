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

<summary>1. cloudflare token</summary>

- [my profile › api tokens](https://dash.cloudflare.com/profile/api-tokens) › create custom token:
    - `Account › Cloudflare Tunnel › Edit`
    - `Account › Access: Apps and Policies › Edit`
    - `Account › Access: Service Tokens › Edit`
    - `Zone › Zone › Read`
    - `Zone › DNS › Edit`
    - `Zone › Cache Rules › Edit`

</details>

<details>

<summary>2. install</summary>

- `mkdir lamp && cd lamp`
- `docker pull ghcr.io/vielhuber/lamp:latest`
- `docker run --rm -v "$PWD:/install" ghcr.io/vielhuber/lamp:latest init` (ships the cli and the boilerplate `.config/env.yaml` and `docker/docker-compose.override.yml`; existing files are kept)
- `sudo ln -s "$(pwd -P)/lamp" /usr/local/bin/lamp`
- optional: list your environments in `.config/env.yaml` and adjust the projects mount (default `/var/www`) in `docker/docker-compose.override.yml`
- `./lamp start`
    - the first start asks for the domain and an optional private [data repository](#data-repository); it asks for an ssh key only if host SSH access fails, then creates tunnel, dns record, access application, service token, cache rule and certificate on its own
    - `Set up phpMyAdmin? [y/N]` adds its private environment to `env.yaml` only on confirmation; its build comes from the configured data folder
    - the final question, `Generate initial environments from folder`, suggests `/var/www`: accept to register its Git checkouts and hosts without running build scripts, or clear the input to skip
    - without a data repository it stops after writing the presets: set `cloudflare.token` and `cloudflare.email` in `.data/settings.yaml` and run `./lamp start` again

</details>

</blockquote>

</details>

<details>

<summary><strong>start</strong></summary>

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
| `./lamp start`                                                                     | start container, wait for health, apply `.config/env.yaml`                                                                                                          |
| `./lamp add [options]`                                                             | create environment, returns json; all options below are optional; `--subdomain` records a static entry in `.config/env.yaml`, without it the environment is dynamic |
| `./lamp stop`                                                                      | stop container, keep all data                                                                                                                                            |
| `./lamp restart`                                                                   | validate yaml, stop, start, apply                                                                                                                                        |
| `./lamp status [--json]`                                                           | container state, health, ports, supervised services                                                                                                                      |
| `./lamp version [--json]`                                                          | host checkout version and container image id                                                                                                                             |
| `./lamp build [<id\|subdomain>]`                                                     | rerun the configured build; without an argument, use the environment containing the current directory                                                                                               |
| `./lamp syncdb <profile>`                                                          | run a `.data/syncdb` profile unchanged inside the container; its target must point at the container databases                                                            |
| `./lamp list [--search <term>]`                                                    | all environments as json; `--search` filters case-insensitively over all values                                                                                          |
| `./lamp show <id\|subdomain>`                                                      | one environment as json                                                                                                                                                  |
| `./lamp branch <id\|subdomain> <branch> [--base <b>] [--operation switch\|rename]` | switch or create branch without rebuild                                                                                                                                  |
| `./lamp exec [<id\|subdomain>] "<command>"`                                        | with an id: in the project directory with its php, setup variables and authenticated `curl`; without: as root in the container                                           |
| `./lamp curl <id\|subdomain> -- <curl args>`                                       | curl the environment's exact https origin with the access service token                                                                                                  |
| `./lamp access <id\|subdomain>`                                                    | origin and access headers as json; secret, for trusted integrations only                                                                                                 |
| `./lamp remove <id\|subdomain>`                                                    | remove environment, owned databases, runtime data; dynamic project directory only                                                                                        |
| `./lamp reset`                                                                     | remove every dynamic environment with its checkout and isolated databases; static environments stay (not `docker-reset`)                                                 |
| `./lamp audit` | audit `/var/www` inside the container; pull remote changes only for clean repositories using fast-forward |
| `./lamp code` | run `code .` in the current host directory inside the running container and open VS Code attached to it |
| `./lamp ssh [<id\|subdomain>]`                                                     | interactive root shell in the container; with an environment: in its project directory with its variables, `git status` first                                            |
| `./lamp cloudflare-setup`                                                          | create or verify tunnel, wildcard dns, access application, service token and cache rule; prints `ok`, `created`, `updated`, `rotated` or `recreated` per item            |
| `./lamp docker-build`                                                              | rebuild the image without layer cache, keep volumes (requires stopped container)                                                                                         |
| `./lamp docker-setup`                                                              | run by the first `start` of a new installation: ask for the domain and an optional data repository, create `.config`, the compose override and, without a data repository, `.data` with commented presets and example files; keeps existing files |
| `./lamp docker-reset`                                                              | automatically stop and remove containers, **all compose volumes** and service images; no rebuild                                                                         |

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

- same as [installation](#installation), minus the `init` step and before the first `./lamp start`

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

- `./lamp docker-reset` (stops automatically, deletes containers, images, databases and all compose volumes without rebuilding; `.data`, `.config` and `/var/www` survive)
- `./lamp start` (re-registers all environments from `.config/env.yaml`, including database imports and builds)

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
| `.config/setup.yaml`                 | host-specific: `domain`, optional `data` repository (mode 600)                    | yes                     |
| `.config/env.yaml`                   | host-specific: desired environments (mode 600)                                    | yes                     |
| `.data/settings.yaml`                | optional `git`, `apache`, `postfix`, `cloudflare`, `php`, `vpn`, … (mode 600)     | yes                     |
| `docker/docker-compose.override.yml` | host-specific: projects mount, extra mounts and ports (gitignored)                | yes                     |
| `.data/build/*.sh`                   | shared repository build scripts (mode 600)                                        | yes                     |
| `.data/syncdb/*.json`                | original syncdb profiles (mode 600)                                               | yes                     |
| `.data/ssh/`                         | container `/root/.ssh` (keys, config, known_hosts)                                | yes                     |
| `.data/vpn/`                         | openvpn / wireguard profiles                                                      | yes                     |
| `.logs/`                             | host-side command logs                                                            | yes                     |
| `/var/www`                           | project checkouts; host directory set in `docker/docker-compose.override.yml`     | yes                     |
| compose volumes                      | mysql, postgresql, redis, apache sites, mail, certificates, `/var/lib/lamp` state | **no**                  |

- `.data` is mounted at `/etc/lamp` by `docker/docker-compose.data.yml`, which the cli loads only without a [data repository](#data-repository); `.config` is mounted at `/etc/lamp-config`; `/root/.ssh` links to `/etc/lamp/ssh`
- `.data` holds what several hosts can share, `.config` what belongs to this host; lamp writes only to `.config`
- `.data`, `.config` and `.logs` are excluded from git and from the image
- on `start` / `restart`, an available host `gh` login for `github.com` is copied into the container using `gh auth token` and [`gh auth login --with-token`](https://cli.github.com/manual/gh_auth_login). The token travels through stdin, never command arguments or logs, and is stored in the container's root-only GitHub CLI configuration. No additional login is needed; without host authentication, existing container credentials stay unchanged.
- back up `.data`, `.config` and database-consistent dumps separately; `docker-reset` is not an update that preserves environments
- initial mysql root / postgres password: `/var/lib/lamp/secrets/database-password` in the state volume, generated on first start; set your own before the first database initialization with `docker compose -f docker/docker-compose.yml run --rm --no-deps app bash -c 'umask 077; mkdir -p /var/lib/lamp/secrets; read -rsp "password: " p; printf "%s\n" "$p" > /var/lib/lamp/secrets/database-password'`

</details>

<details>

<summary>configuration</summary>

- `./lamp docker-setup` writes `.config/setup.yaml` (mode 600) with the answers for `domain` and `data`, and `.data/settings.yaml` (mode 600) with every optional section as a commented example

| key in `setup.yaml` | default  | effect                                                                                       |
| ------------------- | -------- | -------------------------------------------------------------------------------------------- |
| `domain`            | required | base domain of every environment; a change reapplies all environments on `start` / `restart` |
| `data`              | unset    | ssh url of the private [data repository](#data-repository) that replaces `.data`             |

| key in `settings.yaml`                 | default                             | effect                                                                                                                                                                          |
| -------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `git.name`, `git.email`                | unset                               | global git identity inside the container for commits made through `exec` or `ssh`                                                                                               |
| `git.commit_key`, `git.commit_url`, `git.commit_model`, `git.commit_effort`| unset                               | openai compatible endpoint of the `prepare-commit-msg` hook: `git commit` without a message (or with `-m .`) gets a generated one; the hook does nothing without key, url and model|
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

<summary>data repository</summary>

- `data: git@github.com:<owner>/lamp-data.git` in `.config/setup.yaml` replaces the `.data` folder by that private repository: `settings.yaml`, `ssh/`, `vpn/`, `build/`, `syncdb/` are the same on every host, only `.config` differs
- lamp keeps its own clone inside the container at `/var/lib/lamp/data` (state volume), `/etc/lamp` links to it; `docker-reset` deletes it with the rest of the state, the next `start` clones it again
- maintain the data in a normal clone anywhere (e.g. `/var/www/lamp-data`), commit and push; lamp never commits or pushes
- `./lamp docker-setup` asks for the repository, writes the entry, creates no `.data` and clones right away using the host's SSH configuration, keys and agent without prompting; the temporary host checkout is transferred into the container and deleted afterwards. Only if host access fails does it ask for a private ssh key (not echoed, not logged, handed to the container through a pipe and deleted after the clone)
- when the clone is missing later (after `docker-reset`, or when `data` was added to an existing installation by hand, which needs `./lamp stop` and `./lamp start`), the next `start`, `restart` or `cloudflare-setup` tries host SSH access before asking for a key again
- `start`, `restart`, `cloudflare-setup`, `build`, `syncdb` and `add` mirror the repository first (`git fetch` and `git reset --hard` with `ssh/id_rsa` of the clone), so a pushed build script is used by the next `./lamp build <id>` without a restart; when the fetch fails, a warning is printed and the last state is used
- after every clone and fetch the folder is set to owner-only modes, because git stores no private modes and ssh refuses readable keys
- the repository contains private keys, vpn profiles, api tokens and production database credentials; keep it private

</details>

<details>

<summary>environments</summary>

- `.config/env.yaml` is a list of the static environments, without ids; `start` / `restart` reconcile it, `add --subdomain` appends to it, `remove` deletes from it
- an entry and a running environment are the same when every value matches; changing any value in the file removes the old environment and provisions a new one; static directories and fixed databases stay
- dynamic environments (`add` without `--subdomain`) live in the runtime state only, are never written to the file and are never touched by `start` / `restart`; remove them with `lamp remove <id>` or all at once with `lamp reset`
- ids are runtime identifiers reported by `add`, `show` and `list`; `add --id` reuses one and is idempotent: identical settings return the existing environment, different settings fail; every command that takes an id also accepts a subdomain label of a static environment
- a file from the previous id-keyed format is converted on first use; its dynamic entries are dropped from the file, the environments themselves stay

- `./lamp docker-setup` writes an empty `.config/env.yaml` with a commented example and asks whether to add phpmyadmin; existing entries stay
- its final question optionally generates initial environments from a folder (default `/var/www`, immediate Git checkouts only); the folder must be mounted at the same path under `/var/www` in the container. Existing entries and checkouts stay; already registered directories are skipped. On an already configured installation, run `./lamp docker-setup` and then `start` or `restart` to use this step.
- generation reads each checkout's `origin`, sets branch `main`, private visibility, PHP from `.phprc` or `8.5`, and its existing directory; it does not switch checkout branches. Subdomains use lowercase folder names with non-DNS characters replaced by hyphens, trimming leading/trailing hyphens; collisions fail. Webroot is the first existing `_public`, `public`, `new`, `html/br-kk`, or `.`.
- database settings come from literal `syncdb <profile>` calls in the matching data build script (`engine` and `target.database`; SQLite uses the filename without extension). Without syncdb, `postgres` outside comments selects PostgreSQL with the normalized name; otherwise there is no database. Conflicting profiles fail. `nebro` gets VPN profile `nebro` only when enabled in settings. No build override, aliases or proxy settings are added.
- generation is implemented in Bash and uses the container's configured data folder. It registers all entries in one controller call and uses the existing reconciliation batch: two Access checks for the entire set, one connector check, one Apache reload and one PHP-FPM restart per used version. Build scripts are not executed. Pending registrations stay in `.config/initial-environments` and `.config/initial-environments.jsonl` until the batch succeeds; retries keep completed environments and apply only unfinished work. Build requests in older pending queues are ignored.
- optional phpmyadmin: `https://github.com/phpmyadmin/phpmyadmin.git` on branch `STABLE` as subdomain `phpmyadmin`, private; its initial clone uses `--depth 1 --single-branch --no-tags` to download only the selected branch tip, without the large history. Other repositories keep their full history. Uses `build/github.com-phpmyadmin-phpmyadmin.sh` from the configured data folder, without a build override

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
- existing directories are adopted without clone, pull, checkout, chown or build; missing static directories are cloned without building
- `remove` deletes the project directory only for dynamic environments; static directories always stay
- databases: a static environment gets exactly one fixed database `db_name` on `db_engine`, created when missing and never dropped or altered by lamp, reachable as `root` (mysql) or `postgres` (postgres) with the password from `/var/lib/lamp/secrets/database-password`; a `sqlite` database is the file `/var/lib/lamp/environments/<id>/data/<db_name>.sqlite`; dynamic environments get isolated `lamp_<id>` databases on mysql and postgresql with their own account, dropped on `remove`
- mysql and postgresql are published on `127.0.0.1:3306` and `127.0.0.1:5432` of the host for database tools; the old host services must be stopped
- the image ships one postgresql major version (currently 18) and refuses to start on data of another one; before pulling an image with a newer version, dump with `./lamp exec 'pg_dumpall -U postgres' > all.sql`, move `/var/lib/postgresql` aside inside the state volume, start, and restore with `./lamp exec 'psql -U postgres' < all.sql`
- hostnames: `<subdomain-or-id>.<domain>`; every hostname must be unique; no nested subdomains
- reconciliation: static entries are provisioned with hosts and configured databases, without running project build scripts or their imports; environments without a matching entry are removed. Changed scripts and previously failed builds do not trigger builds during start/restart. `[]` removes all static environments, an empty file is invalid; one pass does the access checks once at start and end, one apache reload and one php-fpm restart per version at the end
- `lamp branch <id> <branch>` switches the checkout and updates the entry's `branch` in the file, so the environment keeps matching
- failed environments keep status `failed` and are not served; `restart` reapplies static host configuration without retrying the build. Fix build errors and run `./lamp build <id|subdomain>` explicitly when wanted
- `docker-reset` deletes the runtime state: static environments are re-registered from the file on the next `start`, dynamic environments are gone and their directories under `/var/www/_environments/` become orphans
- `show`, `list` and `add` return json without passwords or build commands; `build <id>` and `syncdb <profile>` print their output live and end with one status line

</details>

<details>

<summary>build scripts</summary>

- `.data/build/<host>-<repository path with / replaced by ->.sh`, e.g. `github.com-owner-project.sh` for `git@github.com:owner/project.git` and `https://github.com/owner/project.git`
- sourced by bash with `set -e` in the checkout, the selected php first on `PATH`, node lts, the variables below and a `syncdb` function
- static environments (including phpMyAdmin) only run scripts on `./lamp build <id|subdomain>`, never during start/restart, initial registration, settings changes or script changes. New dynamic environments still build automatically when lamp creates their checkout; adopted dynamic directories only build on explicit request
- `lamp build` selects the registered environment containing the current directory (including subfolders); the closest project root wins. If no environment matches or the match is ambiguous, specify an id or subdomain.
- Apache lists files and subdirectories when a webroot has no index file (`Options +Indexes`), as in the legacy setup. Project `.htaccess` rules still apply.
- complete output goes to `/var/lib/lamp/environments/<id>/build.log` (mode 600); the path is printed at start and in the failure message, together with the environment hostname and id; `./lamp build <id>` shows it live between `🔨 … building` and `✅ … built in <n>s`
- no build runs without a script or `build` setting
- `./lamp docker-setup` writes the example `.data/build/github.com-owner-project.sh`: syncdb import, `.env` created from an embedded heredoc with `APP_URL` and `DB_*` rewritten from the setup variables, then composer and npm; copy it per project and keep only the steps the project has

| variable                                    | value                                                                                                                                                   |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LAMP_ID`                                   | environment id                                                                                                                                          |
| `LAMP_URL`, `APP_URL`                       | `https://<hostname>`                                                                                                                                    |
| `LAMP_PROJECT_DIR`                          | checkout path                                                                                                                                           |
| `LAMP_DATA_DIR`                             | persistent per-environment data directory                                                                                                               |
| `DB_CONNECTION`                             | static: `mysql`, `pgsql` or `sqlite` from `db_engine`, unset without database; dynamic: `mysql`, a sqlite `syncdb` or `db_engine postgres` switches it                          |
| `DB_HOST`, `DB_PORT`                        | `localhost`, `3306` (mysql) or `5432` (postgres)                                                                                                        |
| `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD` | static: `db_name` with `root` / `postgres` and the container database password, or the sqlite file path; dynamic: `lamp_<id>` with a generated password |
| `PGHOST`, `PGPORT`                          | `localhost`, `5432`; static only with `db_engine: postgres`                                                                                             |
| `PGDATABASE`, `PGUSER`, `PGPASSWORD`        | static: `db_name` / `postgres`; dynamic: `lamp_<id>` / generated password                                                                               |

- the same variables are set in the environment's vhost (`SetEnv`) and reach php-fpm as fastcgi parameters: `getenv('DB_DATABASE')` or `$_SERVER['DB_DATABASE']` in php, laravel's `env()` prefers them over `.env`; cli runs get them through `exec <id>` and `build`
- postgresql projects call `db_engine postgres` at the top of their build script: a dynamic environment then gets `DB_CONNECTION=pgsql` and `DB_PORT=5432` for the rest of the build, `exec` and the vhost (its isolated `lamp_<id>` exists in both servers); in a static environment the call only checks `db_engine` of `env.yaml`
- wordpress: `define('DB_NAME', getenv('DB_DATABASE'))`, `define('DB_USER', getenv('DB_USERNAME'))`, `define('DB_PASSWORD', getenv('DB_PASSWORD'))`, `define('DB_HOST', getenv('DB_HOST'))` in the local branch of `wp-config.php`, no generated files needed
- services: a build may write supervisor `[program:…]` sections to `$LAMP_DATA_DIR/supervisor.conf` (unique program names, absolute paths); lamp loads the file after every build and on container start, restarts changed programs and stops them when the environment is removed; combine with `proxy_port` to route the vhost to the service
- `./lamp audit` checks immediate Git checkouts in `/var/www` inside the running container. GitHub projects whose `origin` belongs to another owner (such as `phpmyadmin/phpmyadmin`) are excluded from the project count, checks, fetches and automatic pulls. Both public and private `vielhuber` repositories remain included; repositories without a recognized GitHub origin are still checked. It compares local checkouts with `gh repo list vielhuber` (excluding `setup` and the profile repository `vielhuber`, mapping `vielhuber.de` to `vielhuber`) and reports missing clones and folders without Git only when found. Hidden folders and Git worktrees are included; `_archive` is skipped for per-project checks. It reads static environments from `/etc/lamp-config/env.yaml` and reports `lamp missing` per project.
- audit clears an interactive terminal before printing its results; redirected output contains no clear-screen sequences. It fetches each upstream with a 10-second timeout, then runs `git pull --ff-only --no-rebase --no-autostash` only when the working tree has no tracked, staged or untracked changes and remote commits are available. It recalculates ahead/behind afterwards. Diverged histories and failed fetches/pulls remain flagged. Nothing is committed, pushed, cloned or built.
- audit also checks npm and Composer major updates, PHP `8.5` and Node `lts/*` version files (including matching WordPress themes), preserving the dependency-folder exclusions. npm checks use `ncu` against `package.json`, including uninstalled development dependencies; they never install packages or modify manifests. `vuejs-tutorial` skips only npm version checks because its lessons intentionally use older dependencies. Failed npm checks remain visible. GitHub/configuration failures remain visible, and failed LAMP checks never count as up to date. It uses the container tools and the GitHub authentication copied from the host on start/restart.
- later shells: `./lamp ssh` opens the host's current directory when it exists in the container; otherwise it keeps the container's default directory. With an environment argument it still opens that environment's project directory. To load its variables manually: `source /var/lib/lamp/environments/<id>/setup.env`, `cd "$LAMP_PROJECT_DIR"`
- from the host, run `lamp code` to open the current folder directly in the running LAMP container. The folder must exist at the same path inside the container. Alternatively, inside `lamp ssh`, run `code .` (or `code /path/to/project`) to open that folder in the host's VS Code attached to the running LAMP container. The host needs VS Code with `code` in PATH; On first use in the session, Dev Containers is checked silently and installed automatically only if missing. The session forwards folder requests through a private temporary directory in `.config`, without a network listener, and removes it on exit. Existing shells must reconnect once to enable this.
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
- `syncdb <profile>` inside a build or `exec <id>` copies the profile, replaces its complete `target` with the environment's database (static: the fixed `db_name` as root, or the sqlite file; dynamic: the isolated `lamp_<id>` or sqlite file), imports with php 8.5 in a temporary directory, deletes the copy; `source` stays unchanged; `replace` stays unchanged for static environments, in dynamic environments the hostnames of the static site become the environment's own: a profile `<project>-<source>-<target>` names its site `<project>.<any domain>` in the replace targets, those hostnames are rewritten in keys and targets to `<id>.<domain>`, alias hosts `<project>-<suffix>.<any domain>` to `<id>-<suffix>.<domain>`, all other hosts stay, so one profile works for the static site and for the chat environments of every instance; the profile engine must match `db_engine`
- mysql and sqlite only, no postgresql; imports use the scoped environment account, syncdb rewrites object definers to it
- a successful import exports `DB_CONNECTION` and `DB_DATABASE` into the running build and `setup.env`; do not call it in a subshell; postgres has no import, use `psql` with the `PG*` variables
- chat environments get `cache: 360` and `threads` (number of cores, at most 16) unless the profile sets them: the dump is fetched at most every six hours, kept in the state volume, and the tables are restored by several clients at once (syncdb 2.1.5); static environments import as the profile says
- every executed `syncdb` imports again; an unchanged start does not run the build at all
- syncdb `>= 2.1.3` resets object definers to the importing account and remaps `ALTER DATABASE` charset statements in routine dumps to the target database

</details>

<details>

<summary>cloudflare</summary>

<blockquote>

<details>

<summary>tunnel</summary>

- `cloudflare-setup` creates the locally managed tunnel `<domain>`, writes `/var/lib/lamp/cloudflare/cloudflared-credentials.json` (state volume) and points the proxied cname `*.<domain>` at it; a tunnel of that name without local credentials is deleted and recreated
- supervisor runs `cloudflared` inside the container; the wildcard ingress forwards `*.<domain>` to apache's `*:443` with the let's encrypt certificate (origin name `<domain>`), everything else gets 404; no container port is published
- certificate: on every `start` / `restart` lamp requests or renews a let's encrypt certificate for `<domain>` and `*.<domain>` through the cloudflare dns challenge with `cloudflare.token` (stored in the `certificates` volume, renewed daily by cron); a domain change requests a new one
- every environment hostname resolves to `127.0.0.1` inside the container (`/etc/hosts`), so builds, `critical`, headless browsers and `curl` inside the container reach the local origin directly with a valid certificate, without cloudflare access
- `start` runs the setup itself when the credentials are missing, e.g. after `docker-reset`: the tunnel is recreated and the service token rotated from `cloudflare.token`
- `domain` changes in `setup.yaml` reapply all environments on the next `start` / `restart`; run `cloudflare-setup` again for the new zone
- never run the same tunnel from two machines

</details>

<details>

<summary>access</summary>

- `cloudflare-setup` creates the self-hosted application `lamp <domain>` for `*.<domain>` (found by its wildcard destination, so several lamp instances with different domains share one account) with policy `developer` (allow `cloudflare.email`) and policy `harness` (service token `lamp`), and writes `/var/lib/lamp/cloudflare/cloudflare-service-token.yaml`; a rerun restores these settings if they were changed
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

- created by `cloudflare-setup` as `/var/lib/lamp/cloudflare/cloudflare-service-token.yaml`; if the file is lost, a rerun rotates the secret
- `./lamp curl <id> -- -sS -o /dev/null -w '%{http_code}' https://<hostname>/` (supported options: output, request, header, data, form, timeouts, user agent, cookies, upload, write-out, `-sSfILG`)
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
- `add`, `build`, `remove` and `reset` of different environments run side by side: clone, import and build of one environment hold no shared lock, only state, vhost, databases and cloudflare changes are serialized; two commands for the same environment wait for each other; `start`, `restart`, `stop` wait until running environment work has finished
- keep environments until their files and databases have been reviewed; archiving a chat does not require `remove`
- run `lamp` on the docker host, directly or over ssh (`/usr/local/bin/lamp` for restricted paths); the harness needs no docker inside its own container

</details>

<details>

<summary>vpn</summary>

- the `vpn` section of `.data/settings.yaml` lists tunnels with `name`, `type` (`openvpn` or `wireguard`), `config`, optional `username` / `password`, `routes` and `hosts`; `./lamp docker-setup` leaves a commented example in the file

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
- `start` and `restart` print status lines and live Git clone progress (object transfer, resolving deltas and checkout) on the terminal; other command output is in that log. Static project build scripts do not run. Data repository clones also show progress.
- `./lamp exec 'supervisorctl status'`; php errors in `/var/log/php-error.log`; xdebug profiles in `/tmp/xdebug` (`?XDEBUG_PROFILE=1` or `XDEBUG_PROFILE=1 php …`)
- vpn client logs in `/var/log/supervisor/vpn-<name>.log`
- project build logs in `/var/lib/lamp/environments/<id>/build.log`
- shutdown waits up to seven minutes for ordered service groups; postgresql uses fast shutdown

</details>

<details>

<summary>verification</summary>

- `bash -n lamp` and `bash -n docker/scripts/initial-environments.sh`
- `for script in docker/docker-build.sh docker/docker-entrypoint.sh docker/docker-start.sh docker/scripts/healthcheck.sh; do bash -n "$script"; done`
- `docker compose -f docker/docker-compose.yml config --quiet`
- `python3 -m unittest discover -s docker/tests`
- the container copies `docker/scripts/` to `/opt/lamp/` at image build time; changed scripts need `./lamp docker-build` or a `docker compose cp` into the running container

</details>

</blockquote>

</details>
