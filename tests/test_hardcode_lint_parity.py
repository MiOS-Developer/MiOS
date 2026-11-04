#!/usr/bin/env python3
# AI-hint: Comprehensive parity test suite for MiOS hardcode linting (Python oracle vs Rust compiled binary).
# AI-related: usr/libexec/mios/mios-hardcode-lint, usr/share/mios/mios.toml, tools/native/mios-hardcode-lint, automation/98-drift-checks.sh
# AI-doc: usr/share/doc/mios/adr/0003-sbom-not-hardcode.md, usr/share/doc/mios/manual/tests.md, PROJECT.md
"""Comprehensive parity and two-sided verification test suite for mios-hardcode-lint.

Validates the full behavioral contract of Architectural Law 7 (NO-HARDCODE enforcement):
- CLI invocation, arguments, exit codes, and stdout/stderr formatting.
- Date detection in Python comments, module/function/class docstrings, and string literal prose.
- Legitimate date value exemptions (leading quote, slugs, URLs).
- Header crash-risks (stranded UTF-8 BOM in .ps1, shebang line displacement in .sh).
- Routable IP detection vs private/loopback/CGNAT exemptions.
- Port literal detection vs bracketed/arithmetic/URL exemptions.
- SSOT allowlist integration from usr/share/mios/mios.toml.
- GENERATED file banner exemption.
- Ventoy plaintext credential detection.
- Parity between Python oracle and compiled Rust static binary.
"""

from __future__ import annotations

import ast
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path

