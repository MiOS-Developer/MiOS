#!/usr/bin/env python3
# AI-hint: Law 13 (NATIVE-DROPINS) differential gate: generates seeded layered fixtures (vendor < vendor.d < host < host.d < user < user.d, with lexical-order, hidden, non-.toml, directory, empty-string and type-flip traps plus [generation.walk] variants) and goes red on ANY disagreement between userenv.sh (through mios-resolver), mios_toml.py and an independent tier-major precedence model. Also proves the SSOT walk rules have one home and that editing it moves both twins.
# AI-related: usr/lib/mios/userenv.sh, usr/lib/mios/mios_toml.py, tools/native/mios-resolver/src/layers.rs, tools/native/mios-resolver/src/merge.rs, tools/native/mios-ssot-walk/src/lib.rs, usr/share/mios/mios.toml [generation.walk], automation/98-drift-checks.sh (check_resolver_twin_parity)
# AI-functions: native_resolver, Fixture, spec_merge, bash_leg, python_leg, walk_list_homes
#
# MIOS_RESOLVER_BIN=<path> selects the native resolver under test (default: the
# tree's own tools/native/target build, else the installed one). Pointing it at a
# pre-fix binary is the negative control: the hidden-fragment and empty-string
# cases go red against it. MIOS_DROPIN_SEED / MIOS_DROPIN_CASES vary the corpus.
import json, os, random, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USERENV = ROOT / "usr/lib/mios/userenv.sh"
LIB = ROOT / "usr/lib/mios"
VENDOR = ROOT / "usr/share/mios/mios.toml"
SEED = int(os.environ.get("MIOS_DROPIN_SEED", "1313"))
CASES = int(os.environ.get("MIOS_DROPIN_CASES", "160"))

# Names that carry the resolver's own inputs or are derived by userenv.sh alone
# (MIOS_PG_BIND_ADDR is check_resolver_twin_equivalence's business).
INPUTS = {"MIOS_VENDOR_TOML", "MIOS_VENDOR_TOML_D", "MIOS_HOST_TOML", "MIOS_HOST_TOML_D",
          "MIOS_PG_BIND_ADDR"}


def native_resolver():
    override = os.environ.get("MIOS_RESOLVER_BIN")
    if override:
        return override if os.access(override, os.X_OK) else None
    for cand in (ROOT / "tools/native/target/debug/mios-resolver",
                 ROOT / "tools/native/target/release/mios-resolver",
                 Path("/usr/libexec/mios/mios-resolver"), Path("/usr/bin/mios-resolver")):
        if os.access(cand, os.X_OK):
            return str(cand)
    return None


# ---- fixture generation -------------------------------------------------------

SECTIONS = ("alpha", "beta", "ai", "units")
KEYS = ("k1", "k2", "k3")
# Lexical traps: 9- sorts after 10-, A before a, _ after letters, 00 first.
FRAG_NAMES = ("10-a.toml", "9-b.toml", "50-z.toml", "A.toml", "a.toml", "_u.toml",
              "00.toml", "99-last.toml")
# Never layers: hidden (systemd skips dotfiles), wrong suffix, directories.
DECOYS = (".hidden.toml", ".99-late.toml", "notes.txt", "x.toml.bak", "X.TOML")


def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    raise TypeError(v)


def render(doc, prefix=""):
    """dict -> TOML text: scalars of a table first, then its sub-tables."""
    out = []
    scalars = [(k, v) for k, v in doc.items() if not isinstance(v, dict)]
    tables = [(k, v) for k, v in doc.items() if isinstance(v, dict)]
    if prefix and (scalars or not tables):
        out.append("[%s]" % prefix)
    for k, v in scalars:
        out.append("%s = %s" % (k, toml_value(v)))
    for k, v in tables:
        out.append(render(v, "%s.%s" % (prefix, k) if prefix else k))
    return "\n".join(out) + "\n"


def walk_rules_variant(rng, tag):
    r = rng.random()
    if r < 0.55:
        return {"excluded_sections": ["units", "generation"], "mostly_dead_sections": ["ai"],
                "emit_keep": ["MIOS_AI_K1"]}
    if r < 0.7:
        return {"emit_keep": rng.choice([["MIOS_AI_K2"], [], ["MIOS_AI_K1", "MIOS_AI_K3"]])}
    if r < 0.8:
        return {"excluded_sections": rng.choice([["beta"], [], ["units", "alpha"]])}
    if r < 0.9:  # malformed shapes: both twins must read them the same way
        return {"excluded_sections": "units", "mostly_dead_sections": ["ai", 7, True],
                "emit_keep": [["MIOS_AI_K1"]]}
    return {"mostly_dead_sections": ["ai", "beta"], "emit_keep": ["MIOS_BETA_K2"]}


