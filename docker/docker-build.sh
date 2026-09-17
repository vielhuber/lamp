#!/usr/bin/env bash
set -euo pipefail

# README instructions are retained below as comments; only unprefixed commands run.
# Oracle, docker-osx and Windows/WSL administration are intentionally excluded.
# Build this script in Docker; never execute it on the host.
test "${LAMP_IMAGE_BUILD:-}" = 1
test "$(dpkg --print-architecture)" = amd64
export DEBIAN_FRONTEND=noninteractive
export COMPOSER_ALLOW_SUPERUSER=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export NVM_DIR=/root/.nvm
export PATH="/root/.local/bin:/root/.cargo/bin:/root/.opencode/bin:$PATH"
php_versions=(5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5)
build_directory=$(mktemp -d /tmp/lamp-build.XXXXXXXX)
cd "$build_directory"
# Third-party download servers fail sporadically (HTTP 5xx, resets); retry every curl of this build, and only of this build.
export CURL_HOME=$build_directory
printf 'retry = 5\nretry-delay = 10\nretry-all-errors\n' > "$build_directory/.curlrc"
step_number=0
step_total=$(grep -c "^step '" "${BASH_SOURCE[0]}")
step_started=$SECONDS
step_title='initialization'
step() {
    if (( step_number > 0 )); then
        printf '✅ [%s/%s] Completed in %ss\n' "$step_number" "$step_total" "$((SECONDS - step_started))"
    fi
    ((++step_number))
    step_title=$1
    step_started=$SECONDS
    printf '\n\n%s\nℹ️ [%s/%s] %s\n%s\n' \
        'ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️' \
        "$step_number" "$step_total" "$step_title" \
        'ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️ℹ️'
}
trap 'if [[ $? = 0 ]]; then printf "✅ [%s/%s] Completed in %ss; total build script: %ss\n" "$step_number" "$step_total" "$((SECONDS - step_started))" "$SECONDS"; fi; rm -rf -- "$build_directory"' EXIT
trap 'printf "❌ [%s/%s] %s failed at docker-build.sh line %s after %ss\n" "$step_number" "$step_total" "$step_title" "$LINENO" "$((SECONDS - step_started))" >&2' ERR

step 'base packages and container preparation'

# Keep downloads in the external BuildKit cache, not in image layers.
mv /etc/apt/apt.conf.d/docker-clean "$build_directory/docker-clean"
printf 'APT::Keep-Downloaded-Packages "true";\n' > /etc/apt/apt.conf.d/lamp-build-cache

# Package post-install scripts must not start services while building the image.
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod 755 /usr/sbin/policy-rc.d
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl wget gnupg locales tzdata \
    sudo jq supervisor cron rsyslog procps psmisc openssl bash-completion \
    git build-essential gcc-14 g++-14 cmake autoconf automake libtool nasm pkg-config \
    libssl-dev zlib1g-dev libyaml-dev libreadline-dev libffi-dev libbz2-dev \
    libsqlite3-dev liblzma-dev libgdbm-dev libncurses-dev xz-utils bzip2 \
    fontconfig libpng-dev libfontconfig1 libxrender1 xfonts-75dpi xfonts-base
# Kernel logs belong to the host and are not accessible inside this container.
sed -i '/^module(load="imklog"/s/^/# /' /etc/rsyslog.conf
locale-gen en_US.UTF-8
ln -snf /usr/share/zoneinfo/Europe/Berlin /etc/localtime
printf 'Europe/Berlin\n' > /etc/timezone
mkdir -p /opt/lamp /var/lib/lamp /var/www /run/php /run/mysqld /run/postgresql

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-173 — original instructions (documentation, not executed)
# #### install basic linux packages
#
# - `sudo apt-get install nano curl sshpass zip unzip htop ruby libnotify-bin net-tools pv csh cifs-utils apt-utils software-properties-common iputils-ping gettext sshpass lsof yq time parallel ripgrep`

#### install basic linux packages
step 'install basic linux packages'
apt-get install -y --no-install-recommends nano sshpass zip unzip htop ruby \
    libnotify-bin net-tools pv csh cifs-utils apt-utils software-properties-common \
    iputils-ping gettext lsof yq time parallel ripgrep tmux rsync plocate

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-37 — original instructions (documentation, not executed)
# ##### public (cloudflare)
#
# - `curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
# echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared jammy main' | sudo tee /etc/apt/sources.list.d/cloudflared.list`
# - `sudo apt-get update`
# - `sudo apt-get install cloudflared`
# - `rm ~/.cloudflared/cert.pem` (logout from other sessions)
# - `cloudflared tunnel login`
# - `cloudflared tunnel create TUNNEL`
# - `cloudflared tunnel route dns --overwrite-dns TUNNEL example.com`
# - `cloudflared tunnel route dns --overwrite-dns TUNNEL *.example.com`
#   // admin interface is on cloudflare.com > Zero Trust > Networks > Tunnels
# - manual start
#     - `cloudflared tunnel run --url http://localhost:80 TUNNEL`
# - automatic start
#     - `cloudflared tunnel list`
#     - `sudo mkdir -p /etc/cloudflared`
#     - `sudo nano /etc/cloudflared/config.yml`
#
# ```yaml
# tunnel: TUNNEL-UUID
# credentials-file: /root/.cloudflared/TUNNEL-UUID.json
# originRequest:
#     originServerName: example.dev
# ingress:
#     - service: https://localhost:443
# ```
#
# - `sudo cloudflared service install`
# - `sudo systemctl enable --now cloudflared`
# - `sudo systemctl status cloudflared`

#### public (cloudflare)
step 'public (cloudflare)'
# Use the vendor's distribution-independent repository instead of jammy.
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' > /etc/apt/sources.list.d/cloudflared.list
apt-get update
apt-get install -y cloudflared
mkdir -p /etc/cloudflared
# Tunnel login, DNS changes and credentials are runtime/operator actions.

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-167 — original instructions (documentation, not executed)
# #### prevent password prompt for sudo commands
#
# - `sudo visudo`
# - comment out `%sudo ALL=(ALL:ALL) ALL`
# - `%sudo ALL=(ALL:ALL) NOPASSWD:ALL`

#### prevent password prompt for sudo commands
step 'prevent password prompt for sudo commands'
# The image runs as root; no passwordless sudo rule is necessary.

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-177 — original instructions (documentation, not executed)
# #### disable nginx
#
# - `sudo systemctl disable nginx`
# - `sudo systemctl disable --now nginx`

#### disable nginx
step 'disable nginx'
# nginx is not installed. Apache is the only web server.

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### redis
#
# - `sudo apt-get -y install redis-server`
# - `sudo systemctl start redis-server`
# - `sudo systemctl enable redis-server`
# - `redis-cli`
# - `sudo systemctl restart redis.service`
# - `redis-cli ping`

#### redis
step 'redis'
apt-get install -y redis-server
# Keep local-only access; applications in this all-in-one container use localhost.
sed -i 's/^daemonize .*/daemonize no/; s/^supervised .*/supervised no/; s|^logfile .*|logfile ""|' /etc/redis/redis.conf

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-280 — original instructions (documentation, not executed)
# #### merge .bashrc/.bash_profile
#
# - `nano ~/.bash_profile`
#
# ```
# if [ -f ~/.bashrc ]; then
#     source ~/.bashrc
# fi
# ```

#### merge .bashrc/.bash_profile
step 'merge .bashrc/.bash_profile'
cat > /root/.bash_profile <<'PROFILE'
if [ -f ~/.bashrc ]; then
    source ~/.bashrc
fi
PROFILE

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### ngrok
#
# ```
# curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc \
#   | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null \
#   && echo "deb https://ngrok-agent.s3.amazonaws.com buster main" \
#   | sudo tee /etc/apt/sources.list.d/ngrok.list \
#   && sudo apt update \
#   && sudo apt install ngrok
# ```
#
# - `ngrok help`
#
# - single domain:
#     - `ngrok config add-authtoken AUTH_TOKEN`
#     - `ngrok http 8080 --url DEV_DOMAIN`
# - multiple domains/ports:
#     - `ngrok config edit`
#
# ```
# - tunnels:
#   api1:
#     addr: 8080
#     schemes:
#       - https
#     proto: http
#   api2:
#     addr: 8931
#     schemes:
#       - https
#     proto: http
# ```
#
# - `ngrok start --all`
#
# - open the dev domains once and accept the button once

#### ngrok
step 'ngrok'
curl -fsSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc -o /etc/apt/trusted.gpg.d/ngrok.asc
printf '%s\n' 'deb https://ngrok-agent.s3.amazonaws.com buster main' > /etc/apt/sources.list.d/ngrok.list
apt-get update
apt-get install -y ngrok
# The vendor still names its cross-distribution repository buster.
# Authentication and tunnel startup remain explicit runtime actions.

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-327 — original instructions (documentation, not executed)
# #### nvidia cuda
#
# - Alles von hier ausführen: https://developer.nvidia.com/cuda-downloads?target_os=Linux&target_arch=x86_64&Distribution=WSL-Ubuntu&target_version=2.0&target_type=deb_local
# - `sudo nano ~/.bashrc`
# - `# cuda`
# - `export PATH=/usr/local/cuda/bin${PATH:+:${PATH}}`
# - `export LD_LIBRARY_PATH=/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}`
# - `source ~/.bashrc`
# - `nvidia-smi`
# - `nvcc --version`

#### nvidia cuda
step 'nvidia cuda (disabled)'
# Disabled to reduce image build time.
# Retain CUDA 12.8 for the existing Pascal GPU; CUDA 13 drops that GPU family.
# Install toolkit only, never a Linux GPU driver inside the container.
# CUDA 12.8 does not officially target Ubuntu 26; GPU execution needs a host runtime.
# curl -fsSL https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb -o cuda-keyring.deb
# dpkg -i cuda-keyring.deb
# apt-get update
# apt-get install -y cuda-toolkit-12-8
# cat >> /root/.bashrc <<'CUDA'
# # cuda
# export PATH=/usr/local/cuda/bin${PATH:+:${PATH}}
# export LD_LIBRARY_PATH=/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}
# export NVCC_CCBIN=/usr/bin/g++-14
# CUDA

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-344 — original instructions (documentation, not executed)
# #### smartgit
#
# - `cd /usr/local`
# - `rm -rf ./smartgit`
# - `wget https://downloads.syntevo.com/downloads/smartgit/smartgit-linux-24_1_2.tar.gz`
# - `tar xzf smartgit-linux-*.tar.gz`
# - `rm smartgit-linux-*.tar.gz`
# - `nano /usr/local/bin/sgit`
# - `( /usr/local/smartgit/bin/smartgit.sh . & ) > /dev/null 2>&1`
# - `chmod +x /usr/local/bin/sgit`
# - `sgit`
# - Register existing license: /var/www/lamp/syntevo-non-commercial.lic
# - User Name: Your Name
# - Email: you@example.com
# - Use SmartGit as SSH client
# - Style: Working tree (file oriented)
# - Window > Repositories bis Output (alles anklicken/anzeigen)
# - Edit > Preferences > Allow modifying pushed commits (e.g. forced-push)
# - Edit > Preferences > User Interface > Dark (independent of system)
# - Edit > Preferences > User Interface > On start-up: Don't reopen the last used repositories
# - Edit > Preferences > User Interface > Built-in Text Editors > Font Size: 9
# - Edit > Preferences > Git Config > Fetch and Pull > Rebase local branch onto fetched changes
# - Pro Repository: AI Button > ChatGPT API Key hinterlegen
# - Repository > Search for Repositories > /var/www
# - Manuelles Umbenennen falscher Namen ("www - ...", gtbabel 3x)
# - Optional: Alle Repositories: Rechte Maustaste: Mark as favorite (dies erhöht Performance durch Background Refresh)
# - Wenn non-commercial Lizenz abläuft:
#     - https://www.syntevo.com/register-non-commercial/ > register with github
#     - alternative: rm -rf ~/.config/smartgit/ > download/install/use v21
# - Wenn es Probleme mit GTK gibt:
#     - `nano ~/.config/smartgit/smartgit.vmoptions`, `swtver=4932` hinzufügen
# - Falls Updateprozess innerhalb des Programms scheitert:
#     - Einfach neue tar.gz downloaden, entzippen (und bestehende Dateien überschreiben)

