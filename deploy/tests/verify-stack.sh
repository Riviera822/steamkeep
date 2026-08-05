#!/bin/sh
# SteamVault WP 1.9 -- container verification suite.
#
# Proves that the three images and deploy/compose.yaml actually deliver what the
# Phase-0 PoC and WP 1.1-1.8 established, INSIDE Linux containers: the cache
# stores and serves real Steam CDN bytes, the API answers and authenticates, the
# DNS container redirects A and NODATAs AAAA, and every fail-fast guard fails.
#
# Run it on a Linux host with Docker (this project develops on Windows; the
# canonical place to run this is WSL2):
#
#     sudo sh deploy/tests/verify-stack.sh
#
# It is self-contained and side-effect-free by design:
#   * uses its own Compose project name (steamvault-verify), so it can never
#     touch a real deployment's containers or volumes
#   * publishes every port on 127.0.0.1 and on non-default port numbers, so it
#     cannot collide with a host nginx on :80 or a host resolver on :53
#   * tears its containers and volumes down at the end (images are kept -- they
#     are the artifact under test)
#
# Requires outbound internet: two of the checks talk to the real Steam CDN and
# to a public resolver on purpose. There is no mock -- the whole point of Phase 0
# was that only real traffic settles these questions.
#
# Exit code 0 = every check passed.

set -u

# --- where things are --------------------------------------------------------
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
compose_file="$repo_root/deploy/compose.yaml"

PROJECT=steamvault-verify
TAG=${VAULT_IMAGE_TAG:-0.1.0}

# Non-default, loopback-only host ports (see header).
CORE_PORT=8180
API_PORT=8181
DNS_PORT=15353

# The known-good Phase-0 test object: depot 70403, used from poc/ through
# core/tests/test-core.ps1. Small, stable, and already proven to be a real
# cacheable chunk.
DEPOT=70403
CHUNK=773d10050d99b2544665873ec2125b3bf273e8b2
CDN_HOST=cache2-ams1.steamcontent.com

CORE_URL="http://127.0.0.1:$CORE_PORT"
API_URL="http://127.0.0.1:$API_PORT"
DEPOT_URI="/depot/$DEPOT/chunk/$CHUNK"

TEST_API_KEY="verify-only-not-a-real-key-$$"
TEST_CACHE_IP=192.168.222.50

work=$(mktemp -d)
env_file="$work/verify.env"

pass=0
fail=0

# --- output helpers ----------------------------------------------------------
section() { printf '\n\n## %s\n\n' "$*"; }
step()    { printf '\n### %s\n\n' "$*"; }
say()     { printf '%s\n' "$*"; }
run()     { printf '$ %s\n' "$*"; sh -c "$*" 2>&1 | sed 's/^/    /'; }

ok()   { pass=$((pass + 1)); printf 'PASS  %s\n' "$*"; }
bad()  { fail=$((fail + 1)); printf 'FAIL  %s\n' "$*"; }

# assert_eq <expected> <actual> <description>
assert_eq() {
    if [ "$1" = "$2" ]; then ok "$3 (= $2)"; else bad "$3 -- expected '$1', got '$2'"; fi
}
# assert_contains <haystack> <needle> <description>
assert_contains() {
    case "$1" in
        *"$2"*) ok "$3" ;;
        *)      bad "$3 -- '$2' not found in: $(printf '%s' "$1" | head -c 300)" ;;
    esac
}
# assert_not_contains <haystack> <needle> <description>
assert_not_contains() {
    case "$1" in
        *"$2"*) bad "$3 -- '$2' unexpectedly present" ;;
        *)      ok "$3" ;;
    esac
}

dc() {
    docker compose --env-file "$env_file" -f "$compose_file" -p "$PROJECT" "$@"
}

