#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs MiOS-Portal, the Firefox+Surfer build of mios.toml [desktop.portal.gecko], from the off-image builder's artifact into [desktop.portal.gecko].install_dir: provenance from metrics.json, the artifact's stage proven equal to the CURRENT src/mios-portal by mios-portal-tree verify-stage (one implementation with the builder's stage, fail-closed), tarball + the artifact's install-overlay laid in root-owned and world-readable, then proven equal to the artifact by verify-install; strictly zero network and zero compilation (Law 12).
# AI-doc: usr/share/doc/mios/manual/automation.md
# AI-related: usr/share/mios/mios.toml, src/mios-portal/install/, usr/libexec/mios/mios-portal-tree, usr/libexec/mios/mios-portal-build, tests/test-bake-portal.py
set -euo pipefail
# shellcheck disable=SC1090
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

# Capture and validate incoming DEST parameters BEFORE common.sh/globals.sh sources
# to ensure explicit empty strings fail closed and are not coerced by globals defaults.
if [ "${DEST+x}" = "x" ] && [ -z "$DEST" ]; then
    mios_err "DEST is empty"; exit 1
fi
if [ "${MIOS_DESKTOP_PORTAL_GECKO_INSTALL_DIR+x}" = "x" ] && [ -z "$MIOS_DESKTOP_PORTAL_GECKO_INSTALL_DIR" ]; then
    mios_err "DEST is empty"; exit 1
fi
_INCOMING_DEST="${DEST:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh" 2>/dev/null || true

# Identity and paths come only from the SSOT projection (automation/lib/globals.sh); an unset one
# stops the phase instead of falling back to a literal.
for _v in MIOS_DESKTOP_PORTAL_GECKO_NAME MIOS_DESKTOP_PORTAL_GECKO_BINARY \
          MIOS_DESKTOP_PORTAL_GECKO_INSTALL_DIR MIOS_DESKTOP_PORTAL_BUILDER_ARTIFACT_ROOT; do
    [ -n "${!_v:-}" ] || { mios_err "${_v} unresolved -- render automation/lib/globals.sh from mios.toml"; exit 1; }
done
NAME="$MIOS_DESKTOP_PORTAL_GECKO_NAME"
BIN="$MIOS_DESKTOP_PORTAL_GECKO_BINARY"
DEST="${_INCOMING_DEST:-$MIOS_DESKTOP_PORTAL_GECKO_INSTALL_DIR}"
# One implementation of the stage (sentinel fill, lane mozconfig, branding icons) for the builder
# and this phase; it needs the product tree, the SSOT and python3-pillow, and fails without them.
TREE_TOOL="${SCRIPT_DIR}/../usr/libexec/mios/mios-portal-tree"
[ -f "$TREE_TOOL" ] || TREE_TOOL=/usr/libexec/mios/mios-portal-tree
[ -f "$TREE_TOOL" ] || { mios_err "mios-portal-tree is missing; the artifact cannot be verified"; exit 1; }

# DEST guard: never allow unconstrained or root deletions
[ -n "${DEST:-}" ] || { mios_err "DEST is empty"; exit 1; }
if [ "$DEST" = "/" ] || [ "$DEST" = "/usr" ] || [ "$DEST" = "/usr/lib" ] || [ "$DEST" = "/usr/lib/mios" ]; then
    mios_err "DEST '$DEST' is a protected system directory; refusing rm -rf"; exit 1
fi
case "$DEST" in
    "$MIOS_DESKTOP_PORTAL_GECKO_INSTALL_DIR"|*/"${MIOS_DESKTOP_PORTAL_GECKO_INSTALL_DIR#/}") ;;
    *) mios_err "DEST '$DEST' is not [desktop.portal.gecko].install_dir ($MIOS_DESKTOP_PORTAL_GECKO_INSTALL_DIR)"; exit 1 ;;