#### smartgit
step 'smartgit'
curl -fsSL https://downloads.syntevo.com/downloads/smartgit/smartgit-linux-24_1_2.tar.gz -o smartgit.tar.gz
tar -xzf smartgit.tar.gz -C /usr/local
printf '#!/usr/bin/env bash\n(/usr/local/smartgit/bin/smartgit.sh . "$@" &) >/dev/null 2>&1\n' > /usr/local/bin/sgit
chmod 755 /usr/local/bin/sgit
# Graphical use needs a display socket supplied by the host. Do not bake a license in.

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-378 — original instructions (documentation, not executed)
# #### hide intro text
#
# - `touch ~/.hushlogin`

#### hide intro text
step 'hide intro text'
touch /root/.hushlogin

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-382 — original instructions (documentation, not executed)
# #### pimp command line
#
# - `sudo nano ~/.bashrc`
#
# ```
# # colorize and show git branch name
# alias ls='ls --color'
# LS_COLORS='di=1:fi=0:ln=31:pi=5:so=5:bd=5:cd=5:or=31:mi=0:ex=35:*.rpm=90'
# export LS_COLORS
# parse_git_branch() { git branch 2> /dev/null | sed -e '/^[^*]/d' -e 's/* \(.*\)/(\1)/'; }
# parse_git_tag() { git describe --exact-match --tags 2> /dev/null | sed -e 's/\(.*\)/[\1]/'; }
# PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u\[\033[00m\]@\[\033[01;31m\]\h\[\033[00m\]~\[\e[0;36m\]\t\[\033[00m\]~\[\033[01;34m\]\w\[\033[01;31m\]$(parse_git_branch)\[\033[01;33m\]$(parse_git_tag)\[\033[00m\]\$ '
# ```
#
# - `source ~/.bashrc`

#### pimp command line
step 'pimp command line'
cat >> /root/.bashrc <<'PROMPT'
# colorize and show git branch name
alias ls='ls --color'
LS_COLORS='di=1:fi=0:ln=31:pi=5:so=5:bd=5:cd=5:or=31:mi=0:ex=35:*.rpm=90'
export LS_COLORS
parse_git_branch() { git branch 2> /dev/null | sed -e '/^[^*]/d' -e 's/* \(.*\)/(\1)/'; }
parse_git_tag() { git describe --exact-match --tags 2> /dev/null | sed -e 's/\(.*\)/[\1]/'; }
PS1='${debian_chroot:+($debian_chroot)}\[\033[01;32m\]\u\[\033[00m\]@\[\033[01;31m\]\h\[\033[00m\]~\[\e[0;36m\]\t\[\033[00m\]~\[\033[01;34m\]\w\[\033[01;31m\]$(parse_git_branch)\[\033[01;33m\]$(parse_git_tag)\[\033[00m\]\$ '
PROMPT

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### apache/php/mysql
#
# - `sudo apt-get install apache2 mysql-server`
# - `sudo systemctl start apache2`
# - `sudo systemctl start mysql`
# - `sudo systemctl enable apache2`
# - `sudo systemctl enable mysql`
# - run mysql_secure_installation
#     - the following steps fixes this error: https://www.digitalocean.com/community/tutorials/how-to-install-mysql-on-ubuntu-22-04#step-2-configuring-mysql
#     - `sudo mysql`
#     - `ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'root';`
#     - `exit`
#     - `sudo mysql_secure_installation`
#         - Validate password plugin: n
#         - mysql-root-Passwort: root
#         - Remove anonymous users: y
#         - Disallow root login remotely: n
#         - Remove test database: y
#         - Reload privilege tables: y
#     - `mysql -u root -p`
#     - `ALTER USER 'root'@'localhost' IDENTIFIED WITH auth_socket;`
#     - `exit`
# - allow login to root user without sudo
#     - `sudo mysql -u root`
#     - `DROP USER 'root'@'localhost';`
#     - `CREATE USER 'root'@'%' IDENTIFIED BY '';`
#     - `GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;`
#     - `FLUSH PRIVILEGES;`
#     - `EXIT`
#     - `mysql -u root`
#     - `SET PASSWORD FOR root = 'root';`
#     - `EXIT`

#### apache/php/mysql
step 'apache/php/mysql'
# Suppress package-time database creation; initialize the mounted directory at startup.
printf 'mysql-server mysql-server/data-dir select /var/lib/mysql\n' | debconf-set-selections
apt-get install -y apache2 mysql-server
# Docker's default security profile does not permit NUMA memory policy changes.
printf '[mysqld]\ninnodb_numa_interleave=OFF\n' > /etc/mysql/conf.d/lamp-container.cnf
# Only package-created data exists here; no host data is mounted during the build.
find /var/lib/mysql -mindepth 1 -delete

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### add ppa of officially maintained php versions
#
# - `sudo add-apt-repository ppa:ondrej/php`
# - `sudo apt-get update`

#### add ppa of officially maintained php versions
step 'add ppa of officially maintained php versions'
# The Ondrej PPA has no resolute suite. Use the same maintainer's Sury repository.
curl -fsSL https://packages.sury.org/debsuryorg-archive-keyring.deb -o sury-keyring.deb
dpkg -i sury-keyring.deb
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/debsuryorg-archive-keyring.gpg] https://packages.sury.org/php/ resolute main' > /etc/apt/sources.list.d/php.list
apt-get update

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### php extensions
#
# - `sudo apt-get install -y php5.6 php5.6-fpm libapache2-mod-php5.6 php5.6-mysql php5.6-cli php5.6-common php5.6-xdebug php5.6-apcu php5.6-mbstring php5.6-xmlrpc php5.6-gd php5.6-intl php5.6-xml php5.6-mysql php5.6-mcrypt php5.6-zip php5.6-soap php5.6-curl php5.6-bcmath php5.6-xml php5.6-sqlite php5.6-imap php5.6-opcache php5.6-pgsql php5.6-pdo php5.6-gd php5.6-imagick`
# - `sudo apt-get install -y php7.0 php7.0-fpm libapache2-mod-php7.0 php7.0-mysql php7.0-cli php7.0-common php7.0-xdebug php7.0-apcu php7.0-mbstring php7.0-xmlrpc php7.0-gd php7.0-intl php7.0-xml php7.0-mysql php7.0-mcrypt php7.0-zip php7.0-soap php7.0-curl php7.0-bcmath php7.0-xml php7.0-sqlite php7.0-imap php7.0-opcache php7.0-pgsql php7.0-pdo php7.0-gd php7.0-imagick`
# - `sudo apt-get install -y php7.1 php7.1-fpm libapache2-mod-php7.1 php7.1-mysql php7.1-cli php7.1-common php7.1-xdebug php7.1-apcu php7.1-mbstring php7.1-xmlrpc php7.1-gd php7.1-intl php7.1-xml php7.1-mysql php7.1-uopz php7.1-mcrypt php7.1-zip php7.1-soap php7.1-curl php7.1-bcmath php7.1-xml php7.1-sqlite php7.1-imap php7.1-opcache php7.1-pgsql php7.1-pdo php7.1-gd php7.1-imagick`
# - `sudo apt-get install -y php7.2 php7.2-fpm libapache2-mod-php7.2 php7.2-mysql php7.2-cli php7.2-common php7.2-xdebug php7.2-apcu php7.2-mbstring php7.2-xmlrpc php7.2-gd php7.2-intl php7.2-xml php7.2-mysql php7.2-uopz php7.2-zip php7.2-soap php7.2-curl php7.2-bcmath php7.2-xml php7.2-sqlite php7.2-imap php7.2-opcache php7.2-pgsql php7.2-pdo php7.2-gd php7.2-imagick`
# - `sudo apt-get install -y php7.3 php7.3-fpm libapache2-mod-php7.3 php7.3-mysql php7.3-cli php7.3-common php7.3-xdebug php7.3-apcu php7.3-mbstring php7.3-xmlrpc php7.3-gd php7.3-intl php7.3-xml php7.3-mysql php7.3-uopz php7.3-zip php7.3-soap php7.3-curl php7.3-bcmath php7.3-xml php7.3-sqlite php7.3-imap php7.3-opcache php7.3-pgsql php7.3-pdo php7.3-gd php7.3-imagick`
# - `sudo apt-get install -y php7.4 php7.4-fpm libapache2-mod-php7.4 php7.4-mysql php7.4-cli php7.4-common php7.4-xdebug php7.4-apcu php7.4-mbstring php7.4-xmlrpc php7.4-gd php7.4-intl php7.4-xml php7.4-mysql php7.4-uopz php7.4-zip php7.4-soap php7.4-curl php7.4-bcmath php7.4-xml php7.4-sqlite php7.4-imap php7.4-opcache php7.4-pgsql php7.4-pdo php7.4-gd php7.4-imagick`
# - `sudo apt-get install -y php8.0 php8.0-fpm libapache2-mod-php8.0 php8.0-mysql php8.0-cli php8.0-common php8.0-xdebug php8.0-apcu php8.0-mbstring php8.0-xmlrpc php8.0-gd php8.0-intl php8.0-xml php8.0-mysql php8.0-uopz php8.0-zip php8.0-soap php8.0-curl php8.0-bcmath php8.0-xml php8.0-sqlite php8.0-imap php8.0-opcache php8.0-pgsql php8.0-pdo php8.0-gd php8.0-imagick`
# - `sudo apt-get install -y php8.1 php8.1-fpm libapache2-mod-php8.1 php8.1-mysql php8.1-cli php8.1-common php8.1-xdebug php8.1-apcu php8.1-mbstring php8.1-xmlrpc php8.1-gd php8.1-intl php8.1-xml php8.1-mysql php8.1-uopz php8.1-zip php8.1-soap php8.1-curl php8.1-bcmath php8.1-xml php8.1-sqlite php8.1-imap php8.1-opcache php8.1-pgsql php8.1-pdo php8.1-gd php8.1-imagick`
# - `sudo apt-get install -y php8.2 php8.2-fpm libapache2-mod-php8.2 php8.2-mysql php8.2-cli php8.2-common php8.2-xdebug php8.2-apcu php8.2-mbstring php8.2-xmlrpc php8.2-gd php8.2-intl php8.2-xml php8.2-mysql php8.2-uopz php8.2-zip php8.2-soap php8.2-curl php8.2-bcmath php8.2-xml php8.2-sqlite php8.2-imap php8.2-opcache php8.2-pgsql php8.2-pdo php8.2-gd php8.2-imagick`
# - `sudo apt-get install -y php8.3 php8.3-fpm libapache2-mod-php8.3 php8.3-mysql php8.3-cli php8.3-common php8.3-xdebug php8.3-apcu php8.3-mbstring php8.3-xmlrpc php8.3-gd php8.3-intl php8.3-xml php8.3-mysql php8.3-uopz php8.3-zip php8.3-soap php8.3-curl php8.3-bcmath php8.3-xml php8.3-sqlite php8.3-imap php8.3-opcache php8.3-pgsql php8.3-pdo php8.3-gd php8.3-imagick`
# - `sudo apt-get install -y php8.4 php8.4-fpm libapache2-mod-php8.4 php8.4-mysql php8.4-cli php8.4-common php8.4-xdebug php8.4-apcu php8.4-mbstring php8.4-xmlrpc php8.4-gd php8.4-intl php8.4-xml php8.4-mysql php8.4-uopz php8.4-zip php8.4-soap php8.4-curl php8.4-bcmath php8.4-xml php8.4-sqlite php8.4-imap php8.4-opcache php8.4-pgsql php8.4-pdo php8.4-gd php8.4-imagick`
# - `sudo apt-get install -y php8.5 php8.5-fpm libapache2-mod-php8.5 php8.5-mysql php8.5-cli php8.5-common php8.5-xdebug php8.5-apcu php8.5-mbstring php8.5-xmlrpc php8.5-gd php8.5-intl php8.5-xml php8.5-mysql php8.5-uopz php8.5-zip php8.5-soap php8.5-curl php8.5-bcmath php8.5-xml php8.5-sqlite php8.5-imap php8.5-pgsql php8.5-pdo php8.5-gd php8.5-imagick`
# - note: extensions must not be uncommented in php.ini but installed on the command line

