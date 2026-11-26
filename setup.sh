#!/usr/bin/env bash
set -euo pipefail

fail() { printf '%s\n' "$*" >&2; exit 1; }

here=$(cd -- "$(dirname -- "$0")" && pwd)
tag= api= registry= discovery= client_id= assets=
root="$HOME/.local/share/vitral/guided-setup"
while (( $# )); do
    (( $# >= 2 )) || fail "Missing value for $1"
    case "$1" in
        --tag) tag=$2 ;;
        --api) api=$2 ;;
        --registry) registry=$2 ;;
        --discovery-url) discovery=$2 ;;
        --client-id) client_id=$2 ;;
        --assets-dir) assets=$2 ;;
        --data-dir) root=$2 ;;
        *) fail "Unknown setup option $1" ;;
    esac
    shift 2
done
[[ $tag =~ ^host-agent-publish-v[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.+-]+)?$ && ${#tag} -le 149 ]] ||
    fail "Specify the signed host release with --tag."
[[ $api == https://* && $registry == https://* && $discovery == https://* && -n $client_id ]] ||
    fail "Use the --api, --registry, --discovery-url and --client-id supplied by your Vitral administrator."
(( EUID != 0 )) || fail "Run setup as the account that will use Vitral."

case $(uname -s) in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
    *) fail "This installer supports Linux and macOS." ;;
esac
case $(uname -m) in
    x86_64) arch=amd64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) fail "This architecture has no Vitral setup package." ;;
esac
if [[ $os == darwin && $arch == amd64 ]] && [[ $(sysctl -in hw.optional.arm64) == 1 ]]; then
    arch=arm64
fi

umask 077
host_state="${VITRAL_TUNNEL_HOME:-$HOME/.vitral-tunnel}"
collector_state="$root/collector"
[[ $host_state == /* && $root == /* ]] || fail "Use absolute setup and host state directories."
mkdir -p -- "$root"
[[ -d $root && ! -L $root && -O $root ]] || fail "Setup requires a user-owned data directory."
chmod go-rwx "$root"
work=$(mktemp -d "$root/stage.XXXXXX")
trap 'rm -rf -- "$work"' EXIT

sha256() {
    if [[ $os == darwin ]]; then shasum -a 256 < "$1"; else sha256sum < "$1"; fi
}

dependency() {
    local dep=$1 platform=$2 pin size url actual downloaded
    IFS=$'\t' read -r pin size url < <(
        awk -F '\t' -v platform="$platform" -v dep="$dep" '
            $1 == platform && $2 == dep {print $3 "\t" $4 "\t" $5}
        ' "$here/setup-dependencies.tsv"
    ) || fail "Missing verified dependency for $dep on $platform."
    [[ $pin =~ ^[0-9a-f]{64}$ && $size =~ ^[0-9]+$ && $url == https://* ]] ||
        fail "Invalid dependency pin for $dep on $platform."
    DEP_DIR="$root/$dep-$pin"
    if [[ -e $DEP_DIR || -L $DEP_DIR ]]; then
        [[ -d $DEP_DIR && ! -L $DEP_DIR && -O $DEP_DIR ]] ||
            fail "The retained $dep directory is unsafe."
        if [[ $dep == cosign ]]; then
            [[ -f $DEP_DIR/cosign && ! -L $DEP_DIR/cosign ]] || fail "The retained Cosign is invalid."
            actual=$(sha256 "$DEP_DIR/cosign")
            [[ ${actual%% *} == "$pin" ]] || fail "The retained Cosign failed verification."
            return
        fi
    fi
    printf 'Downloading verified %s\n' "$dep" >&2
    downloaded="$work/$dep.download"
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --max-time 180 --max-filesize "$size" --output "$downloaded" "$url" ||
        fail "Could not download $dep."
    actual=$(sha256 "$downloaded")
    [[ ${actual%% *} == "$pin" && $(wc -c < "$downloaded") -eq $size ]] ||
        fail "Verification failed for $dep."
    mkdir -- "$work/$dep"
    case "$dep" in
        cosign) install -m 0700 -- "$downloaded" "$work/$dep/cosign" ;;
        csl) tar -xzf "$downloaded" --strip-components=1 -C "$work/$dep" ;;
        *) tar -xzf "$downloaded" -C "$work/$dep" ;;
    esac
    if [[ -d $DEP_DIR ]]; then
        "${verified_python:-$work/$dep/python/bin/python3}" -I -S -B "$here/verify-setup-tree.py" "$work/$dep" "$DEP_DIR" ||
            fail "The retained $dep dependency failed verification."
    elif [[ $os == darwin ]]; then
        mv -h -- "$work/$dep" "$DEP_DIR"
    else
        mv -T -- "$work/$dep" "$DEP_DIR"
    fi
}

dependency cosign "$os-$arch"
[[ -x $DEP_DIR/cosign && ! -L $DEP_DIR/cosign ]] || fail "Cosign was not installed."
export PATH="$DEP_DIR:$PATH"
host_dir="$root/$tag"
install_args=(--tag "$tag" --bin-dir "$host_dir")
if [[ -n $assets ]]; then install_args+=(--assets-dir "$assets"); fi
bash "$here/install.sh" "${install_args[@]}"
host="$host_dir/vitral-host"
if ! "$host" setup --help >/dev/null 2>&1; then
    fail "This signed host release does not support guided setup."
fi

dependency python "$os-$arch"
python="$DEP_DIR/python/bin/python3"
verified_python=$python
[[ -x $python ]] || fail "Python was not installed."
dependency csl all
source_dir="$DEP_DIR"
[[ -f $source_dir/csl_sync.py ]] || fail "The verified CSL source is incomplete."

dependency tmux "$os-$arch"
mkdir -p -- "$host_state"
[[ -d $host_state && ! -L $host_state && -O $host_state ]] || fail "Setup requires private host state."
tmux_dir="$host_state/tmux/bin"
[[ ! -L $host_state/tmux && ! -L $tmux_dir ]] || fail "Choose a private Vitral state directory."
mkdir -p -- "$tmux_dir"
[[ -O $host_state/tmux && -O $tmux_dir && ! -L $tmux_dir/tmux ]] ||
    fail "The existing tmux location is not owned by this account."
if [[ -e $tmux_dir/tmux ]]; then
    cmp -s "$DEP_DIR/tmux" "$tmux_dir/tmux" || fail "The managed tmux differs from the verified release."
else
    install -m 0700 -- "$DEP_DIR/tmux" "$tmux_dir/tmux"
fi
[[ -f $tmux_dir/tmux && -x $tmux_dir/tmux && -O $tmux_dir/tmux ]] ||
    fail "The existing Vitral tmux cannot run."

export VITRAL_API="$api" VITRAL_TUNNEL_HOME="$host_state"
"$host" setup --collector-home "$collector_state" --collector-runtime "$python" --collector-source "$source_dir" \
    --registry "$registry" --discovery-url "$discovery" --client-id "$client_id"