cleanup() {
    section "Cleanup"
    say 'Test containers and TEST volumes are removed; the three images are kept (they are the artifact).'
    run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' --profile dns down -v --remove-orphans"
    docker volume rm -f "$PROJECT-split-cache" "$PROJECT-scratch" >/dev/null 2>&1
    rm -rf "$work"
}
trap cleanup EXIT INT TERM

printf '# SteamVault WP 1.9 -- container verification transcript\n\n'
say "date:            $(date -u '+%Y-%m-%dT%H:%M:%SZ') (UTC)"
say "host:            $(uname -srm)"
say "distro:          $(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-unknown}")"
say "docker:          $(docker version --format '{{.Server.Version}}' 2>/dev/null)"
say "docker compose:  $(docker compose version --short 2>/dev/null)"
say "repo:            $repo_root"
say "compose project: $PROJECT (isolated from any real deployment)"

# =============================================================================
section "1. Config drift: the container nginx.conf is the reviewed one"
# =============================================================================
say 'core/docker/nginx.conf.template must stay identical to the reviewed, CDN-tested'
say 'core/nginx/nginx.conf apart from five container-plumbing lines.'
step "1a. Positive: the checked-in pair is in sync"
run "sh '$repo_root/core/docker/check-config-drift.sh'"
if sh "$repo_root/core/docker/check-config-drift.sh" >/dev/null 2>&1; then
    ok "check-config-drift.sh reports the two configs in sync"
else
    bad "check-config-drift.sh reports drift"
fi

step "1b. Negative: an injected difference is actually caught"
say 'Without this, a green drift check would prove nothing. A copy of the pair is'
say 'mutated (proxy_connect_timeout 3s -> 30s in the container template only) and'
say 'the same script is re-run against the copy.'
mkdir -p "$work/drift/nginx" "$work/drift/docker"
cp "$repo_root/core/nginx/nginx.conf" "$work/drift/nginx/nginx.conf"
cp "$repo_root/core/docker/nginx.conf.template" "$work/drift/docker/nginx.conf.template"
cp "$repo_root/core/docker/check-config-drift.sh" "$work/drift/docker/check-config-drift.sh"
sed -i 's/proxy_connect_timeout      3s;/proxy_connect_timeout      30s;/' "$work/drift/docker/nginx.conf.template"
run "sh '$work/drift/docker/check-config-drift.sh' 2>&1 | tail -12"
if sh "$work/drift/docker/check-config-drift.sh" >/dev/null 2>&1; then
    bad "drift check did NOT catch an injected difference"
else
    ok "drift check catches an injected difference (exit non-zero)"
fi

# =============================================================================
section "2. Image builds"
# =============================================================================
for svc in core api dns; do
    step "2.$svc  docker build $svc/"
    if docker build -t "steamvault/vault-$svc:$TAG" "$repo_root/$svc" > "$work/build-$svc.log" 2>&1; then
        ok "vault-$svc image built"
    else
        bad "vault-$svc build FAILED"
        tail -30 "$work/build-$svc.log" | sed 's/^/    /'
    fi
done

step "2.sizes  Built image sizes"
run "docker images --filter reference='steamvault/*' --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.ID}}'"

step "2.pins  Base image pins actually used (tag + digest)"
run "grep -h '^FROM' '$repo_root/core/Dockerfile' '$repo_root/api/Dockerfile' '$repo_root/dns/Dockerfile'"

step "2.sp  SteamPrefill binary in the vault-api image"
say 'Checked by inspection only. This work package deliberately does NOT execute'
say 'SteamPrefill in a container: it has no Steam session, and creating one is the'
say "operator's one-time interactive step (deploy/README.md 'First run')."
run "docker run --rm --entrypoint sh steamvault/vault-api:$TAG -c 'ls -l /opt/steamprefill/SteamPrefill; sha256sum /opt/steamprefill/SteamPrefill; head -c 4 /opt/steamprefill/SteamPrefill | od -c | head -1'"
sp_deps=$(docker run --rm --entrypoint sh "steamvault/vault-api:$TAG" -c 'ldd /opt/steamprefill/SteamPrefill 2>&1' )
say ''
say 'Dynamic libraries the binary needs, resolved inside the image (no "not found"):'
printf '%s\n' "$sp_deps" | sed 's/^/    /'
assert_not_contains "$sp_deps" "not found" "every shared library SteamPrefill needs resolves in the image"