#### php extensions
step 'php extensions'
# sqlite3 is the concrete package; PDO is included by the version's common package.
# Duplicate package names in the README are installed once.
for version in "${php_versions[@]}"; do
    packages=("php$version-fpm")
    for extension in mysql cli common xdebug apcu mbstring xmlrpc gd intl xml zip soap curl bcmath sqlite3 imap pgsql imagick; do
        packages+=("php$version-$extension")
    done
    if [[ "$version" != 8.5 ]]; then
        packages+=("php$version-opcache")
    fi
    if [[ "$version" = 5.6 || "$version" = 7.0 || "$version" = 7.1 ]]; then
        packages+=("php$version-mcrypt")
    fi
    if [[ "$version" = 7.[1-4] || "$version" = 8.5 ]]; then
        packages+=("php$version-uopz")
    fi
    if [[ "$version" = 8.[0-4] ]]; then
        packages+=("php$version-dev")
    fi
    apt-get install -y "${packages[@]}"
done
# PHP 8.5 includes OPcache; php8.5-uopz is supplied by Ubuntu's universe repository.
# Sury has no uopz binaries for PHP 8.0–8.4 on resolute; compile the upstream extension.
curl -fsSL https://github.com/krakjoe/uopz/archive/14c8fc2d6eff14ec9acd926b9cab85d6961c64ac.tar.gz -o uopz.tar.gz
for version in 8.0 8.1 8.2 8.3 8.4; do
    mkdir "uopz-$version"
    tar -xzf uopz.tar.gz --strip-components=1 -C "uopz-$version"
    (
        cd "uopz-$version"
        "phpize$version"
        CC=gcc-14 ./configure --enable-uopz --with-php-config="/usr/bin/php-config$version"
        make -j"$(nproc)"
        make install
    )
    printf 'extension=uopz.so\n' > "/etc/php/$version/mods-available/uopz.ini"
    "php$version" -n -d extension=uopz.so --ri uopz
done

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### uopz: only enable temporarily
#
# - `sudo phpdismod uopz`
# - start: `sudo phpenmod -v 8.X uopz && sudo systemctl restart phpXXX-fpm`
# - stop: `sudo phpdismod -v 8.X uopz && sudo systemctl restart phpXXX-fpm`

#### uopz: only enable temporarily
step 'uopz: only enable temporarily'
phpdismod uopz

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### apache extensions
#
# - `sudo a2enmod rewrite`
# - `sudo a2enmod ssl`
# - `sudo a2enmod vhost_alias`
# - `sudo a2enmod authz_groupfile`
# - `sudo a2enmod headers`
# - `sudo a2enmod cache`
# - `sudo a2enmod expires`
# - `sudo a2enmod actions`
# - `sudo a2enmod alias`
# - `sudo a2enmod proxy_fcgi`
# - `sudo a2enmod proxy`
# - `sudo a2enmod proxy_html`
# - `sudo a2enmod proxy_http`
# - `sudo a2enmod xml2enc`
# - `sudo systemctl restart apache2`

#### apache extensions
step 'apache extensions'
a2enmod rewrite ssl vhost_alias authz_groupfile headers cache expires actions alias \
    proxy_fcgi proxy proxy_html proxy_http xml2enc

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### configs
#
# - setup with presets from [dbf3d6844b3e6159d6b7](https://gist.github.com/vielhuber/dbf3d6844b3e6159d6b7)
# - `sudo nano /etc/mysql/mysql.conf.d/mysqld.cnf`
# - `sudo nano /etc/apache2/apache2.conf`
# - `sudo nano /etc/php/X.X/fpm/pool.d/www.conf` (important❗)
# - ~`sudo nano /etc/php/X.X/apache2/php.ini`~ (not needed, see below)
# - ~`sudo nano /etc/php/X.X/cli/php.ini`~ (not needed, see below)

#### configs
step 'configs'
# Presets: https://gist.github.com/vielhuber/dbf3d6844b3e6159d6b7
cat > /etc/mysql/mysql.conf.d/zz-lamp.cnf <<'MYSQL'
[mysqld]
bind-address = ::
skip-name-resolve
innodb_flush_log_at_trx_commit = 2
innodb_buffer_pool_size = 1G
# MySQL 8.4 replaces two 1G redo log files with an explicit total capacity.
innodb_redo_log_capacity = 2G
innodb_log_buffer_size = 64M
innodb_write_io_threads = 16
max_allowed_packet = 1G
key_buffer_size = 128M
innodb_strict_mode = 0
log_bin_trust_function_creators = 1
# Required by the legacy PHP clients retained in this image.
mysql_native_password = ON
MYSQL
printf 'Timeout 3000\nServerAdmin webmaster@localhost\nServerName localhost\n' > /etc/apache2/conf-available/lamp.conf
a2enconf lamp
for version in "${php_versions[@]}"; do
    children=40
    idle=60s
    if [[ "$version" = 5.6 || "$version" = 7.* || "$version" = 8.0 ]]; then
        children=10
        idle=10s
    fi
    sed -i -E "s/^pm = .*/pm = ondemand/; s/^pm.max_children = .*/pm.max_children = $children/; s/^;?pm.process_idle_timeout = .*/pm.process_idle_timeout = $idle/; s/^;?pm.max_requests = .*/pm.max_requests = 500/; s/^pm.start_servers = .*/pm.start_servers = 15/; s/^pm.min_spare_servers = .*/pm.min_spare_servers = 15/; s/^pm.max_spare_servers = .*/pm.max_spare_servers = 25/" "/etc/php/$version/fpm/pool.d/www.conf"
done

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### setup default page
#
# - `sudo a2dissite 000-default.conf`
# - `nano /etc/apache2/sites-available/000-blank.conf`
#
# ```
# <VirtualHost *:80>
#   DocumentRoot /var/www
#   RewriteEngine On
#   RewriteCond %{HTTP_HOST} \.example\.com$ [NC,OR]
#   RewriteCond %{HTTP_HOST} ^example\.com$ [NC]
#   RewriteRule ^ - [F,L]
#   <Directory /var/www>
#     Options +Indexes
#     AllowOverride None
#     Require all granted
#   </Directory>
# </VirtualHost>
# <VirtualHost *:443>
#   DocumentRoot /var/www
#   RewriteEngine On
#   RewriteCond %{HTTP_HOST} \.example\.com$ [NC,OR]
#   RewriteCond %{HTTP_HOST} ^example\.com$ [NC]
#   RewriteRule ^ - [F,L]
#   <Directory /var/www>
#     Options +Indexes
#     AllowOverride None
#     Require all granted
#   </Directory>
#   SSLEngine on
#   SSLCertificateFile /etc/letsencrypt/live/example.dev/fullchain.pem
#   SSLCertificateKeyFile /etc/letsencrypt/live/example.dev/privkey.pem
# </VirtualHost>
# ```
#
# - `sudo a2ensite 000-blank.conf`
# - `sudo systemctl reload apache2`
# - test https://foo.example.dev / http://192.168.0.2

#### setup default page
step 'setup default page'
a2dissite 000-default.conf
# The controller creates a deny-by-default VHost and explicit per-environment FPM handlers.

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### ssl
#
# - `sudo apt install certbot python3-certbot-dns-cloudflare`
# - `pip install --upgrade pyOpenSSL cryptography certbot certbot-dns-cloudflare`
# - `mkdir -p ~/.secrets/certbot`
# - `nano ~/.secrets/certbot/cloudflare.ini`
# - `dns_cloudflare_api_token = YOUR_CLOUDFLARE_API_TOKEN_WITH_EDIT_ZONE_DNS_PERMISSIONS`
# - `chmod 600 ~/.secrets/certbot/cloudflare.ini`
# - `certbot certonly --dns-cloudflare --dns-cloudflare-credentials ~/.secrets/certbot/cloudflare.ini -d '*.example.dev' -d example.dev --agree-tos --email you@example.com --dns-cloudflare-propagation-seconds 60 --non-interactive`
# - `certbot renew --dry-run`
# - `sudo mv /etc/cron.d/certbot /etc/cron.d/certbot.disabled`
# - `export VISUAL=nano; crontab -e`
# - `0 12 * * * certbot renew --quiet`

#### ssl
step 'ssl'
apt-get install -y certbot python3-certbot-dns-cloudflare
# Ubuntu's managed Python packages replace the README's conflicting system pip upgrade.
rm -f /etc/cron.d/certbot
printf '0 12 * * * root certbot renew --quiet --deploy-hook "apachectl configtest && supervisorctl signal USR1 apache2"\n' > /etc/cron.d/lamp-certbot

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-538 — original instructions (documentation, not executed)
# #### powershell
#
# - `sudo apt-get update`
# - `sudo apt-get install -y wget apt-transport-https software-properties-common`
# - `source /etc/os-release`
# - `wget -q https://packages.microsoft.com/config/ubuntu/$VERSION_ID/packages-microsoft-prod.deb`
# - `sudo dpkg -i packages-microsoft-prod.deb`
# - `rm packages-microsoft-prod.deb`
# - `sudo apt-get update`
# - `sudo apt-get install -y powershell`
# - `pwsh`

#### powershell
step 'powershell'
# The resolute Microsoft repository does not publish PowerShell; use its universal release package.
curl -fsSL https://github.com/PowerShell/PowerShell/releases/download/v7.6.6/powershell_7.6.6-1.deb_amd64.deb -o powershell.deb
printf '%s  powershell.deb\n' '9585f38ab5a026c3fc0995486e26e12050777960fef47a22dca98b577c5d27a7' | sha256sum -c -
apt-get install -y ./powershell.deb

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### postfix
#
# - `sudo apt-get install postfix`
#     - General type: "Internet Site"
#     - System mail name: "example.com"
# - `sudo apt install mailutils`
# - `sudo dpkg-reconfigure postfix`
#     - General type: "Internet Site"
#     - System mail name: "example.com"
#     - Root mail recipient: OK
#     - Other destinations: OK
#     - Force synchronous updates: NO
#     - Local networks: OK
#     - Mailbox size limit: OK
#     - Local address extension: OK
#     - Internet protocols: "ipv4"
# - `sudo nano /etc/postfix/main.cf`
#
# ```
#     myhostname = example.com
#     mydestination =
#     relayhost = [sslout.df.eu]:587
#     smtp_sasl_auth_enable = yes
#     smtp_sasl_security_options = noanonymous
#     smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd
#     smtp_use_tls = yes
#     smtp_tls_CAfile = /etc/ssl/certs/ca-certificates.crt
# ```
#
# - `sudo nano /etc/postfix/sasl_passwd`
# - SMTP credentials: supply a private runtime file; never include credentials in the image.
# - `sudo postmap /etc/postfix/sasl_passwd`
# - `sudo systemctl restart postfix`
# - `sudo systemctl restart rsyslog`
# - `sudo systemctl enable postfix`
# - `sudo systemctl enable rsyslog`
# - `echo "Das ist ein Test" | mail -s "Test bestanden" -a "From: you@example.com" you@example.com`
# - `sudo nano /etc/php/custom.ini`
#     - `sendmail_path = "/usr/sbin/sendmail -t -i"`

#### postfix
step 'postfix'
printf 'postfix postfix/mailname string lamp.localdomain\npostfix postfix/main_mailer_type select Internet Site\n' | debconf-set-selections
apt-get install -y postfix mailutils libsasl2-modules
postconf -e 'myhostname = lamp.localdomain' 'mydestination =' 'relayhost = [sslout.df.eu]:587' \
    'smtp_sasl_auth_enable = yes' 'smtp_sasl_security_options = noanonymous' \
    'smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd' 'smtp_tls_security_level = may' \
    'smtp_tls_CAfile = /etc/ssl/certs/ca-certificates.crt' 'inet_protocols = ipv4' \
    'inet_interfaces = loopback-only'
# Supply /etc/postfix/sasl_passwd at runtime. No test mail is sent automatically.

# README.md#native-database-reference — original instructions (documentation, not executed)
# #### mydumper
#
# - `wget https://github.com/mydumper/mydumper/releases/download/v0.21.3-1/mydumper_0.21.3-1.noble_amd64.deb`
# - `sudo dpkg -i mydumper_*.deb`
# - `rm mydumper_*.deb`
# - `mydumper --version`

#### mydumper
step 'mydumper'
curl -fsSL https://github.com/mydumper/mydumper/releases/download/v0.21.3-1/mydumper_0.21.3-1.noble_amd64.deb -o mydumper.deb
apt-get install -y ./mydumper.deb
# This pinned release only provides a noble binary; dependency resolution must succeed.

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-597 — original instructions (documentation, not executed)
# #### ncdu
#
# - `sudo apt-get install ncdu`
# - `cd /`
# - `ncdu --exclude /mnt`

