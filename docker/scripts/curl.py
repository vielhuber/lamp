#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

import yaml


def main(arguments):
    automatic = arguments[:1] == ["--automatic"]
    if automatic:
        arguments = arguments[1:]
    identity = arguments.pop(0) if arguments else os.environ.get("LAMP_ID", "")
    if not identity:
        raise ValueError("A LAMP environment ID or subdomain is required.")
    if not re.fullmatch(r"[a-f0-9]{12}", identity):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import control
        identity = control.resolve_identity(identity)
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    environment = json.loads((Path("/var/lib/lamp/environments") / identity / "environment.json").read_text())
    origins = [urlsplit(environment["url"]), *(urlsplit("https://" + host) for host in environment.get("hostnames", []))]
    allowed = {(origin.hostname, origin.port or 443) for origin in origins}
    if automatic and not any(
        argument.lower().startswith("https://") and
        (urlsplit(argument).hostname, urlsplit(argument).port or 443) in allowed
        for argument in arguments
    ):
        return subprocess.run(["/usr/bin/curl", *arguments]).returncode
    urls = []
    takes_value = {"--output", "--request", "--header", "--data", "--data-raw", "--data-binary", "--data-urlencode", "--form", "--form-string", "--max-time", "--connect-timeout", "--user-agent", "--cookie", "--cookie-jar", "--upload-file", "--write-out"}
    switches = {"--silent", "--show-error", "--fail", "--fail-with-body", "--head", "--location", "--compressed", "--get", "--globoff"}
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in takes_value or argument in {"-o", "-X", "-H", "-d", "-F", "-m", "-A", "-b", "-c", "-T", "-w"}:
            index += 1
            if index >= len(arguments):
                raise ValueError("Missing curl option value.")
        elif argument in switches or re.fullmatch(r"-[sSfILG]+", argument):
            pass
        elif argument.startswith("-"):
            raise ValueError("Unsupported option for authenticated curl.")
        else:
            urls.append(argument)
        index += 1
    if len(urls) != 1:
        raise ValueError("Use one explicit HTTP(S) URL per authenticated curl invocation.")
    url = urlsplit(urls[0])
    matching = url.scheme == "https" and (url.hostname, url.port or 443) in allowed
    if automatic and not matching:
        return subprocess.run(["/usr/bin/curl", *arguments]).returncode
    if url.username or url.password or not matching:
        raise ValueError("Authenticated curl only accepts this environment's HTTPS origin.")
    if environment.get("visibility") == "public":
        return subprocess.run(["/usr/bin/curl", "--disable", *arguments, "--globoff", "--max-redirs", "0"]).returncode
    headers = yaml.safe_load(Path("/var/lib/lamp/cloudflare/cloudflare-service-token.yaml").read_text())
    names = ("CF-Access-Client-Id", "CF-Access-Client-Secret")
    if not isinstance(headers, dict) or any(not isinstance(headers.get(name), str) or not headers[name] or re.search(r"[\r\n\0]", headers[name]) for name in names):
        raise ValueError("Invalid Cloudflare service token configuration.")
    descriptor = os.memfd_create("lamp-access", 0)
    try:
        os.write(descriptor, "".join(f"{name}: {headers[name]}\n" for name in names).encode())
        os.lseek(descriptor, 0, os.SEEK_SET)
        # Never forward service credentials on redirects, including redirects hidden in curl configuration.
        return subprocess.run(["/usr/bin/curl", "--disable", *arguments, "--globoff", "--max-redirs", "0", "--header", f"@/proc/self/fd/{descriptor}"], pass_fds=(descriptor,)).returncode
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except (OSError, ValueError, yaml.YAMLError):
        print("Authenticated curl failed: check the environment, token file and supported arguments. Redirects must be requested explicitly.", file=sys.stderr)
        sys.exit(1)
