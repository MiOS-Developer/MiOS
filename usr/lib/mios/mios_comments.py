#!/usr/bin/env python3
# AI-hint: The MiOS comment lexer and classifier -- extracts comment blocks from any source file and decides, deterministically, whether each block ST...
# AI-doc: usr/share/doc/mios/manual/mios.md
from __future__ import annotations

import ast
import hashlib
import io
import os
import sys
import re
import subprocess
import tokenize
from dataclasses import dataclass, field
from importlib.machinery import SourceFileLoader
from typing import Iterable

__all__ = ["Block", "Verdict", "Policy", "RefIndex", "lex", "classify", "load_ai_tag",
           "SCAN_EXT", "iter_source_files", "tracked_file_modes"]

# Extensions the corpus covers. One definition, so the gate and the CLI cannot
# disagree about what was counted.
SCAN_EXT = (".py", ".sh", ".bash", ".toml", ".ps1", ".psm1", ".rs", ".service",
            ".container", ".timer", ".socket", ".target", ".conf", ".yml", ".yaml")
_SKIP_DIRS = {".git", "target", "node_modules", "__pycache__", ".venv", ".rustup", ".cargo", "output"}

def _is_libexec_verb(root: str, rel: str) -> bool:
    if not ((rel.startswith("usr/libexec/mios/") or "/usr/libexec/mios/" in rel) and "." not in rel.rsplit("/", 1)[-1]):
        return False
    full = os.path.join(root, rel.replace("/", os.sep))
    try:
        with open(full, "rb") as fh:
            h = fh.read(1024); return h.startswith(b"#!") and b"\x00" not in h
    except OSError:
        return False

def tracked_file_modes(root: str) -> dict[str, str]:
    """Read index modes so link placeholders and real links have one census."""
    git_entry = os.path.join(root, ".git")
    if not os.path.lexists(git_entry):
        # Standalone source fixtures have no Git index. Never follow their links.
        modes = {}
        for dp, dn, fns in os.walk(root):
            dn[:] = [d for d in dn if d not in _SKIP_DIRS and not d.startswith(".")]
            for fn in fns:
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                modes[rel] = "120000" if os.path.islink(full) else "100644"
        return modes

    # It has a .git entry, so it is a git checkout. Read its index with sanitized environment.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        out = subprocess.run(
            ["git", "-c", "core.ignorecase=false", "ls-files", "--stage", "-z"],
            cwd=root, capture_output=True, check=True, env=env,
        )
    except (OSError, subprocess.CalledProcessError):
        raise RuntimeError("cannot read the tracked source index") from None
    modes = {}
    for record in out.stdout.decode("utf-8").split("\0"):
        if not record:
            continue
        metadata, rel = record.split("\t", 1)
        mode, _, stage = metadata.split()
        if stage != "0":
            raise RuntimeError(f"unmerged source index entry: {rel}")
        modes[rel] = mode
    return modes

def iter_source_files(root: str):
    modes = tracked_file_modes(root)
    for rel in sorted(modes):
        if modes[rel] not in ("100644", "100755"):
            continue
        parts = set(rel.split("/"))
        if parts & _SKIP_DIRS:
            continue
        if not (rel.endswith(SCAN_EXT) or _is_libexec_verb(root, rel)):
            continue
        full = os.path.join(root, rel.replace("/", os.sep))
        if os.path.isfile(full) and not os.path.islink(full):
            yield rel, full

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))

# --------------------------------------------------------------------------
# mios-ai-tag interop -- the single definition of taggability + comment style
# --------------------------------------------------------------------------
def load_ai_tag(root: str | None = None):
    base = root or _REPO_ROOT
    path = os.path.join(base, "usr", "libexec", "mios", "mios-ai-tag")
    if not os.path.isfile(path):
        return None
    try:
        return SourceFileLoader("mios_ai_tag", path).load_module()
    except Exception:
        return None

# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Block:
    path: str
    start_line: int          # 1-indexed, inclusive
    end_line: int            # 1-indexed, inclusive
    kind: str                # line | docstring | inline | blockcomment
    style: str               # '#' | '//' | '<!--' | ';' ...
    text: str                # markers stripped, newlines kept
    norm: str                # lowercased, whitespace-collapsed -- hashing input
    sha12: str
    lines: int
    words: int
    attach: str              # file-header | pre-code | inline | orphan
    anchor_code: str         # first following non-blank non-comment line
    in_header_block: bool

@dataclass(frozen=True)
class Verdict:
    cls: str                 # STAY | MIGRATE | DROP | READONLY | MIGRATE_HEADER
    reason: str              # the one rule that fired
    stale: bool
    as_: str = ""            # "" | note | heading-fact | adr-candidate

@dataclass
class Policy:
    """Thresholds and signal patterns, all from mios.toml [docs]."""
    stay_max_lines: int = 2
    stay_max_words: int = 25
    migrate_min_lines: int = 6
    migrate_min_words: int = 60
    hint_max_chars: int = 400
    # Share of a block's words a doc passage must retain before mios-manual's
    # landed() will call the knowledge landed -- the predicate that authorises
    # deleting the source comment. Without it landed() raised AttributeError,
    # so nothing could ever be pruned.
    landing_min_word_ratio: float = 0.90
    blocklist_globs: tuple[str, ...] = ()
    llm_payload_globs: tuple[str, ...] = ()
    ref_allowlist: tuple[str, ...] = ()
    sig_why: tuple[str, ...] = ()
    sig_narrative: tuple[str, ...] = ()
    sig_fact: tuple[str, ...] = ()
    sig_code: tuple[str, ...] = ()
    low_quality_hints: tuple[str, ...] = ()

    _rx: dict = field(default_factory=dict, repr=False, compare=False)

    # Defaults are declared here ONLY as the shape of the contract; the real
    # values live in mios.toml. from_toml overrides everything it finds.
    @staticmethod
    def _as_tuple(v) -> tuple[str, ...]:
        """A TOML value that may be one string or a list of them.

        tuple("abc") is ("a","b","c"), so a bare-string signal silently became a
        per-character alternation and compiled to nonsense. Wrap scalars.
        """
        if v is None:
            return ()
        if isinstance(v, str):
            return (v,)
        return tuple(str(x) for x in v)

    @classmethod
    def from_toml(cls, merged: dict) -> "Policy":
        d = (merged or {}).get("docs", {}) or {}
        sig = d.get("signals", {}) or {}
        ai = (merged or {}).get("ai_tag", {}) or {}
        T = cls._as_tuple
        p = cls(
            stay_max_lines=int(d.get("stay_max_lines", cls.stay_max_lines)),
            stay_max_words=int(d.get("stay_max_words", cls.stay_max_words)),
            migrate_min_lines=int(d.get("migrate_min_lines", cls.migrate_min_lines)),
            migrate_min_words=int(d.get("migrate_min_words", cls.migrate_min_words)),
            hint_max_chars=int(ai.get("hint_max_chars", cls.hint_max_chars)),
            landing_min_word_ratio=float(
                d.get("landing_min_word_ratio", cls.landing_min_word_ratio)),
            blocklist_globs=T(d.get("blocklist_globs")),
            llm_payload_globs=T(d.get("llm_payload_globs")),
            ref_allowlist=T(d.get("ref_allowlist")),
            sig_why=T(sig.get("why")),
            sig_narrative=T(sig.get("narrative")),
            sig_fact=T(sig.get("fact")),
            sig_code=T(sig.get("code")),
            low_quality_hints=T(d.get("low_quality_hints")),
        )
        p._compile()
        return p

    def _compile(self) -> None:
        def alt(pats: Iterable[str]):
            pats = [p for p in pats if p]
            return re.compile("|".join(pats), re.I) if pats else None
        self._rx = {
            "why": alt(self.sig_why),
            "narrative": alt(self.sig_narrative),
            "fact": alt(self.sig_fact),
            "code": alt(self.sig_code),
        }

    def matches(self, which: str, text: str) -> bool:
        if not self._rx:
            self._compile()
        rx = self._rx.get(which)
        return bool(rx and rx.search(text))

