#!/usr/bin/env python3
import base64
import http.client
import json
from pathlib import Path
import secrets
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import control  # noqa: E402

CACHE_RULE = "lamp: bypass cache"
CREDENTIALS = "cloudflared-credentials.json"
SERVICE_TOKEN_FILE = "cloudflare-service-token.yaml"


def request(token, method, path, payload=None, missing=False):
    connection = http.client.HTTPSConnection("api.cloudflare.com", timeout=30)
    try:
        connection.request(method, "/client/v4" + path, body=json.dumps(payload) if payload is not None else None,
                           headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read()
    finally:
        connection.close()
    if response.status == 404 and missing:
        return None
    try:
        result = json.loads(body)
    except ValueError:
        raise RuntimeError(f"Cloudflare API {method} {path.split('?')[0]} returned HTTP {response.status} without json.") from None
    if response.status >= 300 or not result.get("success"):
        messages = "; ".join(str(error.get("message")) for error in result.get("errors") or []) or "no error message"
        raise RuntimeError(f"Cloudflare API {method} {path.split('?')[0]} failed (HTTP {response.status}): {messages}")
    return result["result"]


def listing(token, path):
    records = []
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        result = request(token, "GET", path + f"{separator}page={page}&per_page=100")
        records.extend(result)
        if len(result) < 100:
            return records
        page += 1


def write_private(path, text):
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)


def setup_tunnel(token, account, domain, folder):
    path = folder / CREDENTIALS
    local = json.loads(path.read_text()) if path.is_file() else {}
    tunnels = [tunnel for tunnel in listing(token, f"/accounts/{account}/cfd_tunnel?is_deleted=false") if tunnel["name"] == domain]
    if tunnels and local.get("TunnelID") == tunnels[0]["id"] and local.get("TunnelSecret") and local.get("AccountTag") == account:
        return tunnels[0]["id"], "ok"
    for tunnel in tunnels:
        request(token, "DELETE", f"/accounts/{account}/cfd_tunnel/{tunnel['id']}/connections")
        request(token, "DELETE", f"/accounts/{account}/cfd_tunnel/{tunnel['id']}")
    secret = base64.b64encode(secrets.token_bytes(32)).decode()
    tunnel = request(token, "POST", f"/accounts/{account}/cfd_tunnel", {"name": domain, "config_src": "local", "tunnel_secret": secret})
    write_private(path, json.dumps({"AccountTag": account, "TunnelSecret": secret, "TunnelID": tunnel["id"], "TunnelName": domain}) + "\n")
    return tunnel["id"], "recreated" if tunnels else "created"


def setup_dns(token, zone, domain, tunnel):
    name = "*." + domain
    content = tunnel + ".cfargotunnel.com"
    records = [record for record in listing(token, f"/zones/{zone}/dns_records?name={name}") if record["name"] == name]
    if len(records) > 1:
        raise ValueError(f"Several DNS records exist for {name}; keep one CNAME.")
    if records and records[0]["type"] == "CNAME" and records[0]["content"] == content and records[0].get("proxied"):
        return "ok"
    payload = {"type": "CNAME", "name": name, "content": content, "proxied": True, "ttl": 1}
    if records:
        request(token, "PUT", f"/zones/{zone}/dns_records/{records[0]['id']}", payload)
        return "updated"
    request(token, "POST", f"/zones/{zone}/dns_records", payload)
    return "created"


def setup_service_token(token, account, domain, folder):
    # One token per instance, named after its domain; the local file identifies an existing token by client id.
    name = "lamp " + domain
    path = folder / SERVICE_TOKEN_FILE
    local = yaml.safe_load(path.read_text()) if path.is_file() else {}
    local = local if isinstance(local, dict) else {}
    tokens = listing(token, f"/accounts/{account}/access/service_tokens")
    owned = [item for item in tokens if item["client_id"] == local.get("CF-Access-Client-Id")] or [item for item in tokens if item["name"] == name]
    if len(owned) > 1:
        raise ValueError(f"Several Access service tokens are named {name}; delete the extra ones.")
    if owned and local.get("CF-Access-Client-Id") == owned[0]["client_id"] and local.get("CF-Access-Client-Secret"):
        return owned[0]["id"], "ok"
    if owned:
        created = request(token, "POST", f"/accounts/{account}/access/service_tokens/{owned[0]['id']}/rotate")
        status = "rotated"
    else:
        created = request(token, "POST", f"/accounts/{account}/access/service_tokens", {"name": name, "duration": "forever"})
        status = "created"
    write_private(path, yaml.safe_dump({"CF-Access-Client-Id": created["client_id"], "CF-Access-Client-Secret": created["client_secret"]}))
    return created["id"] if "id" in created else owned[0]["id"], status