step "2.home  HOME for uid 101 exists, is owned by it, and both definitions agree"
say 'Regression guard for the WP 1.9 review blocker: with HOME unwritable,'
say "SteamPrefill's AppConfig static constructor throws before parsing any"
say 'argument, so the documented login and every prefill job die identically.'
run "docker run --rm --entrypoint sh steamvault/vault-api:$TAG -c 'getent passwd 101; echo \"ENV HOME=\$HOME\"; stat -c \"%n %u:%g %a\" \$HOME'"
home_passwd=$(docker run --rm --entrypoint sh "steamvault/vault-api:$TAG" -c 'getent passwd 101 | cut -d: -f6')
home_env=$(docker run --rm --entrypoint sh "steamvault/vault-api:$TAG" -c 'printf %s "$HOME"')
home_own=$(docker run --rm --entrypoint sh "steamvault/vault-api:$TAG" -c 'stat -c "%u:%g" /opt/steamprefill/home')
assert_eq "/opt/steamprefill/home" "$home_passwd" "passwd entry for uid 101 has a real home"
assert_eq "$home_passwd" "$home_env"             "ENV HOME agrees with the passwd entry"
assert_eq "101:101" "$home_own"                  "HOME is owned by the container user"

step "2.smoke  Credential-free SteamPrefill smoke check"
say 'Runs the real binary with stdin closed and NO credentials. Expected sane'
say 'outcome: it starts, reports that a Steam account is required, prompts for a'
say 'username, and then exits because stdin is at EOF. What must NOT appear is a'
say 'TypeInitializationException -- that is the blocker signature, and it fires'
say 'before any prompt, so "reached the username prompt" is the proof it is gone.'
say ''
say 'NO CREDENTIALS ARE ENTERED HERE, EVER. Logging in is the operator step.'
# ANSI is stripped: SteamPrefill colourises mid-sentence (e.g. "A <esc>[38;5;80m
# Steam<esc>[0m account is required"), so raw substring matching is unreliable.
strip_ansi() { sed -e 's/\x1B\[[0-9;]*[A-Za-z]//g'; }
sp_smoke=$(docker run --rm --entrypoint /opt/steamprefill/SteamPrefill \
             "steamvault/vault-api:$TAG" select-apps < /dev/null 2>&1 | strip_ansi | head -12)
printf '%s\n' "$sp_smoke" | sed 's/^/    /'
assert_not_contains "$sp_smoke" "TypeInitializationException" "no TypeInitializationException (the blocker signature)"
assert_not_contains "$sp_smoke" "UnauthorizedAccessException" "no UnauthorizedAccessException reaching for HOME"
assert_contains     "$sp_smoke" "account is required in order to prefill apps" "SteamPrefill starts and reaches its login logic"
assert_contains     "$sp_smoke" "Steam account name" "...and gets as far as prompting for a username"

# =============================================================================
section "3. compose.yaml review surface"
# =============================================================================
cat > "$env_file" <<EOF
# generated by deploy/tests/verify-stack.sh -- test values only
VAULT_API_KEY=$TEST_API_KEY
VAULT_IMAGE_TAG=$TAG
VAULT_CORE_BIND=127.0.0.1
VAULT_CORE_PORT=$CORE_PORT
VAULT_API_BIND=127.0.0.1
VAULT_API_PORT=$API_PORT
VAULT_DNS_BIND=127.0.0.1
VAULT_DNS_PORT=$DNS_PORT
CACHE_IP=$TEST_CACHE_IP
EOF
say 'Test .env used for this run (ports moved off 80/8080/53 because this WSL host'
say 'already has services there; bind kept on loopback so nothing is LAN-visible):'
say ''
sed 's/^/    /' "$env_file"

