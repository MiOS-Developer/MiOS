#!/usr/bin/env python3
# AI-hint: xb-09 / XB-3 gate for the MiOS-Xbox guest GPU driver step. Merges the live vendor SSOT with the staged lane
# fragment through mios_toml.load_merged (read-only), checks the fragment (detect mode, comment per key, no version pins,
# endpoints only in the SSOT), statically checks usr/share/mios/windows/xbox/mios-gpu-driver.ps1 (parses, PowerShell 5.1
# compatible AST, no vendor endpoint/version literals), then runs tests/test-mios-gpu-driver.ps1 under pwsh with all
# network, devices, signatures and installers mocked. Every static check has a planted-defect negative control.
# AI-related: usr/share/mios/windows/xbox/mios-gpu-driver.ps1, tests/test-mios-gpu-driver.ps1, usr/lib/mios/mios_toml.py,
#   /home/mios/mios-desktop-work-1/staged-ssot/xbox-gpu.toml, /.devloop/GOALS-mios-xbox.md (XB-3)
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.environ.get("MIOS_ROOT", "/")
SCRIPT = os.environ.get("MIOS_GPU_SCRIPT", os.path.join(ROOT, "usr/share/mios/windows/xbox/mios-gpu-driver.ps1"))
PS_TEST = os.environ.get("MIOS_GPU_PS_TEST", os.path.join(ROOT, "tests/test-mios-gpu-driver.ps1"))
LIVE = os.path.join(ROOT, "usr/share/mios/mios.toml")
FRAGMENT = os.environ.get("MIOS_GPU_FRAGMENT", "/home/mios/mios-desktop-work-1/staged-ssot/xbox-gpu.toml")
sys.path.insert(0, os.path.join(ROOT, "usr/lib/mios"))
import mios_toml  # noqa: E402

results = []


def check(cond, name):
    results.append((bool(cond), name))
    print(("ok   - " if cond else "FAIL - ") + name)


# ------------------------------------------------------------------ SSOT fragment

def merged_spec(fragment_path):
    merged = mios_toml.load_merged(layers=[LIVE, fragment_path])
    xb = merged.get("windows", {}).get("xbox", {})
    return {"windows": {"xbox": {"drivers": xb.get("drivers", {}), "gpu": xb.get("gpu", {})}}}


def fragment_findings(text, spec):
    """Problems in a staged fragment: [] when it conforms."""
    out = []
    xb = spec["windows"]["xbox"]
    if xb["drivers"].get("gpu") != "detect":
        out.append("windows.xbox.drivers.gpu is not 'detect'")
    gpu = xb["gpu"]
    for k in ("install_stage", "task_name", "record_path", "work_dir", "skip_vendor_ids", "vendors"):
        if k not in gpu:
            out.append("windows.xbox.gpu.%s missing" % k)
    want = {"nvidia": "10DE", "amd": "1002", "intel": "8086"}
    for name, vid in want.items():
        v = gpu.get("vendors", {}).get(name, {})
        if v.get("vendor_id") != vid:
            out.append("vendor %s id %r != %s" % (name, v.get("vendor_id"), vid))
        for k in ("resolver", "download_hosts", "signer_org_regex", "provider_regex", "install_args", "success_exit_codes"):
            if k not in v:
                out.append("vendor %s missing %s" % (name, k))
        for h in v.get("download_hosts", []):
            if "/" in h or ":" in h:
                out.append("vendor %s download host %r is not a bare host" % (name, h))
    nv = gpu.get("vendors", {}).get("nvidia", {})
    if nv.get("components") != ["Display.Driver"]:
        out.append("NVIDIA components must be exactly ['Display.Driver'] (no NVIDIA App/telemetry)")
    # comment per key (key lines only; continuation lines of arrays are exempt)
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("[") or not re.match(r"^[A-Za-z0-9_]+\s*=", s):
            continue
        if "#" not in s and not text.splitlines()[i - 2].strip().startswith("#"):
            out.append("line %d key without a comment: %s" % (i, s[:60]))
    # no pins: no driver version, no versioned package URL
    for i, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        if re.search(r"\b\d{3}\.\d{2}\b|adrenalin-edition-\d|gfx_win_\d|/Windows/\d", code):
            out.append("line %d pins a driver version: %s" % (i, code.strip()[:80]))
        if re.search(r"\d+\.\d+\.\d+\.\d+", code):
            out.append("line %d carries a 4-part version: %s" % (i, code.strip()[:80]))
    return out


with open(FRAGMENT, encoding="utf-8") as fh:
    frag_text = fh.read()
spec = merged_spec(FRAGMENT)
check(not fragment_findings(frag_text, spec), "fragment conforms (detect mode, vendors, comment per key, no pins)")
for f in fragment_findings(frag_text, spec):
    print("     finding:", f)
live_xb = mios_toml.load_merged(layers=[LIVE]).get("windows", {}).get("xbox", {})
check(live_xb.get("drivers", {}).get("gpu") == "detect",
      "live SSOT has drivers.gpu = detect")