#### ncdu
step 'ncdu'
apt-get install -y ncdu

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### node + php + python auto version switching on cd
#
# - `nano ~/.bashrc`
# - https://gist.github.com/vielhuber/021453a7e908f9487917835107ad6ce7
# - `source ~/.bashrc`
# - now place `.nvmrc` / `.phprc` with the version (e.g. `12.10.0` / `8.1`) in the folder, where your `package.json` / `composer.json` lays and `.envrc` with `venv` (or your environment name)

#### node + php + python auto version switching on cd
step 'node + php + python auto version switching on cd'
# Resolve relative virtualenv paths from their .envrc, including nested working directories.
curl -fsSL https://gist.githubusercontent.com/vielhuber/021453a7e908f9487917835107ad6ce7/raw/.bashrc -o /opt/lamp/auto-switch.sh
sed -i 's|source "$(cat "$envrc")/bin/activate"|source "$(cd -- "$(dirname -- "$envrc")" \&\& realpath -- "$(cat "$envrc")")/bin/activate"|' /opt/lamp/auto-switch.sh
printf '\nsource /opt/lamp/auto-switch.sh\n' >> /root/.bashrc

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### php error logging
#
# - `touch /var/log/php-error.log`
# - `chmod +x /var/log/php-error.log`
# - in combination with `error_log` in php.ini logging now works for both php fpm (this is always the case for specific versions) and php as an apache module (this is always the case for general version)

#### php error logging
step 'php error logging'
touch /var/log/php-error.log
chmod 666 /var/log/php-error.log
# Logging needs write permission, not the README's executable bit.

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### create xdebug profile output dir
#
# - `mkdir -p /tmp/xdebug`

#### create xdebug profile output dir
step 'create xdebug profile output dir'
mkdir -p /tmp/xdebug
chmod 1777 /tmp/xdebug

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### shared php.ini configuration
#
# - `sudo nano /etc/php/custom.ini`
#
# ```
# user_ini.filename =
#
# max_execution_time = 4800
# max_input_time = 900
# post_max_size = 800M
# memory_limit = 4096M
# upload_max_filesize = 800M
# max_input_vars = 100000
# max_file_uploads = 5000
# realpath_cache_size = 4M
# ;allow_url_include = On
# ;allow_url_fopen = On
# date.timezone = 'Europe/Berlin'
# display_errors = On
# error_log = /var/log/php-error.log
# ;error_reporting = E_ALL & ~E_NOTICE
# error_reporting = E_ALL
# phar.readonly = 0
# upload_tmp_dir = '/tmp'
#
# [opcache]
# opcache.enable=1
# opcache.enable_cli=0
# opcache.memory_consumption=512
# opcache.interned_strings_buffer=64
# opcache.max_accelerated_files=32531
# opcache.save_comments=1
# opcache.fast_shutdown=0
# opcache.max_file_size=0
# ; we set this to 1 so that we can set revalidate_freq on a project basis to a higher value
# opcache.validate_timestamps=1
# opcache.revalidate_freq=2
#
# [apcu]
# apc.enabled=1
# apc.enable_cli=1
# apc.shm_size=256M
# apc.ttl=900
# apc.gc_ttl=900
#
# [xdebug]
# ; mode (see: https://xdebug.org/docs/all_settings#mode)
# ;   reasonable default
# xdebug.mode=debug,profile
# ;   disabled
# ;xdebug.mode=off
# ;   step debugging
# ;xdebug.mode=debug
# ;   performance profiling (be aware of load/space)
# ;xdebug.mode=profile
# ;   trace profiling (record args)
# ;xdebug.mode=trace
#
# ; starting mode
# ;   always (not recommended)
# ;xdebug.start_with_request=yes
# ;   only when specific get parameters / cookies are set
# ;   (?XDEBUG_TRIGGER=1, ?XDEBUG_PROFILE=1, ?XDEBUG_TRACE=1, ?XDEBUG_SESSION=1)
# ;   this is best in conjunction with Chrome extension "Xdebug helper"
# xdebug.start_with_request=trigger
# ;   folder for analyzing profile dumps
# xdebug.output_dir="/tmp/xdebug"
# ;   not needed, since it is already in /etc/php/7.4/fpm/conf.d/20-xdebug.ini
# ;zend_extension=xdebug.so
# ```
#
# - `ln -s /etc/php/custom.ini /etc/php/5.6/apache2/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.0/apache2/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.1/apache2/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.2/apache2/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.3/apache2/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.4/apache2/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.0/apache2/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.1/apache2/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.2/apache2/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.3/apache2/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.4/apache2/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.5/apache2/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/5.6/fpm/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.0/fpm/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.1/fpm/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.2/fpm/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.3/fpm/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.4/fpm/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.0/fpm/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.1/fpm/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.2/fpm/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.3/fpm/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.4/fpm/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.5/fpm/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/5.6/cli/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.0/cli/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.1/cli/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.2/cli/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.3/cli/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/7.4/cli/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.0/cli/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.1/cli/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.2/cli/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.3/cli/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.4/cli/conf.d/custom.ini`
# - `ln -s /etc/php/custom.ini /etc/php/8.5/cli/conf.d/custom.ini`

#### shared php.ini configuration
step 'shared php.ini configuration'
# The complete original configuration is inserted here from the README.
cat > /etc/php/custom.ini <<'PHPINI'
user_ini.filename =
variables_order = EGPCS

max_execution_time = 4800
max_input_time = 900
post_max_size = 800M
memory_limit = 4096M
upload_max_filesize = 800M
max_input_vars = 100000
max_file_uploads = 5000
realpath_cache_size = 4M
;allow_url_include = On
;allow_url_fopen = On
date.timezone = 'Europe/Berlin'
display_errors = On
error_log = /var/log/php-error.log
;error_reporting = E_ALL & ~E_NOTICE
error_reporting = E_ALL
phar.readonly = 0
upload_tmp_dir = '/tmp'

[opcache]
opcache.enable=1
opcache.enable_cli=0
; JIT cannot run alongside the installed execution-hook extensions.
opcache.jit=off
opcache.jit_buffer_size=0
opcache.memory_consumption=512
opcache.interned_strings_buffer=64
opcache.max_accelerated_files=32531
opcache.save_comments=1
opcache.fast_shutdown=0
opcache.max_file_size=0
; we set this to 1 so that we can set revalidate_freq on a project basis to a higher value
opcache.validate_timestamps=1
opcache.revalidate_freq=2

[apcu]
apc.enabled=1
apc.enable_cli=1
apc.shm_size=256M
apc.ttl=900
apc.gc_ttl=900