def random_value(rng, tag):
    r = rng.random()
    if r < 0.30:
        return "%s-%d" % (tag, rng.randrange(100))
    if r < 0.45:
        return ""
    if r < 0.55:
        return rng.randrange(1, 500)
    if r < 0.60:
        return 0
    if r < 0.68:
        return rng.random() < 0.5
    if r < 0.76:
        return ["%s-l%d" % (tag, i) for i in range(rng.randrange(0, 3))]
    if r < 0.84:
        return {"leaf": "%s-t" % tag, "deep": {"x": rng.randrange(9)}}
    if r < 0.92:
        return "${MIOS_ALPHA_K1:-dflt}/%s" % tag
    return "%s with space" % tag


def random_layer(rng, tag):
    doc = {}
    for sec in rng.sample(SECTIONS, rng.randrange(1, len(SECTIONS) + 1)):
        body = {}
        for key in rng.sample(KEYS, rng.randrange(1, len(KEYS) + 1)):
            body[key] = random_value(rng, tag)
        if rng.random() < 0.3:
            body["sub"] = {"s1": random_value(rng, tag + "s")}
        doc[sec] = body
    if rng.random() < 0.35:
        doc["generation"] = {"walk": walk_rules_variant(rng, tag)}
    return doc


class Fixture:
    """One layered tree on disk plus the description the precedence model reads."""

    def __init__(self, base, rng, index):
        self.dir = Path(base) / ("case-%03d" % index)
        self.vendor = self.dir / "usr/share/mios/mios.toml"
        self.vendor_d = self.dir / "usr/lib/mios/mios.d"
        self.host = self.dir / "etc/mios/mios.toml"
        self.host_d = self.dir / "etc/mios/mios.d"
        self.config = self.dir / "home/.config"
        self.user = self.config / "mios/mios.toml"
        self.user_d = self.config / "mios/mios.d"
        self.layers = []  # (path, doc) in the order the SPEC says they apply
        for tier, mono, frag_dir in (("vendor", self.vendor, self.vendor_d),
                                     ("host", self.host, self.host_d),
                                     ("user", self.user, self.user_d)):
            mono.parent.mkdir(parents=True, exist_ok=True)
            if rng.random() < 0.85:
                doc = random_layer(rng, tier)
                if tier == "vendor" and rng.random() < 0.6:
                    doc["generation"] = {"walk": walk_rules_variant(rng, tier)}
                mono.write_text(render(doc))
                self.layers.append((mono, doc))
            if rng.random() < 0.75:
                frag_dir.mkdir(parents=True, exist_ok=True)
                names = rng.sample(FRAG_NAMES, rng.randrange(1, 5))
                written = []
                for name in names:
                    doc = random_layer(rng, "%s.d/%s" % (tier, name))
                    (frag_dir / name).write_text(render(doc))
                    written.append((name, doc))
                for decoy in rng.sample(DECOYS, rng.randrange(0, 3)):
                    (frag_dir / decoy).write_text(render(random_layer(rng, "decoy")))
                if rng.random() < 0.2:
                    (frag_dir / "dir.toml").mkdir()
                # The SPEC order: lexical by basename within the tier.
                for name, doc in sorted(written):
                    self.layers.append((frag_dir / name, doc))

    def env(self):
        return {"MIOS_VENDOR_TOML": str(self.vendor), "MIOS_VENDOR_TOML_D": str(self.vendor_d),
                "MIOS_HOST_TOML": str(self.host), "MIOS_HOST_TOML_D": str(self.host_d),
                "HOME": str(self.dir / "home"), "XDG_CONFIG_HOME": str(self.config)}


def spec_merge(layers):
    """Tier-major precedence restated independently of both twins: later layers
    win, tables merge, and an empty string never overrides a value below it."""
    def merge(dst, src):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            elif v == "" and isinstance(v, str) and k in dst and dst[k] != "":
                continue
            else:
                dst[k] = json.loads(json.dumps(v))
        return dst
    merged = {}
    for _path, doc in layers:
        merge(merged, doc)
    return merged


# ---- the two twins ------------------------------------------------------------