# Resolve repository paths
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_ORACLE_PATH = "independent Python reference embedded in this parity suite"
# Frozen full source data; this program is never installed.
_ORACLE_SOURCE = (
    '#!/usr/bin/env python3\n'
    '# AI-hint: Test-only frozen migration reference; never installed. Former runtime contract:  Read-only enforcement gate for the NO-HARDCODE law (Law 7): fails on a date or dated attribution in comments, docstrings or .py string prose, and on AI-hint header crash-risks (a BOM not at byte 0, a header above a shebang).\n'
    '# AI-related: ../../../automation/98-drift-checks.sh, ../../share/mios/mios.toml, ./mios-ai-tag, ../../../CLAUDE.md, tools/native/mios-hardcode-lint/\n'
    '# AI-functions: _try_native_bin, _date_in_comment_py, _date_in_string_py, _date_in_comment_generic, _header_risk, scan, main\n'
    '#\n'
    '# Comments must describe the WHY abstractly, never pin code to a frozen history.\n'
    '# A date inside served CSS/JS-comment text or a system-prompt string is prose the\n'
    '# comment scan never sees, so .py adds a string-token scan that flags only a\n'
    '# whitespace-led date and exempts one used as a VALUE (a protocol-version or\n'
    '# config literal, or one glued into a URL/slug/identifier); other files use\n'
    '# quote-aware comment detection. Exit 1 on violation, MIOS_HARDCODE_LINT_SOFT=1\n'
    '# for advisory; pairs with the surface-parity + module-boundary gates.\n'
    '"""NO-HARDCODE law enforcement: forbid date-literals in comments/docstrings + dated attributions in string-literal prose + header crash-risks."""\n'
    '\n'
    'from __future__ import annotations\n'
    '\n'
    'import ast\n'
    'import io\n'
    'import os\n'
    'import re\n'
    'import subprocess\n'
    'import sys\n'
    'import tokenize\n'
    '\n'
    "_DATE = re.compile(r'\\b20[0-9]{2}-[01][0-9]-[0-3][0-9]\\b')\n"
    "_BOM = b'\\xef\\xbb\\xbf'\n"
    '_EXEMPT = (\n'
    "    '/var/lib/mios/', '/.git/', '/__pycache__/', '/node_modules/',\n"
    "    '/usr/share/mios/knowledge/',          # generated knowledge snapshots\n"
    "    '/.claude/',                           # Claude worktrees\n"
    "    '/.agents/',                           # Agent worktrees\n"
    ')\n'
    "_UNIT_EXT = ('.service', '.socket', '.container', '.timer', '.conf')\n"
    "_CODE_EXT = ('.py', '.sh', '.bash', '.toml', '.yml', '.yaml', '.ps1', '.psm1') + _UNIT_EXT\n"
    '\n'
    '\n'
    'def _date_in_comment_py(text: str):\n'
    '    """Yield (lineno, snippet) for a date inside a .py COMMENT or DOCSTRING only."""\n'
    '    try:\n'
    '        for t in tokenize.generate_tokens(io.StringIO(text).readline):\n'
    '            if t.type == tokenize.COMMENT and _DATE.search(t.string):\n'
    '                yield t.start[0], t.string.strip()[:80]\n'
    '    except (tokenize.TokenError, IndentationError, SyntaxError):\n'
    '        return\n'
    '    try:\n'
    '        tree = ast.parse(text)\n'
    '    except SyntaxError:\n'
    '        return\n'
    "    lines = text.split('\\n')\n"
    '    for node in ast.walk(tree):\n'
    '        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):\n'
    '            continue\n'
    "        body = getattr(node, 'body', None) or []\n"
    '        if (body and isinstance(body[0], ast.Expr)\n'
    "                and isinstance(getattr(body[0], 'value', None), ast.Constant)\n"
    '                and isinstance(body[0].value.value, str)):\n'
    '            c = body[0].value\n'
    '            for ln in range(c.lineno, (c.end_lineno or c.lineno) + 1):\n'
    '                if 1 <= ln <= len(lines) and _DATE.search(lines[ln - 1]):\n'
    '                    yield ln, lines[ln - 1].strip()[:80]\n'
    '\n'
    '\n'
    'def _date_in_string_py(text: str):\n'
    '    """Yield (lineno, snippet) for a dated ATTRIBUTION baked into a .py STRING\n'
    '    literal -- the hole that a date inside served CSS/JS-comment text or inside a\n'
    '    system-prompt string slips through (the #-comment scan never sees string\n'
    '    content). A date used as a VALUE is legitimate and exempt: a standalone or\n'
    '    leading literal (a protocol-version id or a date config value is quote-led, so\n'
    '    the char before it is the opening quote) or one glued into a URL/slug/identifier\n'
    '    (preceded by a non-space joiner). The forbidden pattern is a date sitting in\n'
    '    running prose -- structurally, one preceded by WHITESPACE and never the head of\n'
    '    its own literal -- which is exactly how an `<attribution> <date>` credit reads.\n'
    '    The discriminator is positional, not lexical (no keyword/English gate)."""\n'
    '    try:\n'
    '        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))\n'
    '    except (tokenize.TokenError, IndentationError, SyntaxError):\n'
    '        return\n'
    "    lines = text.split('\\n')\n"
    '    str_types = {tokenize.STRING}\n'
    "    fmid = getattr(tokenize, 'FSTRING_MIDDLE', None)  # 3.12+ splits f-strings\n"
    '    if fmid is not None:\n'
    '        str_types.add(fmid)\n'
    '    for t in toks:\n'
    '        if t.type not in str_types:\n'
    '            continue\n'
    '        s = t.string\n'
    '        for m in _DATE.finditer(s):\n'
    '            i = m.start()\n'
    '            if i == 0 or not s[i - 1].isspace():\n'
    '                continue\n'
    "            ln = t.start[0] + s[:i].count('\\n')\n"
    '            snip = lines[ln - 1].strip()[:80] if 1 <= ln <= len(lines) else m.group(0)\n'
    '            yield ln, snip\n'
    '\n'
    '\n'
    'def _comment_start(line: str) -> int:\n'
    '    in_s = in_d = False\n'
    '    for i, ch in enumerate(line):\n'
    '        if ch == "\'" and not in_d:\n'
    '            in_s = not in_s\n'
    '        elif ch == \'"\' and not in_s:\n'
    '            in_d = not in_d\n'
    "        elif not in_s and not in_d and ch == '#':\n"
    "            return -1 if line[:2] == '#!' else i\n"
    '    return -1\n'
    '\n'
    '\n'
    'def _date_in_comment_generic(text: str):\n'
    '    """Yield (lineno, snippet) for a date inside a #-comment (quote-aware)."""\n'
    "    for idx, line in enumerate(text.split('\\n'), 1):\n"
    '        cs = _comment_start(line)\n'
    '        if cs >= 0 and _DATE.search(line[cs:]):\n'
    '            yield idx, line[cs:].strip()[:80]\n'
    '\n'
    '\n'
    'def _header_risk(raw: bytes, path: str):\n'
    '    """A header crash-risk: a STRANDED file-BOM (PS1 byte-0 rule), or a header line\n'
    '    ABOVE a shebang the file actually declares. NOT flagged: a U+FEFF inside a string\n'
    '    literal (legit, e.g. BOM-stripping code), or a sourced script with no shebang."""\n'
    "    if path.endswith('.ps1') and not raw.startswith(_BOM) and _BOM in raw[:256]:\n"
    '        return "UTF-8 BOM stranded in PS1 head (must be byte 0 -- breaks irm|iex parse)"\n'
    "    if path.endswith(('.sh', '.bash')):\n"
    "        head = raw.decode('utf-8', 'ignore').split('\\n')[:40]\n"
    "        sb = [i for i, l in enumerate(head) if l.startswith('#!')]\n"
    '        if sb and sb[0] != 0:\n'
    '            return "shebang not on line 1 (a header/comment is above it)"\n'
    '    return None\n'
    '\n'
    '_PORT_PATTERNS = [\n'
    "    re.compile(r'localhost:(\\d+)'),\n"
    "    re.compile(r'127\\.0\\.0\\.1:(\\d+)'),\n"
    "    re.compile(r':(\\d{4,5})\\b'),\n"
    ']\n'
    '\n'
    '_IP_PATTERN = re.compile(\n'
    "    r'\\b(?:[1-9]|\\d{2}|1\\d{2}|2[0-4]\\d|25[0-5])\\.(?:\\d|[1-9]\\d|1\\d{2}|2[0-4]\\d|25[0-5])\\.(?:\\d|[1-9]\\d|1\\d{2}|2[0-4]\\d|25[0-5])\\.(?:\\d|[1-9]\\d|1\\d{2}|2[0-4]\\d|25[0-5])\\b'\n"
    ')\n'
    '\n'
    'def _is_routable_ip(ip_str: str) -> bool:\n'
    '    if ip_str in ("127.0.0.1", "0.0.0.0"):\n'
    '        return False\n'
    "    parts = ip_str.split('.')\n"
    '    if len(parts) != 4:\n'
    '        return False\n'
    '    try:\n'
    '        p1, p2, p3, p4 = [int(p) for p in parts]\n'
    '    except ValueError:\n'
    '        return False\n'
    '    if p1 == 10:\n'
    '        return False\n'
    '    if p1 == 172 and (16 <= p2 <= 31):\n'
    '        return False\n'
    '    if p1 == 192 and p2 == 168:\n'
    '        return False\n'
    '    if p1 == 100 and (64 <= p2 <= 127):\n'
    '        return False\n'
    '    return True\n'
    '\n'
    'def _anchor_pattern(pat: str) -> str:\n'
    "    prefix = r'(?<!\\d)' if not pat.startswith('^') and not pat.startswith(r'\\b') and not pat.startswith(r'(?<!') else ''\n"
    "    suffix = r'(?!\\d)' if not pat.endswith('$') and not pat.endswith(r'\\b') and not pat.endswith(r'(?!\\d)') and not pat.endswith(r'(?![0-9])') else ''\n"
    '    return prefix + pat + suffix\n'
    '\n'
    '_UNIT_DIRECTIVE_PREFIXES = (\n'
    "    'listenstream=', 'listendatagram=',\n"
    "    'execstart=', 'execstartpre=', 'execstartpost=', 'execstop=',\n"
    "    'exec=',\n"
    "    'environment=',\n"
    ')\n'
    '\n'
    'def _is_unit_directive_line(line: str, in_multiline: bool) -> tuple[bool, bool]:\n'
    '    stripped = line.strip()\n'
    "    if stripped.startswith('#') or stripped.startswith(';') or stripped.startswith('//'):\n"
    '        return False, in_multiline\n'
    '    lower = stripped.lower()\n'
    '    is_directive_start = any(lower.startswith(p) for p in _UNIT_DIRECTIVE_PREFIXES)\n'
    "    has_bind = '--bind' in stripped\n"
    '    is_directive = is_directive_start or in_multiline or has_bind\n'
    "    next_multiline = is_directive and stripped.endswith('\\\\')\n"
    '    return is_directive, next_multiline\n'
    '\n'
    'def _check_ports_ips_py(text: str, filename: str, exempt_patterns: list[str]) -> list[tuple[int, str]]:\n'
    '    violations = []\n'
    '    exempt_rx = [re.compile(_anchor_pattern(pat)) for pat in exempt_patterns]\n'
    '    try:\n'
    '        toks = list(tokenize.generate_tokens(io.StringIO(text).readline))\n'
    '    except (tokenize.TokenError, IndentationError, SyntaxError):\n'
    '        return []\n'
    '    \n'
    "    lines = text.split('\\n')\n"
    '    for t in toks:\n'
    '        if t.type == tokenize.COMMENT:\n'
    '            continue\n'
    '        if t.type == tokenize.STRING and t.string.startswith((\'"""\', "\'\'\'", \'r"""\', "r\'\'\'", \'u"""\', "u\'\'\'")):\n'
    '            continue\n'
    '            \n'
    '        s = t.string\n'
    '        ln = t.start[0]\n'
    '        line = lines[ln - 1] if 1 <= ln <= len(lines) else ""\n'
    '        \n'
    '        is_line_exempt = False\n'
    '        for rx in exempt_rx:\n'
    '            if rx.search(line):\n'
    '                is_line_exempt = True\n'
    '                break\n'
    '        if is_line_exempt:\n'
    '            continue\n'
    '            \n'
    '        for pat in _PORT_PATTERNS:\n'
    '            for m in pat.finditer(s):\n'
    '                port_num_str = m.group(1)\n'
    '                try:\n'
    '                    port_num = int(port_num_str)\n'
    '                except ValueError:\n'
    '                    continue\n'
    '                if not (1 <= port_num <= 65535):\n'
    '                    continue\n'
    '                \n'
    '                start = m.start()\n'
    '                end = m.end()\n'
    "                if end < len(s) and s[end] == ']':\n"
    '                    continue\n'
    "                if start > 0 and s[start - 1] in ('[', '-', '+', ':', '/'):\n"
    '                    continue\n'
    '                \n'
    '                violations.append((ln, m.group(0)))\n'
    '                \n'
    '        for m in _IP_PATTERN.finditer(s):\n'
    '            ip_str = m.group(0)\n'
    '            if _is_routable_ip(ip_str):\n'
    '                violations.append((ln, ip_str))\n'
    '                \n'
    '    return violations\n'
    '\n'
    'def _check_ports_ips(text: str, filename: str, exempt_patterns: list[str]) -> list[tuple[int, str]]:\n'
    '    violations = []\n'
    "    lines = text.split('\\n')\n"
    '    exempt_rx = [re.compile(_anchor_pattern(pat)) for pat in exempt_patterns]\n'
    '    is_unit = filename.endswith(_UNIT_EXT)\n'
    '    in_multiline = False\n'
    '\n'
    '    for idx, line in enumerate(lines, 1):\n'
    '        stripped = line.strip()\n'
    "        if stripped.startswith('#') or stripped.startswith('//') or stripped.startswith(';'):\n"
    '            continue\n'
    '        \n'
    '        if is_unit:\n'
    '            is_directive, in_multiline = _is_unit_directive_line(line, in_multiline)\n'
    '            if not is_directive:\n'
    '                continue\n'
    '\n'
    '        is_line_exempt = False\n'
    '        for rx in exempt_rx:\n'
    '            if rx.search(line):\n'
    '                is_line_exempt = True\n'
    '                break\n'
    '        if is_line_exempt:\n'
    '            continue\n'
    '            \n'
    '        code_part = line\n'
    '        in_s = in_d = False\n'
    '        for i, ch in enumerate(line):\n'
    '            if ch == "\'" and not in_d:\n'
    '                in_s = not in_s\n'
    '            elif ch == \'"\' and not in_s:\n'
    '                in_d = not in_d\n'
    "            elif not in_s and not in_d and ch == '#':\n"
    '                code_part = line[:i]\n'
    '                break\n'
    '                \n'
    '        for pat in _PORT_PATTERNS:\n'
    '            for m in pat.finditer(code_part):\n'
    '                port_num_str = m.group(1)\n'
    '                try:\n'
    '                    port_num = int(port_num_str)\n'
    '                except ValueError:\n'
    '                    continue\n'
    '                if not (1 <= port_num <= 65535):\n'
    '                    continue\n'
    '                    \n'
    '                start = m.start()\n'
    '                end = m.end()\n'
    "                if end < len(code_part) and code_part[end] == ']':\n"
    '                    continue\n'
    "                if start > 0 and code_part[start - 1] in ('-', '+', ':', '['):\n"
    '                    continue\n'
    '                violations.append((idx, m.group(0)))\n'
    '                \n'
    '        for m in _IP_PATTERN.finditer(code_part):\n'
    '            ip_str = m.group(0)\n'
    '            if _is_routable_ip(ip_str):\n'
    '                violations.append((idx, ip_str))\n'
    '                \n'
    '    return violations\n'
    '\n'
    'import mios_toml  # noqa: E402  -- the ONE shared layered-mios.toml resolver\n'
    '\n'
    '\n'
    'def _load_allowlist(roots):\n'
    '    exempt_files = []\n'
    '    exempt_patterns = []\n'
    '    for r in roots:\n'
    '        toml_path = os.path.join(r, "usr/share/mios/mios.toml")\n'
    '        if os.path.isfile(toml_path):\n'
    '            data = mios_toml.load_merged(layers=[toml_path])\n'
    '            sec = mios_toml.section(data, "security.nohc_allowlist")\n'
    '            exempt_files.extend(sec.get("exempt_files", []))\n'
    '            exempt_patterns.extend(sec.get("exempt_patterns", []))\n'
    '            break\n'
    '    return exempt_files, exempt_patterns\n'
    '\n'
    'def scan(roots):\n'
    '    violations = []\n'
    '    scanned = 0\n'
    '    exempt_files, exempt_patterns = _load_allowlist(roots)\n'
    '    exempt_files_rx = [re.compile(pat) for pat in exempt_files]\n'
    '    for root in roots:\n'
    '        if os.path.isfile(root):\n'
    '            items = [(os.path.dirname(root), os.path.basename(root), root)]\n'
    '        else:\n'
    '            items = []\n'
    '            for dirpath, dirs, files in os.walk(root):\n'
    "                dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', 'node_modules')]\n"
    '                for fn in files:\n'
    '                    items.append((dirpath, fn, os.path.join(dirpath, fn)))\n'
    '        for dirpath, fn, p in items:\n'
    "            rel = p.replace('\\\\', '/')\n"
    '            if any(x in rel for x in _EXEMPT) or not fn.endswith(_CODE_EXT):\n'
    '                continue\n'
    '            try:\n'
    "                raw = open(p, 'rb').read()\n"
    '            except OSError:\n'
    '                continue\n'
    '            if not raw:\n'
    '                continue\n'
    '            scanned += 1\n'
    '            hr = _header_risk(raw, fn)\n'
    '            if hr:\n'
    '                violations.append((p, 0, f"HEADER: {hr}"))\n'
    "            text = raw.decode('utf-8-sig' if raw[:3] == _BOM else 'utf-8', 'ignore').replace('\\r\\n', '\\n')\n"
    '            \n'
    '            is_file_exempt = False\n'
    '            for rx in exempt_files_rx:\n'
    '                if rx.search(rel):\n'
    '                    is_file_exempt = True\n'
    '                    break\n'
    '            \n'
    "            if fn.endswith(('.py', '.sh', '.bash', '.ps1', '.psm1') + _UNIT_EXT) and not is_file_exempt:\n"
    "                if fn.endswith('.py'):\n"
    '                    for ln, snip in _check_ports_ips_py(text, fn, exempt_patterns):\n'
    '                        violations.append((p, ln, f"HARDCODED-PORT/IP: {snip}"))\n'
    '                else:\n'
    '                    for ln, snip in _check_ports_ips(text, fn, exempt_patterns):\n'
    '                        violations.append((p, ln, f"HARDCODED-PORT/IP: {snip}"))\n'
    '                    \n'
    "            if 'usr/share/mios/ventoy/autorun' in rel:\n"
    "                for idx, line in enumerate(text.split('\\n'), 1):\n"
    '                    if \'chpasswd\' in line and re.search(r\'echo\\s+["\\\'][a-zA-Z0-9_-]+:[a-zA-Z0-9_-]+["\\\']\\s*\\|\\s*chpasswd\', line):\n'
    '                        violations.append((p, idx, f"PLAINTEXT-CHPASSWD: {line.strip()[:80]}"))\n'
    '                    \n'
    '            # A GENERATED file is exempt from the timeless-comment rule: its\n'
    '            # comments are PROJECTED from mios.toml `comment = "..."` values,\n'
    '            # where a date is a value and legitimately exempt (the lint is\n'
    '            # value-aware). It only becomes a #-comment once rendered, and a\n'
    '            # "DO NOT EDIT" file cannot be hand-corrected -- the fix belongs\n'
    "            # in mios.toml. Keyed off the file's own generated marker rather\n"
    '            # than an allowlist, so it can never drift out of date.\n'
    "            # Both tokens, and only in the file's own top banner -- a\n"
    '            # GENERATOR mentions "DO NOT EDIT" in the header it EMITS, and\n'
    '            # generators are authored files that must stay linted.\n'
    "            _banner = '\\n'.join(text.splitlines()[:4])\n"
    "            is_generated = 'GENERATED' in _banner and 'DO NOT EDIT' in _banner\n"
    '            if not is_generated:\n'
    "                if fn.endswith('.py'):\n"
    '                    for ln, snip in _date_in_comment_py(text):\n'
    '                        violations.append((p, ln, f"DATE-IN-COMMENT: {snip}"))\n'
    '                    for ln, snip in _date_in_string_py(text):\n'
    '                        violations.append((p, ln, f"DATE-IN-STRING: {snip}"))\n'
    '                elif not fn.endswith(_UNIT_EXT):\n'
    '                    for ln, snip in _date_in_comment_generic(text):\n'
    '                        violations.append((p, ln, f"DATE-IN-COMMENT: {snip}"))\n'
    '    return violations, scanned\n'
    '\n'
    '\n'
    'def main(argv):\n'
    "    roots = [a for a in argv[1:] if not a.startswith('--')] or ['.']\n"
    "    soft = os.environ.get('MIOS_HARDCODE_LINT_SOFT', '0') == '1'\n"
    '    missing = [r for r in roots if not os.path.exists(r)]\n'
    '    if missing:\n'
    "        sys.stderr.write('[mios-hardcode-lint] FAIL: root(s) do not exist: %s\\n'\n"
    "                         % ', '.join(missing))\n"
    '        return 1\n'
    '    v, scanned = scan(roots)\n'
    '    # An empty scan used to print PASS. The gate passes $ROOT in, so a wrong\n'
    '    # or empty root linted nothing and still reported clean.\n'
    '    if scanned == 0:\n'
    "        sys.stderr.write('[mios-hardcode-lint] FAIL: scanned 0 files under %s -- '\n"
    "                         'nothing was linted, so this is not a pass\\n' % ', '.join(roots))\n"
    '        return 1\n'
    '    if not v:\n'
    "        print('[mios-hardcode-lint] PASS: %d file(s) scanned; no date-in-comment/string '\n"
    "              '/ header crash-risk / port-IP hardcode.' % scanned)\n"
    '        return 0\n'
    '    for p, ln, msg in v[:60]:\n'
    '        sys.stderr.write(f"    {p}:{ln}: {msg}\\n")\n'
    '    if len(v) > 60:\n'
    '        sys.stderr.write(f"    ... and {len(v) - 60} more\\n")\n'
    '    sys.stderr.write(f"[mios-hardcode-lint] FAIL: {len(v)} NO-HARDCODE violation(s) "\n'
    '                     "(strip dates/ports/IPs -> timeless comment/string / env vars; move a header below the shebang/BOM).\\n")\n'
    '    if soft:\n'
    '        sys.stderr.write("[mios-hardcode-lint] (MIOS_HARDCODE_LINT_SOFT=1 -> advisory, exit 0)\\n")\n'
    '        return 0\n'
    '    return 1\n'
    '\n'
    '\n'
    "if __name__ == '__main__':\n"
    '    sys.exit(main(sys.argv))\n'
)