def policy_view(policy):
    return {key: policy.get(key) or [] if key in ("include", "require", "exclude") else policy.get(key)
            for key in ("name", "decision", "precedence", "include", "require", "exclude")}


def setup_application(token, account, domain, email, service_token):
    # The application is identified by its wildcard destination, like control.sync_visibility does; its name carries the domain.
    wildcard = "*." + domain
    name = "lamp " + domain
    applications = [app for app in listing(token, f"/accounts/{account}/access/apps") if wildcard in control.access_destinations(app)]
    if len(applications) > 1:
        raise ValueError(f"Several Access applications cover {wildcard}; delete the extra ones.")
    policies = [
        {"name": "developer", "decision": "allow", "precedence": 1, "include": [{"email": {"email": email}}], "require": [], "exclude": []},
        {"name": "harness", "decision": "non_identity", "precedence": 2, "include": [{"service_token": {"token_id": service_token}}], "require": [], "exclude": []},
    ]
    payload = {"name": name, "type": "self_hosted", "domain": wildcard, "destinations": [{"type": "public", "uri": wildcard}],
               "app_launcher_visible": False, "session_duration": "24h", "policies": policies}
    if not applications:
        request(token, "POST", f"/accounts/{account}/access/apps", payload)
        return "created"
    application = applications[0]
    existing = listing(token, f"/accounts/{account}/access/apps/{application['id']}/policies")
    current = {key: application.get(key) for key in ("name", "type", "domain", "app_launcher_visible", "session_duration")}
    if (current == {key: payload[key] for key in current} and control.access_destinations(application) == {wildcard}
            and [policy_view(policy) for policy in sorted(existing, key=lambda policy: policy.get("precedence") or 0)] == policies):
        return "ok"
    identities = {policy.get("name"): policy["id"] for policy in existing}
    payload["policies"] = [{**policy, "id": identities[policy["name"]]} if policy["name"] in identities else policy for policy in policies]
    request(token, "PUT", f"/accounts/{account}/access/apps/{application['id']}", payload)
    return "updated"


def setup_cache(token, zone, domain):
    rule = {"description": CACHE_RULE, "expression": f'(ends_with(http.host, ".{domain}"))', "action": "set_cache_settings",
            "action_parameters": {"cache": False}, "enabled": True}
    ruleset = request(token, "GET", f"/zones/{zone}/rulesets/phases/http_request_cache_settings/entrypoint", missing=True) or {}
    rules = [{key: item[key] for key in ("id", "description", "expression", "action", "action_parameters", "enabled") if key in item}
             for item in ruleset.get("rules") or []]
    ours = [item for item in rules if item.get("description") == CACHE_RULE]
    if ours and {key: ours[0].get(key) for key in rule} == rule and len(ours) == 1:
        return "ok"
    rules = [item for item in rules if item.get("description") != CACHE_RULE] + [rule]
    request(token, "PUT", f"/zones/{zone}/rulesets/phases/http_request_cache_settings/entrypoint", {"rules": rules})
    return "updated" if ours else "created"


def setup(settings, folder):
    cloudflare = settings.get("cloudflare") or {}
    if not cloudflare.get("token") or not cloudflare.get("email"):
        raise ValueError("Set cloudflare.token and cloudflare.email in settings.yaml first.")
    token, email, domain = cloudflare["token"], cloudflare["email"], settings["domain"]
    zones = [zone for zone in listing(token, f"/zones?name={domain}") if zone["name"] == domain]
    if len(zones) != 1:
        raise ValueError(f"The token does not see exactly one zone named {domain}.")
    zone, account = zones[0]["id"], zones[0]["account"]["id"]
    results = {}
    tunnel, results["tunnel " + domain] = setup_tunnel(token, account, domain, folder)
    results["dns *." + domain] = setup_dns(token, zone, domain, tunnel)
    service_token, results["service token lamp " + domain] = setup_service_token(token, account, domain, folder)
    results["access application lamp " + domain] = setup_application(token, account, domain, email, service_token)
    results["cache rule"] = setup_cache(token, zone, domain)
    return results


def main():
    if sys.argv[1:] != ["setup"]:
        raise SystemExit("Usage: cloudflare.py setup")
    results = setup(control.configuration(), control.STATE / "cloudflare")
    for name, status in results.items():
        print(f"{name}: {status}")
    if any(status != "ok" for status in results.values()):
        print("next: ./lamp restart")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, yaml.YAMLError) as error:
        print(str(error) if not isinstance(error, OSError) else "LAMP filesystem error; check /var/lib/lamp/cloudflare permissions.", file=sys.stderr)
        sys.exit(1)