# --------------------------------------------------------------------------
# Reference resolution -- owned by doc_refs.rs in Rust (Law 14 / task_3395)
# --------------------------------------------------------------------------
class RefIndex:
    """Client of `mios-gate doc-refs-headers`, which resolves every AI header
    path; a block is stale when it names one that did not resolve."""

    def __init__(self, root: str = "", stale: dict[str, set[str]] | None = None) -> None:
        self.root = root
        self.stale = stale or {}

    @staticmethod
    def gate_bin(root: str) -> str | None:
        import shutil
        exe = "mios-gate.exe" if os.name == "nt" else "mios-gate"
        native_dir = os.environ.get("MIOS_NATIVE_BIN_DIR")
        for cand in (os.environ.get("MIOS_GATE_BIN"),
                     native_dir and os.path.join(native_dir, exe),
                     os.path.join(root, "src", "mios-rs", "target", "release", exe),
                     os.path.join(root, "src", "mios-rs", "target", "debug", exe),
                     os.path.join("/usr/libexec/mios", exe)):
            if cand and os.path.isfile(cand):
                return cand
        return None if native_dir else shutil.which(exe)

    @classmethod
    def build(cls, root: str, skip_dirs: Iterable[str] = ()) -> "RefIndex":
        import json
        exe = cls.gate_bin(root)
        if not exe:
            raise RuntimeError("mios-gate is not built, so no reference was resolved")
        proc = subprocess.run([exe, "doc-refs-headers", "--root", root, "--format", "json"],
                              capture_output=True, text=True)
        try:
            data = json.loads(proc.stdout)
        except ValueError:
            raise RuntimeError(f"{exe} doc-refs-headers returned no report"
                               f" (rc={proc.returncode}): {proc.stderr.strip()[:300]}") from None
        if data.get("status") == "could_not_run":
            raise RuntimeError(f"doc-refs-headers could not run: {data.get('summary')}")
        stale: dict[str, set[str]] = {}
        for finding in data.get("findings", []):
            rel, sep, target = finding.partition("\t")
            if sep:
                stale.setdefault(rel, set()).add(target)
        return cls(root, stale)

    def dangling(self, text: str, allowlist: Iterable[str] = (), path: str = "") -> list[str]:
        # The allowlist is applied by the resolver, which reads the same [docs] key.
        return sorted(t for t in self.stale.get(path, ()) if t in text)

# --------------------------------------------------------------------------
# Lexing
# --------------------------------------------------------------------------
_AI_HINT_LINE = re.compile(r"\s*#?\s*AI-hint:")
_AI_KEY_LINE = re.compile(r"\s*#?\s*AI-[a-z]+:")

def _hint_prose_len(text: str) -> int:
    total = 0
    in_hint = False
    for line in text.splitlines():
        if _AI_HINT_LINE.match(line):
            in_hint = True
            total += len(line) + 1
            continue
        if _AI_KEY_LINE.match(line):
            in_hint = False
            continue
        if in_hint:
            # Only PROSE continues a hint. Generated quadlet headers put a bare
            # path banner under the hint ("# /usr/share/containers/systemd/x"),
            # which is one token and no more prose than the shebang above it.
            body = line.lstrip().lstrip("#").strip()
            # A banner ends the hint. `# --- Section ---` is a divider, not a
            # continuation, and counting it swallowed every note beneath it --
            # a 89-character hint measured as 377.
            if body.startswith(("---", "===", "***")) or set(body) <= set("-=*_ "):
                in_hint = False
                continue
            if len(body.split()) >= 3:
                total += len(line) + 1
            else:
                in_hint = False
    return total

