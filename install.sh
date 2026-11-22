#!/usr/bin/env bash
set -euo pipefail

fail() { printf '%s\n' "$*" >&2; exit 1; }

tag=
assets=
destination="${HOME}/.local/bin"
while (( $# )); do
    (( $# >= 2 )) || fail "Missing value for $1"
    case "$1" in
        --tag) tag=$2 ;;
        --assets-dir) assets=$2 ;;
        --bin-dir) destination=$2 ;;
        *) fail "Usage: install.sh --tag TAG [--assets-dir DIR] [--bin-dir DIR]" ;;
    esac
    shift 2
done
[[ $tag =~ ^host-agent-publish-v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.+-]+)?$ && ${#tag} -le 149 ]] ||
    fail "Specify a Vitral host release with --tag."
command -v cosign >/dev/null || fail "Cosign v3 is required to verify the Vitral host release."
[[ $(uname -s) == Linux ]] || fail "This installer supports Linux."
case "$(uname -m)" in
    x86_64) binary=vitral-host-linux-amd64 ;;
    aarch64|arm64) binary=vitral-host-linux-arm64 ;;
    *) fail "This Linux architecture has no Vitral host release." ;;
esac

work=$(mktemp -d)
install_tmp=
trap 'rm -rf -- "$work"; if [[ -n "$install_tmp" ]]; then rm -f -- "$install_tmp"; fi' EXIT
verify_options=()
if [[ $tag =~ ^host-agent-publish-v0\.0\.0-dryrun\.[0-9]+$ ]]; then
    [[ -n $assets ]] || fail "Pass authenticated draft assets with --assets-dir for a dry run."
    export TUF_ROOT="$work/tuf"
    cosign initialize --staging
    root="$TUF_ROOT/tuf-repo-cdn.sigstage.dev/targets/trusted_root.json"
    [[ -s $root ]] || fail "Missing Sigstore staging trust root."
    verify_options+=(--trusted-root "$root" --use-signed-timestamps)
fi

fetch() {
    local file=$1 limit=$2
    if [[ -n $assets ]]; then
        [[ -f $assets/$file && ! -L $assets/$file ]] || fail "Missing release asset $file."
        head -c "$((limit + 1))" -- "$assets/$file" > "$work/$file"
    else
        curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
            --max-time 180 --max-filesize "$limit" --output "$work/$file" \
            "https://github.com/vitral-ai/vitral-host-agent-releases/releases/download/$tag/$file" ||
            fail "Could not download required release asset $file."
    fi
    local size
    size=$(wc -c < "$work/$file")
    (( size > 0 && size <= limit )) || fail "Invalid release asset size for $file."
}

fetch SHA256SUMS 8192
fetch SHA256SUMS.sigstore.json 1048576
identity="https://github.com/vitral-ai/vitral-host-agent-releases/.github/workflows/sign-host-release.yml@refs/tags/$tag"
cosign verify-blob "${verify_options[@]}" --bundle "$work/SHA256SUMS.sigstore.json" \
    --certificate-identity "$identity" --certificate-oidc-issuer https://token.actions.githubusercontent.com \
    "$work/SHA256SUMS" || fail "Vitral release signature verification failed."

line_pattern='^([0-9a-f]{64})  (vitral-host-linux-(amd64|arm64))$'
declare -A hashes=()
while IFS= read -r line; do
    [[ $line =~ $line_pattern ]] || fail "Invalid signed checksum manifest."
    name=${BASH_REMATCH[2]}
    [[ ! -v "hashes[$name]" ]] || fail "Duplicate file in signed checksum manifest."
    hashes[$name]=${BASH_REMATCH[1]}
done < "$work/SHA256SUMS"
[[ ${#hashes[@]} == 2 && -v "hashes[$binary]" ]] || fail "Incomplete signed checksum manifest."

fetch "$binary" 134217728
printf '%s  %s\n' "${hashes[$binary]}" "$binary" |
    (cd "$work" && sha256sum --check --status --strict) || fail "Vitral binary checksum verification failed."
mkdir -p -- "$destination"
install_tmp=$(mktemp "$destination/.vitral-host.XXXXXX")
install -m 0755 -- "$work/$binary" "$install_tmp"
mv -fT -- "$install_tmp" "$destination/vitral-host"
install_tmp=
printf 'Verified and installed %s\n' "$destination/vitral-host"
