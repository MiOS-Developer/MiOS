#!/usr/bin/env python3
# AI-hint: Regression test verifying the // @duration contract and brace structure of all Hyprland windowshade shaders.
# AI-related: usr/share/mios/hyprland/shaders/, usr/share/mios/mios.toml [desktop.hyprland.windowshade]
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Verify every usr/share/mios/hyprland/shaders/*.glsl declares // @duration <float>
and contains no lines starting with '}' before main() ends."""

from __future__ import annotations

import glob
import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.environ.get("MIOS_DRIFT_ROOT") or os.environ.get("MIOS_ROOT") or "/"
_SHADER_DIR = os.path.join(_ROOT, "usr/share/mios/hyprland/shaders")
if not os.path.isdir(_SHADER_DIR):
    candidate = os.path.normpath(os.path.join(_HERE, "..", "usr/share/mios/hyprland/shaders"))
    if os.path.isdir(candidate):
        _SHADER_DIR = candidate
    else:
        _SHADER_DIR = "/usr/share/mios/hyprland/shaders"


_DURATION_RE = re.compile(r"^\s*//\s*@duration\s+([0-9]+(?:\.[0-9]+)?)\s*$", re.MULTILINE)
_MAIN_RE = re.compile(r"\bvoid\s+main\s*\([^)]*\)\s*\{", re.MULTILINE)


def validate_shader(filepath: str) -> None:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Verify @duration contract
    match = _DURATION_RE.search(content)
    if not match:
        raise ValueError(f"{filepath} lacks '// @duration <float>' header declaration")
    duration = float(match.group(1))
    if duration <= 0.0:
        raise ValueError(f"{filepath} has non-positive duration: {duration}")

    # 2. Verify brace structure inside and preceding main()
    main_match = _MAIN_RE.search(content)
    if not main_match:
        # Check single-line main
        if "void main()" in content and "{" in content and "}" in content:
            main_idx = content.find("void main()")
            lines_before = content[:main_idx].splitlines()
            pre_depth = 0
            for idx, line in enumerate(lines_before, 1):
                stripped = line.strip()
                line_opens = line.count("{")
                line_closes = line.count("}")
                if stripped.startswith("}"):
                    if pre_depth <= 0 or pre_depth - line_closes < 0:
                        raise ValueError(f"{filepath}: line {idx} before main starts with '}}': '{line}'")
                pre_depth += line_opens - line_closes
            return
        raise ValueError(f"{filepath} lacks 'void main() {{' definition")

    # Inspect all lines preceding void main() {
    lines_before_main = content[:main_match.start()].splitlines()
    pre_depth = 0
    for idx, line in enumerate(lines_before_main, 1):
        stripped = line.strip()
        line_opens = line.count("{")
        line_closes = line.count("}")
        if stripped.startswith("}"):
            if pre_depth <= 0 or pre_depth - line_closes < 0:
                raise ValueError(f"{filepath}: line {idx} before main starts with '}}': '{line}'")
        pre_depth += line_opens - line_closes

    if pre_depth != 0:
        raise ValueError(f"{filepath}: unclosed brace structure before main()")

    main_start = main_match.end()
    first_newline = content.find("\n", main_start)
    if first_newline != -1:
        same_line = content[main_start:first_newline]
    else:
        same_line = content[main_start:]

    open_count = same_line.count("{")
    close_count = same_line.count("}")
    if close_count > open_count:
        # main() closed on the same line (e.g. none-close.glsl, none-open.glsl)
        return

    depth = 1 + open_count - close_count
    lines_after_main = content[first_newline + 1:].splitlines() if first_newline != -1 else []

    main_ended = False
    for line_idx, line in enumerate(lines_after_main, 1):
        stripped = line.strip()
        line_opens = line.count("{")
        line_closes = line.count("}")

        # If a line starts with '}' before main ends:
        if line.startswith("}") or stripped.startswith("}"):
            if depth - (1 if (stripped.startswith("}") and line_closes > 0) else 0) > 0:
                raise ValueError(
                    f"{filepath}: line {line_idx} inside main starts with '}}' before main ends: '{line}'"
                )

        depth += line_opens - line_closes
        if depth <= 0:
            main_ended = True
            break

    if not main_ended:
        raise ValueError(f"{filepath}: main() does not have a matching closing brace '}}'")



