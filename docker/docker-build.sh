#!/usr/bin/env bash
set -euo pipefail

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

#### install basic linux packages
step 'install basic linux packages'
apt-get install -y --no-install-recommends nano sshpass zip unzip htop ruby \
    libnotify-bin net-tools pv csh cifs-utils apt-utils software-properties-common \
    iputils-ping gettext lsof yq time parallel ripgrep tmux rsync plocate

#### public (cloudflare)
step 'public (cloudflare)'
# Use the vendor's distribution-independent repository instead of jammy.
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' > /etc/apt/sources.list.d/cloudflared.list
apt-get update
apt-get install -y cloudflared
mkdir -p /etc/cloudflared
# Tunnel login, DNS changes and credentials are runtime/operator actions.

#### redis
step 'redis'
apt-get install -y redis-server
# Keep local-only access; applications in this all-in-one container use localhost.
sed -i 's/^daemonize .*/daemonize no/; s/^supervised .*/supervised no/; s|^logfile .*|logfile ""|' /etc/redis/redis.conf

#### merge .bashrc/.bash_profile
step 'merge .bashrc/.bash_profile'
cat > /root/.bash_profile <<'PROFILE'
if [ -f ~/.bashrc ]; then
    source ~/.bashrc
fi
PROFILE

#### hide intro text
step 'hide intro text'
touch /root/.hushlogin

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

#### apache/php/mysql
step 'apache/php/mysql'
# Suppress package-time database creation; initialize the mounted directory at startup.
printf 'mysql-server mysql-server/data-dir select /var/lib/mysql\n' | debconf-set-selections
apt-get install -y apache2 mysql-server
# Docker's default security profile does not permit NUMA memory policy changes.
printf '[mysqld]\ninnodb_numa_interleave=OFF\n' > /etc/mysql/conf.d/lamp-container.cnf
# Only package-created data exists here; no host data is mounted during the build.
find /var/lib/mysql -mindepth 1 -delete

#### add ppa of officially maintained php versions
step 'add ppa of officially maintained php versions'
# The Ondrej PPA has no resolute suite. Use the same maintainer's Sury repository.
curl -fsSL https://packages.sury.org/debsuryorg-archive-keyring.deb -o sury-keyring.deb
dpkg -i sury-keyring.deb
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/debsuryorg-archive-keyring.gpg] https://packages.sury.org/php/ resolute main' > /etc/apt/sources.list.d/php.list
apt-get update

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

#### uopz: only enable temporarily
step 'uopz: only enable temporarily'
phpdismod uopz

#### apache extensions
step 'apache extensions'
a2enmod rewrite ssl vhost_alias authz_groupfile headers cache expires actions alias \
    proxy_fcgi proxy proxy_html proxy_http xml2enc

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

#### setup default page
step 'setup default page'
a2dissite 000-default.conf
# The controller creates a deny-by-default VHost and explicit per-environment FPM handlers.

#### ssl
step 'ssl'
apt-get install -y certbot python3-certbot-dns-cloudflare
# Ubuntu's managed Python packages replace the README's conflicting system pip upgrade.
rm -f /etc/cron.d/certbot
printf '0 12 * * * root certbot renew --quiet --deploy-hook "apachectl configtest && supervisorctl signal USR1 apache2"\n' > /etc/cron.d/lamp-certbot

#### powershell
step 'powershell'
# The resolute Microsoft repository does not publish PowerShell; use its universal release package.
curl -fsSL https://github.com/PowerShell/PowerShell/releases/download/v7.6.6/powershell_7.6.6-1.deb_amd64.deb -o powershell.deb
printf '%s  powershell.deb\n' '9585f38ab5a026c3fc0995486e26e12050777960fef47a22dca98b577c5d27a7' | sha256sum -c -
apt-get install -y ./powershell.deb

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

#### mydumper
step 'mydumper'
curl -fsSL https://github.com/mydumper/mydumper/releases/download/v0.21.3-1/mydumper_0.21.3-1.noble_amd64.deb -o mydumper.deb
apt-get install -y ./mydumper.deb
# This pinned release only provides a noble binary; dependency resolution must succeed.

#### ncdu
step 'ncdu'
apt-get install -y ncdu

#### node + php + python auto version switching on cd
step 'node + php + python auto version switching on cd'
# Resolve relative virtualenv paths from their .envrc, including nested working directories.
curl -fsSL https://gist.githubusercontent.com/vielhuber/021453a7e908f9487917835107ad6ce7/raw/.bashrc -o /opt/lamp/auto-switch.sh
sed -i 's|source "$(cat "$envrc")/bin/activate"|source "$(cd -- "$(dirname -- "$envrc")" \&\& realpath -- "$(cat "$envrc")")/bin/activate"|' /opt/lamp/auto-switch.sh
printf '\nsource /opt/lamp/auto-switch.sh\n' >> /root/.bashrc

#### php error logging
step 'php error logging'
touch /var/log/php-error.log
chmod 666 /var/log/php-error.log
# Logging needs write permission, not the README's executable bit.