# `<<EOF`, `<<-'PY'`, `<<"SQL"` -- captures the terminator so the body can be
# skipped. Not matched when it is itself inside a comment.
_HEREDOC = re.compile(r"(?<!\S)<<-?\s*(?P<tag>'[A-Za-z_][A-Za-z0-9_]*'"
                      r'|"[A-Za-z_][A-Za-z0-9_]*"'
                      r"|[A-Za-z_][A-Za-z0-9_]*)")
_MARKER = re.compile(r"^\s*(?:#+|//+|;+|--|<!--|\*|/\*)\s?")
_HTML_END = re.compile(r"--!?>")
_END_MARKER = re.compile(r"\s*(?:--!?>|\*/)\s*$")
_WORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./:-]*")

_STYLE_BY_EXT = {
    ".py": "#", ".sh": "#", ".bash": "#", ".toml": "#", ".yml": "#", ".yaml": "#",
    ".ps1": "#", ".psm1": "#", ".service": "#", ".container": "#", ".timer": "#",
    ".socket": "#", ".target": "#", ".conf": "#", ".nft": "#", ".cfg": "#",
    ".rs": "//", ".go": "//", ".c": "//", ".h": "//", ".cs": "//", ".ts": "//",
    ".js": "//", ".tsx": "//", ".mjs": "//",
    ".md": "<!--", ".html": "<!--", ".xml": "<!--",
}

def _style_for(path: str, ai_tag=None) -> str:
    if ai_tag is not None:
        for fname in ("comment_style", "style_for", "_comment_style"):
            fn = getattr(ai_tag, fname, None)
            if callable(fn):
                try:
                    s = fn(path)
                    if s:
                        return s[0] if isinstance(s, (tuple, list)) else s
                except Exception:
                    pass
    return _STYLE_BY_EXT.get(os.path.splitext(path)[1].lower(), "#")

def _strip(line: str) -> str:
    return _END_MARKER.sub("", _MARKER.sub("", line)).rstrip()

def _mk(path, start, end, kind, style, body_lines, attach, anchor, in_header) -> Block:
    text = "\n".join(body_lines)
    norm = re.sub(r"\s+", " ", text.lower()).strip()
    words = len(_WORD.findall(text))
    return Block(
        path=path, start_line=start, end_line=end, kind=kind, style=style,
        text=text, norm=norm,
        sha12=hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12],
        lines=len(body_lines), words=words, attach=attach,
        anchor_code=anchor, in_header_block=in_header,
    )

# Files whose Python lex degraded to the regex lexer (or lost docstring blocks)
# in this process. Whether that happens depends on the INTERPRETER, not the
# file: PEP 701 sources parse on 3.12+ and not before, so the same tree would
# otherwise yield a different census per machine. mios-manual's ledger refuses
# to emit an artifact while this is non-empty.
DEGRADED: list[str] = []

def _lex_python(path: str, src: str) -> list[Block]:
    out: list[Block] = []
    lines = src.splitlines()
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        DEGRADED.append(path)   # interpreter-dependent: see DEGRADED
        return _lex_generic(path, src, "#")

    run: list[str] = []
    run_start = 0
    prev_end = 0
    for t in toks:
        if t.type == tokenize.COMMENT:
            row = t.start[0]
            first_on_line = not lines[row - 1][: t.start[1]].strip()
            if not first_on_line:
                out.append(_mk(path, row, row, "inline", "#", [_strip(t.string)],
                               "inline", lines[row - 1].strip(), False))
                continue
            if run and row == prev_end + 1:
                run.append(_strip(t.string))
            else:
                if run:
                    out.append(_finish_run(path, run, run_start, prev_end, "#", lines))
                run, run_start = [_strip(t.string)], row
            prev_end = row
    if run:
        out.append(_finish_run(path, run, run_start, prev_end, "#", lines))

    # Docstrings via ast -- module, classes, functions.
    try:
        tree = ast.parse(src)
    except SyntaxError:
        DEGRADED.append(path)   # drops docstring blocks: see DEGRADED
        return out
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        body0 = node.body[0]
        start = getattr(body0, "lineno", 1)
        end = getattr(body0, "end_lineno", start)
        out.append(_mk(path, start, end, "docstring", '"""', doc.splitlines(),
                       "file-header" if isinstance(node, ast.Module) else "pre-code",
                       "", False))
    out.sort(key=lambda b: (b.start_line, b.end_line))
    return out