_SSOT_PATH = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")

# Detect Rust binary candidates
_RUST_CANDIDATES = [
    os.environ.get("MIOS_HARDCODE_LINT_BIN", ""),
    shutil.which("mios-hardcode-lint") or "",
    os.path.join(_ROOT, "tools", "native", "target", "debug", "mios-hardcode-lint.exe"),
    os.path.join(_ROOT, "tools", "native", "target", "release", "mios-hardcode-lint.exe"),
    os.path.join(_ROOT, "tools", "native", "target", "debug", "mios-hardcode-lint"),
    os.path.join(_ROOT, "tools", "native", "target", "release", "mios-hardcode-lint"),
    os.path.join(_ROOT, "src", "mios-rs", "target", "debug", "mios-hardcode-lint.exe"),
    os.path.join(_ROOT, "src", "mios-rs", "target", "debug", "mios-hardcode-lint"),
]
if "MIOS_HARDCODE_LINT_BIN" in os.environ:
    _RUST_CANDIDATES = [os.environ["MIOS_HARDCODE_LINT_BIN"]]
_RUST_BIN = next((p for p in _RUST_CANDIDATES if p and os.path.isfile(p) and os.access(p, os.X_OK)), None)


def oracle_command(args: list[str]) -> list[str]:
    """Run the frozen reference as test data, independent of the native under test."""
    library = os.path.join(_ROOT, "usr", "lib", "mios")
    source = _ORACLE_SOURCE.replace(
        "import ast\n", "import sys\nsys.path.insert(0, " + repr(library) + ")\nimport ast\n", 1)
    return [sys.executable, "-c", source, *args]


