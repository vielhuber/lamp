#!/usr/bin/env bash
set -euo pipefail

test -f /.dockerenv
umask 022
mkdir -p /etc/lamp /etc/lamp/ssh
chmod 700 /etc/lamp/ssh
rm -rf /root/.ssh
ln -s /etc/lamp/ssh /root/.ssh
if [[ ! -f /etc/lamp-config/setup.yaml ]]; then
    printf '%s\n' 'Missing /etc/lamp-config/setup.yaml. Create .config/setup.yaml on the host as documented in README.md.' >&2
    exit 1
fi
mkdir -p /var/lib/lamp/secrets /var/lib/lamp/ssh /var/lib/lamp/syncdb /run/lamp-supervisor \
    /run/php /run/mysqld /run/postgresql /run/redis /run/sshd /var/log/supervisor /tmp/xdebug
chmod 700 /var/lib/lamp/secrets /var/lib/lamp/ssh
rm -f /run/lamp-supervisor/cloudflared.conf /run/lamp-supervisor/ngrok.conf
chmod 1777 /tmp/xdebug
chown mysql:mysql /run/mysqld /var/lib/mysql
chown postgres:postgres /run/postgresql /var/lib/postgresql
chown redis:redis /run/redis /var/lib/redis

#### database credentials
# For the README's local password, supply it in this file before the first start.
# Otherwise create one per installation, never a shared password baked into the image.
if [[ ! -f /var/lib/lamp/secrets/database-password ]]; then
    if [[ -d /var/lib/mysql/mysql || -f /var/lib/postgresql/18/main/PG_VERSION ]]; then
        printf '%s\n' 'Database data exists but its password file is missing. Restore the matching runtime state.' >&2
        exit 1
    fi
    (umask 077; openssl rand -hex 32 > /var/lib/lamp/secrets/database-password)
fi
test -s /var/lib/lamp/secrets/database-password
chmod 600 /var/lib/lamp/secrets/database-password
database_password=$(< /var/lib/lamp/secrets/database-password)
if [[ -z "$database_password" || "$database_password" = *$'\n'* || "$database_password" = *$'\r'* ]]; then
    printf '%s\n' 'The database password must be one non-empty line.' >&2
    exit 1
fi
unset database_password

#### apache/php/mysql
# Disable mod_php left in older images when using the updated entrypoint.
for module in /etc/apache2/mods-enabled/php*.load; do
    if [[ -e "$module" ]]; then
        a2dismod "$(basename "$module" .load)"
    fi
done

# Only initialize an empty volume. Interrupted initialization requires manual inspection.
if [[ -f /var/lib/lamp/mysql-initializing ]]; then
    printf '%s\n' 'MySQL initialization was interrupted. Inspect the data; it will not be overwritten.' >&2
    exit 1