#### create xdebug profile output dir
step 'create xdebug profile output dir'
mkdir -p /tmp/xdebug
chmod 1777 /tmp/xdebug

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

#### local environment permissions
step 'local environment permissions'
# Preserve the development-only root FPM pools, but never recursively chmod host projects.
for version in "${php_versions[@]}"; do
    sed -i 's/^user = .*/user = root/; s/^group = .*/group = root/' "/etc/php/$version/fpm/pool.d/www.conf"
done

#### fix font errors
step 'fix font errors'
fc-cache -f

#### composer
step 'composer'
curl -fsSL https://getcomposer.org/installer -o composer-setup.php
composer_checksum=$(curl -fsSL https://composer.github.io/installer.sig)
printf '%s  composer-setup.php\n' "$composer_checksum" | sha384sum -c -
php8.5 composer-setup.php --install-dir=/usr/local/bin --filename=composer
printf '\n# hide composer sudo message\nexport COMPOSER_ALLOW_SUPERUSER=1\n' >> /root/.bashrc
php8.5 /usr/local/bin/composer global config --no-plugins allow-plugins.vildanbina/composer-upgrader true
php8.5 /usr/local/bin/composer global require --no-interaction vildanbina/composer-upgrader

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

#### yarn
step 'yarn'

#### python
step 'python'
apt-get install -y python3 python3-pip python3-venv python-is-python3

#### rust
step 'rust'
curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs -o rustup-install.sh
sh rustup-install.sh -y --profile minimal --component rustfmt,clippy

#### go
step 'go'
apt-get install -y golang

#### gettext
step 'gettext'

#### git
step 'git'
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

#### gh (github command line)
step 'gh (github command line)'
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /usr/share/keyrings/githubcli-archive-keyring.gpg
printf '%s\n' 'deb [arch=amd64 signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main' > /etc/apt/sources.list.d/github-cli.list
apt-get update
apt-get install -y gh

#### git-filter-repo
step 'git-filter-repo'
mkdir -p /root/.git-filter-repo
curl -fsSL https://raw.githubusercontent.com/newren/git-filter-repo/main/git-filter-repo -o /root/.git-filter-repo/git-filter-repo
chmod 755 /root/.git-filter-repo/git-filter-repo
printf '\nexport PATH="$PATH:/root/.git-filter-repo"\n' >> /root/.bashrc

#### bash completion
step 'bash completion'
cat >> /root/.bashrc <<'COMPLETION'
if [ -f /etc/bash_completion ] && ! shopt -oq posix; then
    source /etc/bash_completion
fi
COMPLETION

#### subversion (svn)
step 'subversion (svn)'
apt-get install -y subversion

#### ssh client
step 'ssh client'
apt-get install -y openssh-client
install -d -m 700 /root/.ssh

#### ssh server
step 'ssh server'
apt-get install -y openssh-server
# Generate distinct host keys on the persistent runtime volume, not in the image.
rm -f /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub
printf 'PasswordAuthentication no\nPermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/lamp.conf

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

#### postgres
step 'postgres'
apt-get install -y postgresql-common
printf 'create_main_cluster = false\n' > /etc/postgresql-common/createcluster.conf
/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
apt-get update
apt-get install -y postgresql-18 postgresql-client-18 postgresql-18-jit

#### ghostscript
step 'ghostscript'
apt-get install -y ghostscript

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

#### pdftk
step 'pdftk'
apt-get install -y pdftk

#### wkhtmltopdf
step 'wkhtmltopdf'
curl -fsSL https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-2/wkhtmltox_0.12.6.1-2.jammy_amd64.deb -o wkhtmltox.deb
apt-get install -y ./wkhtmltox.deb

#### cpdf
step 'cpdf'
curl -fsSL https://github.com/coherentgraphics/cpdf-binaries/archive/master.zip -o cpdf.zip
unzip -q cpdf.zip
install -m 755 cpdf-binaries-master/Linux-Intel-64bit/cpdf /usr/local/bin/cpdf

#### pdfinfo
step 'pdfinfo'
apt-get install -y poppler-utils

#### tesseract
step 'tesseract'
apt-get install -y tesseract-ocr libtesseract-dev tesseract-ocr-deu

#### msgconvert
step 'msgconvert'
apt-get install -y libemail-outlook-message-perl

#### jpegoptim
step 'jpegoptim'
apt-get install -y jpegoptim

#### mozjpeg
step 'mozjpeg'
curl -fsSL https://github.com/mozilla/mozjpeg/archive/refs/tags/v4.1.1.tar.gz -o mozjpeg.tar.gz
tar -xzf mozjpeg.tar.gz
cmake -S mozjpeg-4.1.1 -B mozjpeg-4.1.1/build -G 'Unix Makefiles' -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build mozjpeg-4.1.1/build -j2
cmake --install mozjpeg-4.1.1/build
ln -s /opt/mozjpeg/bin/jpegtran /usr/local/bin/mozjpeg

#### pngquant
step 'pngquant'
apt-get install -y pngquant

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

#### webp (cwebp/dwebp)
step 'webp (cwebp/dwebp)'
apt-get install -y webp

#### exiftool
step 'exiftool'
apt-get install -y libimage-exiftool-perl

#### phpmyadmin
step 'phpmyadmin'
php8.5 /usr/local/bin/composer create-project --no-interaction --no-dev phpmyadmin/phpmyadmin /opt/phpmyadmin
php8.5 /usr/local/bin/composer --working-dir=/opt/phpmyadmin update \
    paragonie/sodium_compat symfony/cache symfony/process twig/twig \
    --with-dependencies --no-dev --no-interaction
ln -s /var/lib/lamp/phpmyadmin/config.inc.php /opt/phpmyadmin/config.inc.php

#### speedtest cli
step 'speedtest cli'
curl -fsSL https://packagecloud.io/ookla/speedtest-cli/gpgkey | gpg --dearmor -o /usr/share/keyrings/ookla.gpg
printf '%s\n' 'deb [signed-by=/usr/share/keyrings/ookla.gpg] https://packagecloud.io/ookla/speedtest-cli/ubuntu/ jammy main' > /etc/apt/sources.list.d/ookla_speedtest-cli.list
apt-get update
apt-get install -y speedtest

#### harnesses
step 'harnesses'
curl -fsSL https://claude.ai/install.sh -o claude-install.sh
bash claude-install.sh
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

#### cliproxyapi
step 'cliproxyapi'
curl -fsSL https://github.com/router-for-me/CLIProxyAPI/releases/download/v7.2.159/CLIProxyAPI_7.2.159_linux_amd64.tar.gz -o cliproxyapi.tar.gz
mkdir cliproxyapi
tar -xzf cliproxyapi.tar.gz -C cliproxyapi
install -m 755 cliproxyapi/cli-proxy-api /usr/local/bin/cli-proxy-api
install -m 644 cliproxyapi/config.example.yaml /opt/lamp/cliproxyapi.example.yaml
sed -i 's/^request-log: .*/request-log: true/; s/^logs-max-total-size-mb: .*/logs-max-total-size-mb: 500/; s|^auth-dir: .*|auth-dir: "/var/lib/lamp/cliproxyapi/auth"|' /opt/lamp/cliproxyapi.example.yaml

#### httrack
step 'httrack'
apt-get install -y httrack webhttrack

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
    trap - EXIT
    set +u
    source /root/.rvm/scripts/rvm
    CC=gcc-14 CXX=g++-14 rvm install ruby-3.1.2 --disable-binary --verify-downloads 2 --no-docs -j "$(nproc)"
)
rm -rf -- /root/.rvm/src/ruby-3.1.2 /root/.rvm/archives/ruby-3.1.2.tar.gz

