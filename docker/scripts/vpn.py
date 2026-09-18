#!/usr/bin/env python3
import ipaddress
import os
from pathlib import Path
import re
import shlex
import sys

import yaml


if sys.argv[1:] not in (["prepare"], ["validate"]):
    raise SystemExit("Usage: vpn.py prepare|validate")

try:
    configuration = Path("/etc/lamp/config/settings.yaml")
    profiles = Path("/etc/lamp/vpn")
    runtime = Path("/run/lamp-vpn")
    supervisor = Path("/run/lamp-supervisor")
    settings = {"enabled": False, "tunnels": []}
    if configuration.exists():
        original = configuration.read_text()
        nodes = [yaml.compose(original, Loader=yaml.SafeLoader)]
        visited = set()
        while nodes:
            node = nodes.pop()
            if id(node) in visited:
                raise ValueError("YAML aliases are not supported")
            visited.add(id(node))
            if isinstance(node, yaml.MappingNode):
                keys = [key.value for key, _ in node.value
                        if isinstance(key, yaml.ScalarNode) and key.tag == "tag:yaml.org,2002:str"]
                if len(keys) != len(node.value) or len(set(keys)) != len(keys):
                    raise ValueError("Invalid YAML keys")
                nodes.extend(value for _, value in node.value)
            if isinstance(node, yaml.SequenceNode):
                nodes.extend(node.value)
        configuration_value = yaml.safe_load(original)
        if not isinstance(configuration_value, dict):
            raise ValueError("Invalid configuration")
        settings = configuration_value.get("vpn", settings)
    if (not isinstance(settings, dict) or set(settings) - {"enabled", "tunnels"}
            or type(settings.get("enabled")) is not bool or not isinstance(settings.get("tunnels"), list)):
        raise ValueError("Invalid VPN settings")
    files = {}
    names = set()
    aliases = {}
    for tunnel in settings["tunnels"] if settings["enabled"] else []:
        if not isinstance(tunnel, dict) or set(tunnel) - {"name", "type", "config", "username", "password", "routes", "hosts"}:
            raise ValueError("Invalid tunnel settings")
        name = tunnel.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,11}", name) or name in names:
            raise ValueError("Invalid or duplicate tunnel name")
        names.add(name)
        kind = tunnel.get("type", "openvpn")
        if kind not in ("openvpn", "wireguard") or not isinstance(tunnel.get("config"), str):
            raise ValueError("Invalid tunnel type or profile")
        profile = Path(tunnel["config"]).resolve()
        if not profile.is_relative_to(profiles) or not profile.is_file():
            raise ValueError("Profiles must be files below /etc/lamp/vpn")
        content = profile.read_text()
        hosts = tunnel.get("hosts", {})
        if not isinstance(hosts, dict):
            raise ValueError("Invalid host mappings")
        for hostname, address in hosts.items():
            if not isinstance(hostname, str) or not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9.-]*[a-z0-9])?", hostname):
                raise ValueError("Invalid host name")
            address = str(ipaddress.ip_address(address))
            if hostname in aliases and aliases[hostname] != address:
                raise ValueError("Conflicting host mapping")
            aliases[hostname] = address
        if kind == "openvpn":
            content = re.sub(r"(?im)^\s*(?:ip-win32|route-method|block-outside-dns|register-dns|win-sys|dhcp-renew|dhcp-release)\b[^\n]*", "", content)
            if re.search(r"(?im)^\s*(?:--)?(?:redirect-gateway|redirect-private|daemon|config)\b", content):
                raise ValueError("OpenVPN profiles must run in foreground without default-route redirects or nested configs")
            target = runtime / (name + ".ovpn")
            files[target] = content
            arguments = ["/usr/sbin/openvpn", "--cd", str(profile.parent), "--config", str(target),
                         "--route-nopull", "--auth-nocache", "--auth-retry", "none"]
            username, password = tunnel.get("username"), tunnel.get("password")
            if username is not None or password is not None:
                if any(not isinstance(value, str) or not value or any(char in value for char in "\r\n\0")
                       for value in (username, password)):
                    raise ValueError("Invalid VPN credentials")
                auth = runtime / (name + ".auth")
                files[auth] = username + "\n" + password + "\n"
                arguments += ["--auth-user-pass", str(auth)]
            routes = tunnel.get("routes")
            if not isinstance(routes, list) or not routes:
                raise ValueError("OpenVPN requires explicit routes")
            for route in routes:
                network = ipaddress.ip_network(route, strict=False)
                if not network.prefixlen:
                    raise ValueError("Default routes are not allowed")
                arguments += (["--route", str(network.network_address), str(network.netmask)] if network.version == 4
                              else ["--route-ipv6", str(network)])
            command = "exec " + shlex.join(arguments) + "\n"
        if kind == "wireguard":
            if any(key in tunnel for key in ("username", "password", "routes")):
                raise ValueError("WireGuard uses credentials and AllowedIPs from its profile")
            content = re.sub(r"(?im)^\s*DNS\s*=[^\n]*", "", content)
            content = re.sub(r"(?im)^\s*\[interface\]\s*$", "[Interface]", content)
            content = re.sub(r"(?im)^\s*\[peer\]\s*$", "[Peer]", content)
            for routes in re.findall(r"(?im)^\s*AllowedIPs\s*=([^\n#]+)", content):
                if any(not ipaddress.ip_network(route.strip(), strict=False).prefixlen for route in routes.split(",")):
                    raise ValueError("Default routes are not allowed")
            target = runtime / ("wg-" + name + ".conf")
            files[target] = content
            quoted = shlex.quote(str(target))
            command = (f"wg-quick up {quoted}\n"
                       f"trap 'wg-quick down {quoted}' EXIT\n"
                       "trap 'exit 0' TERM INT\n"
                       "while ip link show " + shlex.quote(target.stem) + " >/dev/null 2>&1; do\n"
                       "    sleep 5 & wait $!\n"
                       "done\nexit 1\n")
        launcher = runtime / (name + ".sh")
        files[launcher] = "#!/usr/bin/env bash\nset -euo pipefail\numask 077\n" + command
        files[supervisor / ("vpn-" + name + ".conf")] = (
            f"[program:vpn-{name}]\ncommand=/bin/bash {launcher}\nuser=root\n"
            "autostart=false\nautorestart=false\nstartsecs=2\nstartretries=0\n"
            "stopasgroup=true\nkillasgroup=true\nstopwaitsecs=30\npriority=15\numask=0077\n"
            f"stdout_logfile=/var/log/supervisor/vpn-{name}.log\nstdout_logfile_maxbytes=5MB\n"
            "stdout_logfile_backups=2\nredirect_stderr=true\n")
    hosts_file = Path("/etc/hosts")
    hosts_content = re.sub(r"(?ms)^# BEGIN lamp-vpn-hosts\n.*?^# END lamp-vpn-hosts\n?", "", hosts_file.read_text()).rstrip() + "\n"
    if aliases:
        hosts_content += "# BEGIN lamp-vpn-hosts\n"
        hosts_content += "".join(f"{address} {hostname}\n" for hostname, address in sorted(aliases.items()))
        hosts_content += "# END lamp-vpn-hosts\n"
    if sys.argv[1] == "prepare":
        os.umask(0o077)
        runtime.mkdir(mode=0o700, exist_ok=True)
        runtime.chmod(0o700)
        for path in [*supervisor.glob("vpn-*.conf"), *runtime.iterdir()]:
            path.unlink()
        for path, content in files.items():
            path.write_text(content)
            path.chmod(0o600)
        hosts_file.write_text(hosts_content)
        if configuration.exists():
            configuration.chmod(0o600)
    print(f"VPN configuration valid: {len(names)} manual tunnel(s); no connection started.")
except (OSError, ValueError, TypeError, yaml.YAMLError):
    raise SystemExit("Invalid VPN configuration; check settings.yaml under vpn and private profiles. Details withheld to protect credentials.") from None