def bash_leg(fixture, resolver, stage):
    """userenv.sh exactly as a login shell sources it, through mios-resolver."""
    env = dict(fixture.env(), PATH="%s:/usr/bin:/bin" % stage)
    res = subprocess.run(
        ["bash", "-c", '. "$1" >/dev/null 2>&1 || exit 97; env -0', "_", str(USERENV)],
        env=env, capture_output=True, timeout=60)
    if res.returncode != 0:
        return {"<error>": "userenv.sh exit %d" % res.returncode}
    out = {}
    for pair in res.stdout.decode("utf-8", "replace").split("\0"):
        k, sep, v = pair.partition("=")
        if sep and k.startswith("MIOS_") and k not in INPUTS:
            out[k] = v
    return out


def native_merged(fixture, resolver):
    env = dict(fixture.env(), PATH="/usr/bin:/bin")
    res = subprocess.run([resolver, "--emit=json"], env=env, capture_output=True, timeout=60)
    if res.returncode != 0:
        return {"<error>": "mios-resolver exit %d" % res.returncode}
    return json.loads(res.stdout)["merged"]


PY_LEG = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import mios_toml
results = []
for case in json.load(sys.stdin):
    os.environ.clear(); os.environ.update(case)
    os.environ["MIOS_RESOLVER_NATIVE"] = "0"
    mios_toml.clear_cache()
    try:
        merged = mios_toml.load_merged()
        exports = mios_toml.emit_exports()
    except Exception as e:  # a crash is a result to compare, not a test error
        merged = exports = {"<error>": type(e).__name__}
    results.append({"merged": merged, "exports": exports})
