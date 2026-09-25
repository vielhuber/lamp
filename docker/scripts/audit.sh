#!/usr/bin/env bash
set -uo pipefail
shopt -s nullglob dotglob
if [[ -t 1 && "${TERM:-dumb}" != dumb ]]; then clear; fi
cd /var/www || exit 1

audit_git_repository() {
    [[ -e "$1/.git" ]] && git -C "$1" rev-parse --git-dir >/dev/null 2>&1
}

audit_repository_identity() {
    local remote="${1,,}"
    remote=${remote%/}
    remote=${remote#git@github.com:}
    remote=${remote#https://github.com/}
    remote=${remote#http://github.com/}
    remote=${remote#ssh://git@github.com/}
    printf '%s\n' "${remote%.git}"
}

audit_own_repository() {
    audit_git_repository "$1" || return 1
    local remote
    remote=$(git -C "$1" remote get-url origin 2>/dev/null) || return 0
    if [[ "${remote,,}" =~ ^(git@github\.com:|https?://github\.com/|ssh://git@github\.com/)([^/]+)/ ]]; then
        [[ "${BASH_REMATCH[2]}" = vielhuber ]] || grep -Fxiq -- "${BASH_REMATCH[2]}" <<< "$organizations"
        return
    fi
    return 0
}

declare -A ignored=()
if ignored_folders=$(yq -r '.ignore_from_audit // [] | .[]' /etc/lamp-config/setup.yaml); then
    while IFS= read -r folder; do
        [[ -n "$folder" ]] && ignored["$folder"]=1
    done <<< "$ignored_folders"
else
    printf '⚠️ ignore_from_audit in setup.yaml could not be read.\n'
fi

if organizations=$(gh org list --limit 10000) && repositories=$(while IFS= read -r owner; do
    [[ -n "$owner" ]] || continue
    gh repo list "$owner" --no-archived --limit 10000 --json name,sshUrl --jq '.[] | "\(.name) \(.sshUrl)"' || exit 1
done <<< "$(printf '%s\n' vielhuber "$organizations" | sort -u)" | sort -u); then
    declare -A local_repositories=()
    for directory in */; do
        audit_git_repository "$directory" || continue
        remote=$(git -C "$directory" remote get-url origin 2>/dev/null) || continue
        [[ -n "$remote" ]] || continue
        local_repositories["$(audit_repository_identity "$remote")"]=1
    done
    missing=0
    while read -r name url; do
        [[ -z "$name" || "$name" = setup || "$name" = vielhuber ]] && continue
        if [[ ! -v local_repositories["$(audit_repository_identity "$url")"] ]]; then
            [[ "$missing" -eq 0 ]] && printf '\n🔎 GitHub repositories missing locally\n\n'
            printf '⛔ %s [not cloned]\n' "$name"
            missing=$((missing+1))
        fi
    done <<< "$repositories"
    [[ "$missing" -gt 0 ]] && printf '%d repositories missing locally.\n\n' "$missing"
else
    printf '⚠️ GitHub repository check failed.\n'
fi

missing=0
for d in */; do
    [[ "$d" = .git/ || "$d" = _environments/ || -v ignored["${d%/}"] ]] && continue
    if ! audit_git_repository "$d"; then
        [[ "$missing" -eq 0 ]] && printf '\n🔎 Folders without a Git repository\n\n'
        printf '⛔ %s [no git]\n' "${d%/}"
        missing=$((missing+1))
    fi
done
[[ "$missing" -gt 0 ]] && printf '%d folders without a Git repository.\n\n' "$missing"

if lamp_directories=$(yq -r 'if type != "array" then error("Expected an environment list") else .[] | select(.subdomain != null) | .directory // (.subdomain | if type == "array" then .[0] else . end) end' /etc/lamp-config/env.yaml |
    while IFS= read -r directory; do
        case "$directory" in /*) ;; *) directory="/var/www/$directory" ;; esac
        realpath -m -- "$directory"
    done); then
    lamp_read=1
else
    lamp_read=0
    printf '⚠️ LAMP environment configuration could not be read.\n'
fi

max=0
projects=0
up_to_date=0
for d in ./*/; do
    n=$(basename "$d")
    [[ "$n" = _archive || -v ignored["$n"] ]] && continue
    audit_own_repository "$d" || continue
    projects=$((projects+1))
    [[ ${#n} -gt $max ]] && max=${#n}
done
printf 'Analyzing %d projects...\n\n' "$projects"

for d in ./*/; do
    n=$(basename "$d")
    [[ "$n" = _archive || -v ignored["$n"] ]] && continue
    audit_own_repository "$d" || continue
    lamp_ok=0
    if [[ "$lamp_read" -eq 0 ]]; then
        lamp_ok=2
    elif ! grep -Fxq -- "$(realpath -- "$d")" <<< "$lamp_directories"; then
        lamp_ok=1
    fi
    npm_ok=0
    comp_ok=0
    phprc_ok=0
    nvmrc_ok=0
    mod_ok=0
    sync_ok=0
    fetch_timeout=0
    fetch_failed=0
    pull_failed=0
    if s=$(git -C "$d" status --porcelain); then
        [[ -n "$s" ]] && mod_ok=1
    else
        mod_ok=1
    fi
    a=0
    b=0
    if git -C "$d" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
        fetch_result=0
        timeout 60s git -C "$d" fetch --all --prune >/dev/null 2>&1 || fetch_result=$?
        [[ "$fetch_result" -eq 124 ]] && fetch_timeout=1
        [[ "$fetch_result" -ne 0 && "$fetch_result" -ne 124 ]] && fetch_failed=1
        a=$(git -C "$d" rev-list --count --left-only HEAD...@{u})
        b=$(git -C "$d" rev-list --count --right-only HEAD...@{u})
        if [[ "$mod_ok" -eq 0 && "$fetch_result" -eq 0 && "$b" -gt 0 ]]; then
            if git -C "$d" pull --ff-only --no-rebase --no-autostash >/dev/null 2>&1; then
                a=$(git -C "$d" rev-list --count --left-only HEAD...@{u})
                b=$(git -C "$d" rev-list --count --right-only HEAD...@{u})
            else
                pull_failed=1
            fi
        fi
    fi
    [[ "$a" -gt 0 || "$b" -gt 0 ]] && sync_ok=1

    while IFS= read -r -d '' manifest; do
        directory=${manifest%/*}
        case "$manifest" in
            */package.json)
                [[ "$n" = vuejs-tutorial ]] && continue
                if nout=$(cd "$directory" && ncu --jsonUpgraded --no-upgrade --install never --minimal --dep prod,dev,optional,peer --timeout 60000 --retry 0 2>/dev/null) &&
                    major_updates=$(jq -e --slurpfile package "$manifest" '
                        def major: capture("^[~^<>=v[:space:]]*(?<major>[0-9]+)").major | tonumber;
                        ($package[0] | (.dependencies // {}) + (.devDependencies // {}) + (.optionalDependencies // {}) + (.peerDependencies // {})) as $dependencies |
                        [to_entries[] | select((.value | major) > ($dependencies[.key] | major))] | length
                    ' <<< "$nout" 2>/dev/null); then
                    [[ "$major_updates" -gt 0 && "$npm_ok" -eq 0 ]] && npm_ok=1
                else
                    npm_ok=2
                fi
                ;;
            */composer.json)
                (cd "$directory" && php8.5 "$(command -v composer)" outdated -D --strict --major-only >/dev/null 2>&1) || comp_ok=1
                ;;
        esac
    done < <(find "$d" -type d \( -name node_modules -o -name vendor -o -name venv -o -name .venv -o -name _tests -o -name tests -o -name test -o -path '*/gists/gists' -o -name .git -o -name _archive -o -name wp-admin -o -name wp-includes -o -path '*/wp-content/plugins' -o -path '*/wp-content/mu-plugins' -o -path '*/wp-content/uploads' -o -path '*/wp-content/languages' -o \( -path '*/wp-content/themes/*' ! -path "*/wp-content/themes/$n" ! -path "*/wp-content/themes/$n/*" \) \) -prune -o -type f \( -name package.json -o -name composer.json \) -print0)

    if [[ -f "$d/.phprc" ]]; then
        [[ "$(cat "$d/.phprc")" = 8.5 ]] || phprc_ok=1
    elif [[ -f "$d/wp-content/themes/$n/.phprc" ]]; then
        [[ "$(cat "$d/wp-content/themes/$n/.phprc")" = 8.5 ]] || phprc_ok=1
    fi
    if [[ -f "$d/.nvmrc" ]]; then
        [[ "$(cat "$d/.nvmrc")" = 'lts/*' ]] || nvmrc_ok=1
    elif [[ -f "$d/wp-content/themes/$n/.nvmrc" ]]; then
        [[ "$(cat "$d/wp-content/themes/$n/.nvmrc")" = 'lts/*' ]] || nvmrc_ok=1
    fi

    if [[ "$lamp_ok" -eq 0 && "$npm_ok" -eq 0 && "$comp_ok" -eq 0 && "$phprc_ok" -eq 0 && "$nvmrc_ok" -eq 0 && "$mod_ok" -eq 0 && "$sync_ok" -eq 0 && "$fetch_timeout" -eq 0 && "$fetch_failed" -eq 0 && "$pull_failed" -eq 0 ]]; then
        up_to_date=$((up_to_date+1))
    else
        m=''
        [[ "$lamp_ok" -eq 1 ]] && m="${m}lamp missing, "
        [[ "$lamp_ok" -eq 2 ]] && m="${m}lamp check failed, "
        [[ "$npm_ok" -eq 1 ]] && m="${m}npm, "
        [[ "$npm_ok" -eq 2 ]] && m="${m}npm check failed, "
        [[ "$comp_ok" -eq 1 ]] && m="${m}composer, "
        [[ "$phprc_ok" -eq 1 ]] && m="${m}php, "
        [[ "$nvmrc_ok" -eq 1 ]] && m="${m}nvm, "
        [[ "$mod_ok" -eq 1 ]] && m="${m}modified, "
        [[ "$sync_ok" -eq 1 ]] && m="${m}behind/ahead, "
        [[ "$fetch_timeout" -eq 1 ]] && m="${m}fetch timeout, "
        [[ "$fetch_failed" -eq 1 ]] && m="${m}fetch failed, "
        [[ "$pull_failed" -eq 1 ]] && m="${m}pull failed, "
        printf "⛔ %-${max}s  [%s]\n" "$n" "${m%, }"
    fi
done
printf '\n✅ %d projects are up to date.\n\n' "$up_to_date"