step "3a. Rendered configuration (docker compose config)"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' --profile dns config"

step "3b. No secret is baked into compose.yaml"
if grep -nE '(VAULT_API_KEY[[:space:]]*[:=][[:space:]]*[A-Za-z0-9])' "$compose_file" | grep -v '\${' > "$work/secret.txt"; then
    bad "compose.yaml appears to contain a literal API key"
    sed 's/^/    /' "$work/secret.txt"
else
    ok "compose.yaml contains no literal secret (VAULT_API_KEY only as a required \${...} reference)"
fi

step "3c. Port 53 is never published on 0.0.0.0"
rendered=$(dc --profile dns config 2>/dev/null)
assert_not_contains "$rendered" "0.0.0.0:53" "rendered config does not publish :53 on 0.0.0.0"
bare53=$(grep -nE '^[[:space:]]*-[[:space:]]*"?53:53' "$compose_file" || true)
assert_eq "" "$bare53" "compose.yaml has no bare 53:53 mapping"

step "3d. VAULT_API_KEY is required, not defaulted"
noKey=$(docker compose --env-file /dev/null -f "$compose_file" -p "$PROJECT" config 2>&1 >/dev/null)
say "$noKey" | sed 's/^/    /'
assert_contains "$noKey" "VAULT_API_KEY" "compose refuses to render without VAULT_API_KEY"

# =============================================================================
section "4. Stack up (vault-core + vault-api)"
# =============================================================================
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' up -d"

say ''
say 'Waiting for both healthchecks to report healthy...'
i=0
while [ "$i" -lt 60 ]; do
    core_h=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-core)" 2>/dev/null || echo starting)
    api_h=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-api)" 2>/dev/null || echo starting)
    [ "$core_h" = "healthy" ] && [ "$api_h" = "healthy" ] && break
    i=$((i + 1))
    sleep 2
done
say "vault-core health: $core_h    vault-api health: $api_h"
assert_eq "healthy" "$core_h" "vault-core container healthcheck"
assert_eq "healthy" "$api_h"  "vault-api container healthcheck"

step "4a. vault-core boot log (preflight output)"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' logs vault-core"

step "4b. Container users and the shared cache volume"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-core sh -c 'ps -o user,args | head -4; echo; stat -c \"%n %U:%G %a\" /vault /vault/cache /vault/cache/depot /vault/tmp'"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-api sh -c 'id; stat -c \"%n %u:%g\" /vault/cache /data /opt/steamprefill/Config'"

# =============================================================================
section "5. vault-core behaviour"
# =============================================================================
step "5a. /health"
health=$(curl -s --max-time 10 "$CORE_URL/health")
say "    $health"
assert_eq "ok" "$(printf '%s' "$health" | tr -d '\n')" "GET /health returns ok"

step "5b. LanCache heartbeat (ADR-0001 req 1 -- SteamPrefill refuses to prefill without it)"
hb=$(curl -s -D - -o /dev/null --max-time 10 "$CORE_URL/lancache-heartbeat")
# printf '%s\n', not '%s': command substitution strips the trailing newline, and
# without restoring it the next PASS line gets appended to the last header line
# instead of starting its own -- which makes `grep -c '^PASS'` under-count the
# results by one against the summary. (Found reviewing the first recorded run.)
printf '%s\n' "$hb" | sed 's/^/    /'
assert_contains "$hb" "X-LanCache-Processed-By: steamvault" "heartbeat carries X-LanCache-Processed-By"

step "5c. Temp paths are not web-reachable (WP 1.1 S3 fix)"
tmp_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$CORE_URL/tmp/proxy/anything")
assert_eq "404" "$tmp_code" "GET /tmp/proxy/... returns 404"