with tempfile.TemporaryDirectory(prefix="mios-gpu-frag-") as td:
    def planted(old, new):
        assert old in frag_text, old
        p = os.path.join(td, "planted.toml")
        t = frag_text.replace(old, new, 1)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(t)
        return fragment_findings(t, merged_spec(p))
    check(planted('gpu = "detect"', 'gpu = "baked"'), "NEG fragment: drivers.gpu = baked is rejected")
    check(planted('components = ["Display.Driver"]', 'components = ["Display.Driver", "NvApp"]'),
          "NEG fragment: NVIDIA App component is rejected")
    check(planted('channel = "latest" ', 'version = "617.42"   # pin\nchannel = "latest" '),
          "NEG fragment: a pinned driver version is rejected")
    check(planted('vendor_id = "10DE"', 'vendor_id = "10DE"\nextra_key = 1'), "NEG fragment: uncommented key is rejected")

# ------------------------------------------------------------------ static checks on the script

PWSH = shutil.which("pwsh")

AST_PROBE = r"""
param([string]$Path)
$t = $null; $e = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$t, [ref]$e)
$bad = @()
foreach ($x in $e) { $bad += "parse: " + $x.Message }
foreach ($tok in $t) { if ($tok.Kind -in 'QuestionQuestion','QuestionQuestionEquals','QuestionDot','QuestionLBracket','AndAnd','OrOr') { $bad += "ps7-only token: " + $tok.Text + " line " + $tok.Extent.StartLineNumber } }
foreach ($n in $ast.FindAll({ param($a) $a.GetType().Name -in 'TernaryExpressionAst','PipelineChainAst' }, $true)) { $bad += "ps7-only syntax: " + $n.Extent.Text }
foreach ($n in $ast.FindAll({ param($a) $a -is [System.Management.Automation.Language.CommandParameterAst] -and $a.ParameterName -in 'AsHashtable','Parallel','SkipCertificateCheck' }, $true)) { $bad += "ps7-only parameter: -" + $n.ParameterName }
$bad | ConvertTo-Json -Compress
"""


def script_findings(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    for pat, why in ((r"(?i)nvidia\.com|geforce\.com|amd\.com|intel\.com", "vendor endpoint literal (must come from the SSOT)"),
                     (r"\b\d{3}\.\d{2}-desktop|adrenalin-edition-\d|gfx_win_\d", "driver version pin"),
                     (r"(?i)-ForceUnsigned|testsigning|nointegritychecks", "signature enforcement bypass"),
                     (r"(?i)SkipCertificateCheck|ServerCertificateValidationCallback", "TLS validation bypass")):
        if re.search(pat, code):
            out.append(why)
    for fn in ("Get-MiOSSignature", "Test-MiOSSigner", "Test-MiOSAllowedUrl", "Resolve-MiOSRedirectUrl", "Write-MiOSJsonAtomic"):
        if fn + " " not in code and fn + "(" not in code:
            out.append("missing " + fn)
    if PWSH:
        with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False) as pf:
            pf.write(AST_PROBE)
        try:
            r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", pf.name, path],
                               capture_output=True, text=True, timeout=120)
            txt = r.stdout.strip()
            if r.returncode != 0:
                out.append("AST probe failed: " + r.stderr.strip()[:200])
            elif txt and txt != "null":
                v = json.loads(txt)
                out.extend(v if isinstance(v, list) else [v])
        finally:
            os.unlink(pf.name)
    return out


f = script_findings(SCRIPT)
check(not f, "script static checks (parses, PS 5.1 syntax, no vendor literal/pin/bypass)")
for x in f:
    print("     finding:", x)
with open(SCRIPT, encoding="utf-8") as fh:
    src = fh.read()
with tempfile.TemporaryDirectory(prefix="mios-gpu-neg-") as td:
    def planted_script(old, new):
        assert old in src, old
        p = os.path.join(td, "planted.ps1")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(src.replace(old, new, 1))
        return script_findings(p)
    check(planted_script("$ErrorActionPreference = 'Stop'", "$ErrorActionPreference = 'Stop'\n$u = 'https://us.download.nvidia.com/x.exe'"),
          "NEG script: hardcoded NVIDIA URL is caught")
    check(planted_script("$ErrorActionPreference = 'Stop'", "$ErrorActionPreference = 'Stop'\n$x = $null ?? 1"),
          "NEG script: PS7-only ?? operator is caught" if PWSH else "NEG script: (pwsh absent, AST probe skipped)")
    check(planted_script("$ErrorActionPreference = 'Stop'", "$ErrorActionPreference = 'Stop'\nbcdedit /set testsigning on"),
          "NEG script: test-signing bypass is caught")

# ------------------------------------------------------------------ behaviour tests under pwsh (all I/O mocked)

if not PWSH:
    print("SKIP - pwsh not available on this host: behaviour tests not run")
else:
    with tempfile.TemporaryDirectory(prefix="mios-gpu-spec-") as td:
        sp = os.path.join(td, "xbox-spec.json")
        with open(sp, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", PS_TEST, "-ScriptPath", SCRIPT, "-SpecPath", sp],
                           capture_output=True, text=True, timeout=300)
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr[-4000:])
        m = re.search(r"RESULT pass=(\d+) fail=(\d+)", r.stdout)
        check(r.returncode == 0 and m and int(m.group(2)) == 0 and int(m.group(1)) >= 40,
              "pwsh behaviour tests (positive + negative controls) green")

failed = [n for ok, n in results if not ok]
print("SUMMARY %d/%d passed" % (len(results) - len(failed), len(results)))
sys.exit(1 if failed else 0)
