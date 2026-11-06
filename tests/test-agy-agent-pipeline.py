#!/usr/bin/env python3
# AI-hint: Unit tests for MiOS Antigravity CLI (AGY) agent, subagents, workflows, and CI/CD artifacting integration.
# AI-doc: usr/share/doc/mios/manual/ch04-the-agentic-ai-stack.md
"""Test suite for MiOS Antigravity CLI (AGY) agent and subagents configuration."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestAgyAgentPipeline(unittest.TestCase):
    """Verifies AGY agent and subagents configuration files for MiOS development and CI/CD."""

    def test_rules_agents_md_invariants(self):
        """Verifies .agents/rules/AGENTS.md exists and enforces core invariants."""
        rules_path = REPO_ROOT / ".agents" / "rules" / "AGENTS.md"
        self.assertTrue(rules_path.is_file(), f"Missing {rules_path}")
        content = rules_path.read_text(encoding="utf-8")

        # Invariants checks
        self.assertIn(".git", content)
        self.assertIn("/var", content)
        self.assertIn("Unified Kernel Image", content)
        self.assertIn("venus", content)
        self.assertIn("vfio", content.lower())
        self.assertIn("blade", content.lower())

        # Universal OpenAI endpoint check
        self.assertIn("MIOS_AI_ENDPOINT", content)
        self.assertIn("/v1/", content)

        # Rust & Keyring safety net
        self.assertIn("Rust", content)
        self.assertIn("Keyring", content)

    def test_subagents_json_schema_and_roles(self):
        """Verifies .agents/subagents.json contains valid JSON and required subagents."""
        subagents_path = REPO_ROOT / ".agents" / "subagents.json"
        self.assertTrue(subagents_path.is_file(), f"Missing {subagents_path}")

        data = json.loads(subagents_path.read_text(encoding="utf-8"))
        self.assertIn("subagents", data)
        subagents = data["subagents"]
        self.assertIsInstance(subagents, list)

        # Canonical roles are MiOS project conventions.
        required_roles = {
            "orchestrator",
            "worker",
            "auditor",
            "reviewer",
            "challenger",
            "explorer",
            "publisher",
            "developer",
        }
        found_names = set()
        for sa in subagents:
            self.assertIn("name", sa)
            self.assertIn("role", sa)
            self.assertIn("description", sa)
            self.assertIn("tools", sa)
            self.assertIn("system_prompt", sa)
            self.assertIsInstance(sa["tools"], list)
            self.assertTrue(len(sa["tools"]) > 0)
            self.assertTrue(len(sa["system_prompt"]) > 20)
            found_names.add(sa["name"])

        missing = required_roles - found_names
        self.assertFalse(missing, f"Missing required canonical subagents: {missing}")

    def test_workflows_exist_and_formatted(self):
        """Verifies .agents/skills/ (or .agents/workflows/) has valid dev-loop, pipeline, and artifacting skills/workflows."""
        skills_dir = REPO_ROOT / ".agents" / "skills"
        workflows_dir = REPO_ROOT / ".agents" / "workflows"
        self.assertTrue(skills_dir.is_dir() or workflows_dir.is_dir(), f"Missing {skills_dir} or {workflows_dir}")

        expected = ["dev-loop", "pipeline", "artifacting"]
        for name in expected:
            skill_path = skills_dir / name / "SKILL.md"
            wf_path = workflows_dir / f"{name}.md"
            wf_bak = workflows_dir / f"{name}.md.bak"
            self.assertTrue(skill_path.is_file() or wf_path.is_file() or wf_bak.is_file(), f"Missing skill/workflow for {name}")
            target = skill_path if skill_path.is_file() else (wf_path if wf_path.is_file() else wf_bak)
            txt = target.read_text(encoding="utf-8")
            self.assertTrue(txt.startswith("---"), f"Skill/Workflow {name} missing YAML frontmatter")
            self.assertIn("name:", txt)
            self.assertIn("description:", txt)

    def test_commands_antigravity_definitions(self):
        """Verifies commands/antigravity/ canonical definitions and absence of duplicate commands/agy/."""
        # Single canonical owner: commands/antigravity
        cmd_dir = REPO_ROOT / "commands" / "antigravity"
        self.assertTrue(cmd_dir.is_dir(), f"Missing canonical {cmd_dir}")

        for cmd_name in ["dev-loop.toml", "pipeline.toml", "agents.toml"]:
            cmd_file = cmd_dir / cmd_name
            self.assertTrue(cmd_file.is_file(), f"Missing {cmd_file}")
            txt = cmd_file.read_text(encoding="utf-8")
            self.assertIn("[command]", txt)
            self.assertIn("name =", txt)
            import tomllib
            workflow = tomllib.loads(txt)["command"]["workflow"]
            self.assertTrue((REPO_ROOT / workflow).is_file(), f"Unresolved command workflow: {workflow}")

        # Deduplication check: commands/agy must NOT exist as a redundant copy (task_3407)
        duplicate_dir = REPO_ROOT / "commands" / "agy"
        self.assertFalse(duplicate_dir.exists(), f"Duplicate directory {duplicate_dir} should not exist; keep commands/antigravity as canonical owner")

    def test_github_agent_definitions(self):
        """Verifies .github/agents/ definitions for agy-pipeline and agy-subagents."""
        gh_agents_dir = REPO_ROOT / ".github" / "agents"
        self.assertTrue(gh_agents_dir.is_dir(), f"Missing {gh_agents_dir}")

        for ag_file in ["agy-pipeline.agent.md", "agy-subagents.agent.md"]:
            path = gh_agents_dir / ag_file
            self.assertTrue(path.is_file(), f"Missing {path}")
            txt = path.read_text(encoding="utf-8")
            self.assertTrue(txt.startswith("---"))
            self.assertIn("name:", txt)
            self.assertIn("description:", txt)

    def test_plugin_bundle_structure(self):
        """Verifies .agents/plugins/mios-cicd/ bundle manifest, rules, and skills."""
        plugin_dir = REPO_ROOT / ".agents" / "plugins" / "mios-cicd"
        self.assertTrue(plugin_dir.is_dir(), f"Missing {plugin_dir}")

        manifest = plugin_dir / "plugin.json"
        self.assertTrue(manifest.is_file(), f"Missing {manifest}")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data.get("name"), "mios-cicd")

        rules = plugin_dir / "rules" / "AGENTS.md"
        self.assertTrue(rules.is_file(), f"Missing {rules}")

        skill = plugin_dir / "skills" / "dev-pipeline" / "SKILL.md"
        self.assertTrue(skill.is_file(), f"Missing {skill}")
        skill_txt = skill.read_text(encoding="utf-8")
        self.assertIn("name: dev-pipeline", skill_txt)

    def test_wrapper_scripts_and_installer(self):
        """Verifies mios-agent-agy wrapper and install-mios-agents.sh integration."""
        installer_path = REPO_ROOT / "install-mios-agents.sh"
        self.assertTrue(installer_path.is_file())
        installer_txt = installer_path.read_text(encoding="utf-8")
        self.assertIn("mios-agent-agy", installer_txt)

        # Check local installed binary if present
        local_wrapper = Path("/usr/local/bin/mios-agent-agy")
        if local_wrapper.exists():
            self.assertTrue(os.access(local_wrapper, os.X_OK))

        # Check CI/CD runner script
        cicd_runner = REPO_ROOT / "automation" / "cicd" / "05-run-agy-pipeline-agent.sh"
        self.assertTrue(cicd_runner.is_file())
        self.assertTrue(os.access(cicd_runner, os.X_OK))

        # Check bash syntax
        bash_bin = shutil.which("bash") or "bash"
        res = subprocess.run([bash_bin, "-n", cicd_runner.as_posix()], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in {cicd_runner}: {res.stderr}")
        res = subprocess.run([bash_bin, "-n", installer_path.as_posix()], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in {installer_path}: {res.stderr}")

    def test_installer_stages_wrappers_against_the_current_tree(self):
        """Runs install-mios-agents.sh into a DESTDIR and drives the staged mios-agent-agy."""
        installer_path = REPO_ROOT / "install-mios-agents.sh"
        installer_txt = installer_path.read_text(encoding="utf-8")
        # Law 7: the endpoint and model are never literal defaults in the installer or its wrappers.
        self.assertIsNone(re.search(r"MIOS_AI_(ENDPOINT|MODEL):-[^$]", installer_txt),
                          "install-mios-agents.sh hardcodes an endpoint/model default")
        bash_bin = shutil.which("bash") or "bash"
        wrappers = ["mios-agent-claude", "mios-agent-gemini", "mios-agent-agy", "mios-llm"]
        with tempfile.TemporaryDirectory(prefix="mios-agents-stage-") as tmpdir:
            stage = Path(tmpdir) / "root"
            env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
            env.update(DESTDIR=str(stage), MIOS_AGREEMENT_BANNER="off")
            res = subprocess.run([bash_bin, installer_path.as_posix()], capture_output=True,
                                 text=True, env=env, cwd=tmpdir)
            self.assertEqual(res.returncode, 0, f"installer failed: {res.stdout}\n{res.stderr}")

            # The staged prompt is the canonical one, not the root redirector, and the
            # /etc host-override tier is left to the operator.
            prompt = stage / "usr" / "share" / "mios" / "ai" / "system.md"
            self.assertEqual(prompt.read_bytes(),
                             (REPO_ROOT / "usr" / "share" / "mios" / "ai" / "system.md").read_bytes())
            self.assertFalse((stage / "etc").exists(), "installer wrote the /etc host-override tier")

            bindir = stage / "usr" / "local" / "bin"
            for name in wrappers:
                wrapper = bindir / name
                self.assertTrue(wrapper.is_file(), f"installer did not stage {wrapper}")
                self.assertTrue(os.access(wrapper, os.X_OK) or os.name == "nt", f"{wrapper} not executable")
                self.assertTrue(wrapper.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n"),
                                f"{wrapper} has no shebang")
                res = subprocess.run([bash_bin, "-n", wrapper.as_posix()], capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, f"Syntax error in {wrapper}: {res.stderr}")

            # mios-agent-agy resolves the endpoint and model from install.env, lets a
            # caller's export win, and refuses to run agy when neither supplies them.
            mock_dir = Path(tmpdir) / "bin"
            mock_dir.mkdir()
            marker = Path(tmpdir) / "agy-ran"
            mock = mock_dir / "agy"
            mock.write_text("#!/bin/sh\n"
                            f"echo \"$MIOS_AI_ENDPOINT|$MIOS_AI_MODEL|$*\" > '{marker.as_posix()}'\n",
                            encoding="utf-8")
            os.chmod(mock, 0o755)
            etc_fixture = Path(tmpdir) / "etc-mios"
            etc_fixture.mkdir()
            (etc_fixture / "install.env").write_text(
                "MIOS_AI_ENDPOINT=http://ssot.invalid/v1\nMIOS_AI_MODEL=ssot-model\n", encoding="utf-8")
            etc_empty = Path(tmpdir) / "etc-empty"
            etc_empty.mkdir()
            run_env = {k: v for k, v in env.items() if k != "DESTDIR"}
            run_env.update(PATH=f"{mock_dir}{os.pathsep}{env.get('PATH', '')}",
                           DBUS_SESSION_BUS_ADDRESS="unix:path=/nonexistent")
            agy = [bash_bin, (bindir / "mios-agent-agy").as_posix(), "-p", "task"]

            res = subprocess.run(agy, capture_output=True, text=True, cwd=REPO_ROOT,
                                 env={**run_env, "MIOS_ETC_DIR": str(etc_fixture)})
            self.assertEqual(res.returncode, 0, f"mios-agent-agy failed: {res.stderr}")
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), "http://ssot.invalid/v1|ssot-model|-p task")

            res = subprocess.run(agy, capture_output=True, text=True, cwd=REPO_ROOT,
                                 env={**run_env, "MIOS_ETC_DIR": str(etc_fixture),
                                      "MIOS_AI_ENDPOINT": "http://caller.invalid/v1",
                                      "MIOS_AI_MODEL": "caller-model"})
            self.assertEqual(res.returncode, 0, f"mios-agent-agy failed: {res.stderr}")
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), "http://caller.invalid/v1|caller-model|-p task")

            marker.unlink()
            res = subprocess.run(agy, capture_output=True, text=True, cwd=REPO_ROOT,
                                 env={**run_env, "MIOS_ETC_DIR": str(etc_empty)})
            self.assertNotEqual(res.returncode, 0, "mios-agent-agy ran with no resolvable endpoint")
            self.assertIn("MIOS_AI_ENDPOINT is unset", res.stderr)
            self.assertFalse(marker.exists(), "agy was executed without a resolved endpoint")

    def test_agents_command_definitions(self):
        """Verifies canonical commands/antigravity/agents.toml and agents workflow."""
        antigravity_agents = REPO_ROOT / "commands" / "antigravity" / "agents.toml"
        self.assertTrue(antigravity_agents.is_file(), f"Missing canonical {antigravity_agents}")
        agy_agents = REPO_ROOT / "commands" / "agy" / "agents.toml"
        self.assertFalse(agy_agents.exists(), f"Duplicate {agy_agents} must not exist")

        skill = REPO_ROOT / ".agents" / "skills" / "agents" / "SKILL.md"
        workflow = REPO_ROOT / ".agents" / "workflows" / "agents.md"
        wf_bak = REPO_ROOT / ".agents" / "workflows" / "agents.md.bak"
        self.assertTrue(skill.is_file() or workflow.is_file() or wf_bak.is_file(), "Missing agents skill or workflow")
        target = skill if skill.is_file() else (workflow if workflow.is_file() else wf_bak)
        wf_txt = target.read_text(encoding="utf-8")
        self.assertIn("pipeline-auditor", wf_txt)
        self.assertIn("pipeline-worker", wf_txt)
        self.assertIn("mios-dev", wf_txt)

    def test_workspace_agents_md_files(self):
        """Verifies .agents/agents/ contains canonical definitions for all standard MiOS-Dev agents."""
        agents_dir = REPO_ROOT / ".agents" / "agents"
        self.assertTrue(agents_dir.is_dir(), f"Missing {agents_dir}")

        expected = [
            "orchestrator.md",
            "worker.md",
            "auditor.md",
            "reviewer.md",
            "challenger.md",
            "explorer.md",
            "publisher.md",
            "developer.md",
        ]
        for fname in expected:
            fpath = agents_dir / fname
            self.assertTrue(fpath.is_file(), f"Missing {fpath}")
            txt = fpath.read_text(encoding="utf-8")
            self.assertTrue(txt.startswith("---"), f"{fname} missing YAML frontmatter")
            self.assertIn("name:", txt)
            self.assertIn("description:", txt)

    def test_cicd_scripts_behavioural_and_failure_propagation(self):
        """Verifies automation/cicd/ 01-05 execution, absence of skip-permissions, and failure propagation."""
        bash_bin = shutil.which("bash") or "bash"
        python_bin = sys.executable

        # 1. Verify all 5 scripts exist and are executable
        scripts = [
            REPO_ROOT / "automation" / "cicd" / "01-ingest-daily-telemetry.sh",
            REPO_ROOT / "automation" / "cicd" / "02-distill-agent-weights.py",
            REPO_ROOT / "automation" / "cicd" / "03-build-bootc-oci.sh",
            REPO_ROOT / "automation" / "cicd" / "04-deploy-atomic-switch.sh",
            REPO_ROOT / "automation" / "cicd" / "05-run-agy-pipeline-agent.sh",
        ]
        for s in scripts:
            self.assertTrue(s.is_file(), f"Missing {s}")
            self.assertTrue(os.access(s, os.X_OK) or os.name == "nt", f"{s} not executable")

        # 2. Invariant: 05-run-agy-pipeline-agent.sh must NOT contain --dangerously-skip-permissions or || true
        content_05 = (REPO_ROOT / "automation" / "cicd" / "05-run-agy-pipeline-agent.sh").read_text(encoding="utf-8")
        self.assertNotIn("--dangerously-skip-permissions", content_05)
        self.assertNotIn("|| true", content_05)

        # 3. Behavioral test: 01-04 run and succeed
        res_01 = subprocess.run([bash_bin, scripts[0].as_posix()], capture_output=True, text=True)
        self.assertEqual(res_01.returncode, 0, f"01 failed: {res_01.stderr}")
        self.assertIn("[01-INGEST]", res_01.stdout)

        res_02 = subprocess.run([python_bin, str(scripts[1])], capture_output=True, text=True)
        self.assertEqual(res_02.returncode, 0, f"02 failed: {res_02.stderr}")
        self.assertIn("[02-DISTILL]", res_02.stdout)

        res_03 = subprocess.run([bash_bin, scripts[2].as_posix()], capture_output=True, text=True)
        self.assertEqual(res_03.returncode, 0, f"03 failed: {res_03.stderr}")
        self.assertIn("[03-BUILD]", res_03.stdout)

        res_04 = subprocess.run([bash_bin, scripts[3].as_posix()], capture_output=True, text=True)
        self.assertEqual(res_04.returncode, 0, f"04 failed: {res_04.stderr}")
        self.assertIn("[04-DEPLOY]", res_04.stdout)

        # 4. Behavioral test for 05: Failure propagation (Negative Control)
        # When agy exits non-zero, 05-run-agy-pipeline-agent.sh MUST fail (non-zero returncode).
        with tempfile.TemporaryDirectory(prefix="mock-agy-") as tmpdir:
            tmp_path = Path(tmpdir)
            mock_sh = tmp_path / "agy"
            mock_sh.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            os.chmod(mock_sh, 0o755)
            if os.name == "nt":
                mock_bat = tmp_path / "agy.bat"
                mock_bat.write_text("@echo off\nexit /b 1\n", encoding="utf-8")

            env = dict(os.environ)
            env["PATH"] = f"{tmpdir}{os.pathsep}{env.get('PATH', '')}"
            res_fail = subprocess.run(
                [bash_bin, scripts[4].as_posix()],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(
                res_fail.returncode,
                0,
                "05-run-agy-pipeline-agent.sh must fail when agy fails; failure was swallowed!",
            )

            # 5. Success behavioral test: When agy succeeds
            mock_sh.write_text("#!/bin/sh\necho {}\nexit 0\n", encoding="utf-8")
            if os.name == "nt":
                mock_bat.write_text("@echo off\necho {}\nexit /b 0\n", encoding="utf-8")

            res_ok = subprocess.run(
                [bash_bin, scripts[4].as_posix()],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(res_ok.returncode, 0, f"05 failed with mock agy: {res_ok.stderr}")
            self.assertIn("Completed Successfully", res_ok.stdout)


if __name__ == "__main__":
    unittest.main()