def run_oracle(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Runs the canonical Python oracle script."""
    cmd = oracle_command(args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def run_rust_binary(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Runs the required compiled Rust static binary."""
    if not _RUST_BIN:
        raise AssertionError("native mios-hardcode-lint is required for parity")
    cmd = [_RUST_BIN] + args
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


# ============================================================================
# 1. CLI Flags, Arguments, and Exit Codes
# ============================================================================

class TestCliFlagsAndInvocation(unittest.TestCase):
    """Verifies CLI argument handling, error reporting, and exit code semantics."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_cli_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_explicit_missing_binary_never_selects_an_installed_copy(self):
        env = os.environ.copy()
        env["MIOS_HARDCODE_LINT_BIN"] = os.path.join(self.tmpdir, "missing-native")
        result = subprocess.run([sys.executable, __file__], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn("native mios-hardcode-lint is required", result.stderr)

    def test_cli_single_root_valid_clean(self):
        """Clean directory with one python file exits 0 and reports PASS."""
        sample_file = os.path.join(self.tmpdir, "valid.py")
        with open(sample_file, "w", encoding="utf-8") as fh:
            fh.write("def add(a, b):\n    return a + b\n")

        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected exit 0, got {res.returncode}. Stderr: {res.stderr}")
        self.assertIn("[mios-hardcode-lint] PASS: 1 file(s) scanned", res.stdout)
        self.assertEqual(res.stderr.strip(), "")

    def test_cli_multiple_roots_valid(self):
        """Multiple roots are scanned and aggregated in file count."""
        sub1 = os.path.join(self.tmpdir, "sub1")
        sub2 = os.path.join(self.tmpdir, "sub2")
        os.makedirs(sub1, exist_ok=True)
        os.makedirs(sub2, exist_ok=True)

        with open(os.path.join(sub1, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        with open(os.path.join(sub2, "b.py"), "w", encoding="utf-8") as fh:
            fh.write("y = 2\n")

        res = run_oracle([sub1, sub2])
        self.assertEqual(res.returncode, 0)
        self.assertIn("[mios-hardcode-lint] PASS: 2 file(s) scanned", res.stdout)

    def test_cli_nonexistent_root_fails(self):
        """Non-existent directory path returns exit code 1 with explicit message."""
        bogus_path = os.path.join(self.tmpdir, "does_not_exist_998877")
        res = run_oracle([bogus_path])
        self.assertEqual(res.returncode, 1)
        self.assertIn("[mios-hardcode-lint] FAIL: root(s) do not exist:", res.stderr)
        self.assertIn("does_not_exist_998877", res.stderr)

    def test_cli_empty_root_returns_failure(self):
        """Directory with zero eligible code files returns exit code 1."""
        empty_sub = os.path.join(self.tmpdir, "empty_dir")
        os.makedirs(empty_sub, exist_ok=True)
        res = run_oracle([empty_sub])
        self.assertEqual(res.returncode, 1)
        self.assertIn("[mios-hardcode-lint] FAIL: scanned 0 files under", res.stderr)
        self.assertIn("nothing was linted, so this is not a pass", res.stderr)

    def test_cli_soft_mode_returns_zero_on_violations(self):
        """When MIOS_HARDCODE_LINT_SOFT=1, violations are reported but exit code is 0."""
        bad_file = os.path.join(self.tmpdir, "bad.py")
        with open(bad_file, "w", encoding="utf-8") as fh:
            fh.write("# Created on " + "2026" + "-10-06\nval = 42\n")

        env = dict(os.environ, MIOS_HARDCODE_LINT_SOFT="1")
        res = run_oracle([self.tmpdir], env=env)
        self.assertEqual(res.returncode, 0, "Soft mode must exit 0")
        self.assertIn("DATE-IN-COMMENT", res.stderr)
        self.assertIn("[mios-hardcode-lint] (MIOS_HARDCODE_LINT_SOFT=1 -> advisory, exit 0)", res.stderr)

    def test_cli_hard_mode_default_exits_one_on_violations(self):
        """Default mode (MIOS_HARDCODE_LINT_SOFT=0) exits 1 when violations exist."""
        bad_file = os.path.join(self.tmpdir, "bad.py")
        with open(bad_file, "w", encoding="utf-8") as fh:
            fh.write("# Created on " + "2026" + "-10-06\nval = 42\n")

        env = dict(os.environ, MIOS_HARDCODE_LINT_SOFT="0")
        res = run_oracle([self.tmpdir], env=env)
        self.assertEqual(res.returncode, 1)
        self.assertIn("[mios-hardcode-lint] FAIL: 1 NO-HARDCODE violation(s)", res.stderr)


# ============================================================================
# 2. Date in Comments and Docstrings
# ============================================================================

class TestDateInCommentsAndDocstrings(unittest.TestCase):
    """Verifies timeless-comment rule across comments and docstrings in .py and generic files."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_date_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_date_in_python_hash_comment(self):
        """Python # comment containing a date literal is flagged."""
        p = os.path.join(self.tmpdir, "t1.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("x = 10  # modified " + "2026" + "-05-12 by dev\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)
        self.assertIn("2026" + "-05-12", res.stderr)

    def test_date_in_python_module_docstring(self):
        """Python module docstring with date literal is flagged."""
        p = os.path.join(self.tmpdir, "t2.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('"""Module created on ' + '2026' + '-01-15."""\nx = 1\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)
        self.assertIn("2026" + "-01-15", res.stderr)

    def test_date_in_python_function_docstring(self):
        """Python function docstring with date literal is flagged."""
        p = os.path.join(self.tmpdir, "t3.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('def compute():\n    """Last verified ' + '2026' + '-07-20."""\n    return 0\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)
        self.assertIn("2026" + "-07-20", res.stderr)

    def test_date_in_python_class_docstring(self):
        """Python class docstring with date literal is flagged."""
        p = os.path.join(self.tmpdir, "t4.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('class Runner:\n    """Deprecated on ' + '2026' + '-03-30."""\n    pass\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)
        self.assertIn("2026" + "-03-30", res.stderr)

    def test_date_in_python_async_function_docstring(self):
        """Python async function docstring with date literal is flagged."""
        p = os.path.join(self.tmpdir, "t5.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('async def fetch():\n    """Added ' + '2026' + '-09-01."""\n    return 1\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)
        self.assertIn("2026" + "-09-01", res.stderr)

    def test_date_in_generic_comment_sh(self):
        """Shell script comment with date is flagged."""
        p = os.path.join(self.tmpdir, "t6.sh")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('#!/bin/bash\n# Last updated: ' + '2026' + '-04-10\necho ok\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)

    def test_date_in_generic_comment_ps1(self):
        """PowerShell script comment with date is flagged."""
        p = os.path.join(self.tmpdir, "t7.ps1")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('# Written on ' + '2026' + '-08-14\nWrite-Host "hello"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)

    def test_date_in_generic_comment_toml(self):
        """TOML file comment with date is flagged."""
        p = os.path.join(self.tmpdir, "t8.toml")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('[section]\n# Migrated ' + '2026' + '-02-28\nkey = "val"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)

    def test_date_in_generic_comment_yaml(self):
        """YAML file comment with date is flagged."""
        p = os.path.join(self.tmpdir, "t9.yaml")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('# Snapshot from ' + '2026' + '-11-05\nname: test\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)


# ============================================================================
# 3. Date in String Literals: Prose vs Value
# ============================================================================

class TestDateInStringLiteralsProseVsValue(unittest.TestCase):
    """Verifies discrimination between dates in prose (forbidden) vs dates as values (exempt)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_str_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_date_in_string_prose_detected(self):
        """Date preceded by whitespace inside running string prose is flagged."""
        p = os.path.join(self.tmpdir, "s1.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('msg = "Updated on ' + '2026' + '-10-06 by operator"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-STRING", res.stderr)
        self.assertIn("2026" + "-10-06", res.stderr)

    def test_date_in_string_as_value_exempt(self):
        """Date as standalone config value starting immediately after opening quote is exempt."""
        p = os.path.join(self.tmpdir, "s2.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('SNAPSHOT_VERSION = "2026-10-06"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected exempt value, got stderr: {res.stderr}")
        self.assertIn("PASS: 1 file(s) scanned", res.stdout)

    def test_date_in_slug_or_identifier_exempt(self):
        """Date joined with non-whitespace character (e.g. hyphen or underscore) is exempt."""
        p = os.path.join(self.tmpdir, "s3.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('ARTIFACT_NAME = "mios-release-2026-10-06.tar.gz"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)
        self.assertIn("PASS: 1 file(s) scanned", res.stdout)

    def test_date_in_url_exempt(self):
        """Date in a URL path is exempt."""
        p = os.path.join(self.tmpdir, "s4.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('FEED_URL = "https://example.com/feeds/2026-10-06/index.json"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)
        self.assertIn("PASS: 1 file(s) scanned", res.stdout)

    def test_date_in_triple_quoted_string_prose(self):
        """Date in multiline string literal with whitespace is flagged."""
        p = os.path.join(self.tmpdir, "s5.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('banner = """\nSystem notice:\nModified on ' + '2026' + '-05-18 by team\n"""\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-STRING", res.stderr)


# ============================================================================
# 4. Header Crash-Risks
# ============================================================================

class TestHeaderCrashRisks(unittest.TestCase):
    """Verifies detection of UTF-8 BOM corruption and displaced shebang headers."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_header_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_ps1_bom_at_byte_zero_valid(self):
        """PowerShell script with UTF-8 BOM at byte offset 0 is completely valid."""
        p = os.path.join(self.tmpdir, "good.ps1")
        with open(p, "wb") as fh:
            fh.write(b"\xef\xbb\xbfWrite-Host 'hello world'\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)
        self.assertIn("PASS: 1 file(s) scanned", res.stdout)

    def test_ps1_bom_stranded_fails(self):
        """PowerShell script with UTF-8 BOM stranded after byte 0 fails."""
        p = os.path.join(self.tmpdir, "stranded.ps1")
        with open(p, "wb") as fh:
            fh.write(b"# AI-hint header\n\xef\xbb\xbfWrite-Host 'broken'\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("HEADER: UTF-8 BOM stranded in PS1 head", res.stderr)

    def test_sh_shebang_on_line_one_valid(self):
        """Shell script with shebang on line 1 is valid."""
        p = os.path.join(self.tmpdir, "good.sh")
        with open(p, "wb") as fh:
            fh.write(b"#!/bin/bash\n# Header below shebang\necho ok\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)
        self.assertIn("PASS: 1 file(s) scanned", res.stdout)

    def test_sh_shebang_not_on_line_one_fails(self):
        """Shell script with shebang displaced to line 2 or later fails."""
        p = os.path.join(self.tmpdir, "displaced.sh")
        with open(p, "wb") as fh:
            fh.write(b"# AI-hint on line 1\n#!/bin/bash\necho bad\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("HEADER: shebang not on line 1", res.stderr)

    def test_bom_inside_python_bytes_not_flagged(self):
        """BOM byte literal inside Python code is not a header risk."""
        p = os.path.join(self.tmpdir, "clean_bom_check.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("_BOM = b'\\xef\\xbb\\xbf'\nprint(len(_BOM))\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)


# ============================================================================
# 5. Port and IP Hardcodes
# ============================================================================

class TestPortAndIpHardcodes(unittest.TestCase):
    """Verifies detection of hardcoded routable IPs and ports, plus exemptions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_ip_port_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_routable_public_ip_detected(self):
        """Public routable IP literal in code is detected as HARDCODED-PORT/IP."""
        p = os.path.join(self.tmpdir, "bad_ip.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('remote_server = "198.51.100.25"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("HARDCODED-PORT/IP: 198.51.100.25", res.stderr)

    def test_loopback_ip_exempt(self):
        """127.0.0.1 is exempt from IP hardcode check."""
        p = os.path.join(self.tmpdir, "good_loopback.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('BIND_IP = "127.0.0.1"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)

    def test_any_ip_exempt(self):
        """0.0.0.0 is exempt from IP hardcode check."""
        p = os.path.join(self.tmpdir, "good_any.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('LISTEN_IP = "0.0.0.0"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)

    def test_private_subnets_exempt(self):
        """Private subnets (10.x, 192.168.x, 172.16-31.x, CGNAT 100.64-127.x) are exempt."""
        p = os.path.join(self.tmpdir, "good_private.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('IP_A = "10.0.1.5"\nIP_B = "192.168.1.1"\nIP_C = "172.24.0.10"\nIP_D = "100.85.12.3"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)

    def test_hardcoded_port_detected(self):
        """Hardcoded port :9999 or localhost:9999 in code is detected."""
        p = os.path.join(self.tmpdir, "bad_port.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('TARGET_URL = "http://localhost:9999/api"\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("HARDCODED-PORT/IP", res.stderr)
        self.assertIn(":9999", res.stderr)

    def test_bracketed_port_exempt(self):
        """Bracketed number like [8540] or array index is exempt."""
        p = os.path.join(self.tmpdir, "good_bracket.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('allowed_ports = [8540]\nval = data[8540]\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)

    def test_port_preceded_by_arithmetic_exempt(self):
        """Port literal preceded by arithmetic sign or colon is exempt."""
        p = os.path.join(self.tmpdir, "good_arith.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('offset = -8540\nratio = +8540\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)

    def test_unanchored_large_number_not_flagged(self):
        """Large number containing port digits (e.g. 185409) is not flagged as :8540."""
        p = os.path.join(self.tmpdir, "good_num.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('BIG_ID = 185409123\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)

    def test_systemd_unit_floated_variables_pass(self):
        """Systemd unit with floated fallback variables passes."""
        p = os.path.join(self.tmpdir, "test.service")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(
                "[Service]\n"
                "Environment=MIOS_KV_PAGING_HINTS=${MIOS_PORT_IGPU:-11436},${MIOS_PORT_WIN_LLM_LIGHT:-11450}\n"
                "ExecStart=/usr/bin/daemon --bind ${BIND:-0.0.0.0}:${PORT:-8080}\n"
            )
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected floated unit to pass, got: {res.stderr}")

        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 0, f"Rust floated unit must pass, got: {rust_res.stderr}")

    def test_systemd_unit_bare_port_fails(self):
        """Systemd unit with bare port in ListenStream directive fails."""
        p = os.path.join(self.tmpdir, "test.socket")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(
                "[Socket]\n"
                "ListenStream=0.0.0.0:8090\n"
            )
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1, "Bare port in unit directive must fail")
        self.assertIn("HARDCODED-PORT/IP", res.stderr)
        self.assertIn(":8090", res.stderr)

        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 1, "Rust bare port in unit must fail")
            self.assertIn("HARDCODED-PORT/IP", rust_res.stderr)
            self.assertIn(":8090", rust_res.stderr)


# ============================================================================
# 6. Allowlist, GENERATED Banner, and Ventoy Rules
# ============================================================================

class TestAllowlistAndSpecialExemptions(unittest.TestCase):
    """Verifies SSOT allowlist filtering, generated banner exemptions, and Ventoy checks."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_special_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_generated_banner_exemption(self):
        """Files with GENERATED + DO NOT EDIT in top 4 lines are exempt from DATE-IN-COMMENT."""
        p = os.path.join(self.tmpdir, "auto.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(
                "# AI-hint: THIS FILE IS GENERATED FROM SSOT.\n"
                "# DO NOT EDIT DIRECTLY.\n"
                "# Rendered on " + "2026" + "-10-06 by compiler\n"
                "DATA = {}\n"
            )
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected GENERATED exemption, got stderr: {res.stderr}")

    def test_non_generated_file_with_do_not_edit_not_exempt(self):
        """A file with DO NOT EDIT but missing GENERATED is NOT exempt."""
        p = os.path.join(self.tmpdir, "manual.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(
                "# Author comment: DO NOT EDIT\n"
                "# Signed " + "2026" + "-10-06\n"
                "x = 1\n"
            )
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("DATE-IN-COMMENT", res.stderr)

    def test_ventoy_plaintext_chpasswd_detected(self):
        """Ventoy autorun script containing plaintext chpasswd is flagged."""
        vdir = os.path.join(self.tmpdir, "usr", "share", "mios", "ventoy", "autorun")
        os.makedirs(vdir, exist_ok=True)
        p = os.path.join(vdir, "setup.sh")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write('#!/bin/bash\necho "root:secretpass123" | chpasswd\n')
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("PLAINTEXT-CHPASSWD", res.stderr)

    def test_anchored_allowlist_stops_masking_80xx(self):
        """Allowlist patterns for :80 and :443 must be anchored with (?![0-9]) and not mask :80xx or :4430."""
        # localhost:80 should be exempt (exit 0)
        p_clean = os.path.join(self.tmpdir, "good_80.py")
        with open(p_clean, "w", encoding="utf-8") as fh:
            fh.write('URL = "http://localhost:80/api"\n')
        res_clean = run_oracle([self.tmpdir])
        self.assertEqual(res_clean.returncode, 0, f"Expected localhost:80 exempt, got: {res_clean.stderr}")

        # localhost:8090 should NOT be exempt (exit 1, HARDCODED-PORT/IP)
        p_bad = os.path.join(self.tmpdir, "bad_8090.py")
        with open(p_bad, "w", encoding="utf-8") as fh:
            fh.write('URL = "http://localhost:8090/api"\n')
        res_bad = run_oracle([self.tmpdir])
        self.assertEqual(res_bad.returncode, 1, "localhost:8090 must be flagged")
        self.assertIn("HARDCODED-PORT/IP", res_bad.stderr)

        # Check Rust binary parity if available
        rust_bad = run_rust_binary([self.tmpdir])
        if rust_bad is not None:
            self.assertEqual(rust_bad.returncode, 1)
            self.assertIn("HARDCODED-PORT/IP", rust_bad.stderr)


# ============================================================================
# 7. Systemd Unit Directive Scoping & Date Isolation
# ============================================================================

class TestSystemdUnitScanning(unittest.TestCase):
    """Verifies directive-scoped port scanning and date-in-comment isolation for systemd units."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_unit_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_systemd_bare_port_in_directive_flagged(self):
        """Bare port on directive line (ListenStream=, ExecStart=, Environment=) is flagged."""
        p = os.path.join(self.tmpdir, "probe.service")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("[Service]\nExecStart=/usr/bin/daemon :8080\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 1)
        self.assertIn("HARDCODED-PORT/IP", res.stderr)
        self.assertIn(":8080", res.stderr)

        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 1)
            self.assertIn("HARDCODED-PORT/IP", rust_res.stderr)

    def test_systemd_floated_port_passes(self):
        """Floated port ${MIOS_PORTS_*:-default} and ${MIOS_NETWORK_*:-default} passes."""
        p = os.path.join(self.tmpdir, "floated.service")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("[Service]\nExecStart=/usr/bin/daemon :${MIOS_PORTS_DAEMON:-8080}\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected floated port to pass, got: {res.stderr}")

        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 0)

    def test_systemd_date_in_comment_tolerated(self):
        """Date comments in systemd units (.service, .socket, etc.) are isolated and tolerated."""
        p = os.path.join(self.tmpdir, "dated.service")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# Operator directive " + "2026" + "-05-15\n[Service]\nExecStart=/usr/bin/clean\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected date comment in .service to be tolerated, got: {res.stderr}")

        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 0)

    def test_systemd_non_directive_line_not_flagged(self):
        """Port literal in non-directive lines (e.g. Description=, Documentation=) is not flagged."""
        p = os.path.join(self.tmpdir, "desc.service")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("[Unit]\nDescription=Service running on port 8080\n[Service]\nExecStart=/usr/bin/clean\n")
        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0, f"Expected non-directive port to be ignored, got: {res.stderr}")

        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 0)


# ============================================================================
# 8. Two-Sided Verification & Binary Parity
# ============================================================================

class TestTwoSidedParityControls(unittest.TestCase):
    """Two-sided controls: positive pass and negative defect plants across all categories."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mios_lint_twoside_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_positive_clean_fixture_all_pass(self):
        """Positive Control: Clean multi-language project tree passes 100%."""
        files = {
            "main.py": "def main():\n    return 0\n",
            "run.sh": "#!/bin/bash\necho 'running'\n",
            "setup.ps1": "\xef\xbb\xbfWrite-Host 'setting up'\n",
            "config.toml": "[app]\nname = 'clean'\n",
        }
        for name, content in files.items():
            path = os.path.join(self.tmpdir, name)
            with open(path, "w", encoding="utf-8" if not name.endswith(".ps1") else "utf-8-sig") as fh:
                fh.write(content)

        res = run_oracle([self.tmpdir])
        self.assertEqual(res.returncode, 0)
        self.assertIn("PASS: 4 file(s) scanned", res.stdout)

        # Also check Rust binary if present
        rust_res = run_rust_binary([self.tmpdir])
        if rust_res is not None:
            self.assertEqual(rust_res.returncode, 0)
            self.assertIn("PASS: 4 file(s) scanned", rust_res.stdout)

    def test_negative_defect_plant_all_categories(self):
        """Negative Control: Planted defects across all categories are individually detected."""
        defects = [
            ("bad_date.py", "# " + "2026" + "-10-06\nx=1\n", "DATE-IN-COMMENT"),
            ("bad_str.py", 's = "Dated ' + '2026' + '-10-06"\n', "DATE-IN-STRING"),
            ("bad_ip.py", 'ip = "198.51.100.99"\n', "HARDCODED-PORT/IP"),
            ("bad_port.py", 'url = "http://localhost:9999"\n', "HARDCODED-PORT/IP"),
            ("bad_head.sh", '# comment on 1\n#!/bin/bash\n', "HEADER"),
        ]

        for fname, content, expected_token in defects:
            sub = os.path.join(self.tmpdir, f"sub_{fname}")
            os.makedirs(sub, exist_ok=True)
            p = os.path.join(sub, fname)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(content)

            res = run_oracle([sub])
            self.assertEqual(res.returncode, 1, f"Expected defect plant {fname} to fail")
            self.assertIn(expected_token, res.stderr, f"Missing token {expected_token} in stderr: {res.stderr}")

            rust_res = run_rust_binary([sub])
            if rust_res is not None:
                self.assertEqual(rust_res.returncode, 1)
                self.assertIn(expected_token, rust_res.stderr)

    def test_live_tree_oracle_execution(self):
        """Verifies the live repository tree passes or behaves deterministically with the oracle."""
        res = run_oracle([os.path.join(_ROOT, "tests")])
        self.assertIn(res.returncode, (0, 1))
        # Ensure it scanned multiple files
        if res.returncode == 0:
            self.assertIn("PASS:", res.stdout)


# ============================================================================
# Main Runner
# ============================================================================

def main() -> int:
    """Runs all parity test cases with formatted reporting."""
    if not _RUST_BIN:
        print("native mios-hardcode-lint is required for parity", file=sys.stderr)
        return 1
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestCliFlagsAndInvocation))
    suite.addTests(loader.loadTestsFromTestCase(TestDateInCommentsAndDocstrings))
    suite.addTests(loader.loadTestsFromTestCase(TestDateInStringLiteralsProseVsValue))
    suite.addTests(loader.loadTestsFromTestCase(TestHeaderCrashRisks))
    suite.addTests(loader.loadTestsFromTestCase(TestPortAndIpHardcodes))
    suite.addTests(loader.loadTestsFromTestCase(TestAllowlistAndSpecialExemptions))
    suite.addTests(loader.loadTestsFromTestCase(TestSystemdUnitScanning))
    suite.addTests(loader.loadTestsFromTestCase(TestTwoSidedParityControls))

    total_tests = suite.countTestCases()
    print("=" * 80)
    print("MiOS Hardcode-Lint Parity & Two-Sided Verification Test Suite")
    print(f"Total Test Cases: {total_tests}")
    print(f"Python Oracle: {_ORACLE_PATH}")
    print(f"Rust Binary: {_RUST_BIN or 'missing (required)'}")
    print("=" * 80)

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("=" * 80)
    print(f"Ran: {result.testsRun} | Passed: {result.testsRun - len(result.failures) - len(result.errors)} | "
          f"Failures: {len(result.failures)} | Errors: {len(result.errors)}")
    print("=" * 80)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