step "5d. Host allowlist (ADR-0001 req 4 -- no open proxy)"
forged=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H 'Host: evil.example.com' "$CORE_URL$DEPOT_URI")
assert_eq "403" "$forged" "a forged non-Steam Host is refused on the miss path"

step "5e. REAL Steam CDN cache test: MISS -> stored in the volume -> HIT"
say "object: $DEPOT_URI  (Host: $CDN_HOST)"
say ''
miss=$(curl -s -o "$work/miss.bin" -w 'http=%{http_code} bytes=%{size_download} seconds=%{time_total}' \
        --max-time 120 -H "Host: $CDN_HOST" "$CORE_URL$DEPOT_URI")
say "    MISS  $miss"
miss_code=$(printf '%s' "$miss" | sed -n 's/.*http=\([0-9]*\).*/\1/p')
assert_eq "200" "$miss_code" "cold request returns 200 from the real Steam CDN"

sleep 1
stored=$(dc exec -T vault-core sh -c "ls -l /vault/cache/depot/$DEPOT/chunk/$CHUNK 2>&1")
say "    stored: $stored"
assert_not_contains "$stored" "No such file" "the response was proxy_store'd into the volume at the path-faithful location"

hit=$(curl -s -o "$work/hit.bin" -w 'http=%{http_code} bytes=%{size_download} seconds=%{time_total}' \
        --max-time 120 -H "Host: $CDN_HOST" "$CORE_URL$DEPOT_URI")
say "    HIT   $hit"
hit_code=$(printf '%s' "$hit" | sed -n 's/.*http=\([0-9]*\).*/\1/p')
assert_eq "200" "$hit_code" "warm request returns 200"

miss_sha=$(sha256sum "$work/miss.bin" | cut -d' ' -f1)
hit_sha=$(sha256sum "$work/hit.bin" | cut -d' ' -f1)
disk_sha=$(dc exec -T vault-core sh -c "sha256sum /vault/cache/depot/$DEPOT/chunk/$CHUNK" | cut -d' ' -f1)
miss_size=$(stat -c %s "$work/miss.bin")
say ''
say "    sha256 MISS body : $miss_sha  ($miss_size bytes)"
say "    sha256 HIT  body : $hit_sha"
say "    sha256 on disk   : $disk_sha"
assert_eq "$miss_sha" "$hit_sha"  "MISS and HIT bodies are byte-identical"
assert_eq "$miss_sha" "$disk_sha" "the stored file is byte-identical to what the client received"

step "5f. ?nocache=1 bypass (ADR-0001 req 3 -- SteamPrefill's speed probe)"
nc_code=$(curl -s -o "$work/nocache.bin" -w '%{http_code}' --max-time 120 -H "Host: $CDN_HOST" "$CORE_URL$DEPOT_URI?nocache=1")
nc_sha=$(sha256sum "$work/nocache.bin" | cut -d' ' -f1)
assert_eq "200" "$nc_code" "?nocache=1 request succeeds"
assert_eq "$miss_sha" "$nc_sha" "?nocache=1 returns the same bytes (refreshed, not corrupted)"

step "5g. Access log: the vault log format reaches docker logs unchanged"
say 'Log rotation is the json-file driver (max-size/max-file in compose.yaml) --'
say 'this is what makes that possible: nginx writes to stdout, not to a file.'
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' logs --no-log-prefix vault-core | grep depot"
logline=$(dc logs --no-log-prefix vault-core 2>/dev/null | grep "$CHUNK" | head -1)
assert_contains "$logline" "cache=" "access log lines carry the vault format's cache= field"

step "5h. json-file log limits are actually applied to the container"
run "docker inspect --format '{{.HostConfig.LogConfig.Type}} {{.HostConfig.LogConfig.Config}}' \$(docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' ps -q vault-core vault-api)"
logcfg=$(docker inspect --format '{{.HostConfig.LogConfig.Type}} {{.HostConfig.LogConfig.Config}}' "$(dc ps -q vault-core)")
assert_contains "$logcfg" "json-file" "vault-core uses the json-file driver"
assert_contains "$logcfg" "max-size" "vault-core has a max-size limit"
assert_contains "$logcfg" "max-file" "vault-core has a max-file limit"

