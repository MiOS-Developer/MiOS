#!/usr/bin/env python3
"""Local image builds hand a caller's GitHub token to the bake as the optional mios_github_token secret.

The bake's GitHub API calls (release metadata and the sha256 digests it verifies downloads against)
share an anonymous 60-requests-per-hour limit per IP. Once that runs out, every later fetch fails and
the build stops. CI passes GITHUB_TOKEN as a build secret. These tests hold the local build entry
points (`just build`/`build-logged`/`build-verbose`, and mios-build-driver behind `mios build`) to the
same contract, running the real snippet text from each file:

- a GITHUB_TOKEN (or GH_TOKEN) becomes `--secret id=mios_github_token,...`, never a build argument;
- with neither set, no secret flag is added, and an unset token never makes podman fail;
- the driver writes the token to a private 0600 file (sudo drops the environment) and logs no value.
"""

import os
import re
import stat
import subprocess
import tempfile
import unittest

ROOT = os.environ.get("MIOS_TEST_ROOT") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
TOKEN = "ghs_TESTTOKEN0123456789"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def recipes(justfile):
    """name -> body of every Justfile recipe that bakes the operator credential."""
    out, name, body = {}, None, []
    for line in justfile.splitlines():
        m = re.match(r"^([A-Za-z][\w-]*)\s*:", line)
        if m and not line.startswith((" ", "\t")):
            if name:
                out[name] = "\n".join(body)
            name, body = m.group(1), []
        elif name:
            body.append(line)
    if name:
        out[name] = "\n".join(body)
    return {k: v for k, v in out.items() if "bake-hash)" in v}


def env_without_tokens(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")}
    env.update(extra)
    return env


class JustfileBuilds(unittest.TestCase):
    def setUp(self):
        self.recipes = recipes(read("Justfile"))

    def test_every_credential_build_recipe_is_covered(self):
        self.assertEqual(sorted(self.recipes), ["build", "build-logged", "build-verbose"])

    def snippet(self, body):
        lines = [l.strip().rstrip("\\").strip() for l in body.splitlines()]
        start = next(i for i, l in enumerate(lines) if l.startswith('_gh_tok="'))
        return "\n".join(lines[start : start + 2])

    def run_snippet(self, body, env):
        script = (
            self.snippet(body)
            + '\nprintf "%s|%s|%s" "$_gh_secret" "${GITHUB_TOKEN-}" "${_gh_tok-unset}"\n'
        )
        return subprocess.run(
            ["sh", "-c", script], env=env, capture_output=True, text=True, check=True
        ).stdout

    def test_token_becomes_the_build_secret(self):
        for name, body in self.recipes.items():
            with self.subTest(recipe=name):
                out = self.run_snippet(body, env_without_tokens(GITHUB_TOKEN=TOKEN))
                self.assertEqual(
                    out, f"--secret id=mios_github_token,env=GITHUB_TOKEN|{TOKEN}|unset"
                )
                # GH_TOKEN alone is exported under the name the secret reads
                out = self.run_snippet(body, env_without_tokens(GH_TOKEN=TOKEN))
                self.assertEqual(
                    out, f"--secret id=mios_github_token,env=GITHUB_TOKEN|{TOKEN}|unset"
                )

    def test_no_token_adds_no_secret(self):
        for name, body in self.recipes.items():
            with self.subTest(recipe=name):
                self.assertEqual(self.run_snippet(body, env_without_tokens()), "||unset")

    def test_podman_build_carries_the_secret_and_no_token_build_arg(self):
        for name, body in self.recipes.items():
            with self.subTest(recipe=name):
                build = body[body.index("podman build") :]
                build = build[: build.index("-t {{LOCAL}}")]
                self.assertIn(
                    "--secret id=mios_user_password_hash,env=MIOS_USER_PASSWORD_HASH $_gh_secret",
                    build,
                )
                self.assertNotRegex(build, r"build-arg[^\n]*(GITHUB|GH)_TOKEN")
                self.assertLess(body.index("_gh_secret="), body.index("podman build"))


class BuildDriver(unittest.TestCase):
    def setUp(self):
        self.src = read("usr/libexec/mios/mios-build-driver")

    def block(self):
        start = self.src.index("_gh_secret=()")
        end = self.src.index("unset _gh_tok", start) + len("unset _gh_tok")
        return self.src[start:end]

    def run_block(self, env):
        with tempfile.TemporaryDirectory() as d:
            script = (
                '_log() { echo "[log] $*"; }\n_secret_dir="$1"\n'
                + self.block()
                + '\nprintf "ARGS:%s\\n" "${_gh_secret[*]-}"\nprintf "TOK:%s\\n" "${_gh_tok-unset}"\n'
            )
            r = subprocess.run(
                ["bash", "-c", script, "drv", d],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            f = os.path.join(d, "mios_github_token")
            content = mode = None
            if os.path.exists(f):
                content = open(f).read()
                mode = stat.S_IMODE(os.stat(f).st_mode)
            return r.stdout, d, content, mode

    def test_token_reaches_podman_through_a_private_file(self):
        for var in ("GITHUB_TOKEN", "GH_TOKEN"):
            with self.subTest(var=var):
                out, d, content, mode = self.run_block(env_without_tokens(**{var: TOKEN}))
                self.assertIn(f"ARGS:--secret id=mios_github_token,src={d}/mios_github_token", out)
                self.assertEqual(content, TOKEN)
                self.assertEqual(mode, 0o600)
                self.assertIn("TOK:unset", out)
                # the log names the mechanism, never the value
                self.assertIn("authenticated", out)
                self.assertNotIn(TOKEN, out.replace(f"{d}/mios_github_token", ""))

    def test_no_token_adds_no_secret_and_says_so(self):
        out, _d, content, _mode = self.run_block(env_without_tokens())
        self.assertIn("ARGS:\n", out)
        self.assertIsNone(content)
        self.assertIn("unauthenticated", out)

    def test_podman_build_carries_the_secret(self):
        build = self.src[self.src.index("sudo podman build") :]
        build = build[: build.index("-t localhost/mios:latest")]
        self.assertIn('"${_gh_secret[@]}"', build)
        self.assertLess(self.src.index("_gh_secret=()"), self.src.index("sudo podman build"))


if __name__ == "__main__":
    unittest.main()