def _finish_run(path, run, start, end, style, lines) -> Block:
    anchor = ""
    for l in lines[end:]:
        if l.strip() and not _MARKER.match(l):
            anchor = l.strip()
            break
    attach = "file-header" if start <= 3 else ("pre-code" if anchor else "orphan")
    in_header = start <= 6 and any("AI-hint" in x or "AI-related" in x or
                                   "AI-functions" in x for x in run)
    return _mk(path, start, end, "line", style, run, attach, anchor, in_header)

def _lex_generic(path: str, src: str, style: str) -> list[Block]:
    lines = src.splitlines()
    out: list[Block] = []
    run: list[str] = []
    run_start = 0

    def flush(end_idx: int):
        nonlocal run, run_start
        if run:
            out.append(_finish_run(path, run, run_start, end_idx, style, lines))
            run = []

    in_block = False
    block_lines: list[str] = []
    block_start = 0
    heredoc_end: str | None = None
    for i, raw in enumerate(lines, 1):
        s = raw.strip()

        # A heredoc BODY is data, not source: '#' lines inside one are Python
        # comments in an embedded script, or plain text. Counting them attributes
        # another language's comments to this file and inflates the census --
        # 98-drift-checks.sh alone embeds many Python heredocs.
        if heredoc_end is not None:
            if s == heredoc_end or s == heredoc_end + "'":
                heredoc_end = None
            continue
        m = None if raw.lstrip().startswith(style) else _HEREDOC.search(raw)
        if m:
            flush(i - 1)
            heredoc_end = m.group("tag").strip("'\"")
            continue
        if style == "<!--":
            if not in_block and s.startswith("<!--"):
                in_block, block_start, block_lines = True, i, [_strip(raw)]
                if _HTML_END.search(s):
                    in_block = False
                    out.append(_mk(path, block_start, i, "blockcomment", style,
                                   block_lines, "file-header" if i <= 3 else "orphan",
                                   "", "AI-hint" in raw))
                continue
            if in_block:
                block_lines.append(_strip(raw))
                if _HTML_END.search(s):
                    in_block = False
                    out.append(_mk(path, block_start, i, "blockcomment", style,
                                   block_lines,
                                   "file-header" if block_start <= 3 else "orphan",
                                   "", any("AI-hint" in x for x in block_lines)))
                continue
            continue

        if s and _MARKER.match(raw) and raw.lstrip().startswith(style):
            if not run:
                run_start = i
            run.append(_strip(raw))
            continue
        # A trailing comment on a code line is inline, never a block.
        if style in ("#", "//") and style in raw and not raw.lstrip().startswith(style):
            idx = raw.find(style)
            if idx > 0 and raw[:idx].strip():
                flush(i - 1)
                out.append(_mk(path, i, i, "inline", style, [_strip(raw[idx:])],
                               "inline", raw[:idx].strip(), False))
                continue
        flush(i - 1)
    flush(len(lines))
    return out

def _find_native_comment_lex() -> str | None:
    bin_name = "mios-comment-lex.exe" if os.name == "nt" else "mios-comment-lex"
    for candidate in [
        os.environ.get("MIOS_COMMENT_LEX_BIN"),
        os.path.join(_REPO_ROOT, "tools", "native", "target", "release", bin_name),
        os.path.join(_REPO_ROOT, "tools", "native", "target", "debug", bin_name),
        os.path.join(_REPO_ROOT, "usr", "bin", bin_name),
    ]:
        if candidate and os.path.isfile(candidate):
            return candidate
    import shutil
    return shutil.which(bin_name)