#### wpscan
step 'wpscan'
apt-get install -y --no-install-recommends ruby-dev ruby-bundler
mkdir -p /opt/lamp/ruby-tools
/usr/bin/gem install --no-document --bindir /opt/lamp/ruby-tools wpscan
printf '#!/usr/bin/env bash\nexec env -u GEM_HOME -u GEM_PATH /usr/bin/ruby /opt/lamp/ruby-tools/wpscan "$@"\n' > /usr/local/bin/wpscan
chmod 755 /usr/local/bin/wpscan

#### wp-cli
step 'wp-cli'
curl -fsSL https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar -o /usr/local/bin/wp
chmod 755 /usr/local/bin/wp
printf '\n# wp-cli\nalias wp="wp --allow-root"\n' >> /root/.bashrc

#### ffmpeg
step 'ffmpeg'
curl -fsSL https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl-shared.tar.xz -o ffmpeg.tar.xz
mkdir ffmpeg
tar -xf ffmpeg.tar.xz -C ffmpeg --strip-components=1
install -m 755 ffmpeg/bin/ffmpeg ffmpeg/bin/ffprobe /usr/local/bin/
cp -a ffmpeg/lib/. /usr/local/lib/
ldconfig

#### youtube-dl
step 'youtube-dl'
# The old yt-dl.org endpoint returns 404; use the project's maintained nightly build.
curl -fsSL https://github.com/ytdl-org/ytdl-nightly/releases/latest/download/youtube-dl -o /usr/local/bin/youtube-dl
chmod 755 /usr/local/bin/youtube-dl

#### inkscape
step 'inkscape'
apt-get install -y inkscape

#### rsvg
step 'rsvg'
apt-get install -y librsvg2-bin

#### xclip (pipe to clipboard)
step 'xclip (pipe to clipboard)'
apt-get install -y xclip

#### whatweb
step 'whatweb'
git clone --depth 1 https://github.com/urbanadventurer/WhatWeb.git whatweb
make -C whatweb install
printf '#!/usr/bin/env bash\nexec env -u GEM_HOME -u GEM_PATH /usr/bin/ruby /usr/share/whatweb/whatweb "$@"\n' > /usr/local/bin/whatweb
chmod 755 /usr/local/bin/whatweb

#### switch cli php version
step 'switch cli php version'
# Preserve the explicitly documented default rather than selecting the newest installed PHP.
update-alternatives --set php /usr/bin/php8.1

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