esac
case "$MIOS_DESKTOP_PORTAL_GECKO_INSTALL_DIR" in
    /|/usr|/usr/lib|/usr/lib/mios|*/..|*/../*|"") mios_err "install_dir '$MIOS_DESKTOP_PORTAL_GECKO_INSTALL_DIR' is not a dedicated directory"; exit 1 ;;
esac

# Locate artifact: explicit run (MIOS_PORTAL_ARTIFACT_DIR), build context, or the newest run under
# the host artifact root. Whichever is chosen must still pass every check below.
CANDIDATES=()
[ -z "${MIOS_PORTAL_ARTIFACT_DIR:-}" ] || CANDIDATES+=("${MIOS_PORTAL_ARTIFACT_DIR}")
[ -z "${CTX:-}" ] || CANDIDATES+=("${CTX}/artifacts/portal" "${CTX}/artifacts/mios-portal")
CANDIDATES+=("${MIOS_DESKTOP_PORTAL_BUILDER_ARTIFACT_ROOT}")

RUN=""
for cand in "${CANDIDATES[@]}"; do
    if [ -d "$cand" ]; then
        if [ -f "$cand/manifest.json" ]; then
            RUN="$cand"
            break
        fi
        found="$(find "$cand" -mindepth 1 -maxdepth 2 -name manifest.json -printf '%h\n' 2>/dev/null | sort | tail -n1 || true)"
        if [ -n "$found" ]; then
            RUN="$found"
            break
        fi
    fi
done

[ -n "$RUN" ] || { mios_err "no ${NAME} artifact found under candidates (${CANDIDATES[*]}) -- run off-image builder first (fatal=true)"; exit 1; }
RUN="$(cd "$RUN" && pwd)"
mios_log "${NAME} artifact: ${RUN}"

# Provenance: the run built the committed tree (staged:), exited 0 and passed its smoke test; the
# manifest names a package and executable. Missing metrics, stage or overlay fail closed.
read -r PKG PKG_SHA EXE VERSION < <(python3 -I - "${RUN}" <<'PY'
import json, os, sys
run = sys.argv[1]
def load(name):
    p = os.path.join(run, name)
    if not os.path.isfile(p):
        sys.exit(f"{name} is missing from the artifact")
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)
m, met = load("manifest.json"), load("metrics.json")
if met.get("exit") != 0:
    sys.exit(f"builder metrics record exit {met.get('exit')!r}, not 0")
prod = str(met.get("product", ""))
if not prod.startswith("staged:"):
    sys.exit(f"builder metrics record product {prod!r}; only a staged src/mios-portal build is installable")
if str(met.get("smoke")) != "0":
    sys.exit(f"builder metrics record smoke {met.get('smoke')!r}; the artifact never passed its smoke test")
for name in ("stage.json",):
    if not os.path.isfile(os.path.join(run, name)):
        sys.exit(f"{name} is missing from the artifact; it cannot be tied to the current tree")
if not os.path.isdir(os.path.join(run, "install-overlay")):
    sys.exit("install-overlay/ is missing from the artifact")
pkg = m["package"]
fields = (os.path.basename(pkg["file"]), pkg["sha256"], os.path.basename(pkg["executable"]), m["resolved"]["firefox"])
if not all(isinstance(f, str) and f and " " not in f for f in fields):
    sys.exit(f"manifest.json fields are incomplete: {fields!r}")
print(*fields)
PY
) || { mios_err "${RUN} provenance check failed"; exit 1; }
[ -n "${VERSION:-}" ] || { mios_err "${RUN} provenance check failed"; exit 1; }

TARBALL="${RUN}/${PKG}"
[ -f "$TARBALL" ] || { mios_err "${TARBALL} named by manifest is missing"; exit 1; }
[ "$(sha256sum "$TARBALL" | cut -d' ' -f1)" = "$PKG_SHA" ] || { mios_err "${TARBALL} sha256 mismatch"; exit 1; }

# The artifact's stage must equal the stage of the CURRENT product tree, and its install overlay
# the staged install/ (bidirectional; no Pillow, no tree or no stage.json -> fail).
python3 -I "$TREE_TOOL" verify-stage --stage "${RUN}/stage.json" --overlay "${RUN}/install-overlay" \
    || { mios_err "Stage hash check failed: ${RUN} was not built from the current src/mios-portal"; exit 1; }

mios_log "Installing ${NAME} ${VERSION} from ${TARBALL} into ${DEST}"
PARENT="$(dirname "$DEST")"
mkdir -p "$PARENT"
NEW="$(mktemp -d "${PARENT}/.$(basename "$DEST").new.XXXXXX")"
OLD=""
cleanup() { [ -z "${NEW:-}" ] || rm -rf "$NEW"; [ -z "${OLD:-}" ] || rm -rf "$OLD"; }
trap cleanup EXIT
tar -xJf "$TARBALL" -C "$NEW" --strip-components=1 --no-same-owner
# The overlay is the artifact's own copy of the staged install/ (verified above), never src/.
tar -C "${RUN}/install-overlay" -cf - . | tar -xf - -C "$NEW" --no-same-owner
# Root-owned (when root), world-readable, nothing group/other-writable: the user who runs the
# browser must read every file, and must not be able to edit what fapolicyd lets execute.
[ "$(id -u)" -ne 0 ] || chown -R 0:0 "$NEW"
chmod -R u+rwX,go+rX,go-w "$NEW"
chmod 0755 "$NEW"
[ -x "${NEW}/${EXE}" ] || { mios_err "${EXE} is not executable after unpack"; exit 1; }
[ "$EXE" = "$BIN" ] || ln -sfn "$EXE" "${NEW}/${BIN}"

if [ -e "$DEST" ] || [ -L "$DEST" ]; then
    OLD="$(mktemp -u "${PARENT}/.$(basename "$DEST").old.XXXXXX")"
    mv "$DEST" "$OLD"
fi
mv "$NEW" "$DEST"
NEW=""
# On a live SELinux host the staged tree carries its temp dir's labels; give it the policy's.
# (An image build has no SELinux loaded, so selinuxenabled is false there and this is skipped.)
if [ "$(id -u)" -eq 0 ] && command -v selinuxenabled >/dev/null 2>&1 && selinuxenabled; then
    restorecon -R "$DEST" || { mios_err "restorecon -R ${DEST} failed"; exit 1; }
fi

ROOT_OWNED=()
[ "$(id -u)" -ne 0 ] || ROOT_OWNED=(--root-owned)
if ! python3 -I "$TREE_TOOL" verify-install --run "$RUN" --dest "$DEST" --binary "$BIN" "${ROOT_OWNED[@]}"; then
    mios_err "${DEST} is not the artifact ${RUN} after install"
    if [ -n "$OLD" ]; then
        rm -rf "$DEST" && mv "$OLD" "$DEST" && OLD="" && mios_err "rolled back to the previous ${DEST}"
    fi
    exit 1
fi

record_version mios-portal "$VERSION" "${RUN}/manifest.json" 2>/dev/null || true
mios_ok "Installed ${NAME} ${VERSION} into ${DEST}"
