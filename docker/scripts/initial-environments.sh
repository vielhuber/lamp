#!/usr/bin/env bash
set -euo pipefail
umask 077

config=/etc/lamp-config
data=/etc/lamp
projects=/var/www
controller=/opt/lamp/control.py
[[ -f "$config/initial-environments" ]] || exit 0
folder=$(realpath -e -- "$(< "$config/initial-environments")")
if [[ ! -d "$folder" || ( "$folder" != "$projects" && "$folder" != "$projects/"* ) ]]; then
    echo '❌ The initial environments folder must be mounted under /var/www.' >&2
    exit 1
fi
pending="$config/initial-environments.jsonl"
temporary=$(mktemp "$config/initial-environments.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT

if [[ ! -e "$pending" ]]; then
    existing=$(yq -c '.' "$config/env.yaml")
    if ! nebro_vpn=$(yq -r '(.vpn.enabled == true) and any(.vpn.tunnels[]?; .name == "nebro")' "$data/settings.yaml" 2>/dev/null); then
        echo '❌ Invalid settings.yaml.' >&2
        exit 1
    fi
    declare -A subdomains=()
    while IFS= read -r -d '' project; do
        [[ -e "$project/.git" ]] || continue
        name=${project##*/}
        directory=${project#"$projects/"}
        if jq -e --arg directory "$directory" 'any(.[]; .directory == $directory)' <<< "$existing" > /dev/null; then
            continue
        fi
        subdomain=$(printf '%s' "${name,,}" | sed -E 's/[^a-z0-9-]/-/g; s/^-+//; s/-+$//')
        if [[ ! "$subdomain" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ || -n ${subdomains[$subdomain]+set} ]]; then
            echo "❌ Invalid or duplicate subdomain for $name." >&2
            exit 1
        fi
        subdomains[$subdomain]=$name
        if jq -e --arg subdomain "$subdomain" 'any(.[]; (.subdomain | if type == "array" then . else [.] end) | index($subdomain) != null)' <<< "$existing" > /dev/null; then
            echo "❌ $name: subdomain already belongs to another environment." >&2
            exit 1
        fi
        remote=$(git -c safe.directory="$project" -C "$project" config --get remote.origin.url)
        if [[ ! "$remote" =~ ^(git@([A-Za-z0-9.-]+):|https://([A-Za-z0-9.-]+)/)([A-Za-z0-9_./-]+)$ ]]; then
            echo "❌ $name: origin must be an SSH or credential-free HTTPS repository URL." >&2
            exit 1
        fi
        host=${BASH_REMATCH[2]:-${BASH_REMATCH[3]}}
        repository=${BASH_REMATCH[4]%/}
        repository=${repository%.git}
        build="$data/build/${host,,}-${repository//\//-}.sh"
        php=8.5
        if [[ -f "$project/.phprc" ]]; then php=$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$project/.phprc"); fi
        for webroot in _public public new html/br-kk .; do
            [[ -d "$project/$webroot" ]] && break
        done
        engine= database= postgres=false
        if [[ -f "$build" ]]; then
            while IFS= read -r line || [[ -n "$line" ]]; do
                if [[ ${line%%#*} =~ (^|[^[:alnum:]_])postgres([^[:alnum:]_]|$) ]]; then postgres=true; fi
                [[ "$line" =~ ^[[:space:]]*syncdb[[:space:]]+ ]] || continue
                read -r command profile remainder <<< "$line"
                profile=${profile%;}
                if [[ "$profile" == \"*\" || "$profile" == \'*\' ]]; then profile=${profile:1:-1}; fi
                if [[ ! "$profile" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
                    echo "❌ $name: syncdb needs a literal profile name." >&2
                    exit 1
                fi
                profile_engine=$(jq -er '.engine | select(. == "mysql" or . == "postgres" or . == "sqlite")' "$data/syncdb/$profile.json")
                profile_database=$(jq -er '.target.database | select(type == "string" and length > 0)' "$data/syncdb/$profile.json")
                if [[ "$profile_engine" = sqlite ]]; then
                    profile_database=${profile_database##*/}
                    profile_database=${profile_database%.*}
                fi
                if [[ ! "$profile_database" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$ ]]; then
                    echo "❌ $name: invalid database name in syncdb profile." >&2
                    exit 1
                fi
                if [[ -n "$engine" && ( "$engine" != "$profile_engine" || "$database" != "$profile_database" ) ]]; then
                    echo "❌ $name: syncdb profiles refer to different databases." >&2
                    exit 1
                fi
                engine=$profile_engine database=$profile_database
            done < "$build"
        fi
        if [[ -z "$engine" && "$postgres" = true ]]; then engine=postgres database=$subdomain; fi
        arguments=(--git "$remote" --branch main --php "$php" --subdomain "$subdomain" --directory "$directory" --webroot "$webroot" --visibility private)
        if [[ "$name" = nebro && "$nebro_vpn" = true ]]; then arguments+=(--vpn nebro); fi
        if [[ -n "$engine" ]]; then arguments+=(--db-name "$database" --db-engine "$engine"); fi
        has_build=false
        if [[ -f "$build" ]]; then has_build=true; fi
        jq -cn --arg subdomain "$subdomain" --argjson build "$has_build" \
            '$ARGS.positional | {arguments: ., subdomain: $subdomain, build: $build}' --args -- "${arguments[@]}" >> "$temporary"
    done < <(find "$folder" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)
    mv "$temporary" "$pending"
fi

while IFS= read -r entry < "$pending"; do
    mapfile -d '' -t arguments < <(jq -j '.arguments[] | ., "\u0000"' <<< "$entry")
    subdomain=$(jq -r '.subdomain' <<< "$entry")
    echo "📥 registering initial environment $subdomain"
    python3 "$controller" add "${arguments[@]}" > /dev/null
    if [[ $(jq -r '.build' <<< "$entry") = true ]]; then
        python3 "$controller" build "$subdomain"
    fi
    tail -n +2 "$pending" > "$temporary"
    mv "$temporary" "$pending"
done
rm -- "$pending" "$config/initial-environments"
echo '✅ initial environments generated'