# =============================================================================
section "6. vault-api behaviour"
# =============================================================================
step "6a. GET /v1/health (the one unauthenticated route, by design)"
apihealth=$(curl -s --max-time 10 "$API_URL/v1/health")
say "    $apihealth"
assert_contains "$apihealth" '"status":"ok"' "GET /v1/health returns status ok"

step "6b. Auth is enforced"
code_nokey=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$API_URL/v1/games")
assert_eq "401" "$code_nokey" "GET /v1/games without X-Api-Key is 401"
code_wrong=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "X-Api-Key: wrong" "$API_URL/v1/games")
assert_eq "401" "$code_wrong" "GET /v1/games with a wrong key is 401"
code_docs=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$API_URL/openapi.json")
assert_eq "404" "$code_docs" "/openapi.json is disabled"

step "6c. PUT/GET mapping round-trip through the container"
put=$(curl -s --max-time 10 -X PUT "$API_URL/v1/mapping/441" \
        -H "X-Api-Key: $TEST_API_KEY" -H 'Content-Type: application/json' \
        -d '{"appid": 440, "app_name": "Team Fortress 2"}')
say "    PUT  /v1/mapping/441 -> $put"
assert_contains "$put" '"depotid":441' "PUT /v1/mapping/441 accepted"

getmap=$(curl -s --max-time 10 -H "X-Api-Key: $TEST_API_KEY" "$API_URL/v1/mapping")
say "    GET  /v1/mapping     -> $getmap"
assert_contains "$getmap" '{"depotid":441,"appid":440}' "GET /v1/mapping returns the round-tripped pair"

games=$(curl -s --max-time 10 -H "X-Api-Key: $TEST_API_KEY" "$API_URL/v1/games")
say "    GET  /v1/games       -> $games"
assert_contains "$games" '"appid":440' "GET /v1/games shows the app created by the mapping"

step "6d. The API sees the SAME cache volume vault-core just wrote into"
say 'This is the shared-volume/uid contract: vault-core (uid 101) stored a real'
say 'depot chunk above; vault-api (also uid 101) must be able to size it.'
summary=$(curl -s --max-time 20 -H "X-Api-Key: $TEST_API_KEY" "$API_URL/v1/cache/summary")
say "    GET  /v1/cache/summary -> $summary"
unmapped=$(printf '%s' "$summary" | sed -n 's/.*"unmapped_depots":{"count":\([0-9]*\).*/\1/p')
assert_eq "1" "$unmapped" "vault-api sees exactly 1 unmapped depot on disk (the $DEPOT chunk vault-core cached)"
assert_not_contains "$summary" '"total_bytes":0' "vault-api reports non-zero bytes for the shared cache volume"

step "6e. The database landed on its own volume"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-api sh -c 'ls -l /data'"

step "6f. SteamPrefill on the other two invocation paths (still credential-free)"
say 'The blocker was reproduced on three paths and they derive HOME differently,'
say 'so all three are guarded. Step 2.smoke covered plain `docker run`; these two'
say 'are the DOCUMENTED login command and the exec-into-a-running-container case.'
say ''
say 'Again: no credentials are entered. Reaching the username prompt IS the pass.'

sp_run=$(dc run --rm --no-deps -T vault-api \
           /opt/steamprefill/SteamPrefill select-apps < /dev/null 2>&1 | strip_ansi | head -8)
say ''
say '    $ docker compose run --rm --no-deps vault-api /opt/steamprefill/SteamPrefill select-apps'
printf '%s\n' "$sp_run" | sed 's/^/    /'
assert_not_contains "$sp_run" "TypeInitializationException" "compose run: no TypeInitializationException"
assert_contains     "$sp_run" "account is required in order to prefill apps" "compose run (the documented login flow) reaches SteamPrefill's login logic"

