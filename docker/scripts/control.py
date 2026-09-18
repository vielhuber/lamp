#!/usr/bin/env python3
import argparse
import fcntl
import fnmatch
import hashlib
import http.client
import json
import os
import pty
from pathlib import Path
import re
import secrets
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import uuid
from urllib.parse import urlsplit

import yaml

CONFIGURATION = Path("/etc/lamp")
STATE = Path("/var/lib/lamp")
PROJECTS = Path("/var/www")
SITES = Path("/etc/apache2/sites-available")
ENABLED = Path("/etc/apache2/sites-enabled")
APACHE_SETTINGS = Path("/etc/apache2/conf-available/lamp.conf")
MAILNAME = Path("/etc/mailname")
SASL_PASSWORD = Path("/etc/postfix/sasl_passwd")
LETSENCRYPT = Path("/etc/letsencrypt")
HOSTS = Path("/etc/hosts")
DATABASE_ENGINES = ("mysql", "postgres", "sqlite")
PHP_VERSIONS = ("5.6", "7.0", "7.1", "7.2", "7.3", "7.4", "8.0", "8.1", "8.2", "8.3", "8.4", "8.5")
ENVIRONMENT_DEFAULTS = {"branch": None, "subdomain": None, "aliases": None, "directory": None, "db_name": None, "db_engine": None,
                        "webroot": None, "php": None, "vpn": None, "proxy_port": None, "proxy_exclude": None, "visibility": "private", "build": None}


def write_json(path, value):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
        json.dump(value, temporary, indent=4)
        temporary.write("\n")
    os.replace(temporary.name, path)


def run(arguments, *, input=None, cwd=None, environment=None, capture=False, output=None):
    result = subprocess.run(arguments, input=input, cwd=cwd, env=environment, text=True,
                            stdout=subprocess.PIPE if capture else output or subprocess.DEVNULL,
                            stderr=output or subprocess.DEVNULL)
    if result.returncode:
        raise RuntimeError(f"{Path(arguments[0]).name} failed (exit {result.returncode})"
                           + ("." if output else "; command output was withheld to protect credentials."))
    return result.stdout or ""


def validate_identity(identity):
    if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{12}", identity):
        raise ValueError("Invalid environment ID.")
    return identity


def resolve_identity(value):
    """Accept an environment id or a subdomain label and return the id."""
    if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{12}", value):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ValueError("Invalid environment ID or subdomain.")
    matches = [environment["id"] for environment in environments() if value in subdomain_labels(environment)]
    if not matches:
        raise ValueError(f"No environment has the subdomain {value}.")
    if len(matches) > 1:
        raise ValueError(f"Several environments have the subdomain {value}; use the id.")
    return matches[0]


def configuration():
    value = yaml.safe_load((CONFIGURATION / "config" / "settings.yaml").read_text())
    sections = {"git": ("name", "email"), "apache": ("admin",), "postfix": ("hostname", "relayhost", "username", "password"),
                "cloudflare": ("token", "email"), "database": ("password",), "composer": ("github",)}
    if (not isinstance(value, dict) or "domain" not in value or set(value) - {"domain", "vpn", "php", *sections}
            or any(value.get(key) is not None and not isinstance(value[key], dict) for key in ("vpn", "php", *sections))):
        raise ValueError("settings.yaml must contain domain and optionally git, apache, postfix, cloudflare, database, php and vpn mappings; see README.md.")
    php = value.get("php") or {}
    if set(php) - {"xdebug"} or any(not isinstance(entry, bool) for entry in php.values()):
        raise ValueError("php may contain only xdebug: true or false.")
    dns_name = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
    domain = value["domain"]
    if not isinstance(domain, str) or len(domain) > 240 or not re.fullmatch(dns_name, domain):
        raise ValueError("domain must be a lowercase DNS name without scheme, path or port.")
    for section, keys in sections.items():
        entries = value.get(section) or {}
        if (set(entries) - set(keys)
                or any(not isinstance(entry, str) or not entry.strip() or re.search(r"[\r\n\0]", entry) for entry in entries.values())
                or any(re.search(r"\s", entries[key]) for key in ("admin", "hostname", "relayhost", "token", "email", "github") if key in entries)
                or (section == "cloudflare" and entries and (set(entries) != set(keys) or "@" not in entries["email"]))
                or ("hostname" in entries and not re.fullmatch(dns_name, entries["hostname"]))
                or ("relayhost" in entries and not re.fullmatch(r"\[?[A-Za-z0-9.-]+\]?(?::[0-9]{1,5})?", entries["relayhost"]))
                or (section == "postfix" and (("username" in entries) != ("password" in entries) or ("username" in entries and "relayhost" not in entries)))):
            raise ValueError(f"{section} may contain only {', '.join(keys)} as nonempty single-line values; hostname must be a lowercase DNS name, relayhost a host or [host]:port, username and password need each other and a relayhost, cloudflare needs token and email.")
    return value