def lex(path: str, raw: bytes | None = None, ai_tag=None) -> list[Block]:
    """Comment blocks in one file.

    A block is a maximal run of consecutive full-line comments; a blank line, a
    code line, or a style change ends it.
    """
    # Never the native binary: the census must not vary with what is on PATH.
    if raw is None:
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return []
    src = raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
    style = _style_for(path, ai_tag)
    if path.endswith(".py") or (src.startswith("#!") and "python" in src.splitlines()[0]):
        return _lex_python(path, src)
    return _lex_generic(path, src, style)

# --------------------------------------------------------------------------
# Classification -- ordered, first match wins, exactly one reason
# --------------------------------------------------------------------------
_BANNER_ONLY = re.compile(r"^[\s\-=*_~+.#]*$")
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")

def _glob_any(path: str, globs: Iterable[str]) -> bool:
    from fnmatch import fnmatch
    p = path.replace(os.sep, "/")
    return any(fnmatch(p, g) or g.strip("*") in p for g in globs if g)

def classify(block: Block, policy: Policy, refindex: RefIndex | None = None) -> Verdict:
    """Exactly one verdict, from the first rule that fires (spec section 2.2)."""
    text = block.text
    stripped = [l for l in text.splitlines() if l.strip()]
    L, W = block.lines, block.words

    # R7 is an axis, not a class: evaluated for every block.
    stale = bool(refindex and refindex.dangling(text, policy.ref_allowlist, block.path))

    # R0 GENERATED-SOURCE -- extracting from an artifact multi-counts the source.
    if _glob_any(block.path, policy.blocklist_globs):
        return Verdict("DROP", "generated-artifact", stale)

    # R1 READONLY-PAYLOAD -- this string is sent to a model; never edit it.
    if block.kind == "docstring" and _glob_any(block.path, policy.llm_payload_globs):
        return Verdict("READONLY", "llm-payload", stale)

    # R2 HEADER
    if block.in_header_block:
        hint_len = _hint_prose_len(text)
        if (hint_len or len(text)) > policy.hint_max_chars:
            return Verdict("MIGRATE_HEADER", "overlong-hint", stale)
        return Verdict("STAY", "ai-header", stale)

    # R3 COMMENTED-OUT CODE
    if stripped:
        codey = sum(1 for l in stripped if policy.matches("code", l))
        if codey * 2 >= len(stripped) and (W / max(L, 1)) < 4:
            return Verdict("DROP", "commented-out-code", stale)

    why = policy.matches("why", text)
    flat = " ".join(stripped).strip()
    looks_like_label = bool(
        flat
        and (flat.isupper()
             or flat.endswith(":")
             or all(w[:1].isupper() for w in flat.split() if w[:1].isalpha()))
    )
    if stripped and (all(_BANNER_ONLY.match(l) for l in stripped)
                     or (W <= 8 and not _SENTENCE_END.search(text)
                         and not why and looks_like_label)):
        if policy.matches("fact", text):
            return Verdict("MIGRATE", "banner-fact", stale, "heading-fact")
        return Verdict("DROP", "banner", stale)

    narr = policy.matches("narrative", text)

    # R6 INLINE (checked before R5's default so a trailing comment is never
    # deleted; R5(b) may still promote a fat one to MIGRATE below).
    if block.kind == "inline" and not (W > policy.stay_max_words and narr):
        return Verdict("STAY", "inline-scoped", stale)

    # R5 SIZE + SIGNAL -- the core split.
    if L <= policy.stay_max_lines and W <= policy.stay_max_words:
        return Verdict("STAY", "local-scoped", stale)
    if L <= policy.stay_max_lines and W > policy.stay_max_words and narr:
        return Verdict("MIGRATE", "fat-inline-narrative", stale, "note")
    if L >= policy.migrate_min_lines or W >= policy.migrate_min_words:
        as_ = "adr-candidate" if (narr and W >= 250) else ""
        return Verdict("MIGRATE",
                       "narrative-history" if narr else "narrative-rationale",
                       stale, as_)
    if narr:
        return Verdict("MIGRATE", "midsize-narrative", stale)
    return Verdict("STAY", "midsize-why", stale)