def test_positive_control() -> None:
    shader_files = sorted(glob.glob(os.path.join(_SHADER_DIR, "*.glsl")))
    assert len(shader_files) >= 8, f"Expected at least 8 shaders in {_SHADER_DIR}, found {len(shader_files)}"
    for sf in shader_files:
        validate_shader(sf)


def test_negative_controls() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        # Neg 1: Missing @duration
        s1 = os.path.join(tmpdir, "missing_duration.glsl")
        with open(s1, "w", encoding="utf-8") as f:
            f.write("#version 320 es\nvoid main() {\n    fragColor = vec4(1.0);\n}\n")
        try:
            validate_shader(s1)
            raise AssertionError("Negative control failed: accepted missing @duration")
        except ValueError:
            pass

        # Neg 2: Invalid duration (0.0)
        s2 = os.path.join(tmpdir, "zero_duration.glsl")
        with open(s2, "w", encoding="utf-8") as f:
            f.write("#version 320 es\n// @duration 0.0\nvoid main() {\n    fragColor = vec4(1.0);\n}\n")
        try:
            validate_shader(s2)
            raise AssertionError("Negative control failed: accepted 0.0 duration")
        except ValueError:
            pass

        # Neg 3: Negative duration
        s3 = os.path.join(tmpdir, "neg_duration.glsl")
        with open(s3, "w", encoding="utf-8") as f:
            f.write("#version 320 es\n// @duration -0.5\nvoid main() {\n    fragColor = vec4(1.0);\n}\n")
        try:
            validate_shader(s3)
            raise AssertionError("Negative control failed: accepted negative duration")
        except ValueError:
            pass

        # Neg 4: Indented line starting with '}' inside main before main ends
        s4 = os.path.join(tmpdir, "indented_brace.glsl")
        with open(s4, "w", encoding="utf-8") as f:
            f.write("#version 320 es\n// @duration 0.3\nvoid main() {\n    if (true) {\n        discard;\n    }\n    fragColor = vec4(1.0);\n}\n")
        try:
            validate_shader(s4)
            raise AssertionError("Negative control failed: accepted indented '}' inside main")
        except ValueError:
            pass

        # Neg 5: Column 0 line starting with '}' inside main before main ends
        s5 = os.path.join(tmpdir, "col0_brace.glsl")
        with open(s5, "w", encoding="utf-8") as f:
            f.write("#version 320 es\n// @duration 0.3\nvoid main() {\n    if (true) {\n        discard;\n}\n    fragColor = vec4(1.0);\n}\n")
        try:
            validate_shader(s5)
            raise AssertionError("Negative control failed: accepted col-0 '}' inside main before main ends")
        except ValueError:
            pass

        # Neg 6: Missing closing brace for main()
        s6 = os.path.join(tmpdir, "unclosed_main.glsl")
        with open(s6, "w", encoding="utf-8") as f:
            f.write("#version 320 es\n// @duration 0.3\nvoid main() {\n    fragColor = vec4(1.0);\n")
        try:
            validate_shader(s6)
            raise AssertionError("Negative control failed: accepted unclosed main()")
        except ValueError:
            pass

        # Neg 7: Stray line starting with '}' on line 1 before void main()
        s7 = os.path.join(tmpdir, "line1_brace.glsl")
        with open(s7, "w", encoding="utf-8") as f:
            f.write("}\n#version 320 es\n// @duration 0.3\nvoid main() {\n    fragColor = vec4(1.0);\n}\n")
        try:
            validate_shader(s7)
            raise AssertionError("Negative control failed: accepted '}' on line 1 before main")
        except ValueError:
            pass

        # Neg 8: Stray line starting with '}' after @duration but before void main()
        s8 = os.path.join(tmpdir, "pre_main_brace.glsl")
        with open(s8, "w", encoding="utf-8") as f:
            f.write("#version 320 es\n// @duration 0.5\n}\nvoid main() {\n    fragColor = vec4(1.0);\n}\n")
        try:
            validate_shader(s8)
            raise AssertionError("Negative control failed: accepted '}' before void main()")
        except ValueError:
            pass



def main() -> int:
    print("[test-hyprland-shader-contract] Validating all shaders in usr/share/mios/hyprland/shaders/...")
    test_positive_control()
    print("[test-hyprland-shader-contract] Running negative controls on invalid shader fixtures...")
    test_negative_controls()
    print("[test-hyprland-shader-contract] PASS: All shaders satisfy contract.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