json.dump(results, sys.stdout)
"""


def python_leg(fixtures):
    """mios_toml.py over every fixture in one interpreter, pure Python tier."""
    cases = [dict(f.env(), PATH="/usr/bin:/bin") for f in fixtures]
    res = subprocess.run([sys.executable, "-I", "-c", PY_LEG, str(LIB)], input=json.dumps(cases),
                         capture_output=True, text=True, timeout=600, env={"PATH": "/usr/bin:/bin"})
    if res.returncode != 0:
        raise AssertionError("mios_toml.py leg crashed:\n" + res.stderr[-2000:])
    out = json.loads(res.stdout)
    for r in out:
        r["exports"] = {k: str(v) for k, v in r["exports"].items() if k not in INPUTS}
    return out


def diff(a, b, label_a, label_b, limit=6):
    lines = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            lines.append("    %s: %s=%r %s=%r" % (k, label_a, a.get(k), label_b, b.get(k)))
    return lines[:limit] + (["    ... %d more" % (len(lines) - limit)] if len(lines) > limit else [])


# ---- single home of the walk rules -------------------------------------------

def walk_list_homes(root):
    """Tracked sources (other than the SSOT) that restate the walk's exclusion
    list. The signature is the list itself: three of its distinctive members on
    one line, or a constant named after it."""
    try:
        files = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True,
                               check=True).stdout.decode().split("\0")
    except (OSError, subprocess.CalledProcessError):
        files = [str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file()]
    homes = []
    for rel in files:
        if not rel.endswith((".py", ".rs", ".sh", ".ps1", ".psm1")):
            continue
        try:
            text = (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if ('"install_phases"' in line and '"btop"' in line and '"messages"' in line) \
                    or line.lstrip().startswith(("EXCLUDED_SECTIONS =", "WALK_EMIT_KEEP =",
                                                 "WALK_MOSTLY_DEAD =", "pub const EXCLUDED_SECTIONS",
                                                 "pub const WALK_EMIT_KEEP", "pub const WALK_MOSTLY_DEAD")):
                homes.append(rel)
                break
    return homes


class Law13DropinParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolver = native_resolver()
        if not cls.resolver:
            raise unittest.SkipTest("cannot run: no mios-resolver (build tools/native or set MIOS_RESOLVER_BIN)")
        cls.base = tempfile.mkdtemp(prefix="mios-l13-")
        cls.stage = os.path.join(cls.base, "bin")
        os.mkdir(cls.stage)
        os.symlink(cls.resolver, os.path.join(cls.stage, "mios-resolver"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_generated_layered_fixtures_agree_three_ways(self):
        rng = random.Random(SEED)
        fixtures = [Fixture(self.base, rng, i) for i in range(CASES)]
        py = python_leg(fixtures)
        failures = []
        for i, (fx, pyr) in enumerate(zip(fixtures, py)):
            spec = spec_merge(fx.layers)
            nat = native_merged(fx, self.resolver)
            shell = bash_leg(fx, self.resolver, self.stage)
            problems = []
            problems += diff(spec, pyr["merged"], "spec", "mios_toml.py")
            problems += diff(spec, nat, "spec", "mios-resolver")
            problems += diff(shell, pyr["exports"], "userenv.sh", "mios_toml.py")
            if problems:
                failures.append("case %03d (%s):\n%s" % (i, fx.dir, "\n".join(problems)))
        self.assertFalse(failures, "%d/%d layered fixtures disagree (seed %d):\n%s"
                         % (len(failures), CASES, SEED, "\n".join(failures[:8])))

    def test_tier_major_beats_lexical_order(self):
        """A vendor fragment sorting last can never outrank the host monolith,
        and a hidden or non-.toml fragment is never a layer."""
        d = Path(self.base) / "tier-major"
        (d / "v.d").mkdir(parents=True)
        (d / "h.d").mkdir()
        (d / "v.toml").write_text('[alpha]\nk1 = "vendor"\nk2 = "vendor"\nk3 = 5\n')
        (d / "v.d/zzz-99.toml").write_text('[alpha]\nk1 = "vendor-frag"\nk2 = "vendor-frag"\n')
        (d / "h.toml").write_text('[alpha]\nk1 = "host"\n')
        (d / "h.d/.late.toml").write_text('[alpha]\nk1 = "hidden"\n')
        (d / "h.d/late.toml.bak").write_text('[alpha]\nk1 = "backup"\n')
        (d / "h.d/10-x.toml").write_text('[alpha]\nk3 = ""\n')
        fx = Fixture.__new__(Fixture)
        fx.dir, fx.vendor, fx.vendor_d = d, d / "v.toml", d / "v.d"
        fx.host, fx.host_d, fx.config = d / "h.toml", d / "h.d", d / "cfg"
        want = {"MIOS_ALPHA_K1": "host", "MIOS_ALPHA_K2": "vendor-frag", "MIOS_ALPHA_K3": "5"}
        shell = bash_leg(fx, self.resolver, self.stage)
        py = python_leg([fx])[0]["exports"]
        for name, value in want.items():
            self.assertEqual(shell.get(name), value, "userenv.sh/mios-resolver %s" % name)
            self.assertEqual(py.get(name), value, "mios_toml.py %s" % name)

    def test_editing_the_ssot_walk_moves_both_twins(self):
        """F5F56GQW: the walk rules live in [generation.walk] only, and an edit
        there changes userenv.sh's and mios_toml.py's output identically."""
        d = Path(self.base) / "walk-edit"
        d.mkdir()
        text = VENDOR.read_text(encoding="utf-8")
        listed = 'excluded_sections = ["containers", '
        self.assertIn(listed, text, "[generation.walk].excluded_sections moved; update this test")
        outputs = {}
        for label, body in (("shipped", text), ("edited", text.replace(listed, "excluded_sections = [", 1))):
            vendor = d / label / "usr/share/mios/mios.toml"
            vendor.parent.mkdir(parents=True)
            vendor.write_text(body, encoding="utf-8")
            fx = Fixture.__new__(Fixture)
            fx.dir, fx.vendor, fx.vendor_d = d / label, vendor, d / label / "none.d"
            fx.host, fx.host_d, fx.config = d / label / "h.toml", d / label / "h.d", d / label / "cfg"
            shell = bash_leg(fx, self.resolver, self.stage)
            py = python_leg([fx])[0]["exports"]
            self.assertEqual(diff(shell, py, "userenv.sh", "mios_toml.py"), [], label)
            outputs[label] = {k for k in shell if k.startswith("MIOS_CONTAINERS_")}
        self.assertFalse(outputs["shipped"], "the shipped walk exports [containers]")
        self.assertTrue(outputs["edited"], "dropping containers from the SSOT list changed nothing")

    def test_walk_rules_have_one_home(self):
        self.assertEqual(walk_list_homes(ROOT), [], "the walk lists are restated outside mios.toml")
        # Negative control: a re-introduced private copy is found.
        with tempfile.TemporaryDirectory() as tmp:
            planted = Path(tmp) / "usr/lib/mios/mios_toml.py"
            planted.parent.mkdir(parents=True)
            planted.write_text('EXCLUDED_SECTIONS = {"containers", "install_phases", "btop", "messages"}\n')
            self.assertEqual(walk_list_homes(tmp), ["usr/lib/mios/mios_toml.py"])


if __name__ == "__main__":
    unittest.main()
