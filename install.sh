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
case "$(uname -s)" in
    Linux) platform=linux ;;
    Darwin) platform=darwin ;;
    *) fail "This installer supports Linux and macOS." ;;
esac
case "$(uname -m)" in
    x86_64) arch=amd64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) fail "This architecture has no Vitral host release." ;;
esac
# An Apple silicon machine can run this shell through Rosetta.
if [[ $platform == darwin && $arch == amd64 ]] && [[ $(sysctl -in hw.optional.arm64) == 1 ]]; then
    arch=arm64
fi
binary="vitral-host-$platform-$arch"

work=$(mktemp -d)
install_tmp=
trap 'rm -rf -- "$work"; if [[ -n "$install_tmp" ]]; then rm -f -- "$install_tmp"; fi' EXIT
verify_options=(--certificate-oidc-issuer https://token.actions.githubusercontent.com)
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
        head -c "$((limit + 1))" < "$assets/$file" > "$work/$file"
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
    --certificate-identity "$identity" \
    "$work/SHA256SUMS" || fail "Vitral release signature verification failed."

line_pattern='^([0-9a-f]{64})  (vitral-host-((darwin|linux)-(amd64|arm64)|windows-(amd64|arm64)\.exe))$'
expected=(vitral-host-darwin-amd64 vitral-host-darwin-arm64 vitral-host-linux-amd64
          vitral-host-linux-arm64 vitral-host-windows-amd64.exe vitral-host-windows-arm64.exe)
line_count=$(wc -l < "$work/SHA256SUMS")
if (( line_count == 2 )) && {
    [[ $tag == host-agent-publish-v0.1.0 ]] ||
    [[ $tag =~ ^host-agent-publish-v0\.0\.0-dryrun\.[12]$ ]]
}; then
    expected=(vitral-host-linux-amd64 vitral-host-linux-arm64)
fi
[[ $line_count -eq ${#expected[@]} ]] || fail "Incomplete signed checksum manifest."
index=0
checksum=
while IFS= read -r line || [[ -n $line ]]; do
    [[ $index -lt ${#expected[@]} && $line =~ $line_pattern && ${BASH_REMATCH[2]} == "${expected[$index]}" ]] ||
        fail "Invalid signed checksum manifest."
    if [[ ${expected[$index]} == "$binary" ]]; then
        checksum=${BASH_REMATCH[1]}
    fi
    index=$((index + 1))
done < "$work/SHA256SUMS"
[[ -n $checksum ]] || fail "This release has no binary for $platform/$arch."

fetch "$binary" 134217728
if [[ $platform == darwin ]]; then
    actual=$(shasum -a 256 < "$work/$binary")
else
    actual=$(sha256sum < "$work/$binary")
fi
[[ ${actual%% *} == "$checksum" ]] || fail "Vitral binary checksum verification failed."
mkdir -p -- "$destination"
[[ ! -d "$destination/vitral-host" ]] || fail "Install destination is a directory."
install_tmp=$(mktemp "$destination/.vitral-host.XXXXXX")
install -m 0755 -- "$work/$binary" "$install_tmp"
if [[ $platform == darwin ]]; then
    mv -fh -- "$install_tmp" "$destination/vitral-host"
else
    mv -fT -- "$install_tmp" "$destination/vitral-host"
fi
install_tmp=
printf 'Verified and installed %s\n' "$destination/vitral-host"