def apply_settings(settings):
    # xdebug stays loaded in trigger mode unless php.xdebug is false; the module is toggled for every php version, cli and fpm.
    run(["phpenmod" if (settings.get("php") or {}).get("xdebug", True) else "phpdismod", "-v", "ALL", "-s", "ALL", "xdebug"])
    github = (settings.get("composer") or {}).get("github")
    if github:
        run(["composer", "config", "--global", "github-oauth.github.com", github])
    else:
        subprocess.run(["composer", "config", "--global", "--unset", "github-oauth.github.com"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    identity = settings.get("git") or {}
    for key in ("name", "email"):
        if key in identity:
            run(["git", "config", "--global", "user." + key, identity[key]])
        else:
            subprocess.run(["git", "config", "--global", "--unset-all", "user." + key], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    admin = (settings.get("apache") or {}).get("admin", "webmaster@localhost")
    APACHE_SETTINGS.write_text(f"Timeout 3000\nServerAdmin {admin}\nServerName localhost\n")
    hostname = (settings.get("postfix") or {}).get("hostname", "lamp.localdomain")
    MAILNAME.write_text(hostname + "\n")
    postfix = settings.get("postfix") or {}
    relayhost = postfix.get("relayhost", "")
    run(["postconf", "-e", "myhostname = " + hostname, "relayhost = " + relayhost])
    if "username" in postfix:
        SASL_PASSWORD.write_text(f"{relayhost} {postfix['username']}:{postfix['password']}\n")
        SASL_PASSWORD.chmod(0o600)
        run(["postmap", str(SASL_PASSWORD)])
        if SASL_PASSWORD.with_suffix(".db").exists():
            SASL_PASSWORD.with_suffix(".db").chmod(0o600)
    else:
        SASL_PASSWORD.unlink(missing_ok=True)
        SASL_PASSWORD.with_suffix(".db").unlink(missing_ok=True)


def load_environment(identity):
    identity = validate_identity(identity)
    path = STATE / "environments" / identity / "environment.json"
    if not path.is_file():
        raise ValueError("Environment not found.")
    environment = json.loads(path.read_text())
    if environment["id"] != identity:
        raise ValueError("Environment metadata does not match its directory.")
    return environment


def environments():
    return [load_environment(path.parent.name) for path in sorted((STATE / "environments").glob("*/environment.json"))]


def save_environment(environment):
    write_json(STATE / "environments" / environment["id"] / "environment.json", environment)


def specification(environment):
    return explicit_directory({**ENVIRONMENT_DEFAULTS, **migrate_specification(environment.get("applied") or {
        key: environment.get(key, default) for key, default in {"git": None, "syncdb": None, **ENVIRONMENT_DEFAULTS}.items()})})


def explicit_directory(value):
    # Static entries always name their folder; the first subdomain label is the default.
    labels = subdomain_labels(value)
    if labels and value.get("directory") is None:
        value["directory"] = labels[0]
    return value


def root_password():
    return (STATE / "secrets" / "database-password").read_text().strip()


def ensure_certificate(settings):
    """Obtain or renew the Let's Encrypt wildcard certificate for the domain through the cloudflare dns challenge."""
    token = (settings.get("cloudflare") or {}).get("token")
    if not token:
        raise ValueError("Set cloudflare.token and cloudflare.email in settings.yaml; the wildcard certificate needs the dns challenge.")
    credentials = STATE / "secrets" / "cloudflare.ini"
    credentials.write_text("dns_cloudflare_api_token = " + token + "\n")
    credentials.chmod(0o600)
    domain = settings["domain"]
    if (LETSENCRYPT / "live" / domain / "fullchain.pem").exists():
        run(["certbot", "renew", "--non-interactive", "--quiet"], output=sys.stderr)
        return
    print(f"Requesting the Let's Encrypt certificate for {domain} and *.{domain}.", file=sys.stderr)
    run(["certbot", "certonly", "--non-interactive", "--agree-tos", "--email", settings["cloudflare"]["email"], "--dns-cloudflare",
         "--dns-cloudflare-credentials", str(credentials), "--dns-cloudflare-propagation-seconds", "30",
         "--cert-name", domain, "-d", domain, "-d", "*." + domain], output=sys.stderr)


def sync_hosts():
    """Point every environment hostname at the container itself, so builds and tests reach the local origin directly."""
    names = sorted({host for environment in environments() for host in hostnames(environment)})
    lines = [line for line in HOSTS.read_text().splitlines() if not line.endswith("# lamp")]
    lines += [f"127.0.0.1 {host} # lamp" for host in names]
    HOSTS.write_text("\n".join(lines) + "\n")


def apply_database_password(settings):
    """Set the mysql root and postgres passwords to database.password from settings.yaml, keeping every consumer in step."""
    password = (settings.get("database") or {}).get("password")
    if password is None or password == root_password():
        return False
    escaped = password.replace("\\", "\\\\").replace("'", "\\'")
    run(["mysql"], input=f"ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY '{escaped}'; "
                         f"ALTER USER 'root'@'%' IDENTIFIED WITH mysql_native_password BY '{escaped}';\n")
    run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"], input="ALTER ROLE postgres PASSWORD '" + password.replace("'", "''") + "';\n")
    secret = STATE / "secrets" / "database-password"
    secret.write_text(password + "\n")
    secret.chmod(0o600)
    Path("/root/.my.cnf").write_text("[client]\nuser=root\npassword=" + json.dumps(password, ensure_ascii=False) + "\n")
    Path("/root/.my.cnf").chmod(0o600)
    Path("/root/.pgpass").write_text("*:5432:*:postgres:" + password.replace("\\", "\\\\").replace(":", "\\:") + "\n")
    Path("/root/.pgpass").chmod(0o600)
    for environment in environments():
        if environment["status"] == "ready" and environment.get("db_name"):
            write_setup(environment)
            vhost(environment)
    reload_apache()
    return True


def migrate_specification(value):
    if not isinstance(value, dict) or "syncdb" not in value:
        return value
    value = dict(value)
    profile = value.pop("syncdb")
    if profile is not None:
        if not isinstance(profile, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", profile):
            raise ValueError("Invalid syncdb profile name.")
        build = value.get("build")
        if build is not None and not isinstance(build, str):
            raise ValueError("build must be a string or null.")
        value["build"] = "syncdb " + shlex.quote(profile) + (" && {\n" + build + "\n}" if build else "")
    return value


def validate_specification(value):
    if not isinstance(value, dict) or set(value) - {"git", *ENVIRONMENT_DEFAULTS}:
        raise ValueError("Environment settings must contain only git, branch, php, build, subdomain, aliases, directory, db_name, db_engine, webroot, proxy_port, proxy_exclude, vpn and visibility.")
    value = {**ENVIRONMENT_DEFAULTS, **value}
    if value["visibility"] is None:
        value["visibility"] = "private"
    if value["visibility"] not in ("private", "public"):
        raise ValueError("visibility must be private or public.")
    value.setdefault("git", None)
    if value["aliases"] is not None:
        if (not isinstance(value["aliases"], list)
                or any(not isinstance(alias, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", alias)
                       for alias in value["aliases"])
                or len(set(value["aliases"])) != len(value["aliases"])):
            raise ValueError("aliases must be a list of unique lowercase DNS labels or null.")
        value["aliases"] = value["aliases"] or None
    if value["git"] is not None:
        build_script(value["git"])
    if value["php"] is not None and value["php"] not in PHP_VERSIONS:
        raise ValueError("Invalid PHP version; quote PHP versions in YAML.")
    labels = subdomain_labels(value)
    explicit_directory(value)
    if (value["db_name"] is None) != (value["db_engine"] is None):
        raise ValueError("db_name and db_engine need each other.")
    if value["db_name"] is not None:
        if not labels:
            raise ValueError("db_name is for static environments; dynamic environments get isolated databases.")
        if value["db_engine"] not in DATABASE_ENGINES:
            raise ValueError("db_engine must be mysql, postgres or sqlite.")
        if not isinstance(value["db_name"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}", value["db_name"]):
            raise ValueError("db_name must be a database name of letters, digits, underscores and hyphens.")
    if value["directory"] is not None and (not labels or not isinstance(value["directory"], str)
                                           or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}(?:/[a-z0-9][a-z0-9_-]{0,62}){0,3}", value["directory"])):
        raise ValueError("directory must be a lowercase folder path under /var/www without parent segments and requires a subdomain.")
    for key in ("branch", "build", "webroot"):
        if value[key] is not None and (not isinstance(value[key], str) or not value[key] or "\0" in value[key]):
            raise ValueError("branch, build and webroot must be nonempty strings or null.")
    if value["webroot"] is not None and (not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", value["webroot"])
                                         or ".." in Path(value["webroot"]).parts):
        raise ValueError("webroot must be a relative project directory without parent traversal; use . for the repository root.")
    if value["proxy_port"] is not None and (type(value["proxy_port"]) is not int or not 1 <= value["proxy_port"] <= 65535):
        raise ValueError("proxy_port must be an integer between 1 and 65535 or null.")
    if value["proxy_exclude"] is not None and (value["proxy_port"] is None or not isinstance(value["proxy_exclude"], str)
            or not re.fullmatch(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/?", value["proxy_exclude"])):
        raise ValueError("proxy_exclude requires proxy_port and an absolute path such as /admin, without special characters.")
    if value["branch"]:
        run(["git", "check-ref-format", "--branch", value["branch"]])
    if value["vpn"] is not None:
        if not isinstance(value["vpn"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,11}", value["vpn"]):
            raise ValueError("vpn must be a configured tunnel name or null.")
        run(["python3", str(Path(__file__).with_name("vpn.py")), "validate"])
        vpn = configuration().get("vpn", {"enabled": False, "tunnels": []})
        if not vpn["enabled"] or value["vpn"] not in {tunnel["name"] for tunnel in vpn["tunnels"]}:
            raise ValueError("The required VPN must be enabled and configured in settings.yaml under vpn.")
    return value


def build_script(remote):
    match = re.fullmatch(r"(?:git@([A-Za-z0-9.-]+):|https://([A-Za-z0-9.-]+)/)([A-Za-z0-9_./-]+)", remote) if isinstance(remote, str) else None
    if match is None:
        raise ValueError("Use an SSH git@host:path or credential-free HTTPS repository URL.")
    host = (match[1] or match[2]).lower()
    repository = match[3].rstrip("/").removesuffix(".git")
    if any(part in ("", ".", "..") for part in (host, *repository.split("/"))):
        raise ValueError("Repository URLs must not contain empty or relative path segments.")
    return CONFIGURATION / "build" / (host + "-" + repository.replace("/", "-") + ".sh")


def resolve_build(value):
    command = value["build"]
    if command is not None:
        return command, hashlib.sha256(command.encode()).hexdigest()
    if value["git"] is None:
        return None, None
    path = build_script(value["git"])
    if not path.resolve().is_relative_to((CONFIGURATION / "build").resolve()):
        raise ValueError("Build scripts must resolve inside .data/build.")
    try:
        contents = path.read_bytes()
    except FileNotFoundError:
        return None, None
    return "source " + shlex.quote(str(path)), hashlib.sha256(contents).hexdigest()


def resolve_php(project, version):
    if version is not None:
        return version
    try:
        version = (project / ".phprc").read_text().strip()
    except FileNotFoundError:
        return "8.5"
    if version not in PHP_VERSIONS:
        raise ValueError("The repository .phprc must contain one supported PHP version, for example 8.5.")
    return version


def ensure_vpn(name):
    if name is None:
        return
    command = ["supervisorctl", "pid", "vpn-" + name]
    status = subprocess.run(command, text=True, capture_output=True)
    if status.returncode != 0 and (status.returncode != 7 or status.stdout.strip() != "0"):
        raise RuntimeError("Cannot determine the required VPN process state; details withheld.")
    if int(status.stdout.strip()) > 0:
        return
    print(f"Starting required VPN {name}.", file=sys.stderr)
    run(["supervisorctl", "start", "vpn-" + name])
    if int(run(command, capture=True).strip()) <= 0:
        raise RuntimeError("The required VPN process did not start.")


def subdomain_labels(value):
    subdomain = value.get("subdomain")
    if subdomain is None:
        return []
    labels = [subdomain] if isinstance(subdomain, str) else subdomain
    if (not isinstance(labels, list) or not labels
            or any(not isinstance(label, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                   for label in labels)
            or len(set(labels)) != len(labels)):
        raise ValueError("subdomain must be a lowercase DNS label, a nonempty list of unique lowercase DNS labels or null.")
    return labels


def environment_hostname(identity, value, settings):
    labels = subdomain_labels(value)
    subdomain = labels[0] if labels else validate_identity(identity)
    hostname = subdomain + "." + settings["domain"]
    if len(hostname) > 253:
        raise ValueError("The resulting hostname exceeds 253 characters.")
    return hostname


def environment_url(identity, value, settings):
    return "https://" + environment_hostname(identity, value, settings)


def environment_hostnames(identity, value, settings):
    primary = environment_hostname(identity, value, settings)
    label = primary.split(".", 1)[0]
    return [primary, *(environment_hostname(identity, {"subdomain": secondary}, settings)
                       for secondary in subdomain_labels(value)[1:]),
            *(environment_hostname(identity, {"subdomain": label + "-" + alias}, settings)
                       for alias in value.get("aliases") or [])]


def hostnames(environment):
    return environment.get("hostnames") or [environment["hostname"]]


def project_path(identity, value):
    validate_identity(identity)
    labels = subdomain_labels(value)
    project = PROJECTS / (value.get("directory") or labels[0]) if labels else PROJECTS / "_environments" / identity
    if project.resolve() != project.absolute():
        raise ValueError("Project paths must not contain symlinks.")
    return project


def environment_project(environment):
    project = project_path(environment["id"], environment)
    if str(project) != environment["path"]:
        raise ValueError("Stored project path does not match its environment.")
    if (environment.get("project_owned") or not subdomain_labels(environment)) and project.exists():
        status = project.stat()
        if [status.st_dev, status.st_ino] != environment.get("project_identity"):
            raise ValueError("The managed project directory was replaced; refusing to modify or delete it.")
    return project


def validate_domains(desired, settings):
    names = [host for identity, value in desired.items() for host in environment_hostnames(identity, value, settings)]
    if len(set(names)) != len(names):
        raise ValueError("Multiple environments request the same domain; every hostname must be unique.")


def ordered_settings(settings):
    return {key: settings.get(key) for key in ("git", *ENVIRONMENT_DEFAULTS)
            if key not in ("php", "build", "aliases") or settings.get(key) is not None}


def write_desired(entries, original):
    path = CONFIGURATION / "config" / "env.yaml"
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temporary:
        # One document per entry, separated by blank lines, so the file stays readable when edited by hand.
        temporary.write("\n".join(yaml.safe_dump([ordered_settings(entry)], sort_keys=False, allow_unicode=True) for entry in entries) or "[]\n")
    try:
        if (path.read_bytes() if path.exists() else None) != original:
            raise ValueError("env.yaml changed concurrently; retry without overwriting the edit.")
        os.replace(temporary.name, path)
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def read_desired():
    path = CONFIGURATION / "config" / "env.yaml"
    if not path.exists():
        write_desired([entry for entry in (validate_specification(specification(item)) for item in environments()) if subdomain_labels(entry)], None)
    original = path.read_bytes()
    node = yaml.compose(original, Loader=yaml.SafeLoader)
    legacy = isinstance(node, yaml.MappingNode)
    if not isinstance(node, (yaml.SequenceNode, yaml.MappingNode)):
        raise ValueError("env.yaml must contain a list of static environments; use [] for none, not an empty file.")
    for mapping in ([settings for _, settings in node.value] if legacy else node.value):
        if not isinstance(mapping, yaml.MappingNode):
            raise ValueError("Each environment must contain a settings mapping.")
        keys = [key.value for key, _ in mapping.value if isinstance(key, yaml.ScalarNode) and key.tag == "tag:yaml.org,2002:str"]
        if len(keys) != len(mapping.value) or len(set(keys)) != len(keys):
            raise ValueError("YAML keys must be unique strings; merge keys are not supported.")
    value = yaml.safe_load(original)
    raw = list(value.values()) if legacy else value
    entries = [validate_specification(migrate_specification(settings)) for settings in raw]
    if legacy:
        # Older files were keyed by ID and listed dynamic environments; those live in the runtime state only now.
        entries = [entry for entry in entries if subdomain_labels(entry)]
    if any(not subdomain_labels(entry) for entry in entries):
        raise ValueError("env.yaml lists static environments only; every entry needs a subdomain. Create dynamic environments with lamp add.")
    if any(entry in entries[index + 1:] for index, entry in enumerate(entries)):
        raise ValueError("env.yaml contains the same environment twice.")
    if legacy or any(list(settings) != list(ordered_settings(entry)) for settings, entry in zip(raw, entries)):
        write_desired(entries, original)
        original = path.read_bytes()
    path.chmod(0o600)
    return entries, original


def desired_state(entries, current=None):
    if current is None:
        current = {item["id"]: item for item in environments()}
    desired = {}
    pending = list(entries)
    for identity, environment in current.items():
        applied = specification(environment)
        if not subdomain_labels(applied):
            desired[identity] = applied
        elif applied in pending:
            pending.remove(applied)
            desired[identity] = applied
    for entry in pending:
        identity = uuid.uuid4().hex[:12]
        while identity in desired or (STATE / "environments" / identity).exists():
            identity = uuid.uuid4().hex[:12]
        desired[identity] = entry
    return desired


def check_checkout(environment, desired):
    project = environment_project(environment)
    if project != project_path(environment["id"], desired):
        raise ValueError("Changing subdomain would change the project directory; create a separate environment instead.")
    if not environment.get("project_owned"):
        return
    applied = environment.get("checkout", specification(environment))
    if all(applied[key] == desired[key] for key in ("git", "branch")):
        return
    if project.is_symlink():
        raise ValueError("Refusing to change a checkout replaced by a symlink.")
    if not project.exists():
        return
    if environment.get("status") == "failed" and not any(project.iterdir()):
        return
    dirty = run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=project, capture=True)
    unpublished = run(["git", "rev-list", "--max-count=1", "HEAD", "--branches", "--not", "--remotes"], cwd=project, capture=True)
    if dirty or unpublished:
        raise ValueError(f"Environment {environment['id']} has local changes or unpublished commits; preserve them before changing git/branch.")


def reconcile(settings, desired, *, validate_only=False):
    validate_domains(desired, settings)
    current = {item["id"]: item for item in environments()}
    for identity, value in desired.items():
        if identity in current:
            check_checkout(current[identity], value)
    if validate_only:
        return
    apply_database_password(settings)
    sync_visibility(settings, desired, publish=False)
    for identity, value in desired.items():
        if identity in current and (current[identity].get("project_owned") or current[identity].get("setup_environment")):
            ensure_vpn(value["vpn"])
    retired = False
    for identity, environment in current.items():
        if identity not in desired or environment_hostname(identity, desired[identity], settings) != environment["hostname"]:
            site = ENABLED / ("lamp-" + identity + ".conf")
            if site.exists():
                site.unlink()
                retired = True
    if retired:
        reload_apache()
    for identity in current.keys() - desired.keys():
        remove(identity)
    batch = {"php": set(), "reload": False, "connected": False}
    try:
        for identity, value in desired.items():
            environment = current.get(identity)
            if not subdomain_labels(value) and environment is not None:
                # Existing dynamic environments are kept as they are; only add --id, build <id> and remove <id> touch them.
                continue
            apply_entry(settings, identity, value, environment, batch)
    finally:
        for version in sorted(batch["php"]):
            run(["supervisorctl", "restart", "php" + version + "-fpm"])
        if batch["reload"]:
            reload_apache()
    sync_visibility(settings, desired)


def apply_entry(settings, identity, value, environment, batch):
    applied = {**specification(environment), "visibility": value["visibility"],
               "subdomain": value["subdomain"], "aliases": value["aliases"]} if environment else None
    reason = None
    if environment is None:
        reason = "new entry in env.yaml"
    elif environment["status"] != "ready":
        reason = "previous attempt " + environment["status"] + ", retrying"
    elif applied != value:
        reason = "settings changed in env.yaml"
    elif environment["url"] != environment_url(identity, value, settings):
        reason = "domain changed, new url " + environment_url(identity, value, settings)
    elif environment["php"] != resolve_php(environment_project(environment), value["php"]):
        reason = "php version changed"
    elif environment.get("project_owned") and environment.get("build_hash") != resolve_build(value)[1]:
        reason = "build script changed, rebuilding"
    if reason is not None:
        add(argparse.Namespace(**value), settings, identity, environment, reason=reason, batch=batch)
    elif environment.get("visibility") != value["visibility"] or specification(environment) != value:
        if hostnames(environment) != environment_hostnames(identity, value, settings):
            environment["aliases"] = value["aliases"]
            environment["hostnames"] = environment_hostnames(identity, value, settings)
            try:
                vhost(environment)
            except (OSError, RuntimeError):
                (ENABLED / ("lamp-" + identity + ".conf")).unlink(missing_ok=True)
                raise
            finally:
                batch["reload"] = True
        environment["subdomain"] = value["subdomain"]
        environment["visibility"] = value["visibility"]
        environment["applied"] = value
        save_environment(environment)


def cloudflare_request(method, resource, payload=None):
    token = (configuration().get("cloudflare") or {}).get("token")
    if not token:
        raise ValueError("Set cloudflare.token and cloudflare.email in settings.yaml and run lamp cloudflare-setup.")
    account = json.loads((CONFIGURATION / "cloudflare" / "cloudflared-credentials.json").read_text()).get("AccountTag")
    if not isinstance(account, str) or not re.fullmatch(r"[a-f0-9]{32}", account):
        raise ValueError("Invalid cloudflared-credentials.json; run lamp cloudflare-setup.")
    connection = http.client.HTTPSConnection("api.cloudflare.com", timeout=20)
    try:
        connection.request(method, "/client/v4/accounts/" + account + "/access/apps" + resource,
                           body=json.dumps(payload) if payload is not None else None,
                           headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = connection.getresponse()
        result = json.loads(response.read())
        if response.status >= 300 or not result.get("success"):
            raise RuntimeError(f"Cloudflare Access API failed (HTTP {response.status}); response withheld to protect credentials.")
        return result
    except (OSError, http.client.HTTPException, ValueError) as error:
        raise RuntimeError(f"Cloudflare Access API connection or response failed ({type(error).__name__}); check token, permissions and connectivity.") from None
    finally:
        connection.close()


def access_destinations(application):
    return {value.rstrip("/") for value in [application.get("domain"),
            *(item.get("uri") for item in application.get("destinations", []) if item.get("type") == "public")]
            if isinstance(value, str) and value}


def cloudflare_list(resource=""):
    records = []
    page = 1
    while True:
        result = cloudflare_request("GET", resource + f"?page={page}&per_page=100")
        records.extend(result["result"])
        if page >= result.get("result_info", {}).get("total_pages", 1):
            return records
        page += 1


def wait_access(hostname, public):
    deadline = time.monotonic() + 30
    while True:
        connection = http.client.HTTPSConnection(hostname, timeout=5)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            location = urlsplit(response.getheader("Location", ""))
            protected = (response.status in (302, 303, 307) and location.scheme == "https"
                         and (location.hostname or "").endswith(".cloudflareaccess.com")
                         and location.path.startswith("/cdn-cgi/access/login"))
            if protected == (not public) and response.status < 500:
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Cloudflare Access change for {hostname} could not be verified; its origin remains disabled.")
        time.sleep(1)


def sync_visibility(settings, desired, *, identities=None, publish=True):
    current = {item["id"]: item for item in environments()}
    scope = set(desired) | set(current) if identities is None else set(identities)
    prefix = "lamp-public:" + settings["domain"] + ":"
    try:
        applications = cloudflare_list()
        wildcard = [app for app in applications if "*." + settings["domain"] in access_destinations(app)]
        if len(wildcard) != 1 or wildcard[0].get("type") != "self_hosted":
            raise ValueError("Configure exactly one self-hosted wildcard Access application before managing visibility.")
        policies = cloudflare_list("/" + wildcard[0]["id"] + "/policies")
        if not policies or any(policy.get("decision") not in ("allow", "deny", "non_identity")
                               or (policy.get("decision") == "non_identity" and {"everyone": {}} in policy.get("include", [])) for policy in policies):
            raise ValueError("The wildcard Access application must have authentication policies and no Bypass rule.")
        managed = {}
        for app in applications:
            name = app.get("name") or ""
            if name.startswith(prefix) and re.fullmatch(r"[a-f0-9]{12}", name[len(prefix):]):
                identity = name[len(prefix):]
                host = app.get("domain", "")
                if (identity in managed or app.get("type") != "self_hosted"
                        or not re.fullmatch(r"[a-z0-9-]+\." + re.escape(settings["domain"]), host)
                        or any(not re.fullmatch(r"[a-z0-9-]+\." + re.escape(settings["domain"]), destination)
                               for destination in access_destinations(app))):
                    raise ValueError("Conflicting LAMP Access application; resolve it in Cloudflare before retrying.")
                managed[identity] = app
        if identities is None:
            scope |= set(managed)
        for identity in scope & desired.keys():
            names = environment_hostnames(identity, desired[identity], settings)
            for app in applications:
                if app["id"] == wildcard[0]["id"] or any(app == owned and owner in scope for owner, owned in managed.items()):
                    continue
                if any(fnmatch.fnmatchcase(hostname, destination.split("/", 1)[0])
                       for hostname in names for destination in access_destinations(app)):
                    raise ValueError("A separate Access application already covers an environment hostname; LAMP will not overwrite it.")
        for identity, app in managed.items():
            if identity not in scope:
                continue
            value = desired.get(identity)
            if (value is not None and value["visibility"] == "public"
                    and access_destinations(app) == set(environment_hostnames(identity, value, settings))):
                continue
            site = ENABLED / ("lamp-" + identity + ".conf")
            if site.exists():
                site.unlink()
                if Path("/run/apache2/apache2.pid").exists():
                    reload_apache()
            cloudflare_request("DELETE", "/" + app["id"])
            if value is not None:
                for hostname in sorted(access_destinations(app) | set(environment_hostnames(identity, value, settings))):
                    wait_access(hostname, False)
        if publish:
            for identity in scope & desired.keys():
                value = desired[identity]
                if value["visibility"] != "public":
                    continue
                hostname = environment_hostname(identity, value, settings)
                app = managed.get(identity)
                policy = {"name": "Public", "decision": "bypass", "include": [{"everyone": {}}], "precedence": 1}
                payload = {"name": prefix + identity, "domain": hostname, "type": "self_hosted",
                           "app_launcher_visible": False, "policies": [policy]}
                names = environment_hostnames(identity, value, settings)
                if len(names) > 1:
                    payload["destinations"] = [{"type": "public", "uri": host} for host in names]
                existing = app is not None and access_destinations(app) == set(names)
                policies = cloudflare_list("/" + app["id"] + "/policies") if existing else []
                if not (len(policies) == 1 and policies[0].get("decision") == "bypass"
                        and policies[0].get("include") == [{"everyone": {}}]
                        and not policies[0].get("require") and not policies[0].get("exclude")):
                    cloudflare_request("PUT" if existing else "POST", "/" + app["id"] if existing else "", payload)
                    for hostname in names:
                        wait_access(hostname, True)
        restored = False
        for identity in scope & desired.keys() & current.keys():
            if (current[identity]["status"] == "ready"
                    and hostnames(current[identity]) == environment_hostnames(identity, desired[identity], settings)
                    and not (ENABLED / ("lamp-" + identity + ".conf")).exists()):
                vhost(current[identity])
                restored = True
        if restored and Path("/run/apache2/apache2.pid").exists():
            reload_apache()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        disabled = False
        for identity in scope:
            site = ENABLED / ("lamp-" + identity + ".conf")
            if site.exists():
                site.unlink()
                disabled = True
        if disabled and Path("/run/apache2/apache2.pid").exists():
            reload_apache()
        raise


def database(environment, remove=False):
    name = "lamp_" + validate_identity(environment["id"])
    if subdomain_labels(environment) and not remove:
        # The fixed database is created when missing and never touched otherwise; lamp never drops it.
        engine, fixed = environment.get("db_engine"), environment.get("db_name")
        if engine == "mysql":
            run(["mysql"], input=f"CREATE DATABASE IF NOT EXISTS `{fixed}`;\n")
        elif engine == "postgres":
            existing = run(["psql", "-X", "-U", "postgres", "-d", "postgres", "-At"], input=f"SELECT datname FROM pg_database WHERE datname='{fixed}';\n", capture=True)
            if not existing.strip():
                run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"], input=f'CREATE DATABASE "{fixed}";\n')
        elif engine == "sqlite":
            Path(sqlite_path(environment)).touch(mode=0o644, exist_ok=True)
        return
    password = environment["password"]
    if not re.fullmatch(r"[a-f0-9]{48}", password):
        raise ValueError("Invalid generated database password.")
    if remove:
        if environment.get("mysql_owned"):
            run(["mysql"], input=f"DROP DATABASE IF EXISTS {name}; DROP USER IF EXISTS '{name}'@'localhost';\n")
        if environment.get("postgres_owned"):
            run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"], input=
                f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE);\nDROP ROLE IF EXISTS "{name}";\n')
        return
    existing_mysql = run(["mysql", "-N"], input=f"SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='{name}' UNION SELECT User FROM mysql.user WHERE User='{name}';\n", capture=True)
    existing_postgres = run(["psql", "-X", "-U", "postgres", "-d", "postgres", "-At"], input=
        f"SELECT datname FROM pg_database WHERE datname='{name}' UNION SELECT rolname FROM pg_roles WHERE rolname='{name}';\n", capture=True)
    if existing_mysql.strip() or existing_postgres.strip():
        raise ValueError("Generated database/user name already exists; existing data was not touched.")
    environment["mysql_owned"] = True
    save_environment(environment)
    grant_name = name.replace("_", "\\_")
    run(["mysql"], input=f"CREATE DATABASE {name}; CREATE USER '{name}'@'localhost' IDENTIFIED WITH mysql_native_password BY '{password}'; GRANT ALL PRIVILEGES ON `{grant_name}`.* TO '{name}'@'localhost';\n")
    environment["postgres_owned"] = True
    save_environment(environment)
    run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"], input=
        f"CREATE ROLE \"{name}\" LOGIN PASSWORD '{password}';\nCREATE DATABASE \"{name}\" OWNER \"{name}\";\nREVOKE CONNECT, TEMPORARY ON DATABASE \"{name}\" FROM PUBLIC;\n")


def sqlite_path(environment):
    return str(STATE / "environments" / environment["id"] / "data" / (environment["db_name"] + ".sqlite"))


def environment_variables(environment):
    directory = STATE / "environments" / environment["id"]
    variables = {
        "LAMP_ID": environment["id"], "LAMP_URL": environment["url"], "APP_URL": environment["url"],
        "LAMP_PROJECT_DIR": environment["path"], "LAMP_DATA_DIR": str(directory / "data"),
    }
    if subdomain_labels(environment):
        # Static environments use one fixed database of their own name, reachable with the root account.
        engine = environment.get("db_engine")
        if engine == "mysql":
            variables.update({"DB_CONNECTION": "mysql", "DB_HOST": "localhost", "DB_PORT": "3306", "DB_DATABASE": environment["db_name"],
                              "DB_USERNAME": "root", "DB_PASSWORD": root_password()})
        elif engine == "postgres":
            password = root_password()
            variables.update({"DB_CONNECTION": "pgsql", "DB_HOST": "localhost", "DB_PORT": "5432", "DB_DATABASE": environment["db_name"],
                              "DB_USERNAME": "postgres", "DB_PASSWORD": password, "PGHOST": "localhost", "PGPORT": "5432",
                              "PGDATABASE": environment["db_name"], "PGUSER": "postgres", "PGPASSWORD": password})
        elif engine == "sqlite":
            variables.update({"DB_CONNECTION": "sqlite", "DB_DATABASE": sqlite_path(environment)})
        return variables
    name = "lamp_" + environment["id"]
    variables.update({
        "DB_CONNECTION": environment["engine"], "DB_HOST": "localhost", "DB_PORT": "3306",
        "DB_DATABASE": name, "DB_USERNAME": name, "DB_PASSWORD": environment["password"],
        "PGHOST": "localhost", "PGPORT": "5432", "PGDATABASE": name, "PGUSER": name,
        "PGPASSWORD": environment["password"],
    })
    if environment["engine"] == "sqlite":
        variables["DB_DATABASE"] = str(directory / "data" / "database.sqlite")
    return variables


def build_environment(environment):
    directory = STATE / "environments" / environment["id"]
    variables = environment_variables(environment)
    bin_directory = directory / "bin"
    bin_directory.mkdir(exist_ok=True)
    php = bin_directory / "php"
    php.unlink(missing_ok=True)
    php.symlink_to("/usr/bin/php" + environment["php"])
    curl = bin_directory / "curl"
    curl.write_text('#!/bin/sh\nexec python3 /opt/lamp/curl.py --automatic ' + shlex.quote(environment["id"]) + ' "$@"\n')
    curl.chmod(0o700)
    variables["PATH"] = str(bin_directory) + ":" + os.environ["PATH"].removeprefix(str(bin_directory) + ":")
    variables["COMPOSER_ALLOW_SUPERUSER"] = "1"
    return variables


def write_setup(environment):
    variables = build_environment(environment)
    # setup.env only prepends the environment's bin directory, so the sourcing shell keeps its own PATH (rvm, nvm).
    bin_directory = STATE / "environments" / environment["id"] / "bin"
    contents = "".join(f"export {key}={shlex.quote(value)}\n" if key != "PATH" else f'export PATH={shlex.quote(str(bin_directory))}:"$PATH"\n'
                       for key, value in variables.items())
    contents += f'''syncdb() {{
    local settings
    settings=$(python3 {shlex.quote(str(Path(__file__).resolve()))} syncdb "$@") || return $?
    eval "$settings"
}}
export -f syncdb
'''
    path = Path(environment["setup_environment"])
    path.write_text(contents)
    path.chmod(0o600)
    return variables


def sync_database(environment, profile_name):
    if not isinstance(profile_name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", profile_name):
        raise ValueError("Invalid syncdb profile name.")
    profile = json.loads((CONFIGURATION / "syncdb" / (profile_name + ".json")).read_text())
    if not isinstance(profile, dict) or profile.get("engine") not in ("mysql", "sqlite"):
        raise ValueError("syncdb currently supports mysql and sqlite, not PostgreSQL.")
    if not isinstance(profile.get("source"), dict):
        raise ValueError("The syncdb profile needs a source object.")
    if subdomain_labels(environment):
        engine = environment.get("db_engine")
        if engine is None:
            raise ValueError("Set db_name and db_engine for this environment before importing.")
        if engine != profile["engine"]:
            raise ValueError(f"The syncdb profile is for {profile['engine']} but the environment uses {engine}.")
        if engine == "mysql":
            profile["target"] = {"host": "localhost", "port": "3306", "database": environment["db_name"], "username": "root",
                                 "password": root_password(), "ssh": False, "cmd": "mysql", "sql_log_bin": False}
        else:
            profile["target"] = {"database": sqlite_path(environment), "ssh": False}
        run_sync(environment, profile)
        return
    environment["engine"] = profile["engine"]
    name = "lamp_" + environment["id"]
    if environment["engine"] == "mysql":
        profile["target"] = {"host": "localhost", "port": "3306", "database": name,
                             "username": name, "password": environment["password"], "ssh": False,
                             "cmd": "mysql", "sql_log_bin": False}
    else:
        profile["target"] = {"database": str(STATE / "environments" / environment["id"] / "data" / "database.sqlite"), "ssh": False}
    environment["database"] = profile["target"]["database"]
    run_sync(environment, profile)


def run_profile(profile_name):
    """Run a .data/syncdb profile exactly as written; its target credentials must fit the container databases."""
    source = CONFIGURATION / "syncdb" / (profile_name + ".json")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", profile_name) or not source.is_file():
        raise ValueError(f"No syncdb profile {profile_name} in .data/syncdb.")
    destination = STATE / "syncdb" / (profile_name + ".json")
    shutil.copyfile(source, destination)
    try:
        with tempfile.TemporaryDirectory(prefix="lamp-sync-") as working_directory:
            run(["php8.5", "/root/.syncdb/vendor/vielhuber/syncdb/src/syncdb.php", profile_name], cwd=working_directory, output=sys.stderr)
    finally:
        destination.unlink(missing_ok=True)


def run_sync(environment, profile):
    profile_name = "lamp-" + environment["id"]
    destination = STATE / "syncdb" / (profile_name + ".json")
    write_json(destination, profile)
    try:
        # syncdb cleans its working directory; never run it in a project or profile directory.
        with tempfile.TemporaryDirectory(prefix="lamp-sync-") as working_directory:
            # stdout is reserved for the exports the build's syncdb function evaluates; progress and errors go to the build log.
            run(["php8.5", "/root/.syncdb/vendor/vielhuber/syncdb/src/syncdb.php", profile_name], cwd=working_directory, output=sys.stderr)
    finally:
        destination.unlink(missing_ok=True)


def vhost(environment):
    hostname = environment["hostname"]
    root = environment["document_root"]
    aliases = "    ServerAlias " + " ".join(hostnames(environment)[1:]) + "\n" if len(hostnames(environment)) > 1 else ""
    # The build variables reach php-fpm as fastcgi parameters: getenv() and $_SERVER in php, env() in laravel.
    variables = ""
    for key, value in environment_variables(environment).items():
        variables += f'    SetEnv {key} "' + value.replace("\\", "\\\\").replace('"', '\\"') + '"\n'
    settings = f'''    ServerName {hostname}
{aliases}    DocumentRoot "{root}"
{variables}    <Directory "{root}">
        Options -Indexes +FollowSymLinks
        AllowOverride All
        CGIPassAuth On
        Require all granted
    </Directory>
    <Proxy "fcgi://localhost-stream/" enablereuse=on flushpackets=on>
    </Proxy>
    <FilesMatch "\\.php$">
        <If "%{{HTTP:Accept}} -strmatch '*text/event-stream*'">
            SetHandler "proxy:unix:/run/php/php{environment['php']}-fpm.sock|fcgi://localhost-stream/"
            SetEnv no-gzip 1
            RequestHeader unset Accept-Encoding
        </If>
        <Else>
            SetHandler "proxy:unix:/run/php/php{environment['php']}-fpm.sock|fcgi://localhost/"
        </Else>
    </FilesMatch>
    <FilesMatch "\\.ph(?:ar|ps|tml)$">
        Require all denied
    </FilesMatch>
    <LocationMatch "(^|/)\\.">
        Require all denied
    </LocationMatch>
'''
    if environment.get("proxy_port") is not None:
        settings += '    ProxyPreserveHost On\n'
        if environment.get("proxy_exclude") is not None:
            settings += f'    ProxyPass "{environment["proxy_exclude"]}" "!"\n'
        upstream = f'http://127.0.0.1:{environment["proxy_port"]}/'
        settings += f'    ProxyPass "/" "{upstream}"\n    ProxyPassReverse "/" "{upstream}"\n'
    domain = hostname.split(".", 1)[1]
    value = f'''<VirtualHost *:443>
{settings}    SSLEngine on
    SSLCertificateFile "{LETSENCRYPT}/live/{domain}/fullchain.pem"
    SSLCertificateKeyFile "{LETSENCRYPT}/live/{domain}/privkey.pem"
</VirtualHost>
'''
    path = SITES / ("lamp-" + environment["id"] + ".conf")
    path.write_text(value)
    link = ENABLED / path.name
    if not link.exists():
        link.symlink_to(path)


def reload_apache():
    run(["apachectl", "configtest"])
    parent = run(["supervisorctl", "pid", "apache2"], capture=True).strip()
    workers = Path(f"/proc/{parent}/task/{parent}/children").read_text().split()
    run(["apachectl", "-k", "graceful"])
    deadline = time.monotonic() + 30
    while any(Path("/proc", worker).exists() for worker in workers):
        if time.monotonic() >= deadline:
            run(["supervisorctl", "restart", "apache2"])
            return
        time.sleep(0.05)


def connector(settings):
    credentials = CONFIGURATION / "cloudflare" / "cloudflared-credentials.json"
    program = Path("/run/lamp-supervisor/cloudflared-lamp.conf")
    if not credentials.exists():
        program.unlink(missing_ok=True)
        return False
    value = json.loads(credentials.read_text())
    if (not isinstance(value, dict)
            or not isinstance(value.get("TunnelID"), str)
            or not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value["TunnelID"])
            or not isinstance(value.get("AccountTag"), str)
            or not re.fullmatch(r"[a-f0-9]{32}", value["AccountTag"])
            or not isinstance(value.get("TunnelSecret"), str) or not value["TunnelSecret"]):
        raise ValueError("Invalid cloudflared-credentials.json; supply the existing tunnel's credentials JSON.")
    credentials.chmod(0o600)
    path = STATE / "cloudflared.yml"
    path.write_text(yaml.safe_dump({
        "tunnel": value["TunnelID"], "credentials-file": str(credentials),
        "ingress": [
            {"hostname": "*." + settings["domain"], "service": "https://127.0.0.1:443",
             "originRequest": {"originServerName": settings["domain"]}},
            {"service": "http_status:404"},
        ],
    }))
    path.chmod(0o600)
    program.write_text(f"""[program:cloudflared-lamp]
command=/usr/bin/cloudflared --no-autoupdate --config {path} tunnel --metrics 127.0.0.1:20246 run
priority=60
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
""")
    return True


def show(environment):
    return {key: value for key, value in {"visibility": "private", **environment,
            "urls": [environment["url"], *("https://" + host for host in environment.get("hostnames", [])[1:])]}.items() if key not in
            ("password", "build", "build_hash", "applied", "checkout", "syncdb", "synced_profile", "databases_ready", "mysql_owned", "postgres_owned", "project_identity")}


def ensure_connector(settings):
    if not connector(settings):
        raise ValueError("Environments require the tunnel credentials; run lamp cloudflare-setup.")
    run(["supervisorctl", "reread"])
    run(["supervisorctl", "update"])
    run(["curl", "--noproxy", "*", "--fail", "--silent", "--retry", "30", "--retry-connrefused",
         "--retry-delay", "1", "--max-time", "2", "--retry-max-time", "60", "http://127.0.0.1:20246/ready"])


def add(arguments, settings, identity, current=None, *, force_build=False, reason="creating", batch=None):
    # batch collects deferred work of a reconcile run: one apache reload and one php-fpm restart per version at the end,
    # access checks and the connector are handled once by the caller.
    desired = validate_specification({key: getattr(arguments, key) for key in ("git", *ENVIRONMENT_DEFAULTS)})
    project = project_path(identity, desired)
    if current:
        check_checkout(current, desired)
        if not project.is_dir():
            raise ValueError("The registered project directory is missing; existing files will not be recreated automatically.")
    elif project.exists() and not project.is_dir():
        raise ValueError("The project target exists but is not a directory.")
    if batch is None:
        sync_visibility(settings, {identity: desired}, identities={identity}, publish=False)
    project_owned = current.get("project_owned", False) if current else not project.exists()
    build, build_hash = resolve_build(desired)
    ensure_vpn(desired["vpn"])
    if batch is None or not batch.get("connected"):
        ensure_connector(settings)
        if batch is not None:
            batch["connected"] = True
    hostname = environment_hostname(identity, desired, settings)
    url = environment_url(identity, desired, settings)
    directory = STATE / "environments" / identity
    directory.mkdir(mode=0o700, exist_ok=current is not None)
    (directory / "data").mkdir(mode=0o755, exist_ok=True)
    if current is None and project_owned:
        previous_umask = os.umask(0o022)
        try:
            project.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            project.mkdir(mode=0o755)
        finally:
            os.umask(previous_umask)
    applied = specification(current) if current else None
    checkout = current.get("checkout", applied) if current else None
    environment = {
        **(current or {}),
        "id": identity, "git": arguments.git, "branch": arguments.branch, "php": arguments.php,
        "build": arguments.build, "subdomain": arguments.subdomain, "aliases": desired["aliases"], "directory": desired["directory"],
        "hostnames": environment_hostnames(identity, desired, settings), "vpn": arguments.vpn, "visibility": desired["visibility"],
        "webroot": arguments.webroot, "proxy_port": arguments.proxy_port, "proxy_exclude": arguments.proxy_exclude,
        "hostname": hostname, "url": url, "path": str(project), "db_name": desired["db_name"], "db_engine": desired["db_engine"],
        "project_owned": project_owned, "project_identity": [project.stat().st_dev, project.stat().st_ino],
        "engine": (current.get("engine") or "mysql") if current else "mysql", "database": "lamp_" + identity,
        "password": current["password"] if current else secrets.token_hex(24), "status": "creating",
        "applied": applied or desired,
        "setup_environment": str(directory / "setup.env"),
        "data_path": str(directory / "data"),
        "mysql_database": "lamp_" + identity, "postgres_database": "lamp_" + identity,
    }
    environment.pop("syncdb", None)
    environment.pop("synced_profile", None)
    if subdomain_labels(desired):
        environment["engine"] = desired["db_engine"]
        environment["database"] = sqlite_path(environment) if desired["db_engine"] == "sqlite" else desired["db_name"]
        environment["mysql_database"] = desired["db_name"] if desired["db_engine"] == "mysql" else None
        environment["postgres_database"] = desired["db_name"] if desired["db_engine"] == "postgres" else None
    elif environment["engine"] == "sqlite":
        environment["database"] = str(directory / "data" / "database.sqlite")
    save_environment(environment)
    try:
        if current:
            (ENABLED / ("lamp-" + identity + ".conf")).unlink(missing_ok=True)
            if batch is None:
                reload_apache()
            else:
                batch["reload"] = True
        os.umask(0o022)
        print(f"{hostname} [{identity}] {project}: {reason}", file=sys.stderr)
        git_environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"}
        if project_owned and arguments.git is not None and not (project / ".git").exists():
            print(f"{hostname} [{identity}] {project}: cloning {arguments.git}", file=sys.stderr)
            clone = ["git", "clone"]
            branch = arguments.branch
            if branch and getattr(arguments, "base_branch", None):
                found = run(["git", "ls-remote", "--heads", arguments.git, "refs/heads/" + branch], environment=git_environment, capture=True)
                if not found.strip():
                    branch = arguments.base_branch
            if branch:
                clone += ["--branch", branch]
            run(clone + ["--", arguments.git, str(project)], environment=git_environment)
            if arguments.branch and branch != arguments.branch:
                run(["git", "switch", "--create", arguments.branch], cwd=project)
        elif project_owned and arguments.git and checkout and any(checkout[key] != desired[key] for key in ("git", "branch")):
            branch = arguments.branch
            if branch is None:
                remote = run(["git", "ls-remote", "--symref", arguments.git, "HEAD"], environment=git_environment, capture=True)
                matches = re.findall(r"^ref: refs/heads/(.+)\tHEAD$", remote, re.MULTILINE)
                if len(matches) != 1:
                    raise ValueError("Cannot resolve the repository's default branch.")
                branch = matches[0]
                run(["git", "check-ref-format", "--branch", branch])
            run(["git", "fetch", "--no-tags", arguments.git, "refs/heads/" + branch], cwd=project, environment=git_environment)
            run(["git", "checkout", "--no-overwrite-ignore", "-B", branch, "FETCH_HEAD"], cwd=project)
            run(["git", "remote", "set-url", "origin", arguments.git], cwd=project)
            run(["git", "update-ref", "refs/remotes/origin/" + branch, "HEAD"], cwd=project)
            run(["git", "branch", "--set-upstream-to=origin/" + branch, branch], cwd=project)
        environment["branch"] = run(["git", "branch", "--show-current"], cwd=project, capture=True).strip() if project_owned and arguments.git else desired["branch"]
        environment["commit"] = run(["git", "rev-parse", "HEAD"], cwd=project, capture=True).strip() if project_owned and arguments.git else None
        environment["checkout"] = {key: desired[key] for key in ("git", "branch")}
        environment["php"] = resolve_php(project, desired["php"])
        save_environment(environment)
        if subdomain_labels(desired) or (not environment.get("mysql_owned") and not environment.get("postgres_owned")):
            database(environment)
        elif not environment.get("databases_ready", current is not None and current["status"] == "ready"):
            raise ValueError("Incomplete database initialization; remove this environment before recreating it.")
        environment["databases_ready"] = True
        save_environment(environment)
        os.umask(0o077)
        variables = write_setup(environment)
        # Adopted directories are never built automatically; lamp build <id> is the explicit way.
        if build is not None and (project_owned or force_build):
            log = directory / "build.log"
            print(f"{hostname} [{identity}] {project}: building, log {log}", file=sys.stderr)
            with log.open("wb") as handle:
                os.umask(0o022)
                try:
                    # The build runs on a pseudo-terminal, so tools show progress (pv, npm) as they would interactively;
                    # the complete output goes to the log and to the terminal at the same time.
                    master, slave = pty.openpty()
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 160, 0, 0))
                    # set -x after setup.env: every build command is echoed, the variable exports are not
                    process = subprocess.Popen(["bash", "-c", "set -e\nsource " + shlex.quote(environment["setup_environment"]) + "\nset -x\n" + build],
                                               cwd=project, env={**os.environ, "TERM": "xterm-256color", **variables},
                                               stdin=subprocess.DEVNULL, stdout=slave, stderr=slave, close_fds=True)
                    os.close(slave)
                    try:
                        while True:
                            try:
                                chunk = os.read(master, 65536)
                            except OSError:
                                break
                            if not chunk:
                                break
                            handle.write(chunk)
                            sys.stderr.buffer.write(chunk)
                            sys.stderr.buffer.flush()
                    finally:
                        os.close(master)
                    if process.wait():
                        raise RuntimeError(f"bash failed (exit {process.returncode}). Build log: {log}")
                finally:
                    environment = load_environment(identity)
            # Services declared by the build in $LAMP_DATA_DIR/supervisor.conf start, restart or stop here.
            run(["supervisorctl", "reread"])
            run(["supervisorctl", "update"])
        os.umask(0o077)
        environment["document_root"] = str(project)
        for subdirectory in ("public", "web"):
            if (project / subdirectory / "index.php").is_file():
                environment["document_root"] = str(project / subdirectory)
                break
        if arguments.webroot is not None:
            document_root = project / arguments.webroot
            if not document_root.is_dir() or not document_root.resolve().is_relative_to(project.resolve()):
                raise ValueError("webroot must exist after the build and resolve to a directory inside the project.")
            environment["document_root"] = str(document_root)
        if not Path(environment["document_root"]).resolve().is_relative_to(project.resolve()):
            raise ValueError("The document root must resolve inside the project directory.")
        if project_owned:
            run(["chown", "-R", "www-data:www-data", str(project), str(directory / "data")])
        (directory / "data").chmod(0o755)
        directory.chmod(0o711)
        for path in directory.iterdir():
            if path.is_file():
                path.chmod(0o600)
        vhost(environment)
        sync_hosts()
        if batch is None:
            run(["supervisorctl", "restart", "php" + environment["php"] + "-fpm"])
            reload_apache()
            sync_visibility(settings, {identity: desired}, identities={identity})
        else:
            batch["php"].add(environment["php"])
            batch["reload"] = True
        environment["status"] = "ready"
        environment["applied"] = desired
        environment["build_hash"] = build_hash
        save_environment(environment)
    except (RuntimeError, ValueError, OSError):
        environment["status"] = "failed"
        save_environment(environment)
        (ENABLED / ("lamp-" + identity + ".conf")).unlink(missing_ok=True)
        if batch is None:
            try:
                reload_apache()
            except RuntimeError:
                pass
        else:
            batch["reload"] = True
        print(f"Environment {identity} failed; correct its YAML settings and restart, or remove it.", file=sys.stderr)
        raise
    return show(environment)


def remove(identity):
    environment = load_environment(identity)
    project = environment_project(environment)
    environment["status"] = "removing"
    save_environment(environment)
    name = "lamp-" + identity + ".conf"
    (ENABLED / name).unlink(missing_ok=True)
    reload_apache()
    sync_visibility(configuration(), {}, identities={identity}, publish=False)
    if environment.get("mysql_owned") or environment.get("postgres_owned"):
        database(environment, remove=True)
    if not subdomain_labels(environment) and project.exists():
        shutil.rmtree(project)
    (SITES / name).unlink(missing_ok=True)
    shutil.rmtree(STATE / "environments" / identity)
    sync_hosts()
    run(["supervisorctl", "reread"])
    run(["supervisorctl", "update"])
    return {"id": identity, "status": "removed"}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "validate", "reconcile"):
        commands.add_parser(command)
    commands.add_parser("list").add_argument("--search")
    for command in ("show", "remove", "access", "build"):
        commands.add_parser(command).add_argument("id", type=resolve_identity)
    execute = commands.add_parser("exec")
    execute.add_argument("id", type=resolve_identity)
    execute.add_argument("script")
    branch = commands.add_parser("branch")
    branch.add_argument("id", type=resolve_identity)
    branch.add_argument("branch")
    branch.add_argument("--base", default="main")
    branch.add_argument("--operation", choices=("switch", "rename"), default="switch")
    commands.add_parser("syncdb").add_argument("profile")
    commands.add_parser("sync").add_argument("profile")
    create = commands.add_parser("add")
    create.add_argument("--git")
    create.add_argument("--id", type=validate_identity)
    create.add_argument("--base-branch")
    create.add_argument("--branch")
    create.add_argument("--php", choices=PHP_VERSIONS)
    create.add_argument("--vpn")
    create.add_argument("--build")
    create.add_argument("--subdomain")
    create.add_argument("--directory")
    create.add_argument("--db-name", dest="db_name")
    create.add_argument("--db-engine", dest="db_engine", choices=DATABASE_ENGINES)
    create.add_argument("--alias", dest="aliases", action="append")
    create.add_argument("--webroot")
    create.add_argument("--proxy-port", type=int)
    create.add_argument("--proxy-exclude")
    create.add_argument("--visibility", choices=("private", "public"))
    arguments = parser.parse_args()
    if not Path("/.dockerenv").exists():
        raise ValueError("Run the controller through lamp on the Docker host.")
    os.umask(0o077)
    if arguments.command == "exec":
        environment = load_environment(arguments.id)
        if environment["status"] != "ready":
            raise ValueError("Environment is not ready.")
        os.chdir(environment_project(environment))
        setup = environment.get("setup_environment")
        os.execvp("bash", ["bash", "-c", "set -e\n" + ("source " + shlex.quote(setup) + "\n" if setup else "") + arguments.script])
    if arguments.command == "syncdb":
        # The parent build already holds control.lock while this child performs the import.
        environment = load_environment(os.environ.get("LAMP_ID"))
        if not environment.get("databases_ready"):
            raise ValueError("syncdb requires an initialized LAMP environment.")
        print("Importing into the environment database.", file=sys.stderr)
        sync_database(environment, arguments.profile)
        save_environment(environment)
        variables = write_setup(environment)
        for key in ("DB_CONNECTION", "DB_DATABASE"):
            print(f"export {key}={shlex.quote(variables[key])}")
        return
    if arguments.command in ("list", "show", "access"):
        if arguments.command == "list":
            result = [show(item) for item in environments()]
            if arguments.search is not None:
                term = arguments.search.lower()
                result = [record for record in result
                          if any(term in str(value).lower() for field in record.values()
                                 for value in (field if isinstance(field, list) else [field]) if value is not None)]
        elif arguments.command == "show":
            result = show(load_environment(arguments.id))
        else:
            environment = load_environment(arguments.id)
            names = () if environment.get("visibility") == "public" else ("CF-Access-Client-Id", "CF-Access-Client-Secret")
            headers = yaml.safe_load((CONFIGURATION / "cloudflare" / "cloudflare-service-token.yaml").read_text()) if names else {}
            if not isinstance(headers, dict) or any(not isinstance(headers.get(name), str) or not headers[name] or re.search(r"[\r\n\0]", headers[name]) for name in names):
                raise ValueError("Invalid Cloudflare service token configuration.")
            result = {"origin": environment["url"], "headers": {name: headers[name] for name in names}}
        print(json.dumps(result, indent=4))
        return
    settings = configuration()
    with (STATE / "control.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries, original = read_desired()
        desired = desired_state(entries)
        validate_domains(desired, settings)
        if arguments.command == "validate":
            reconcile(settings, desired, validate_only=True)
            return
        if arguments.command == "prepare":
            sync_visibility(settings, desired, publish=False)
            for directory in (STATE / "environments", STATE / "syncdb", PROJECTS, CONFIGURATION / "ssh"):
                directory.mkdir(parents=True, exist_ok=True)
            (STATE / "environments").chmod(0o711)
            run(["git", "config", "--global", "--replace-all", "safe.directory", str(PROJECTS) + "/*",
                 "^" + re.escape(str(PROJECTS)) + "/"])
            apply_settings(settings)
            ensure_certificate(settings)
            sync_hosts()
            # Leftovers of the former built-in phpMyAdmin vhost and of the local https certificates.
            for leftover in (ENABLED / "lamp-phpmyadmin.conf", SITES / "lamp-phpmyadmin.conf", ENABLED / "phpmyadmin.conf",
                             *(STATE / "environments").glob("*/tls.*")):
                leftover.unlink(missing_ok=True)
            for leftover in ("phpmyadmin", "default"):
                shutil.rmtree(STATE / "environments" / leftover, ignore_errors=True)
            for environment in environments():
                pending = bool(subdomain_labels(specification(environment))) and (
                    desired.get(environment["id"]) != specification(environment)
                    or environment["url"] != environment_url(environment["id"], desired[environment["id"]], settings)
                    or environment["php"] != resolve_php(environment_project(environment), desired[environment["id"]]["php"])
                    or (environment.get("project_owned") and environment.get("build_hash") != resolve_build(desired[environment["id"]])[1]))
                if environment["status"] != "ready" or pending:
                    (ENABLED / ("lamp-" + environment["id"] + ".conf")).unlink(missing_ok=True)
                else:
                    # Rewritten on every start so vhost format changes reach existing environments.
                    vhost(environment)
            default = SITES / "000-000-lamp-deny.conf"
            default.write_text(f"""<VirtualHost *:80>
    ServerName lamp.invalid
    <Location />
        Require all denied
    </Location>
</VirtualHost>
<VirtualHost *:443>
    ServerName {settings['domain']}
    SSLEngine on
    SSLCertificateFile "{LETSENCRYPT}/live/{settings['domain']}/fullchain.pem"
    SSLCertificateKeyFile "{LETSENCRYPT}/live/{settings['domain']}/privkey.pem"
    <Location />
        Require all denied
    </Location>
</VirtualHost>
""")
            link = ENABLED / default.name
            if not link.exists():
                link.symlink_to(default)
            if not connector(settings) and desired:
                raise ValueError("Configured environments require the tunnel credentials; run lamp cloudflare-setup.")
            return
        if arguments.command == "branch":
            environment = load_environment(arguments.id)
            if environment["status"] != "ready":
                raise ValueError("Environment is not ready.")
            project = environment_project(environment)
            if not environment.get("git"):
                raise ValueError("Environment has no Git repository.")
            run(["git", "check-ref-format", "--branch", arguments.branch])
            run(["git", "check-ref-format", "--branch", arguments.base])
            if run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=project, capture=True).strip():
                raise ValueError("Commit or discard all changes before changing the branch.")
            index_lock = Path(run(["git", "rev-parse", "--git-path", "index.lock"], cwd=project, capture=True).strip())
            if not index_lock.is_absolute():
                index_lock = project / index_lock
            deadline = time.monotonic() + 10
            while index_lock.exists():
                if time.monotonic() >= deadline:
                    raise ValueError("Git index is busy; wait for its owner. No lock file was removed.")
                time.sleep(0.1)
            previous_branch = run(["git", "branch", "--show-current"], cwd=project, capture=True).strip()
            if not previous_branch:
                raise ValueError("The environment must be on a branch before switching.")
            if previous_branch == arguments.branch:
                command = None
            elif arguments.operation == "rename":
                run(["git", "branch", "--move", arguments.branch], cwd=project)
            else:
                refs = run(["git", "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin"], cwd=project, capture=True).splitlines()
                if "refs/heads/" + arguments.branch not in refs:
                    run(["git", "fetch", "--no-tags", "origin"], cwd=project, environment={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
                    refs = run(["git", "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes/origin"], cwd=project, capture=True).splitlines()
                if "refs/heads/" + arguments.branch in refs:
                    command = ["git", "switch", "--no-overwrite-ignore", arguments.branch]
                elif "refs/remotes/origin/" + arguments.branch in refs:
                    command = ["git", "switch", "--no-overwrite-ignore", "--create", arguments.branch, "--track", "origin/" + arguments.branch]
                else:
                    base = arguments.base if "refs/heads/" + arguments.base in refs else "origin/" + arguments.base
                    command = ["git", "switch", "--no-overwrite-ignore", "--create", arguments.branch, base]
                run(command, cwd=project)
            applied = specification(environment)
            try:
                if applied in entries:
                    entries[entries.index(applied)] = {**applied, "branch": arguments.branch}
                    write_desired(entries, original)
            except (OSError, ValueError):
                if previous_branch != arguments.branch:
                    rollback = ["git", "branch", "--move", previous_branch] if arguments.operation == "rename" else ["git", "switch", "--no-overwrite-ignore", previous_branch]
                    run(rollback, cwd=project)
                raise
            environment["branch"] = arguments.branch
            environment["applied"]["branch"] = arguments.branch
            environment["checkout"]["branch"] = arguments.branch
            environment["commit"] = run(["git", "rev-parse", "HEAD"], cwd=project, capture=True).strip()
            save_environment(environment)
            result = show(environment)
        elif arguments.command == "add":
            identity = arguments.id or uuid.uuid4().hex[:12]
            if arguments.base_branch:
                run(["git", "check-ref-format", "--branch", arguments.base_branch])
            requested = validate_specification({key: getattr(arguments, key) for key in ("git", *ENVIRONMENT_DEFAULTS)})
            if arguments.id and (STATE / "environments" / identity / "environment.json").is_file():
                existing = load_environment(identity)
                applied = specification(existing)
                if any(applied.get(key) != requested[key]
                       for key in ("git", *ENVIRONMENT_DEFAULTS) if getattr(arguments, key) is not None):
                    raise ValueError("Environment ID already exists with different settings.")
                if existing["status"] != "ready":
                    raise ValueError("Environment exists but is not ready; inspect and repair it before retrying.")
                if not environment_project(existing).is_dir():
                    raise ValueError("Environment project directory is missing.")
                if existing.get("git") and run(["git", "branch", "--show-current"], cwd=environment_project(existing), capture=True).strip() != applied["branch"]:
                    raise ValueError("The checkout branch differs from its configuration; select the branch through lamp branch before retrying.")
                print(json.dumps(show(existing), indent=4))
                return
            static = bool(subdomain_labels(requested))
            if static and requested in entries:
                listed = next(key for key, value in desired.items() if value == requested)
                if (STATE / "environments" / listed / "environment.json").is_file():
                    existing = load_environment(listed)
                    if existing["status"] == "ready":
                        print(json.dumps(show(existing), indent=4))
                        return
                    raise ValueError("This environment is listed and exists but is not ready; restart to retry, or remove it.")
                desired.pop(listed)
                identity = arguments.id or listed
            elif static:
                entries.append(requested)
            while identity in desired or (STATE / "environments" / identity).exists():
                if arguments.id:
                    raise ValueError("Environment ID already exists; reconcile its configuration first.")
                identity = uuid.uuid4().hex[:12]
            desired[identity] = requested
            validate_domains(desired, settings)
            requested_hosts = set(environment_hostnames(identity, requested, settings))
            if any(requested_hosts & set(hostnames(item)) for item in environments()):
                raise ValueError("The requested domain is still assigned to an existing environment; reconcile or remove it first.")
            if static:
                write_desired(entries, original)
            result = add(arguments, settings, identity)
        elif arguments.command == "reconcile":
            reconcile(settings, desired)
            return
        elif arguments.command == "sync":
            run_profile(arguments.profile)
            result = {"profile": arguments.profile, "status": "imported"}
        elif arguments.command == "build":
            identity = validate_identity(arguments.id)
            environment = load_environment(identity)
            if identity not in desired:
                raise ValueError("Environment is not listed in env.yaml; add its entry or remove the environment.")
            if resolve_build(desired[identity])[0] is None:
                raise ValueError("Environment has no build; add a repository script in .data/build or a build setting.")
            result = add(argparse.Namespace(**desired[identity]), settings, identity, environment, force_build=True, reason="build requested")
        else:
            identity = validate_identity(arguments.id)
            applied = specification(load_environment(identity))
            if applied in entries:
                entries.remove(applied)
                write_desired(entries, original)
            result = remove(identity)
        print(json.dumps(result, indent=4))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as error:
        if isinstance(error, OSError):
            print("LAMP filesystem/process error; check configuration, profile files and permissions.", file=sys.stderr)
        elif isinstance(error, (json.JSONDecodeError, yaml.YAMLError)):
            print("Invalid JSON/YAML configuration.", file=sys.stderr)
        else:
            print(str(error), file=sys.stderr)
        sys.exit(1)