sp_exec=$(dc exec -T vault-api \
            /opt/steamprefill/SteamPrefill select-apps < /dev/null 2>&1 | strip_ansi | head -8)
say ''
say '    $ docker compose exec vault-api /opt/steamprefill/SteamPrefill select-apps'
printf '%s\n' "$sp_exec" | sed 's/^/    /'
assert_not_contains "$sp_exec" "TypeInitializationException" "compose exec: no TypeInitializationException"
assert_contains     "$sp_exec" "account is required in order to prefill apps" "compose exec reaches SteamPrefill's login logic"

step "6g. What SteamPrefill actually wrote under HOME (now persistent)"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-api sh -c 'find /opt/steamprefill/home -maxdepth 3 | head -20; echo; stat -c \"%n %u:%g\" /opt/steamprefill/home'"

# =============================================================================
section "7. vault-dns (--profile dns)"
# =============================================================================
step "7a. Fail-fast: no CACHE_IP"
say 'dns/README.md makes CACHE_IP required with no default; the entrypoint must'
say 'refuse to start rather than emit address=/steamcontent.com/ with no address.'
nocacheip=$(docker run --rm "steamvault/vault-dns:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$nocacheip" | sed 's/^/    /'
assert_contains "$nocacheip" "FATAL" "vault-dns refuses to start without CACHE_IP"
assert_not_contains "$nocacheip" "exit=0" "...and exits non-zero"

step "7b. Fail-fast: CACHE_IP that is not a plain IPv4 address"
badip=$(docker run --rm -e 'CACHE_IP=1.2.3.4
log-queries' "steamvault/vault-dns:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$badip" | sed 's/^/    /'
assert_contains "$badip" "FATAL" "a CACHE_IP carrying an injected config line is refused"

step "7c. Start the dns profile"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' --profile dns up -d vault-dns"
sleep 3
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' logs vault-dns"

step "7d. Rendered dnsmasq.conf inside the container"
run "docker compose --env-file '$env_file' -f '$compose_file' -p '$PROJECT' exec -T vault-dns sh -c 'grep -v \"^#\" /run/vault-dns/dnsmasq.conf | grep -v \"^\$\"'"

step "7e. A query for a Steam CDN name -> the cache IP"
a_ans=$(dig +short @127.0.0.1 -p "$DNS_PORT" A "$CDN_HOST" 2>&1)
say "    dig +short @127.0.0.1 -p $DNS_PORT A $CDN_HOST"
say "      -> $a_ans"
assert_eq "$TEST_CACHE_IP" "$(printf '%s' "$a_ans" | tr -d '\n')" "A query is answered with CACHE_IP"

wild_ans=$(dig +short @127.0.0.1 -p "$DNS_PORT" A anything.else.steamcontent.com 2>&1)
say "    wildcard subdomain -> $wild_ans"
assert_eq "$TEST_CACHE_IP" "$(printf '%s' "$wild_ans" | tr -d '\n')" "the wildcard covers arbitrary subdomains"

step "7f. AAAA -> NODATA (ADR-0001 req 6: the IPv6 bypass stays closed)"
aaaa=$(dig @127.0.0.1 -p "$DNS_PORT" AAAA "$CDN_HOST" 2>&1)
printf '%s\n' "$aaaa" | grep -E 'status:|ANSWER SECTION|^cache2|ANSWER:' | sed 's/^/    /'
assert_contains "$aaaa" "status: NOERROR" "AAAA answer status is NOERROR"
assert_contains "$aaaa" "ANSWER: 0" "AAAA answer contains zero records (NODATA, not Valve's real IPv6)"

step "7g. Everything else is still forwarded upstream"
fwd=$(dig +short @127.0.0.1 -p "$DNS_PORT" A example.com 2>&1)
say "    dig +short A example.com -> $(printf '%s' "$fwd" | tr '\n' ' ')"
if [ -n "$fwd" ]; then ok "non-steamcontent.com queries are forwarded and answered"; else bad "upstream forwarding returned nothing"; fi

step "7h. vault-dns healthcheck"
dns_h=""
i=0
while [ "$i" -lt 20 ]; do
    dns_h=$(docker inspect --format '{{.State.Health.Status}}' "$(dc ps -q vault-dns)" 2>/dev/null || echo starting)
    [ "$dns_h" = "healthy" ] && break
    i=$((i + 1)); sleep 2
done
assert_eq "healthy" "$dns_h" "vault-dns container healthcheck"

# =============================================================================
section "8. vault-core fail-fast guards"
# =============================================================================
say 'Each of these is a deployment mistake that would otherwise be silent.'

step "8a. cache/ and tmp/ split across two filesystems"
say 'Simulated with a tmpfs over /vault/tmp (a different st_dev), which is exactly'
say 'what a second volume mount would look like to the preflight.'
docker volume create "$PROJECT-scratch" >/dev/null
split=$(docker run --rm -v "$PROJECT-scratch:/vault" --tmpfs /vault/tmp "steamvault/vault-core:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$split" | grep -E 'FATAL|st_dev|exit=' | sed 's/^/    /'
assert_contains "$split" "DIFFERENT" "a split cache//tmp mount is refused at boot"
assert_not_contains "$split" "exit=0" "...and exits non-zero"

step "8b. An empty VAULT_RESOLVER"
emptyres=$(docker run --rm -v "$PROJECT-scratch:/vault" -e VAULT_RESOLVER= "steamvault/vault-core:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$emptyres" | grep -E 'FATAL|exit=' | sed 's/^/    /'
assert_contains "$emptyres" "VAULT_RESOLVER is empty" "an empty resolver is refused"

step "8c. A VAULT_RESOLVER carrying an nginx-config injection"
inj=$(docker run --rm -v "$PROJECT-scratch:/vault" -e 'VAULT_RESOLVER=1.1.1.1; return 200 "pwned";' "steamvault/vault-core:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$inj" | grep -E 'FATAL|exit=' | sed 's/^/    /'
assert_contains "$inj" "refusing" "a resolver value with config-injection characters is refused"

step "8d. A misconfigured envsubst filter leaves a placeholder unrendered"
unrendered=$(docker run --rm -v "$PROJECT-scratch:/vault" -e 'NGINX_ENVSUBST_FILTER=^NOTHING_' "steamvault/vault-core:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$unrendered" | grep -E 'FATAL|unsubstituted|exit=' | sed 's/^/    /'
assert_contains "$unrendered" "unsubstituted" "an unrendered \${VAULT_...} placeholder is caught before nginx starts"

step "8e. A cache directory the worker user cannot write"
mkdir -p "$work/rootonly/cache/depot" "$work/rootonly/tmp"
chmod 0755 "$work/rootonly" "$work/rootonly/cache" "$work/rootonly/tmp"
chown -R 0:0 "$work/rootonly" 2>/dev/null
chmod 0555 "$work/rootonly/cache" "$work/rootonly/tmp"
ro=$(docker run --rm -v "$work/rootonly:/vault" "steamvault/vault-core:$TAG" 2>&1; echo "exit=$?")
printf '%s\n' "$ro" | grep -E 'FATAL|chown|exit=' | sed 's/^/    /'
assert_contains "$ro" "not writable" "a cache directory the nginx worker cannot write is refused"

# =============================================================================
section "9. Result"
# =============================================================================
say "checks passed: $pass"
say "checks failed: $fail"
if [ "$fail" -eq 0 ]; then
    say ''
    say 'ALL CHECKS PASSED'
    exit_code=0
else
    say ''
    say 'THERE WERE FAILURES'
    exit_code=1
fi
exit "$exit_code"
