#!/usr/bin/env python3
# AI-hint: Golden-master gate for the env.baseline sync stage: `mios-gen env-snapshot` must print byte-identically to the retired bash body of usr/libexec/mios/mios-env-snapshot (frozen below as LEGACY_BODY) over the real vendor SSOT and seeded layered vendor fixtures (escapes, CR, non-ASCII, ${MIOS_*} references, pgvector bind derivation, lexical and decoy fragments); the one deliberate difference (a <root>/etc/mios admin tier) and a mutated legacy leg must both be seen.
# AI-related: tools/native/mios-gen/src/env_snapshot.rs, usr/libexec/mios/mios-env-snapshot, usr/lib/mios/userenv.sh, tools/native/mios-resolver/src/emit.rs, usr/share/mios/reference/env-baseline.txt, tests/golden/README.md (env-snapshot)
# AI-functions: native_bin, legacy_leg, native_leg, Tree, TestEnvSnapshotNativeParity
#
# MIOS_GEN_BIN / MIOS_RESOLVER_BIN select the binaries under test (default: the
# tree's own tools/native/target build, else the installed one). The legacy leg
# runs userenv.sh, which evals whichever mios-resolver is first on its PATH, so
# both binaries must come from the same build.
import json, os, random, shutil, subprocess, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = int(os.environ.get("MIOS_ENVSNAP_SEED", "2718"))
CASES = int(os.environ.get("MIOS_ENVSNAP_CASES", "24"))

# The bash body as it stood before the port (main 0a46915eb), byte for byte
# below the shebang. It is the reference leg: the port claims to reproduce it.
LEGACY_BODY = r'''#!/usr/bin/env bash
set -euo pipefail

export LC_ALL=C LANG=C

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

USERENV_SH="${ROOT_DIR}/tools/lib/userenv.sh"
if [[ ! -f "${USERENV_SH}" ]]; then
    USERENV_SH="${ROOT_DIR}/usr/lib/mios/userenv.sh"
fi

if [[ ! -f "${USERENV_SH}" ]]; then
    echo "[mios-env-snapshot] ERROR: Cannot locate userenv.sh resolver" >&2
    exit 1
fi

VENDOR_TOML="${MIOS_VENDOR_TOML:-${ROOT_DIR}/usr/share/mios/mios.toml}"

_MIOS_ENV_SNAPSHOT_EXCLUDE='^(MIOS_ROOT|MIOS_TOML_ROOT|MIOS_VENDOR_TOML|MIOS_CONFIG_DIR|MIOS_USER_TOML|MIOS_USER_TOML_D|MIOS_HOST_TOML_D|MIOS_REPO_URL|MIOS_REPO_ROOT|MIOS_REPO_DIR)='

_mios_escape_pairs() {
    local kv
    while IFS= read -r -d '' kv; do
        case "$kv" in
            MIOS_*) ;;
            *) continue ;;
        esac
        kv="${kv//\\/\\\\}"
        kv="${kv//$'\n'/\\n}"
        printf '%s\n' "$kv"
    done
}

env -i \
    PATH="${PATH}:/usr/bin:/bin" \
    HOME=/nonexistent \
    XDG_CONFIG_HOME=/nonexistent/.config \
    MIOS_ROOT="${ROOT_DIR}" \
    MIOS_TOML_ROOT="${ROOT_DIR}" \
    MIOS_VENDOR_TOML="${VENDOR_TOML}" \
    MIOS_USER_TOML=/nonexistent/user.toml \
    MIOS_USER_TOML_D=/nonexistent/user.d \
    bash -c "source \"${USERENV_SH}\" >/dev/null 2>&1 && env -0" \
    | _mios_escape_pairs \
    | grep -vE "${_MIOS_ENV_SNAPSHOT_EXCLUDE}" \
    | sort
'''


def native_bin(name, env_var):
    override = os.environ.get(env_var)
    if override:
        return override if os.access(override, os.X_OK) else None
    for cand in (ROOT / "tools/native/target/release" / name, ROOT / "tools/native/target/debug" / name,
                 Path("/usr/libexec/mios") / name, Path("/usr/bin") / name):
        if os.access(cand, os.X_OK):
            return str(cand)
    return None


