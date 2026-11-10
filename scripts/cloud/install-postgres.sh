#!/usr/bin/env bash
# User-owned PostgreSQL tools; no system service or administrator access needed.
set -euo pipefail
version=14.24
checksum=a7fa7ed3d558172355f51406097a7bd4f6b473be80f311ef7cda96bf383d8897
prefix="${XDG_CACHE_HOME:-$HOME/.cache}/codex-cloud/postgresql-$version"
command -v flock >/dev/null || { echo 'Required build tool: flock' >&2; exit 1; }
mkdir -p "$(dirname "$prefix")"
exec 9>"$prefix.lock"
flock 9
if [[ -f "$prefix/.cloud-install-complete" && -x "$prefix/bin/postgres" ]]; then
  "$prefix/bin/postgres" --version
  exit 0
fi
for tool in curl tar sha256sum gcc make; do
  command -v "$tool" >/dev/null || { echo "Required build tool: $tool" >&2; exit 1; }
done
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
echo "Building official PostgreSQL $version in the user cache; the first run takes several minutes."
archive="postgresql-$version.tar.bz2"
curl --fail --show-error --location --max-time 120 \
  "https://ftp.postgresql.org/pub/source/v$version/$archive" -o "$work/$archive"
(cd "$work" && echo "$checksum  $archive" | sha256sum --check --status)
tar -xjf "$work/$archive" -C "$work"
cd "$work/postgresql-$version"
./configure --prefix="$prefix" --without-readline --without-zlib > "$work/configure.log" 2>&1 || {
  cat "$work/configure.log" >&2; exit 1;
}
# Limit compile parallelism so application work can continue in the environment.
make -j4 > "$work/build.log" 2>&1 || { tail -60 "$work/build.log" >&2; exit 1; }
make install > "$work/install.log" 2>&1 || { tail -60 "$work/install.log" >&2; exit 1; }
"$prefix/bin/postgres" --version
touch "$prefix/.cloud-install-complete"
