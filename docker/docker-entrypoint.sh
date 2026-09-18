#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != init ]]; then
    # without a mounted .data folder /etc/lamp is the clone of the data repository inside the state volume
    if ! mountpoint -q /etc/lamp; then
        rm -rf /etc/lamp
        ln -s /var/lib/lamp/data /etc/lamp
    fi
    exec "$@"
fi

if [[ "$#" -ne 1 ]]; then
    printf 'init does not accept additional arguments.\n' >&2
    exit 1
fi
if [[ ! -d /install ]]; then
    printf 'Mount the installation directory at /install.\n' >&2
    exit 1
fi

# init only ships the host cli and compose files; .data and .config are never touched, so it also refreshes an existing installation.
temporary_directory="/install/.lamp-init.$$"
trap 'rm -rf "$temporary_directory"' EXIT INT TERM
mkdir "$temporary_directory" "$temporary_directory/docker"
cp /app/lamp "$temporary_directory/lamp"
cp /app/docker/docker-compose.yml /app/docker/docker-compose.data.yml "$temporary_directory/docker/"
chmod 755 "$temporary_directory/lamp"
if [[ "$(id -u)" -eq 0 ]]; then
    chown -hR "$(stat -c '%u:%g' /install)" "$temporary_directory"
fi
mkdir -p /install/docker
mv -f "$temporary_directory/lamp" /install/lamp
mv -f "$temporary_directory/docker/docker-compose.yml" "$temporary_directory/docker/docker-compose.data.yml" /install/docker/
rm -rf "$temporary_directory"
trap - EXIT INT TERM

# boilerplate that can be edited before the first start; existing files are kept
bash /install/lamp presets
if [[ "$(id -u)" -eq 0 ]]; then
    chown -hR "$(stat -c '%u:%g' /install)" /install/.config /install/docker/docker-compose.override.yml
fi

printf 'lamp initialized. adjust .config/env.yaml and docker/docker-compose.override.yml if needed, then run ./lamp start.\n'