GEN = native_bin("mios-gen", "MIOS_GEN_BIN")
RESOLVER = native_bin("mios-resolver", "MIOS_RESOLVER_BIN")


def legacy_leg(root, body=LEGACY_BODY):
    """Run the frozen bash body from inside `root`, as the stage did, with the
    resolver under test first on PATH. Returns stdout bytes."""
    script = Path(root) / "usr/libexec/mios/mios-env-snapshot-legacy"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(body)
    try:
        env = {"PATH": "%s:/usr/bin:/bin" % os.path.dirname(RESOLVER)}
        return subprocess.run(["bash", str(script)], env=env, capture_output=True, check=True,
                              timeout=120).stdout
    finally:
        script.unlink()


def native_leg(root):
    return subprocess.run([GEN, "env-snapshot", "--root", str(root)], env={"PATH": "/usr/bin:/bin"},
                          capture_output=True, check=True, timeout=120).stdout


def first_difference(a, b):
    la, lb = a.splitlines(), b.splitlines()
    for i, (x, y) in enumerate(zip(la, lb)):
        if x != y:
            return "line %d:\n  legacy: %r\n  native: %r" % (i + 1, x[:200], y[:200])
    return "line counts %d (legacy) vs %d (native)" % (len(la), len(lb))


# ---- fixtures ----------------------------------------------------------------

def toml_str(s):
    return json.dumps(s, ensure_ascii=False)


def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return toml_str(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    raise TypeError(v)


def render(doc):
    out = []
    for sec, body in doc.items():
        out.append("[%s]" % sec)
        out.extend("%s = %s" % (k, toml_value(v)) for k, v in body.items())
    return "\n".join(out) + "\n"


# Values that exercise every step of the legacy pipeline: the `\` and newline
# escapes, the CR strip, byte order of non-ASCII, ${MIOS_*} resolution, '=' and
# quotes inside a value, and the empty string (never exported).
VALUES = ("plain", "back\\slash", "two\nlines", "cr\rhere", "trailing\\", "ümlaut ✓", "😀 astral",
          "${MIOS_ALPHA_K1:-dflt}/x", "${MIOS_ALPHA_K2}", "a=b=c", "it's \"quoted\"", "", "  spaced  ",
          "tab\there", "$HOME", "`echo no`")
FRAGS = ("10-a.toml", "9-b.toml", "A.toml", "a.toml", "_u.toml", "00.toml")
DECOYS = (".hidden.toml", "x.toml.bak", "notes.txt")


def random_doc(rng, tag):
    doc = {}
    for sec in rng.sample(("alpha", "beta", "gamma"), rng.randrange(1, 4)):
        body = {}
        for key in rng.sample(("k1", "k2", "k3", "k4"), rng.randrange(1, 5)):
            r = rng.random()
            if r < 0.6:
                body[key] = rng.choice(VALUES) + ("" if rng.random() < 0.5 else "-" + tag)
            elif r < 0.75:
                body[key] = rng.randrange(0, 70000)
            elif r < 0.85:
                body[key] = rng.random() < 0.5
            else:
                body[key] = [rng.choice(VALUES[:6]) for _ in range(rng.randrange(0, 3))]
        doc[sec] = body
    if rng.random() < 0.5:
        doc["pgvector"] = {"listen_loopback": rng.choice([True, False, "off", "0", "yes"])}
    if rng.random() < 0.5:
        doc["network"] = {"loopback": "127.0.0.%d" % rng.randrange(1, 9), "bind_all": rng.choice(["0.0.0.0", ""])}
    return doc


class Tree:
    """A minimal tree the legacy body runs from: vendor tiers plus userenv.sh."""

    def __init__(self, base, name):
        self.dir = Path(base) / name
        for rel in ("usr/lib/mios/userenv.sh", "tools/lib/userenv.sh"):
            dst = self.dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, dst)
        self.vendor = self.dir / "usr/share/mios/mios.toml"
        self.vendor.parent.mkdir(parents=True, exist_ok=True)

    def write(self, rel, text):
        path = self.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def seeded_tree(base, rng, index):
    tree = Tree(base, "case-%03d" % index)
    vendor = random_doc(rng, "vendor")
    # The legacy fallback reads ~/.config/mios/*.toml when no username resolves;
    # every real tree sets one, so every fixture does too.
    vendor["identity"] = {"username": "mios"}
    tree.vendor.write_text(render(vendor), encoding="utf-8")
    if rng.random() < 0.7:
        for name in rng.sample(FRAGS, rng.randrange(1, 4)):
            tree.write("usr/lib/mios/mios.d/" + name, render(random_doc(rng, name)))
        for name in rng.sample(DECOYS, rng.randrange(0, 2)):
            tree.write("usr/lib/mios/mios.d/" + name, render(random_doc(rng, "decoy")))
    return tree


class TestEnvSnapshotNativeParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (GEN and RESOLVER):
            raise unittest.SkipTest("cannot run: no mios-gen or mios-resolver (build tools/native or set "
                                    "MIOS_GEN_BIN and MIOS_RESOLVER_BIN)")
        if subprocess.run([GEN, "env-snapshot", "--help"], capture_output=True).returncode != 0:
            raise unittest.SkipTest("cannot run: %s has no env-snapshot verb" % GEN)
        cls.tmp = tempfile.mkdtemp(prefix="mios-envsnap-")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def assertSameBytes(self, legacy, native, label):
        if legacy != native:
            self.fail("%s: native snapshot differs from the legacy bash body at %s"
                      % (label, first_difference(legacy, native)))

    def test_real_vendor_ssot(self):
        tree = Tree(self.tmp, "real")
        shutil.copy2(ROOT / "usr/share/mios/mios.toml", tree.vendor)
        frags = ROOT / "usr/lib/mios/mios.d"
        if frags.is_dir():
            shutil.copytree(frags, tree.dir / "usr/lib/mios/mios.d")
        legacy, native = legacy_leg(tree.dir), native_leg(tree.dir)
        self.assertGreater(len(native.splitlines()), 1000)
        self.assertSameBytes(legacy, native, "real SSOT")
        # The committed baseline is this tree's own native snapshot.
        self.assertEqual((ROOT / "usr/share/mios/reference/env-baseline.txt").read_bytes(), native)

    def test_seeded_layered_fixtures(self):
        rng = random.Random(SEED)
        failures = []
        for i in range(CASES):
            tree = seeded_tree(self.tmp, rng, i)
            legacy, native = legacy_leg(tree.dir), native_leg(tree.dir)
            if legacy != native:
                failures.append("case %03d (%s): %s" % (i, tree.dir, first_difference(legacy, native)))
        self.assertEqual(failures, [], "\n".join(failures[:6]))

    def test_admin_tier_is_the_one_deliberate_difference(self):
        rng = random.Random(SEED + 1)
        tree = seeded_tree(self.tmp, rng, 900)
        before = native_leg(tree.dir)
        self.assertSameBytes(legacy_leg(tree.dir), before, "before the plant")
        # On a deployed host the root is `/` and this is the operator's live tier.
        tree.write("etc/mios/mios.toml", '[alpha]\nhostonly = "planted-admin"\n')
        legacy = legacy_leg(tree.dir)
        self.assertIn(b"MIOS_ALPHA_HOSTONLY=planted-admin", legacy)
        self.assertEqual(native_leg(tree.dir), before, "the native snapshot read <root>/etc/mios")

    def test_a_mutated_legacy_leg_goes_red(self):
        # Negative control for the harness itself: drop the backslash escape.
        mutated = LEGACY_BODY.replace('kv="${kv//\\\\/\\\\\\\\}"\n', "")
        self.assertNotEqual(mutated, LEGACY_BODY, "the mutation did not land")
        tree = Tree(self.tmp, "mutant")
        tree.vendor.write_text(render({"identity": {"username": "mios"}, "alpha": {"k1": "back\\slash"}}))
        self.assertNotEqual(legacy_leg(tree.dir, mutated), native_leg(tree.dir))
        self.assertEqual(legacy_leg(tree.dir), native_leg(tree.dir))


if __name__ == "__main__":
    unittest.main()