[xdebug]
; mode (see: https://xdebug.org/docs/all_settings#mode)
;   reasonable default
xdebug.mode=debug,profile
;   disabled
;xdebug.mode=off
;   step debugging
;xdebug.mode=debug
;   performance profiling (be aware of load/space)
;xdebug.mode=profile
;   trace profiling (record args)
;xdebug.mode=trace

; starting mode
;   always (not recommended)
;xdebug.start_with_request=yes
;   only when specific get parameters / cookies are set
;   (?XDEBUG_TRIGGER=1, ?XDEBUG_PROFILE=1, ?XDEBUG_TRACE=1, ?XDEBUG_SESSION=1)
;   this is best in conjunction with Chrome extension "Xdebug helper"
xdebug.start_with_request=trigger
;   folder for analyzing profile dumps
xdebug.output_dir="/tmp/xdebug"
;   not needed, since it is already in /etc/php/7.4/fpm/conf.d/20-xdebug.ini
;zend_extension=xdebug.so
PHPINI
printf '\nsendmail_path = "/usr/sbin/sendmail -t -i"\n' >> /etc/php/custom.ini
for version in "${php_versions[@]}"; do
    for sapi in fpm cli; do
        ln -s /etc/php/custom.ini "/etc/php/$version/$sapi/conf.d/custom.ini"
    done
    if [[ "$version" = 5.6 || "$version" = 7.0 || "$version" = 7.1 ]]; then
        cat > "/etc/php/$version/mods-available/lamp-xdebug.ini" <<'XDEBUG'
; Xdebug 2 does not understand the shared Xdebug 3 mode/trigger settings.
xdebug.remote_enable=1
xdebug.remote_autostart=0
xdebug.remote_port=9003
xdebug.profiler_enable=0
xdebug.profiler_enable_trigger=1
xdebug.profiler_output_dir=/tmp/xdebug
XDEBUG
        phpenmod -v "$version" lamp-xdebug
    fi
done

# README.md#native-server-reference — original instructions (documentation, not executed)
# #### local environment permissions
#
# - reset
#     - `chown -R root:root /var/www`
#     - `chmod 00755 /var`
#     - `chmod 00755 /var/www`
#     - `find /var/www -type d -exec chmod 00755 {} \;`
#     - `find /var/www -type f -exec chmod 00644 {} \;`
# - run php as root
#     - `nano /etc/php/5.6/fpm/pool.d/www.conf`
#     - `nano /etc/php/7.0/fpm/pool.d/www.conf`
#     - `nano /etc/php/7.1/fpm/pool.d/www.conf`
#     - `nano /etc/php/7.2/fpm/pool.d/www.conf`
#     - `nano /etc/php/7.3/fpm/pool.d/www.conf`
#     - `nano /etc/php/7.4/fpm/pool.d/www.conf`
#     - `nano /etc/php/8.0/fpm/pool.d/www.conf`
#     - `nano /etc/php/8.1/fpm/pool.d/www.conf`
#     - `nano /etc/php/8.2/fpm/pool.d/www.conf`
#     - `nano /etc/php/8.3/fpm/pool.d/www.conf`
#     - `nano /etc/php/8.4/fpm/pool.d/www.conf`
#     - `nano /etc/php/8.5/fpm/pool.d/www.conf`
#         - **be aware: comment out with ";" instead of "#" :)**
#             - `user = root`
#             - `group = root`
#     - `nano /etc/init.d/php5.6-fpm`
#     - `nano /etc/init.d/php7.0-fpm`
#     - `nano /etc/init.d/php7.1-fpm`
#     - `nano /etc/init.d/php7.2-fpm`
#     - `nano /etc/init.d/php7.3-fpm`
#     - `nano /etc/init.d/php7.4-fpm`
#     - `nano /etc/init.d/php8.0-fpm`
#     - `nano /etc/init.d/php8.1-fpm`
#     - `nano /etc/init.d/php8.2-fpm`
#     - `nano /etc/init.d/php8.3-fpm`
#     - `nano /etc/init.d/php8.4-fpm`
#     - `nano /etc/init.d/php8.5-fpm`
#         - `DAEMON_ARGS="-R --daemonize --fpm-config $CONFFILE"`
#     - `sudo systemctl edit php5.6-fpm`
#     - `sudo systemctl edit php7.0-fpm`
#     - `sudo systemctl edit php7.1-fpm`
#     - `sudo systemctl edit php7.2-fpm`
#     - `sudo systemctl edit php7.3-fpm`
#     - `sudo systemctl edit php7.4-fpm`
#     - `sudo systemctl edit php8.0-fpm`
#     - `sudo systemctl edit php8.1-fpm`
#     - `sudo systemctl edit php8.2-fpm`
#     - `sudo systemctl edit php8.3-fpm`
#     - `sudo systemctl edit php8.4-fpm`
#     - `sudo systemctl edit php8.5-fpm`
#         - Oberhalb einfügen:
#         - `[Service]`
#         - `ExecStart=`
#         - `ExecStart=/usr/sbin/php-fpm5.6 -R --nodaemonize --fpm-config /etc/php/5.6/fpm/php-fpm.conf`
#         - `[Service]`
#         - `ExecStart=`
#         - `ExecStart=/usr/sbin/php-fpm7.0 -R --nodaemonize --fpm-config /etc/php/7.0/fpm/php-fpm.conf`
#         - `[Service]`
#         - `ExecStart=`
#         - `ExecStart=/usr/sbin/php-fpm7.1 -R --nodaemonize --fpm-config /etc/php/7.1/fpm/php-fpm.conf`
#         - `[Service]`
#         - `ExecStart=`
#         - `ExecStart=/usr/sbin/php-fpm7.2 -R --nodaemonize --fpm-config /etc/php/7.2/fpm/php-fpm.conf`
#         - `[Service]`
#         - `ExecStart=`
#         - `ExecStart=/usr/sbin/php-fpm7.3 -R --nodaemonize --fpm-config /etc/php/7.3/fpm/php-fpm.conf`
#         - `[Service]`
#         - `ExecStart=`
#         - `ExecStart=/usr/sbin/php-fpm7.4 -R --nodaemonize --fpm-config /etc/php/7.4/fpm/php-fpm.conf`
#         - `[Service]`
#         - `ExecStart=`
#         - `ExecStart=/usr/sbin/php-fpm8.0 -R --nodaemonize --fpm-config /etc/php/8.0/fpm/php-fpm.conf`
#         - `[Service]`
#         - `ExecStart=`
#         - `ExecStart=/usr/sbin/php-fpm8.1 -R --nodaemonize --fpm-config /etc/php/8.1/fpm/php-fpm.conf`
#         - `[Service]`
#         - `ExecStart=`
#         - `ExecStart=/usr/sbin/php-fpm8.2 -R --nodaemonize --fpm-config /etc/php/8.2/fpm/php-fpm.conf`
#         - `[Service]`
#         - `ExecStart=`
#         - `ExecStart=/usr/sbin/php-fpm8.3 -R --nodaemonize --fpm-config /etc/php/8.3/fpm/php-fpm.conf`
#         - `[Service]`
#         - `ExecStart=`
#         - `ExecStart=/usr/sbin/php-fpm8.4 -R --nodaemonize --fpm-config /etc/php/8.4/fpm/php-fpm.conf`
#         - `[Service]`
#         - `ExecStart=`
#         - `ExecStart=/usr/sbin/php-fpm8.5 -R --nodaemonize --fpm-config /etc/php/8.5/fpm/php-fpm.conf`
#     - `systemctl restart php5.6-fpm`
#     - `systemctl restart php7.0-fpm`
#     - `systemctl restart php7.1-fpm`
#     - `systemctl restart php7.2-fpm`
#     - `systemctl restart php7.3-fpm`
#     - `systemctl restart php7.4-fpm`
#     - `systemctl restart php8.0-fpm`
#     - `systemctl restart php8.1-fpm`
#     - `systemctl restart php8.2-fpm`
#     - `systemctl restart php8.3-fpm`
#     - `systemctl restart php8.4-fpm`
#     - `systemctl restart php8.5-fpm`
#     - `systemctl enable php5.6-fpm`
#     - `systemctl enable php7.0-fpm`
#     - `systemctl enable php7.1-fpm`
#     - `systemctl enable php7.2-fpm`
#     - `systemctl enable php7.3-fpm`
#     - `systemctl enable php7.4-fpm`
#     - `systemctl enable php8.0-fpm`
#     - `systemctl enable php8.1-fpm`
#     - `systemctl enable php8.2-fpm`
#     - `systemctl enable php8.3-fpm`
#     - `systemctl enable php8.4-fpm`
#     - `systemctl enable php8.5-fpm`

#### local environment permissions
step 'local environment permissions'
# Preserve the development-only root FPM pools, but never recursively chmod host projects.
for version in "${php_versions[@]}"; do
    sed -i 's/^user = .*/user = root/; s/^group = .*/group = root/' "/etc/php/$version/fpm/pool.d/www.conf"
done
# Supervisor launches FPM with -R; systemd and SysV overrides are unnecessary.

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-847 — original instructions (documentation, not executed)
# #### fix font errors
#
# - if fonts are garbled: `sudo fc-cache -f -v`

#### fix font errors
step 'fix font errors'
fc-cache -f

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-898 — original instructions (documentation, not executed)
# #### enable cron
#
# - `sudo systemctl enable cron`

#### enable cron
step 'enable cron'
# cron runs in the foreground under Supervisor; no systemd enable operation.


#### sync bills
step 'sync bills'
# Personal network cronjobs are retained as examples, never activated in a shared image.


#### make backups
step 'make backups'
# Host-specific backup paths must be mounted and scheduled explicitly by the operator.

# README.md#native-language-reference — original instructions (documentation, not executed)
# #### composer
#
# - `sudo php -r "copy('https://getcomposer.org/installer', 'composer-setup.php');"`
# - `sudo php composer-setup.php`
# - `sudo php -r "unlink('composer-setup.php');"`
# - `sudo mv composer.phar /usr/local/bin/composer`
# - hide sudo message:
#     - `sudo nano ~/.bashrc`
#     - `# hide composer sudo message`
#     - `export COMPOSER_ALLOW_SUPERUSER=1`
#     - `source ~/.bashrc`
# - `composer self-update`
# - `composer --version` # 2
# - Composer authentication: configure credentials at runtime.
# - Install `composer upgrade-all`
#     - `composer global require vildanbina/composer-upgrader`

#### composer
step 'composer'
curl -fsSL https://getcomposer.org/installer -o composer-setup.php
composer_checksum=$(curl -fsSL https://composer.github.io/installer.sig)
printf '%s  composer-setup.php\n' "$composer_checksum" | sha384sum -c -
php8.5 composer-setup.php --install-dir=/usr/local/bin --filename=composer
printf '\n# hide composer sudo message\nexport COMPOSER_ALLOW_SUPERUSER=1\n' >> /root/.bashrc
php8.5 /usr/local/bin/composer global config --no-plugins allow-plugins.vildanbina/composer-upgrader true
php8.5 /usr/local/bin/composer global require --no-interaction vildanbina/composer-upgrader

# README.md#native-language-reference — original instructions (documentation, not executed)
# #### node / npm
#
# - nvm
#     - `sudo curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash`
#     - restart terminal
#     - `nvm --version`
#     - `nvm ls`
#     - install/upgrade new/specific node versions
#         - `nvm install node`
#         - `nvm install --lts`
#         - `nvm install 16.17.0`
#         - `nvm install 14.18.0`
#         - `nvm install 12.10.0`
#         - `nvm install 10.16.3`
#         - `nvm alias default 16.17.0`
#         - `nvm use 16.17.0`
#     - install/upgrade to latest npm version (do this for every installed node version)
#         - `nvm install-latest-npm && nvm install --latest-npm`
#     - upgrade lts
#         - `nvm install --lts --reinstall-packages-from=current`
#     - Cache leeren (falls sich package-lock.json ändert: `npm cache verify` bzw `npm cache clean -f`)
# - nativ (obsolet)
#     - https://nodejs.org/en/download/package-manager/#debian-and-ubuntu-based-linux-distributions
#     - `curl -sL https://deb.nodesource.com/setup_12.x | sudo -E bash -`
#     - `sudo apt-get install -y nodejs`
#     - `sudo apt-get install -y build-essential`
# - prevent permission errors / download errors
#     - method 1:
#         - `npm cache verify`
#         - `npm cache clean -force`
#         - `rm -rf node_modules`
#         - `rm package-lock.json`
#     - method 2 (not working):
#         - `nano ~/.npmrc`
#
# ```
# #registry=http://registry.npmjs.org/
# #strict-ssl=false
# #unsafe-perm=true
# ```
#
# - install ncu
#     - `npm install -g npm-check-updates`
#     - `sudo nano ~/.bashrc`
#     - `# npm-check-updates`
#     - `alias ncu='"$(npm prefix -g)/bin/ncu" --retry 0 --timeout 5000'`
#     
# - login npm
#     - `export BROWSER=none && npm login`

#### node / npm
step 'node / npm'
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh -o nvm-install.sh
bash nvm-install.sh
# nvm and rvm use unset variables internally; keep strict mode outside these subshells.
(
    set +u
    source /root/.nvm/nvm.sh
    for version in node --lts; do
        nvm install "$version"
        nvm install-latest-npm
    done
    nvm use --lts
    nvm alias default "$(nvm current)"
    npm install -g npm-check-updates gulp-cli svgo @google/clasp corepack @openai/codex
    corepack enable
    corepack prepare yarn@stable --activate
    ln -s "$(dirname "$(command -v node)")" /opt/lamp/node-tools
)
export PATH="/opt/lamp/node-tools:$PATH"
ln -s /opt/lamp/node-tools/node /usr/local/bin/node
ln -s /opt/lamp/node-tools/npm /usr/local/bin/npm
ln -s /opt/lamp/node-tools/npx /usr/local/bin/npx
for tool in ncu svgo clasp corepack codex yarn yarnpkg; do
    printf '#!/usr/bin/env bash\nexec /opt/lamp/node-tools/node /opt/lamp/node-tools/%s "$@"\n' "$tool" > "/usr/local/bin/$tool"
    chmod 755 "/usr/local/bin/$tool"
done
cat >> /root/.bashrc <<'NODE'
# global tools stay available when selecting a different project runtime
export PATH="$PATH:/opt/lamp/node-tools"
# npm-check-updates
alias ncu='/usr/local/bin/ncu --retry 0 --timeout 5000'
NODE

# README.md#native-language-reference — original instructions (documentation, not executed)
# #### yarn
#
# - `corepack enable`
# - `corepack prepare yarn@stable --activate`
# - `yarn --version`

#### yarn
step 'yarn'
# Installed with corepack on the modern Node LTS above.

# README.md#native-language-reference — original instructions (documentation, not executed)
# #### python
#
# - install python 3.X
#     - `sudo apt-get update`
#     - `sudo apt-get install python3 python3-pip python3-venv`
#     - `python3 --version`
#     - `pip3 --version`
# - install python 2.X
#     - `sudo apt-get install python2`
#     - `python2 --version`
# - change default version
#     - `cd /usr/bin`
#     - `sudo rm python`
#     - `ln -s ./python3 ./python`

#### python
step 'python'
apt-get install -y python3 python3-pip python3-venv python-is-python3
# Python 2 is intentionally omitted; python resolves to Python 3.

# README.md#native-language-reference — original instructions (documentation, not executed)
# #### rust
#
# - `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
# - Proceed with standard installation
# - `exec bash -l`
# - `rustc --version`

#### rust
step 'rust'
curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs -o rustup-install.sh
sh rustup-install.sh -y --profile minimal --component rustfmt,clippy

# README.md#native-language-reference — original instructions (documentation, not executed)
# #### go
#
# - `sudo apt-get install golang`
# - `go version`

#### go
step 'go'
apt-get install -y golang

# README.md#native-language-reference — original instructions (documentation, not executed)
# #### gettext
#
# - `sudo apt-get install gettext`
# - `msgfmt --help`

#### gettext
step 'gettext'
# Installed with the basic packages.

# README.md#native-language-reference — original instructions (documentation, not executed)
# #### gulp
#
# - `npm install --global gulp-cli`

#### gulp
step 'gulp'
# Installed with the modern Node LTS above.

# README.md#native-git-reference — original instructions (documentation, not executed)
# #### git
#
# - `sudo add-apt-repository ppa:git-core/ppa -y`
# - `sudo apt-get update`
# - `sudo apt-get install git -y`
# - `git --version`
# - `git config --global core.ignorecase false`
# - `git config --global core.filemode false`
# - `git config --global core.autocrlf input` # this converts everything to lf on commit, which is ok when using wsl2 (however, there are projects where you want it to be the default value of `false`, set that with `git config core.autocrlf false`)
# - `git config --global core.safecrlf false`
# - `git config --global push.default simple`
# - `git config --global user.name "Your Name"`
# - `git config --global user.email "you@example.com"`
# - `git config --global pull.rebase true` # fast-forward if possible, otherwise rebase
# - `git config --global branch.main.rebase false` # on main: fast-forward if possible, otherwise merge
# - `git config --global core.mergeoptions --no-edit` # prevent editor on merge
# - `git config --global init.defaultBranch main`
# - `git config set advice.skippedCherryPicks false`
# - further do this (--no-edit does sometimes not work):
#     - `sudo nano ~/.bashrc`
#     - `# git`
#     - `export GIT_MERGE_AUTOEDIT=no`
# - node 10 hangs (https://stackoverflow.com/questions/45433130/npm-install-gets-stuck-at-fetchmetadata/72391698#72391698)
#     - `sudo nano ~/.gitconfig`
#     - `[url "https://"]`
#     - `   insteadOf = git://`
# - sign commits/tags with ssh key
#     - `git config --global gpg.format ssh`
#     - `git config --global user.signingkey ~/.ssh/id_rsa.pub`
#     - `git config --global commit.gpgsign true`
#     - `git config --global tag.gpgsign true`
#     - `git config --global push.gpgsign true`
#     - GitHub > Settings > SSH and GPG keys > New SSH key > Key type: Signing Key + id_rsa.pub
# - commit hooks
#     - `git config --global core.hooksPath ~/git-template/hooks`
#     - ai
#         - `nano ~/git-template/hooks/prepare-commit-msg`
#         - Script von https://vielhuber.de/blog/git-commit-messages-mit-chatgpt/
#         - `chmod +x ~/git-template/hooks/prepare-commit-msg`
#     - filesize
#         - `cp /var/www/lamp/docker/git-hooks/pre-commit ~/git-template/hooks/pre-commit`
#         - `chmod +x ~/git-template/hooks/pre-commit`
#     - prevent merge into feature
#         - `cp /var/www/lamp/docker/git-hooks/pre-merge-commit ~/git-template/hooks/pre-merge-commit`
#         - `chmod +x ~/git-template/hooks/pre-merge-commit`
#     - prevent rebase into main
#         - `cp /var/www/lamp/docker/git-hooks/pre-rebase ~/git-template/hooks/pre-rebase`
#         - `chmod +x ~/git-template/hooks/pre-rebase`

#### git
step 'git'
# Ubuntu 26's Git replaces the older host's additional git-core PPA.
apt-get install -y git
git config --global core.ignorecase false
git config --global core.filemode false
git config --global core.autocrlf input
git config --global core.safecrlf false
git config --global push.default simple
git config --global pull.rebase true
git config --global branch.main.rebase false
git config --global core.mergeoptions --no-edit
git config --global init.defaultBranch main
git config --global advice.skippedCherryPicks false
git config --global url.https://.insteadOf git://
git config --global gpg.format ssh
git config --global user.signingkey /root/.ssh/id_rsa.pub
git config --global commit.gpgsign true
git config --global tag.gpgsign true
git config --global push.gpgsign true
git config --global core.hooksPath /root/git-template/hooks
mkdir -p /root/git-template/hooks
cp /opt/lamp/git-hooks/* /root/git-template/hooks/
chmod 755 /root/git-template/hooks/*
printf '\n# git\nexport GIT_MERGE_AUTOEDIT=no\n' >> /root/.bashrc
# Signing keys and the optional AI commit-message hook are supplied at runtime.

# README.md#native-git-reference — original instructions (documentation, not executed)
# #### gh (github command line)
#
# - `curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg`
# - `echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null`
# - `sudo apt update`
# - `sudo apt install gh`

#### gh (github command line)
step 'gh (github command line)'
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg
printf '%s\n' 'deb [arch=amd64 signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main' > /etc/apt/sources.list.d/github-cli.list
apt-get update
apt-get install -y gh

# README.md#native-git-reference — original instructions (documentation, not executed)
# #### git-filter-repo
#
# - `sudo nano ~/.bashrc`
# - `# git-filter-repo`
# - `export PATH="$PATH:${HOME}/.git-filter-repo"`
# - `source ~/.bashrc`
# - `mkdir -p ~/.git-filter-repo`
# - `wget -O ~/.git-filter-repo/git-filter-repo https://raw.githubusercontent.com/newren/git-filter-repo/main/git-filter-repo`
# - `chmod +x ~/.git-filter-repo/git-filter-repo`

#### git-filter-repo
step 'git-filter-repo'
mkdir -p /root/.git-filter-repo
curl -fsSL https://raw.githubusercontent.com/newren/git-filter-repo/main/git-filter-repo -o /root/.git-filter-repo/git-filter-repo
chmod 755 /root/.git-filter-repo/git-filter-repo
printf '\nexport PATH="$PATH:/root/.git-filter-repo"\n' >> /root/.bashrc

# README.md#native-git-reference — original instructions (documentation, not executed)
# #### bash completion
#
# - `sudo apt install git bash-completion`
# - `nano ~/.bashrc`
#
# ```
# if [ -f /etc/bash_completion ] && ! shopt -oq posix; then
#   source /etc/bash_completion
# fi
# ```

#### bash completion
step 'bash completion'
cat >> /root/.bashrc <<'COMPLETION'
if [ -f /etc/bash_completion ] && ! shopt -oq posix; then
    source /etc/bash_completion
fi
COMPLETION

# README.md#native-git-reference — original instructions (documentation, not executed)
# #### subversion (svn)
#
# - `sudo apt-get install subversion`
# - `svn --version`

#### subversion (svn)
step 'subversion (svn)'
apt-get install -y subversion

# README.md#native-git-reference — original instructions (documentation, not executed)
# #### lamp repo
#
# - in this repo we store our ssl certificate, our ssh keys and all current active symlinks
# - `mkdir /var/www/lamp`
# - `cd /var/www/lamp`
# - `git clone git@bitbucket.org:lamp-xyz-git/lamp.git . --config core.autocrlf=false`
# - `chmod +x /var/www/lamp/lamp`
# - `sudo visudo`
#     - add `/var/www/lamp` to `Defaults  secure_path`
# - `sudo nano ~/.bashrc`
# - `# lamp`
# - `export PATH="$PATH:/var/www/lamp"`
# - `source ~/.bashrc`

#### lamp repo
step 'lamp repo'
# The Dockerfile copies the controller and hooks, never the native CLI, keys or profiles.
# The controller is copied after this build so its edits retain the toolchain cache.

# README.md#native-git-reference — original instructions (documentation, not executed)
# #### ssh client
#
# - `mkdir ~/.ssh`
# - `cp /var/www/lamp/id_rsa ~/.ssh/id_rsa`
# - `cp /var/www/lamp/id_rsa.pub ~/.ssh/id_rsa.pub`
# - `cp /var/www/lamp/id_rsa_4096 ~/.ssh/id_rsa_4096`
# - `cp /var/www/lamp/id_rsa_4096.pub ~/.ssh/id_rsa_4096.pub`
# - `chmod 600 ~/.ssh/id_rsa`
# - `chmod 600 ~/.ssh/id_rsa.pub`
# - `chmod 600 ~/.ssh/id_rsa_4096`
# - `chmod 600 ~/.ssh/id_rsa_4096.pub`

#### ssh client
step 'ssh client'
apt-get install -y openssh-client
install -d -m 700 /root/.ssh
# Mount personal SSH credentials at runtime instead of copying repository key files.

# README.md#native-git-reference — original instructions (documentation, not executed)
# #### ssh server
#
# - `apt update`
# - `apt install -y openssh-server`
# - `systemctl enable --now ssh.socket`
# - `chmod 600 /root/.ssh/authorized_keys`
# - use own key to authenticate: `cat ~/.ssh/id_rsa.pub >> /root/.ssh/authorized_keys`

#### ssh server
step 'ssh server'
apt-get install -y openssh-server
# Generate distinct host keys on the persistent runtime volume, not in the image.
rm -f /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub
printf 'PasswordAuthentication no\nPermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/lamp.conf

# README.md#native-database-reference — original instructions (documentation, not executed)
# #### syncdb
#
# - `mkdir ~/.syncdb`
# - `cd ~/.syncdb`
# - `composer require vielhuber/syncdb`
# - `chmod +x vendor/bin/syncdb`
# - `ln -s /var/www/lamp/syncdb ~/.syncdb/profiles`
# - `sudo nano ~/.bashrc`
# - `# syncdb`
# - `export PATH="$PATH:/root/.syncdb/vendor/bin"`
# - `source ~/.bashrc `

#### syncdb
step 'syncdb'
apt-get install -y sqlite3
mkdir -p /root/.syncdb
php8.5 /usr/local/bin/composer --working-dir=/root/.syncdb require --no-interaction 'vielhuber/syncdb:^2.1.2'
for patch in /opt/lamp/patches/syncdb-routine-database.patch /opt/lamp/patches/syncdb-definer.patch; do
    if git -C /root/.syncdb/vendor/vielhuber/syncdb apply --check "$patch"; then
        git -C /root/.syncdb/vendor/vielhuber/syncdb apply "$patch"
    else
        git -C /root/.syncdb/vendor/vielhuber/syncdb apply --reverse --check "$patch"
    fi
done
ln -s /var/lib/lamp/syncdb /root/.syncdb/profiles
printf '\n# syncdb\nexport PATH="$PATH:/root/.syncdb/vendor/bin"\n' >> /root/.bashrc

# README.md#native-database-reference — original instructions (documentation, not executed)
# #### postgres
#
# - `sudo apt install -y postgresql-common`
# - `sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh`
# - `sudo apt update`
# - `sudo apt-get install postgresql-18 postgresql-client-18 postgresql-18-jit`
# - sudo nano /etc/postgresql/18/main/postgresql.conf
#     - listen_addresses = '\*'
#     - port = 5432
# - keep `gin_pending_list_limit` unset (PostgreSQL 18 default: `4MB`)
# - keep `data_sync_retry` unset (PostgreSQL 18 default: `off`); investigate storage errors instead of retrying potentially lost writes
# - `sudo systemctl start postgresql`
# - `sudo systemctl enable postgresql`
# - `sudo -u postgres psql`
# - `\password postgres`
# - root
# - `\q`
# - `nano ~/.pgpass`
# - `*:5432:*:postgres:root`
# - `chmod 0600 ~/.pgpass`
# - sudo nano /etc/postgresql/18/main/pg_hba.conf
#
# ```
# # replace the active local authentication lines with these
# local   all   postgres                  scram-sha-256
# local   all   all                       scram-sha-256
# host    all   all        127.0.0.1/32   scram-sha-256
# host    all   all        ::1/128        scram-sha-256
# ```

#### postgres
step 'postgres'
apt-get install -y postgresql-common
printf 'create_main_cluster = false\n' > /etc/postgresql-common/createcluster.conf
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
apt-get update
apt-get install -y postgresql-18 postgresql-client-18 postgresql-18-jit
# Cluster creation, authentication and port 5432 are configured on the persistent data at startup.

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### ghostscript
#
# - `sudo apt-get install ghostscript`
# - `ghostscript -v`

#### ghostscript
step 'ghostscript'
apt-get install -y ghostscript

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### imagemagick
#
# - do this beforehand
#     - remove old installations
#         - `sudo rm -f /usr/local/bin/magick /usr/local/bin/convert`
#         - `sudo rm -rf /usr/local/lib/ImageMagick-* /usr/local/etc/ImageMagick-7`
#         - `apt remove "*imagemagick*" --purge -y && apt autoremove --purge -y`
#     - fix cmake
#         - `sudo rm /usr/local/bin/cmake`
#         - `sudo apt-get remove cmake`
#         - `sudo apt-get update`
#         - `cmake --version`
#         - `sudo apt-get install cmake`
#     - install opencl headers
#         - `sudo apt-get install -y ocl-icd-opencl-dev opencl-headers`
#
# ```
# t=$(mktemp) && \
# wget 'https://dist.1-2.dev/imei.sh' -qO "$t" && \
# bash "$t" && \
# rm "$t"
# ```
#
# - `convert -version`
# - `sudo nano /usr/local/etc/ImageMagick-7/policy.xml`
# - add/edit
#     - `<policy domain="coder" rights="none" pattern="MVG" />`
#     - `<policy domain="coder" rights="read|write" pattern="PDF" />`
#     - `<policy domain="coder" rights="read|write" pattern="LABEL" />`

#### imagemagick
step 'imagemagick'
# No old-installation cleanup is needed in a fresh image. Retain the IMEI installer.
apt-get install -y ocl-icd-opencl-dev opencl-headers
curl -fsSL https://dist.1-2.dev/imei.sh -o imei.sh
bash imei.sh
# Current IMEI packages use /opt/imei instead of the README's historical /usr/local prefix.
# Packaged PHP imagick uses distro libraries, independently of the IMEI CLI.
for policy in /opt/imei/etc/ImageMagick-7/policy.xml /etc/ImageMagick-*/policy.xml; do
    sed -i -E '/<policy domain="coder" rights="[^"]*" pattern="(MVG|PDF|LABEL)" \/>/d' "$policy"
    sed -i '/<\/policymap>/i\  <policy domain="coder" rights="none" pattern="MVG" />\n  <policy domain="coder" rights="read|write" pattern="PDF" />\n  <policy domain="coder" rights="read|write" pattern="LABEL" />' "$policy"
done

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### pdftk
#
# - `sudo apt update`
# - `sudo apt install pdftk`
# - `pdftk --version`

#### pdftk
step 'pdftk'
apt-get install -y pdftk

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### wkhtmltopdf
#
# - `sudo apt-get install libfontconfig1 libxrender1 xfonts-75dpi xfonts-base`
# - `cd /tmp/`
# - `mkdir dl`
# - `cd dl`
# - `wget https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_amd64.deb`
# - `sudo dpkg -i wkhtmltox_0.12.6.1-2.jammy_amd64.deb`
# - `cd /tmp/`
# - `rm -rf dl`
# - `wkhtmltopdf --version`
# - if an error like "Fontconfig warning: FcPattern object weight does not accept value [0.5 15.3)" appears, clear font cache: `sudo fc-cache -f -v`

#### wkhtmltopdf
step 'wkhtmltopdf'
curl -fsSL https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_amd64.deb -o wkhtmltox.deb
apt-get install -y ./wkhtmltox.deb
# Upstream's last patched-Qt build is for jammy; keep it only if dependencies resolve.

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### cpdf
#
# - `cd /opt/`
# - `wget https://github.com/coherentgraphics/cpdf-binaries/archive/master.zip`
# - `unzip master.zip`
# - `mv cpdf-binaries-master/Linux-Intel-64bit/cpdf /usr/local/bin/cpdf`
# - `rm -rf cpdf-binaries-master`
# - `rm master.zip`
# - `cpdf --help`

#### cpdf
step 'cpdf'
curl -fsSL https://github.com/coherentgraphics/cpdf-binaries/archive/master.zip -o cpdf.zip
unzip -q cpdf.zip
install -m 755 cpdf-binaries-master/Linux-Intel-64bit/cpdf /usr/local/bin/cpdf

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### pdfinfo
#
# - `sudo apt-get install poppler-utils`
# - `pdfinfo`

#### pdfinfo
step 'pdfinfo'
apt-get install -y poppler-utils

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### tesseract
#
# - `sudo apt install tesseract-ocr`
# - `sudo apt install libtesseract-dev`
# - `sudo apt-get install tesseract-ocr-deu`
# - `tesseract --version`

#### tesseract
step 'tesseract'
apt-get install -y tesseract-ocr libtesseract-dev tesseract-ocr-deu

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### msgconvert
#
# - `sudo apt-get install libemail-outlook-message-perl`
# - `msgconvert --version`

#### msgconvert
step 'msgconvert'
apt-get install -y libemail-outlook-message-perl

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### jpegoptim
#
# - `sudo apt-get install jpegoptim`
# - `jpegoptim --version`

#### jpegoptim
step 'jpegoptim'
apt-get install -y jpegoptim

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### mozjpeg
#
# - `sudo apt-get update`
# - `sudo apt-get install -y cmake autoconf automake libtool nasm make pkg-config git libpng-dev`
# - `cd /tmp/`
# - `mkdir mozjpeg`
# - `cd mozjpeg`
# - `wget https://github.com/mozilla/mozjpeg/archive/refs/tags/v4.1.1.tar.gz`
# - `tar -xzvf v*.tar.gz`
# - `cd mozjpeg-*/`
# - `mkdir build && cd build`
# - `sudo cmake -G"Unix Makefiles" ../`
# - `sudo make install`
# - `ln -s /opt/mozjpeg/bin/jpegtran /usr/bin/mozjpeg`
# - `cd ..`
# - `cd ..`
# - `cd ..`
# - `rm -rf mozjpeg`
# - `mozjpeg --version`

#### mozjpeg
step 'mozjpeg'
curl -fsSL https://github.com/mozilla/mozjpeg/archive/refs/tags/v4.1.1.tar.gz -o mozjpeg.tar.gz
tar -xzf mozjpeg.tar.gz
cmake -S mozjpeg-4.1.1 -B mozjpeg-4.1.1/build -G 'Unix Makefiles' -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build mozjpeg-4.1.1/build -j2
cmake --install mozjpeg-4.1.1/build
ln -s /opt/mozjpeg/bin/jpegtran /usr/local/bin/mozjpeg

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### pngquant
#
# - `sudo apt-get install pngquant`
# - `pngquant --version`

#### pngquant
step 'pngquant'
apt-get install -y pngquant

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### svgo
#
# - `npm install -g svgo`
# - `svgo --version`

#### svgo
step 'svgo'
# Installed with the modern Node LTS above.

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### gifsicle
#
# - `sudo apt-get update`
# - `sudo apt-get install -y git gcc cmake libpng-dev pkg-config`
# - `cd /tmp/`
# - `mkdir gifsicle`
# - `cd gifsicle`
# - `wget https://www.lcdf.org/gifsicle/gifsicle-1.93.tar.gz`
# - `tar -xzvf gifsicle*.tar.gz`
# - `cd gifsicle*/`
# - `autoreconf -i`
# - `./configure`
# - `make`
# - `sudo make install`
# - `cd ..`
# - `cd ..`
# - `rm -rf gifsicle/`
# - `gifsicle --version`

#### gifsicle
step 'gifsicle'
curl -fsSL https://www.lcdf.org/gifsicle/gifsicle-1.93.tar.gz -o gifsicle.tar.gz
tar -xzf gifsicle.tar.gz
(
    cd gifsicle-1.93
    autoreconf -i
    ./configure
    make -j2
    make install
)

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### webp (cwebp/dwebp)
#
# - `sudo apt-get install webp`
# - `cwebp -version`
# - `dwebp -version`

#### webp (cwebp/dwebp)
step 'webp (cwebp/dwebp)'
apt-get install -y webp

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### exiftool
#
# - `sudo apt-get install libimage-exiftool-perl`
# - `exiftool`

#### exiftool
step 'exiftool'
apt-get install -y libimage-exiftool-perl

# README.md#native-database-reference — original instructions (documentation, not executed)
# #### phpmyadmin
#
# - `mkdir /var/www/phpmyadmin`
# - `cd /var/www/phpmyadmin`
# - `composer create-project phpmyadmin/phpmyadmin .`
# - `lamp add phpmyadmin php8.5`
# - `cp config.sample.inc.php config.inc.php`
# - `nano config.inc.php`
#     - `$cfg['Servers'][$i]['user'] = 'root';`
#     - `$cfg['Servers'][$i]['AllowNoPassword'] = true;`
#     - `$cfg['Servers'][$i]['host'] = 'localhost';`
#     - `$cfg['Servers'][$i]['password'] = 'root';`
#     - `$cfg['Servers'][$i]['auth_type'] = 'config';`
#     - `$cfg['ExecTimeLimit'] = 6000;`
# - phpMyAdmin cookie secret: generated privately on first container start.
# - https://phpmyadmin.example.dev
#     - "Der phpMyAdmin-Konfigurationsspeicher ist nicht vollständig konfiguriert," => Anklicken + Erzeugen

#### phpmyadmin
step 'phpmyadmin'
# Keep application code outside the project volume; the start script creates its vhost.
php8.5 /usr/local/bin/composer create-project --no-interaction --no-dev phpmyadmin/phpmyadmin /opt/phpmyadmin
# The release lockfile contains vulnerable versions; update within upstream's constraints.
php8.5 /usr/local/bin/composer --working-dir=/opt/phpmyadmin update \
    paragonie/sodium_compat symfony/cache symfony/process twig/twig \
    --with-dependencies --no-dev --no-interaction
# Runtime config contains the database password, so it is not baked into this image.
ln -s /var/lib/lamp/phpmyadmin/config.inc.php /opt/phpmyadmin/config.inc.php

# README.md#native-integrations-reference — original instructions (documentation, not executed)
# #### speedtest cli
#
# - `curl -s https://packagecloud.io/install/repositories/ookla/speedtest-cli/script.deb.sh | bash`
# - `sed -i 's/noble/jammy/g' /etc/apt/sources.list.d/ookla_speedtest-cli.list`
# - `apt-get update`
# - `apt-get install -y speedtest`
# - `speedtest -f json --accept-license --accept-gdpr`

#### speedtest cli
step 'speedtest cli'
# Retain Ookla's CLI and the documented jammy suite instead of spoofing Ubuntu globally.
curl -fsSL https://packagecloud.io/ookla/speedtest-cli/gpgkey | gpg --dearmor -o /usr/share/keyrings/ookla.gpg
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/ookla.gpg] https://packagecloud.io/ookla/speedtest-cli/ubuntu/ jammy main' > /etc/apt/sources.list.d/ookla_speedtest-cli.list
apt-get update
apt-get install -y speedtest
# Accepting licenses and running bandwidth tests remain operator actions.

# README.md#native-integrations-reference — original instructions (documentation, not executed)
# #### harnesses
#
# - Claude Code: https://gist.github.com/vielhuber/6171c85ee799940e4ea75296a3439f85
# - Codex: https://gist.github.com/vielhuber/5b2d7d64a7574ab45ae8122158933c5d
# - Antigravity: https://gist.github.com/vielhuber/3342a36eb0a8f1190f2cddcdae99613f
# - OpenCode: https://gist.github.com/vielhuber/f109292b8c49b2f04a489740796a0758

#### harnesses
step 'harnesses'
# Installation only; do not log in or enable automatic permission bypasses at build time.
curl -fsSL https://claude.ai/install.sh -o claude-install.sh
bash claude-install.sh
# Codex was installed through npm on the modern Node LTS above.
curl -fsSL https://opencode.ai/install -o opencode-install.sh
bash opencode-install.sh
curl -fsSL https://antigravity.google/cli/install.sh -o antigravity-install.sh
bash antigravity-install.sh
mkdir -p /root/.claude /root/.codex /root/.agents /root/.config/opencode /root/.antigravity
ln -s /var/www/skills/AGENTS.md /root/.claude/CLAUDE.md
ln -s /var/www/skills /root/.claude/skills
ln -s /var/www/skills/AGENTS.md /root/.codex/AGENTS.md
ln -s /var/www/skills /root/.agents/skills
ln -s /var/www/skills/AGENTS.md /root/.config/opencode/AGENTS.md
ln -s /var/www/skills /root/.config/opencode/skills
ln -s /var/www/skills/AGENTS.md /root/.antigravity/AGENTS.md

# README.md#native-integrations-reference — original instructions (documentation, not executed)
# #### cliproxyapi
#
# - `mkdir -p /var/www/dashboard`
# - `cd /var/www/dashboard`
# - `curl -fsSL https://raw.githubusercontent.com/router-for-me/cliproxyapi-installer/refs/heads/master/cliproxyapi-installer | bash`
# - `nano /root/cliproxyapi/config.yaml`
#     - Replace `"your-api-key..."` with custom generated keys (`printf 'sk-%s\n' "$(openssl rand -hex 32)"`)
#     - `request-log: true`
#     - `logs-max-total-size-mb: 500`
# - `lamp add dashboard php8.5 dashboard --port=8317 --alias=ai.example.com --exclude=/admin`
# - `sudo systemctl reload apache2`
# - `loginctl enable-linger root`
# - `systemctl --user enable cliproxyapi.service`
# - `systemctl --user start cliproxyapi.service`
# - `systemctl --user status cliproxyapi.service`
# - `cd /root/cliproxyapi && ./cli-proxy-api --codex-login --no-browser`
# - `cd /root/cliproxyapi && ./cli-proxy-api --claude-login --no-browser`
# - `systemctl --user restart cliproxyapi`
# - `https://ai.example.com/admin`

#### cliproxyapi
step 'cliproxyapi'
# Install the pinned binary directly: the upstream installer starts systemd and generates keys.
curl -fsSL https://github.com/router-for-me/CLIProxyAPI/releases/download/v7.2.159/CLIProxyAPI_7.2.159_linux_amd64.tar.gz -o cliproxyapi.tar.gz
mkdir cliproxyapi
tar -xzf cliproxyapi.tar.gz -C cliproxyapi
install -m 755 cliproxyapi/cli-proxy-api /usr/local/bin/cli-proxy-api
install -m 644 cliproxyapi/config.example.yaml /opt/lamp/cliproxyapi.example.yaml
sed -i 's/^request-log: .*/request-log: true/; s/^logs-max-total-size-mb: .*/logs-max-total-size-mb: 500/; s|^auth-dir: .*|auth-dir: "/var/lib/lamp/cliproxyapi/auth"|' /opt/lamp/cliproxyapi.example.yaml
# Supply a private config under /var/lib/lamp/cliproxyapi/config.yaml to enable the service.

# README.md#native-integrations-reference — original instructions (documentation, not executed)
# #### clasp
#
# - `npm i @google/clasp -g`
# - https://script.google.com/home/usersettings => enable
# - `npm i -S @types/google-apps-script`
# - `clasp login`

#### clasp
step 'clasp'
# CLI installed above. Google login and project-local types remain manual.

# README.md#native-integrations-reference — original instructions (documentation, not executed)
# #### httrack
#
# - `sudo apt install httrack webhttrack`

#### httrack
step 'httrack'
apt-get install -y httrack webhttrack

# README.md#native-language-reference — original instructions (documentation, not executed)
# #### ruby (via rvm)
#
# - `gpg --recv-keys 409B6B1796C275462A1703113804BB82D39DC0E3 7D2BAF1CF37B13E2069D6956105BD0E739499BDB`
# - `echo 'export rvm_prefix="$HOME"' > /root/.rvmrc`
# - `echo 'export rvm_path="$HOME/.rvm"' >> /root/.rvmrc`
# - `curl -sSL https://get.rvm.io | bash -s stable`
# - `source ~/.rvm/scripts/rvm`
# - `rvm install ruby-3.1.2`
# - `ruby --version`

#### ruby (via rvm)
step 'ruby (via rvm)'
gpg --batch --keyserver hkps://keyserver.ubuntu.com --recv-keys 409B6B1796C275462A1703113804BB82D39DC0E3 7D2BAF1CF37B13E2069D6956105BD0E739499BDB
printf 'export rvm_prefix="/root"\nexport rvm_path="/root/.rvm"\n' > /root/.rvmrc
curl -fsSL https://get.rvm.io -o rvm-install.sh
bash rvm-install.sh stable
step 'ruby archive download and verification'
ruby_sha512=9155d1150398eaea7c9954af61ecf8dfdb885cfcf63a67bbcf6c92e282cd3ccac0ff9234d039286a9623297b65197441438c37f707e31d270ce2fe11e8f38a44
curl -4 --http1.1 -fsSL --connect-timeout 20 --max-time 600 --retry 3 \
    --speed-limit 10240 --speed-time 30 \
    --write-out 'Ruby archive: %{size_download} bytes in %{time_total}s\n' \
    https://cache.ruby-lang.org/pub/ruby/3.1/ruby-3.1.2.tar.gz \
    -o /root/.rvm/archives/ruby-3.1.2.tar.gz
printf '%s  %s\n' "$ruby_sha512" /root/.rvm/archives/ruby-3.1.2.tar.gz | sha512sum -c -
printf 'ruby-3.1.2.tar.gz=%s\n' "$ruby_sha512" >> /root/.rvm/user/sha512
step 'ruby compilation and installation'
(
    # RVM restores inherited traps; never let its subshell delete the parent's build directory.
    trap - EXIT
    set +u
    source /root/.rvm/scripts/rvm
    CC=gcc-14 CXX=g++-14 rvm install ruby-3.1.2 --disable-binary --verify-downloads 2 --no-docs -j "$(nproc)"
)
rm -rf -- /root/.rvm/src/ruby-3.1.2 /root/.rvm/archives/ruby-3.1.2.tar.gz

# README.md#native-integrations-reference — original instructions (documentation, not executed)
# #### wpscan
#
# - `sudo apt-get install zlib1g-dev`
# - `gem install wpscan`
# - `wpscan --update`
# - `gem update wpscan`
# - `wpscan --url https://www.domain.tld`

#### wpscan
step 'wpscan'
# Use the maintained system Ruby for current WPScan dependencies; legacy Ruby remains in RVM.
apt-get install -y --no-install-recommends ruby-dev ruby-bundler
mkdir -p /opt/lamp/ruby-tools
/usr/bin/gem install --no-document --bindir /opt/lamp/ruby-tools wpscan
printf '#!/usr/bin/env bash\nexec env -u GEM_HOME -u GEM_PATH /usr/bin/ruby /opt/lamp/ruby-tools/wpscan "$@"\n' > /usr/local/bin/wpscan
chmod 755 /usr/local/bin/wpscan
# Database refresh and target scans are runtime actions, not image-build side effects.

# README.md#native-integrations-reference — original instructions (documentation, not executed)
# #### wp-cli
#
# - `curl -O https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar`
# - `chmod +x wp-cli.phar`
# - `sudo mv wp-cli.phar /usr/local/bin/wp`
# - `sudo nano ~/.bashrc`
# - `# wp-cli`
# - `alias wp='wp --allow-root'`
# - `wp --info`

#### wp-cli
step 'wp-cli'
curl -fsSL https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar -o /usr/local/bin/wp
chmod 755 /usr/local/bin/wp
printf '\n# wp-cli\nalias wp="wp --allow-root"\n' >> /root/.bashrc

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### ffmpeg
#
# - `cd /tmp`
# - `wget https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl-shared.tar.xz`
# - `tar -xf ffmpeg-*-shared.tar.xz`
# - `cd ffmpeg-*-shared`
# - `sudo cp bin/ffmpeg bin/ffprobe /usr/local/bin/`
# - `sudo cp -r lib/* /usr/local/lib/`
# - `sudo ldconfig`
# - `cd ..`
# - `rm -rf ffmpeg-*-shared*`
# - `ffmpeg -version`

#### ffmpeg
step 'ffmpeg'
curl -fsSL https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl-shared.tar.xz -o ffmpeg.tar.xz
mkdir ffmpeg
tar -xf ffmpeg.tar.xz -C ffmpeg --strip-components=1
install -m 755 ffmpeg/bin/ffmpeg ffmpeg/bin/ffprobe /usr/local/bin/
cp -a ffmpeg/lib/. /usr/local/lib/
ldconfig

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### youtube-dl
#
# - `sudo curl -L https://yt-dl.org/downloads/latest/youtube-dl -o /usr/local/bin/youtube-dl`
# - `sudo chmod a+rx /usr/local/bin/youtube-dl`
# - `youtube-dl --version`

#### youtube-dl
step 'youtube-dl'
# The old yt-dl.org endpoint returns 404; use the project's maintained nightly build.
curl -fsSL https://github.com/ytdl-org/ytdl-nightly/releases/latest/download/youtube-dl -o /usr/local/bin/youtube-dl
chmod 755 /usr/local/bin/youtube-dl

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### inkscape
#
# - `sudo apt-get install inkscape`

#### inkscape
step 'inkscape'
apt-get install -y inkscape

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### rsvg
#
# - `sudo apt-get install librsvg2-bin`

#### rsvg
step 'rsvg'
apt-get install -y librsvg2-bin

# README.md#native-media-reference — original instructions (documentation, not executed)
# #### xclip (pipe to clipboard)
#
# - `sudo apt-get install xclip`
# - `echo "foo" | xclip`

#### xclip (pipe to clipboard)
step 'xclip (pipe to clipboard)'
apt-get install -y xclip
# Clipboard access requires the host display; do not overwrite the clipboard at build time.

# README.md#native-integrations-reference — original instructions (documentation, not executed)
# #### whatweb
#
# - `mkdir whatweb`
# - `cd whatweb`
# - `git clone https://github.com/urbanadventurer/WhatWeb.git .`
# - `sudo apt install -y ruby ruby-dev ruby-bundler build-essential make libssl-dev zlib1g-dev libyaml-dev`
# - `sudo make install`
#
# ```
# whatweb \
#     --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36" \
#     -a 3 \
#     -v \
#     https://example.com
# ```

#### whatweb
step 'whatweb'
git clone --depth 1 https://github.com/urbanadventurer/WhatWeb.git whatweb
make -C whatweb install
printf '#!/usr/bin/env bash\nexec env -u GEM_HOME -u GEM_PATH /usr/bin/ruby /usr/share/whatweb/whatweb "$@"\n' > /usr/local/bin/whatweb
chmod 755 /usr/local/bin/whatweb
# Scanning the example website is deliberately not part of the build.

# README.md#native-language-reference — original instructions (documentation, not executed)
# #### switch cli php version
#
# - `sudo update-alternatives --config php`
# - `sudo update-alternatives --set php /usr/bin/php8.1` (directly set)
# - always choose manual mode (so newer installed versions do not get taken automatically)
# - `php -v`
# - `/usr/bin/php --version`
# - `/usr/bin/php8.2 --version` (call specific cli version)

#### switch cli php version
step 'switch cli php version'
# Preserve the explicitly documented default rather than selecting the newest installed PHP.
update-alternatives --set php /usr/bin/php8.1

# README.md#native-language-reference — original instructions (documentation, not executed)
# #### switch global php version
#
# - `sudo a2dismod phpY.Y`
# - `sudo a2enmod phpX.X`

# Apache PHP module mode is retired; managed VHosts select their PHP-FPM socket.

#### supervisor
step 'supervisor'
cat > /etc/supervisor/supervisord.conf <<'SUPERVISOR'
[unix_http_server]
file=/run/supervisor.sock
chmod=0700

[supervisord]
nodaemon=true
user=root
logfile=/dev/null
pidfile=/run/supervisord.pid
childlogdir=/var/log/supervisor

[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix:///run/supervisor.sock

[include]
files=/run/lamp-supervisor/*.conf
SUPERVISOR

#### image checks
step 'image checks'
for version in "${php_versions[@]}"; do
    "/usr/bin/php$version" -v
    "/usr/sbin/php-fpm$version" -R -t
done
mysqld --validate-config
dpkg --audit
apt-get check
mv "$build_directory/docker-clean" /etc/apt/apt.conf.d/docker-clean
rm /etc/apt/apt.conf.d/lamp-build-cache
rm -rf /var/lib/apt/lists/*

# Additional README documentation; these are operator actions, not build commands.

# README.md#native-server-reference — original instructions (documentation, not executed)
# ## features
#
# - wsl2 + ubuntu 24
# - simple installation
# - simple usage via command line
# - full control over configuration
# - systemd enabled
# - cronjobs enabled
# - default remote smtp relay for all mailings
# - databases included: mysql (+phpmyadmin), postgresql, oraclesql
# - shared php.ini configuration for all versions
# - switch php/cli version (globally and host based)
# - access via all devices in your local network
# - support for different networks
# - real ssl certificates for all hosts and all registry.npmjs.orgdevices
# - supports reverse proxy configuration
# - native linux performance (can handle node_modules and vendor) with wsl2
# - php debugging and profiling with xdebug

# README.md#installation — original instructions (documentation, not executed)
# ## installation

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#historical-host-reference — original instructions (documentation, not executed)
# #### hosts

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-24 — original instructions (documentation, not executed)
# ##### internal wifi (e.g. cloudflare)
#
# - we abuse our own public domain as a dns that maps to a local ip in order to prevent setting local hosts AND having the ability to access via smartphones/tablets from the same network
# - dns a-records
#     - example.dev => <LAN_IP>
#     - \*.example.dev => <LAN_IP>
# - fritzbox (if needed)
#     - fritz.box > Heimnetz > Netzwerk > Netzwerkeinstellungen > DNS-Rebind-Schutz:
#         - example.com
#         - example.dev
#         - \*.example.dev
#     - restart

# https://github.com/vielhuber/setup/blob/main/_02_WSL.md#legacy-1559 — original instructions (documentation, not executed)
# #### switch clients
#
# - you can setup lamp on multiple clients
# - option 1: point the dns record to the current active client (currently used)
# - option 2: setup a more dynamic approach like 01.project-name.example.dev, 02.project-name.example.dev, ...

# README.md#usage — original instructions (documentation, not executed)
# ## usage

# README.md#native-cli-reference — original instructions (documentation, not executed)
# #### start
#
# - `lamp start`

# README.md#native-cli-reference — original instructions (documentation, not executed)
# #### restart
#
# - `lamp restart`

# README.md#native-cli-reference — original instructions (documentation, not executed)
# #### stop
#
# - `lamp stop`

# README.md#native-cli-reference — original instructions (documentation, not executed)
# #### create project
#
# - `lamp add project-name` # uses default php version
# - `lamp add project-name php8.1` # uses specific php version
# - `lamp add project-name php8.1 custom/subfolder/public` # uses specific folder
# - `lamp add project-name php8.1 custom/subfolder/public --port=3000` # uses specific port
# - `lamp add project-name php8.1 custom/subfolder/public --alias=project.example.com` # sets alias for public usage (e.g. via cloudflare tunnel)

# README.md#native-cli-reference — original instructions (documentation, not executed)
# #### remove project
#
# - `lamp remove project`
