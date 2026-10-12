#!/usr/bin/env python3
# AI-hint: Unit tests for the comment lexer and classifier -- one fixture per classifier rule so every rule is proven to fire, plus lexer tests f...
# AI-doc: usr/share/doc/mios/manual/mios.md
"""Fixtures for mios_comments.

Every classifier rule gets at least one fixture that asserts the exact
(cls, reason) pair. A rule with no fixture is a rule nobody has proven fires --
this repo has a documented history of checks that could not fail, so the bar
here is that each rule is demonstrated, not merely written.

Runs standalone (python3 test_mios_comments.py) so it needs no pytest in the
bake image.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mios_comments as mc  # noqa: E402

FAILED: list[str] = []
PASSED = 0

def check(name, got, want):
    global PASSED
    if got == want:
        PASSED += 1
    else:
        FAILED.append(f"{name}: got {got!r}, want {want!r}")

def policy() -> mc.Policy:
    """Policy from the real SSOT -- the test must exercise shipped values."""
    import tomllib
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    with open(os.path.join(root, "usr", "share", "mios", "mios.toml"), "rb") as fh:
        return mc.Policy.from_toml(tomllib.load(fh))

def blk(text, *, kind="line", path="automation/x.sh", in_header=False):
    lines = text.splitlines()
    return mc._mk(path, 1, len(lines), kind, "#", lines,
                  "pre-code", "echo hi", in_header)

def test_classifier(p):
    # R0 generated artifact -- never a migration source
    check("R0", mc.classify(blk("a long narrative about the operator and a regression",
                                path="automation/lib/globals.sh"), p).reason,
          "generated-artifact")

    # R1 LLM payload docstring is READONLY
    check("R1", mc.classify(blk("You are a helpful assistant. " * 12,
                                kind="docstring",
                                path="usr/share/mios/ai/system.py"), p).cls,
          "READONLY")

    # R2 header within cap stays; over cap migrates the overflow
    check("R2-stay", mc.classify(blk("AI-hint: short purpose line", in_header=True), p).reason,
          "ai-header")
    check("R2-over", mc.classify(blk("AI-hint: " + ("x" * 4000), in_header=True), p).reason,
          "overlong-hint")

    # R3 commented-out code
    check("R3", mc.classify(blk("if [ -f x ]; then\nreturn 1\nfi\ndone"), p).reason,
          "commented-out-code")

    # R4 banner: pure rule, and a banner carrying a fact
    check("R4-drop", mc.classify(blk("-----------------------------"), p).reason, "banner")
    # A banner that carries a FACT is migrated as a heading, not dropped.
    # Must be in label form -- bare lowercase "deprecated" is an ordinary short
    # comment and correctly stays put under the R4 narrowing.
    check("R4-fact", mc.classify(blk("DEPRECATED"), p).as_, "heading-fact")

    # R5a local-scoped short comment stays
    check("R5a", mc.classify(blk("bump the retry count"), p).reason, "local-scoped")

    # R5c long narrative migrates, and a very long one is an ADR candidate.
    # Must be MULTI-LINE: a single long line is L=1, which R5b claims first as
    # fat-inline-narrative.
    long_narr = "\n".join(
        ["The operator hit a regression here previously.",
         "The root cause was a race between the firstboot unit and the pod.",
         "This was reverted once, then reinstated with a different invariant.",
         "See ADR-0004 and Law 8 for why the projection must not be bypassed.",
         "Previously the alternative was rejected because it degraded the lane.",
         "The rationale is recorded here so the next reader does not repeat it.",
         "An incident followed; the operator asked for the guard to stay."] * 6)
    v = mc.classify(blk(long_narr), p)
    check("R5c-cls", v.cls, "MIGRATE")
    check("R5c-reason", v.reason, "narrative-history")
    check("R5c-adr", v.as_, "adr-candidate")

    # R5c non-narrative long block is rationale, not history
    long_plain = "\n".join(["This routine converts the payload into the wire format."] * 8)
    check("R5c-rationale", mc.classify(blk(long_plain), p).reason, "narrative-rationale")

    # R4 must NOT swallow ordinary short comments (the narrowing above)
    check("R4-not-banner", mc.classify(blk("bump the retry count"), p).cls, "STAY")
    check("R4-real-banner", mc.classify(blk("SECTION: PORTS"), p).cls, "DROP")

    # R5d mid-size band: narrative migrates, plain why stays
    mid_narr = "Reverted previously.\nThe operator asked for it.\nSee the incident."
    check("R5d-narr", mc.classify(blk(mid_narr), p).reason, "midsize-narrative")
    mid_plain = "step one here\nstep two here\nstep three here"
    check("R5d-stay", mc.classify(blk(mid_plain), p).reason, "midsize-why")

    # R6 inline comments stay
    check("R6", mc.classify(blk("guard against zero", kind="inline"), p).reason,
          "inline-scoped")

def test_hint_prose_len():
    """The hint cap must measure prose, and must not be dodged by wrapping."""
    nl = chr(10)
    banner = "# AI-hint: short one" + nl + "# /usr/share/mios/x.container" + nl
    check("hint-banner-excluded", mc._hint_prose_len(banner) < 40, True)
    wrapped = "# AI-hint: " + "word " * 40 + nl + "# " + "more prose here " * 20 + nl
    check("hint-wrap-counted", mc._hint_prose_len(wrapped) > 400, True)
    meta = "# AI-hint: short" + nl + "# AI-related: a, b, c, d, e, f, g, h" + nl
    check("hint-meta-excluded", mc._hint_prose_len(meta) < 30, True)

def test_stale(p):
    # The resolver's findings are per file: only the block in that file is stale.
    idx = mc.RefIndex("", {"automation/x.sh": {"tools/gone.py"}})
    gone = blk("AI-related: tools/gone.py")
    check("stale-named", mc.classify(gone, p, idx).stale, True)
    elsewhere = blk("AI-related: tools/gone.py", path="automation/y.sh")
    check("stale-other-file", mc.classify(elsewhere, p, idx).stale, False)
    check("stale-unnamed", mc.classify(blk("AI-related: tools/ok.py"), p, idx).stale, False)
    check("stale-no-index", mc.classify(gone, p).stale, False)

def test_heredoc():
    src = "# outer\ncat <<'EOF'\n# inner\nEOF\n# usage: x <<EOF\n\n# kept\n"
    texts = [b.text for b in mc._lex_generic("t.sh", src, "#")]
    check("heredoc-body-skipped", texts, ["outer", "usage: x <<EOF", "kept"])

def test_lexer():
    src = (
        "#!/usr/bin/env python3\n"
        "# AI-hint: header line\n"
        "\n"
        '"""Module docstring."""\n'
        "\n"
        "# a block comment\n"
        "# spanning two lines\n"
        "x = 1  # trailing inline\n"
    )
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "sample.py")
        open(p, "w", encoding="utf-8").write(src)
        blocks = mc.lex(p)
    kinds = [b.kind for b in blocks]
    check("lex-has-docstring", "docstring" in kinds, True)
    check("lex-has-inline", "inline" in kinds, True)
    two = [b for b in blocks if b.kind == "line" and b.lines == 2]
    check("lex-merges-run", len(two) >= 1, True)
    # A trailing comment must never be swallowed into the preceding block.
    inline = [b for b in blocks if b.kind == "inline"]
    check("lex-inline-text", inline[0].text.strip(), "trailing inline")
    # Same text hashes the same regardless of surrounding whitespace.
    a = blk("Same  Text\nhere")
    b = blk("same text here")
    check("hash-normalised", a.sha12, b.sha12)

def test_html_endings():
    for end in ("-->", "--!>"):
        check("html-strip-" + end, mc._strip("<!-- comment " + end), "comment")
        blocks = mc._lex_generic("x.md", "<!-- first " + end + "\ntext\n<!-- second " + end + "\n", "<!--")
        check("html-count-" + end, len(blocks), 2)
        check("html-bound-" + end, (blocks[0].start_line, blocks[0].end_line), (1, 1))
        check("html-following-" + end, blocks[1].text, "second")
    blocks = mc._lex_generic("x.md", "<!-- first --!\nsecond --!>\ntext\n", "<!--")
    check("html-incomplete-does-not-close", len(blocks), 1)
    check("html-multiline-end", blocks[0].end_line, 2)
    check("html-incomplete-is-content", "first --!" in blocks[0].text, True)


def test_landing_ratio(p: mc.Policy) -> None:
    """Guards mios-manual landed(), which raised AttributeError without it."""
    check("landing-ratio-present", hasattr(p, "landing_min_word_ratio"), True)
    check("landing-ratio-from-ssot", p.landing_min_word_ratio, 0.90)
    check("landing-ratio-is-float", isinstance(p.landing_min_word_ratio, float), True)
    # A default-constructed Policy must carry the contract shape too.
    check("landing-ratio-default", mc.Policy().landing_min_word_ratio, 0.90)

def main() -> int:
    p = policy()
    test_classifier(p)
    test_hint_prose_len()
    test_stale(p)
    test_heredoc()
    test_lexer()
    test_html_endings()
    test_landing_ratio(p)
    print(f"[test_mios_comments] {PASSED} passed, {len(FAILED)} failed")
    for f in FAILED:
        print(f"  FAIL {f}")
    return 1 if FAILED else 0

if __name__ == "__main__":
    sys.exit(main())