fi
if [[ ! -d /var/lib/mysql/mysql ]]; then
    if [[ -n "$(find /var/lib/mysql -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
        printf '%s\n' 'MySQL data directory is not empty; refusing to initialize it.' >&2
        exit 1
    fi
    touch /var/lib/lamp/mysql-initializing
    mysqld --initialize-insecure --user=mysql --datadir=/var/lib/mysql
    mysqld --user=mysql --skip-networking --socket=/run/mysqld/bootstrap.sock --pid-file=/run/mysqld/bootstrap.pid &
    mysql_process=$!
    trap 'kill -TERM "$mysql_process" 2>/dev/null || true; wait "$mysql_process" 2>/dev/null || true' EXIT
    trap 'exit 143' TERM
    trap 'exit 130' INT
    for ((attempt=0; attempt<120; attempt++)); do
        if mysqladmin --socket=/run/mysqld/bootstrap.sock --user=root ping --silent >/dev/null 2>&1; then
            break
        fi
        kill -0 "$mysql_process"
        sleep 1
    done
    mysqladmin --socket=/run/mysqld/bootstrap.sock --user=root ping --silent >/dev/null
    # The native-password verifier avoids plaintext credentials in SQL or process arguments.
    mysql_verifier="*$(tr -d '\r\n' < /var/lib/lamp/secrets/database-password | openssl sha1 -binary | openssl sha1 | awk '{print toupper($NF)}')"
    mysql --socket=/run/mysqld/bootstrap.sock --user=root <<SQL
SET SESSION sql_log_bin = 0;
DROP DATABASE IF EXISTS test;
CREATE USER 'root'@'%' IDENTIFIED WITH mysql_native_password AS '$mysql_verifier';
GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password AS '$mysql_verifier';
SQL
    unset mysql_verifier
    kill -TERM "$mysql_process"
    wait "$mysql_process"
    trap - EXIT TERM INT
    rm /var/lib/lamp/mysql-initializing
fi

#### postgres
postgres_data=/var/lib/postgresql/18/main
if [[ -f /var/lib/lamp/postgresql-initializing ]]; then
    printf '%s\n' 'PostgreSQL initialization was interrupted. Inspect the data; it will not be overwritten.' >&2
    exit 1
fi
if [[ ! -f "$postgres_data/PG_VERSION" ]]; then
    if [[ -n "$(find /var/lib/postgresql -mindepth 1 -print -quit)" ]]; then
        printf '%s\n' 'PostgreSQL data directory is not empty; refusing to initialize it.' >&2
        exit 1
    fi
    touch /var/lib/lamp/postgresql-initializing
    install -d -o postgres -g postgres -m 700 "$postgres_data"
    install -o postgres -g postgres -m 600 /var/lib/lamp/secrets/database-password /run/postgresql/init-password
    runuser -u postgres -- /usr/lib/postgresql/18/bin/initdb -D "$postgres_data" \
        --username=postgres --pwfile=/run/postgresql/init-password \
        --auth-local=scram-sha-256 --auth-host=scram-sha-256 --encoding=UTF8 --locale=C.UTF-8
    rm /run/postgresql/init-password
    cat >> "$postgres_data/postgresql.conf" <<'POSTGRES'
listen_addresses = '*'
port = 5432
unix_socket_directories = '/run/postgresql'
# gin_pending_list_limit and data_sync_retry retain the PostgreSQL 18 defaults.
POSTGRES
    rm /var/lib/lamp/postgresql-initializing
fi
# Published port: connections arrive from the docker bridge, not from localhost.
for network in 0.0.0.0/0 ::/0; do
    grep -qF "host    all             all             $network" "$postgres_data/pg_hba.conf" \
        || printf 'host    all             all             %s            scram-sha-256\n' "$network" >> "$postgres_data/pg_hba.conf"
done
if [[ "$(< "$postgres_data/PG_VERSION")" != 18 ]]; then
    printf '%s\n' 'This image requires PostgreSQL 18 data; migrate other major versions explicitly.' >&2
    exit 1
fi

#### database client credentials
# Written at runtime, never included in image layers.
php8.5 <<'PHP'
<?php
declare(strict_types=1);
$password = rtrim(file_get_contents('/var/lib/lamp/secrets/database-password'), "\r\n");
umask(0077);
file_put_contents('/root/.my.cnf', "[client]\nuser=root\npassword=" . json_encode($password, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n");
$pgPassword = str_replace(['\\', ':'], ['\\\\', '\\:'], $password);
file_put_contents('/root/.pgpass', '*:5432:*:postgres:' . $pgPassword . "\n");
PHP
touch /var/log/php-error.log
chmod 666 /var/log/php-error.log
if [[ -e /etc/apache2/sites-enabled/000-blank.conf ]]; then
    a2dissite 000-blank.conf
fi

#### ssh server
if [[ ! -f /var/lib/lamp/ssh/ssh_host_ed25519_key ]]; then
    ssh-keygen -q -t ed25519 -N '' -f /var/lib/lamp/ssh/ssh_host_ed25519_key
fi
printf 'HostKey /var/lib/lamp/ssh/ssh_host_ed25519_key\n' > /etc/ssh/sshd_config.d/host-key.conf
# Mount authorized_keys in /root/.ssh to permit SSH access; password login stays disabled.

#### supervisor
# Master processes coordinate graceful shutdown of their own workers.
supervisor_program() {
    local name=$1 user=$2 priority=$3 command=$4
    local stop_signal=TERM stop_as_group=true
    local user_directory
    user_directory=$(getent passwd "$user" | cut -d: -f6)
    case "$name" in
        postgresql) stop_signal=INT; stop_as_group=false ;;
        postfix) stop_as_group=false ;;
        apache2) stop_signal=WINCH; stop_as_group=false ;;
        php*-fpm) stop_signal=QUIT; stop_as_group=false ;;
    esac
    cat > "/run/lamp-supervisor/$name.conf" <<PROGRAM
[program:$name]
command=$command
user=$user
environment=HOME="$user_directory",USER="$user",LOGNAME="$user"
priority=$priority
autostart=true
autorestart=unexpected
startsecs=2
stopsignal=$stop_signal
stopasgroup=$stop_as_group
killasgroup=true
stopwaitsecs=60
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
PROGRAM
}
supervisor_program rsyslog root 10 '/usr/sbin/rsyslogd -n -iNONE'
supervisor_program mysql mysql 20 '/usr/sbin/mysqld --authentication-policy=*:mysql_native_password,,'
supervisor_program postgresql postgres 20 '/usr/lib/postgresql/18/bin/postgres -D /var/lib/postgresql/18/main'
supervisor_program redis redis 20 '/usr/bin/redis-server /etc/redis/redis.conf'
for version in 5.6 7.0 7.1 7.2 7.3 7.4 8.0 8.1 8.2 8.3 8.4 8.5; do
    supervisor_program "php$version-fpm" root 30 "/usr/sbin/php-fpm$version -R --nodaemonize --fpm-config /etc/php/$version/fpm/php-fpm.conf"
done
supervisor_program apache2 root 40 "/bin/bash -c 'source /etc/apache2/envvars; exec /usr/sbin/apache2 -D FOREGROUND'"
supervisor_program postfix root 40 "/bin/bash -c 'trap \"/usr/sbin/postfix stop\" TERM INT; /usr/sbin/postfix start-fg & wait \$!'"
supervisor_program ssh root 40 '/usr/sbin/sshd -D -e'
supervisor_program cron root 50 '/usr/sbin/cron -f'

#### ngrok
if [[ -s /root/.config/ngrok/ngrok.yml ]]; then
    supervisor_program ngrok root 60 "$(command -v ngrok) start --all --config /root/.config/ngrok/ngrok.yml"
fi

#### start
python3 /opt/lamp/vpn.py prepare
# tunnel credentials and service token are derived from cloudflare.token; a docker-reset deletes them with the state
if [[ ! -f /var/lib/lamp/cloudflare/cloudflared-credentials.json ]]; then
    python3 /opt/lamp/cloudflare.py setup | grep -v 'next:'
fi
python3 /opt/lamp/control.py prepare
apachectl configtest
/usr/sbin/sshd -t
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
