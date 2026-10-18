#!/usr/bin/env python3
# AI-hint: Tree-Sitter / AST semantic merge resolver for multi-agent git conflicts across Python, TOML, JSON, and Shell.
# AI-related: usr/libexec/mios/git/ast_merge.py, tests/test-ast-merge.py, usr/libexec/mios/git/reconcile_dag.py
"""AST Semantic Merge Resolver for Multi-Agent Git Conflicts (task_499).

Performs 3-way abstract syntax tree (AST) semantic merge resolution across
concurrent agent edits in Python, TOML, JSON, and Shell scripts, avoiding
spurious line-based git conflicts for independent additions, reordered imports,
and disjoint function/class declarations.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass, field
import hashlib
import json
import logging
import os
import re
import sys
import tomllib
from typing import Any, Dict, List, Optional, Set, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mios-ast-merge")

@dataclass
class AstNodeInfo:
    """Metadata representing a parsed top-level syntactic unit."""
    node_type: str  # "import", "function", "class", "assign", "table", "raw"
    identifier: str
    source_code: str
    content_hash: str
    raw_node: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_type": self.node_type,
            "identifier": self.identifier,
            "source_code": self.source_code,
            "content_hash": self.content_hash,
        }

@dataclass
class MergeResult:
    """Result of an AST-based 3-way semantic merge."""
    status: str  # "success", "conflict", "clean_fallback"
    merged_content: str
    conflict_nodes: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=lambda: {"added": 0, "modified": 0, "retained": 0})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

class AstMergeResolver:
    """Semantic 3-way merge resolver utilizing language AST structures."""

    def __init__(self, mock: bool = False) -> None:
        self.mock = mock

    def merge(
        self,
        base_content: str,
        ours_content: str,
        theirs_content: str,
        filename: str = "file.py",
        lang: Optional[str] = None,
    ) -> MergeResult:
        """Performs 3-way semantic merge on base, ours, and theirs file contents."""
        if not lang or lang == "auto":
            lang = self._detect_language(filename)

        if lang == "python":
            return self._merge_python(base_content, ours_content, theirs_content)
        elif lang == "toml":
            return self._merge_toml(base_content, ours_content, theirs_content)
        elif lang == "json":
            return self._merge_json(base_content, ours_content, theirs_content)
        elif lang == "shell":
            return self._merge_shell(base_content, ours_content, theirs_content)
        else:
            return self._merge_line_fallback(base_content, ours_content, theirs_content)

    def _detect_language(self, filename: str) -> str:
        """Determines target language from file extension."""
        ext = os.path.splitext(filename)[1].lower()
        if ext in (".py", ".pyi"):
            return "python"
        elif ext in (".toml",):
            return "toml"
        elif ext in (".json",):
            return "json"
        elif ext in (".sh", ".bash", ".zsh"):
            return "shell"
        return "text"

    def _hash(self, content: str) -> str:
        return hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:16]

    def _parse_python_nodes(self, code: str) -> Dict[str, AstNodeInfo]:
        """Parses Python source into mapped top-level AST nodes."""
        nodes: Dict[str, AstNodeInfo] = {}
        if not code.strip():
            return nodes

        tree = ast.parse(code)

        for stmt in tree.body:
            if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                src = ast.unparse(stmt)
                # Group imports or index by statement text
                key = f"import::{src}"
                nodes[key] = AstNodeInfo(
                    node_type="import",
                    identifier=src,
                    source_code=src,
                    content_hash=self._hash(src),
                    raw_node=stmt,
                )
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                src = ast.unparse(stmt)
                key = f"func::{stmt.name}"
                nodes[key] = AstNodeInfo(
                    node_type="function",
                    identifier=stmt.name,
                    source_code=src,
                    content_hash=self._hash(src),
                    raw_node=stmt,
                )
            elif isinstance(stmt, ast.ClassDef):
                src = ast.unparse(stmt)
                key = f"class::{stmt.name}"
                nodes[key] = AstNodeInfo(
                    node_type="class",
                    identifier=stmt.name,
                    source_code=src,
                    content_hash=self._hash(src),
                    raw_node=stmt,
                )
            elif isinstance(stmt, ast.Assign):
                src = ast.unparse(stmt)
                target_names = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
                target_str = ",".join(target_names) if target_names else "anon"
                key = f"assign::{target_str}::{self._hash(src)}"
                nodes[key] = AstNodeInfo(
                    node_type="assign",
                    identifier=target_str,
                    source_code=src,
                    content_hash=self._hash(src),
                    raw_node=stmt,
                )
            else:
                src = ast.unparse(stmt)
                key = f"stmt::{self._hash(src)}"
                nodes[key] = AstNodeInfo(
                    node_type="raw",
                    identifier="stmt",
                    source_code=src,
                    content_hash=self._hash(src),
                    raw_node=stmt,
                )

        return nodes

    def _merge_python(self, base_code: str, ours_code: str, theirs_code: str) -> MergeResult:
        """Performs 3-way AST merge for Python source code."""
        try:
            base_nodes = self._parse_python_nodes(base_code)
            ours_nodes = self._parse_python_nodes(ours_code)
            theirs_nodes = self._parse_python_nodes(theirs_code)
        except SyntaxError as e:
            logger.warning("Python syntax error while parsing AST for merge (%s); falling back to line merge", e)
            return self._merge_line_fallback(base_code, ours_code, theirs_code)

        if not ours_nodes and not theirs_nodes and not base_nodes:
            return MergeResult(status="success", merged_content="")

        all_keys = list(dict.fromkeys(list(base_nodes.keys()) + list(ours_nodes.keys()) + list(theirs_nodes.keys())))

        merged_body: List[ast.AST] = []
        conflicts: List[str] = []
        stats = {"added": 0, "modified": 0, "retained": 0}

        # Deduplicate imports first
        import_statements: Set[str] = set()

        for key in all_keys:
            in_base = key in base_nodes
            in_ours = key in ours_nodes
            in_theirs = key in theirs_nodes

            # Import handling
            if key.startswith("import::"):
                if in_ours:
                    import_statements.add(ours_nodes[key].source_code)
                elif in_theirs:
                    import_statements.add(theirs_nodes[key].source_code)
                elif in_base:
                    # If neither deleted it explicitly, keep
                    pass
                continue

            # Function / Class / Statement handling
            if in_ours and in_theirs:
                if ours_nodes[key].content_hash == theirs_nodes[key].content_hash:
                    # Identical in both
                    if not in_base:
                        stats["added"] += 1
                    else:
                        stats["retained"] += 1
                    merged_body.append(ours_nodes[key].raw_node)
                else:
                    # Both modified differently
                    if not in_base:
                        # Concurrent disjoint addition with same name -> conflict
                        conflicts.append(f"Concurrent conflicting addition: {key}")
                    elif ours_nodes[key].content_hash == base_nodes[key].content_hash:
                        # Ours didn't change, theirs changed -> accept theirs
                        merged_body.append(theirs_nodes[key].raw_node)
                        stats["modified"] += 1
                    elif theirs_nodes[key].content_hash == base_nodes[key].content_hash:
                        # Theirs didn't change, ours changed -> accept ours
                        merged_body.append(ours_nodes[key].raw_node)
                        stats["modified"] += 1
                    else:
                        # Both changed differently -> semantic conflict!
                        conflicts.append(f"Semantic conflict on node: {key}")
            elif in_ours and not in_theirs:
                if not in_base:
                    # Added in ours
                    merged_body.append(ours_nodes[key].raw_node)
                    stats["added"] += 1
                else:
                    # Deleted in theirs, kept in ours
                    if ours_nodes[key].content_hash == base_nodes[key].content_hash:
                        # Ours untouched, accept deletion
                        pass
                    else:
                        conflicts.append(f"Modify/Delete conflict: {key} (modified in ours, deleted in theirs)")
            elif in_theirs and not in_ours:
                if not in_base:
                    # Added in theirs
                    merged_body.append(theirs_nodes[key].raw_node)
                    stats["added"] += 1
                else:
                    # Deleted in ours, kept in theirs
                    if theirs_nodes[key].content_hash == base_nodes[key].content_hash:
                        # Theirs untouched, accept deletion
                        pass
                    else:
                        conflicts.append(f"Modify/Delete conflict: {key} (deleted in ours, modified in theirs)")

        if conflicts:
            return MergeResult(
                status="conflict",
                merged_content="",
                conflict_nodes=conflicts,
                stats=stats,
            )

        # Rebuild full module
        import_nodes = []
        for imp_src in sorted(list(import_statements)):
            try:
                imp_tree = ast.parse(imp_src)
                if imp_tree.body:
                    import_nodes.append(imp_tree.body[0])
            except Exception:
                pass

        final_module = ast.Module(body=import_nodes + merged_body, type_ignores=[])
        merged_code = ast.unparse(final_module)

        return MergeResult(
            status="success",
            merged_content=merged_code,
            conflict_nodes=[],
            stats=stats,
        )

    def _merge_toml(self, base_str: str, ours_str: str, theirs_str: str) -> MergeResult:
        """3-way dictionary merge for TOML configuration files."""
        try:
            ours_dict = tomllib.loads(ours_str) if ours_str.strip() else {}
            theirs_dict = tomllib.loads(theirs_str) if theirs_str.strip() else {}
            base_dict = tomllib.loads(base_str) if base_str.strip() else {}
        except Exception as e:
            return self._merge_line_fallback(base_str, ours_str, theirs_str)

        merged_dict, conflicts = self._dict_3way_merge(base_dict, ours_dict, theirs_dict)
        if conflicts:
            return MergeResult(status="conflict", merged_content="", conflict_nodes=conflicts)

        # Simple TOML renderer
        rendered = self._render_toml_dict(merged_dict)
        return MergeResult(status="success", merged_content=rendered, conflict_nodes=[], stats={"tables": len(merged_dict)})

    def _merge_json(self, base_str: str, ours_str: str, theirs_str: str) -> MergeResult:
        """3-way dictionary merge for JSON files."""
        try:
            ours_dict = json.loads(ours_str) if ours_str.strip() else {}
            theirs_dict = json.loads(theirs_str) if theirs_str.strip() else {}
            base_dict = json.loads(base_str) if base_str.strip() else {}
        except Exception:
            return self._merge_line_fallback(base_str, ours_str, theirs_str)

        merged_dict, conflicts = self._dict_3way_merge(base_dict, ours_dict, theirs_dict)
        if conflicts:
            return MergeResult(status="conflict", merged_content="", conflict_nodes=conflicts)

        return MergeResult(status="success", merged_content=json.dumps(merged_dict, indent=2), conflict_nodes=[])

    def _dict_3way_merge(self, base: Dict[str, Any], ours: Dict[str, Any], theirs: Dict[str, Any], path: str = "") -> Tuple[Dict[str, Any], List[str]]:
        """Recursively performs 3-way merge on dictionaries."""
        merged: Dict[str, Any] = {}
        conflicts: List[str] = []
        all_keys = set(base.keys()) | set(ours.keys()) | set(theirs.keys())

        for k in sorted(all_keys):
            cur_path = f"{path}.{k}" if path else k
            in_base = k in base
            in_ours = k in ours
            in_theirs = k in theirs

            if in_ours and in_theirs:
                if ours[k] == theirs[k]:
                    merged[k] = ours[k]
                elif isinstance(ours[k], dict) and isinstance(theirs[k], dict) and isinstance(base.get(k, {}), dict):
                    sub_m, sub_c = self._dict_3way_merge(base.get(k, {}), ours[k], theirs[k], cur_path)
                    merged[k] = sub_m
                    conflicts.extend(sub_c)
                elif isinstance(ours[k], list) and isinstance(theirs[k], list):
                    # List union with order preservation
                    base_l = base.get(k, []) if isinstance(base.get(k), list) else []
                    merged[k] = list(dict.fromkeys(base_l + ours[k] + theirs[k]))
                else:
                    if not in_base:
                        conflicts.append(f"Collision on new key '{cur_path}': {ours[k]} vs {theirs[k]}")
                    elif ours[k] == base[k]:
                        merged[k] = theirs[k]
                    elif theirs[k] == base[k]:
                        merged[k] = ours[k]
                    else:
                        conflicts.append(f"Conflicting edits on '{cur_path}': {ours[k]} vs {theirs[k]}")
            elif in_ours and not in_theirs:
                if not in_base:
                    merged[k] = ours[k]
                elif ours[k] != base[k]:
                    conflicts.append(f"Modify/Delete conflict on '{cur_path}'")
            elif in_theirs and not in_ours:
                if not in_base:
                    merged[k] = theirs[k]
                elif theirs[k] != base[k]:
                    conflicts.append(f"Modify/Delete conflict on '{cur_path}'")

        return merged, conflicts

    def _render_toml_dict(self, data: Dict[str, Any], indent: int = 0) -> str:
        """Helper to serialize dict to clean TOML."""
        lines = []
        tables = []
        for k, v in data.items():
            if isinstance(v, dict):
                tables.append((k, v))
            elif isinstance(v, str):
                lines.append(f'{k} = "{v}"')
            elif isinstance(v, bool):
                lines.append(f"{k} = {str(v).lower()}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k} = {v}")
            elif isinstance(v, list):
                lines.append(f"{k} = {json.dumps(v)}")

        for table_name, table_dict in tables:
            lines.append("")
            lines.append(f"[{table_name}]")
            for tk, tv in table_dict.items():
                if isinstance(tv, str):
                    lines.append(f'  {tk} = "{tv}"')
                elif isinstance(tv, bool):
                    lines.append(f"  {tk} = {str(tv).lower()}")
                elif isinstance(tv, (int, float)):
                    lines.append(f"  {tk} = {tv}")
                elif isinstance(tv, list):
                    lines.append(f"  {tk} = {json.dumps(tv)}")
        return "\n".join(lines) + "\n"

    def _merge_shell(self, base_str: str, ours_str: str, theirs_str: str) -> MergeResult:
        """Performs function-block extraction merge for shell scripts."""
        # Simple block-level merge for shell functions
        def extract_shell_funcs(code: str) -> Dict[str, str]:
            funcs: Dict[str, str] = {}
            pattern = re.compile(r"(function\s+([a-zA-Z0-9_-]+)|([a-zA-Z0-9_-]+)\s*\(\))\s*\{([^}]*)\}", re.MULTILINE)
            for m in pattern.finditer(code):
                fname = m.group(2) or m.group(3)
                funcs[fname] = m.group(0)
            return funcs

        ours_f = extract_shell_funcs(ours_str)
        theirs_f = extract_shell_funcs(theirs_str)
        base_f = extract_shell_funcs(base_str)

        all_funcs = set(ours_f.keys()) | set(theirs_f.keys()) | set(base_f.keys())
        merged_funcs = []
        conflicts = []

        for fn in sorted(all_funcs):
            in_ours = fn in ours_f
            in_theirs = fn in theirs_f
            in_base = fn in base_f

            if in_ours and in_theirs:
                if ours_f[fn] == theirs_f[fn]:
                    merged_funcs.append(ours_f[fn])
                elif in_base and ours_f[fn] == base_f[fn]:
                    merged_funcs.append(theirs_f[fn])
                elif in_base and theirs_f[fn] == base_f[fn]:
                    merged_funcs.append(ours_f[fn])
                else:
                    conflicts.append(f"Conflicting shell function implementation: {fn}")
            elif in_ours and not in_theirs:
                if not in_base or ours_f[fn] != base_f.get(fn):
                    merged_funcs.append(ours_f[fn])
            elif in_theirs and not in_ours:
                if not in_base or theirs_f[fn] != base_f.get(fn):
                    merged_funcs.append(theirs_f[fn])

        if conflicts:
            return MergeResult(status="conflict", merged_content="", conflict_nodes=conflicts)

        merged_content = "#!/usr/bin/env bash\n\n" + "\n\n".join(merged_funcs) + "\n"
        return MergeResult(status="success", merged_content=merged_content, conflict_nodes=[])

    def _merge_line_fallback(self, base_str: str, ours_str: str, theirs_str: str) -> MergeResult:
        """Line-based fallback merge."""
        if ours_str == theirs_str:
            return MergeResult(status="success", merged_content=ours_str)
        if ours_str == base_str:
            return MergeResult(status="success", merged_content=theirs_str)
        if theirs_str == base_str:
            return MergeResult(status="success", merged_content=ours_str)

        return MergeResult(
            status="conflict",
            merged_content="",
            conflict_nodes=["Generic line collision in unparsed text"],
        )

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MiOS Tree-Sitter / AST Semantic Merge Resolver (task_499)")
    parser.add_argument("--base", metavar="PATH", help="Path to base common ancestor file")
    parser.add_argument("--ours", metavar="PATH", help="Path to ours file")
    parser.add_argument("--theirs", metavar="PATH", help="Path to theirs file")
    parser.add_argument("--output", metavar="PATH", help="Output path for merged file")
    parser.add_argument("--lang", choices=["python", "toml", "json", "shell", "auto"], default="auto")
    parser.add_argument("--mock", action="store_true", help="Run mock 3-way test merge")
    parser.add_argument("--json", action="store_true", help="Output results in JSON")
    return parser.parse_args()

def main() -> int:
    args = parse_args()
    resolver = AstMergeResolver(mock=args.mock)

    if args.mock:
        base_py = "import os\n\ndef func_a():\n    return 1\n"
        ours_py = "import os\nimport sys\n\ndef func_a():\n    return 1\n\ndef func_b():\n    return 2\n"
        theirs_py = "import os\nimport json\n\ndef func_a():\n    return 1\n\ndef func_c():\n    return 3\n"
        res = resolver.merge(base_py, ours_py, theirs_py, filename="demo.py")
        if args.json:
            print(json.dumps(res.to_dict(), indent=2))
        else:
            print(f"Mock Merge Status: {res.status}")
            print(res.merged_content)
        return 0

    if not (args.base and args.ours and args.theirs):
        parser = parse_args()
        print("Error: --base, --ours, and --theirs are required (or use --mock).", file=sys.stderr)
        return 1

    try:
        with open(args.base, "r", encoding="utf-8") as f:
            base_content = f.read()
        with open(args.ours, "r", encoding="utf-8") as f:
            ours_content = f.read()
        with open(args.theirs, "r", encoding="utf-8") as f:
            theirs_content = f.read()

        result = resolver.merge(
            base_content=base_content,
            ours_content=ours_content,
            theirs_content=theirs_content,
            filename=args.ours,
            lang=args.lang,
        )

        if args.output and result.status == "success":
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(result.merged_content)

        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            if result.status == "success":
                print("Merge succeeded cleanly.")
            else:
                print(f"Merge encountered {len(result.conflict_nodes)} conflict(s):")
                for c in result.conflict_nodes:
                    print(f"  - {c}")

        return 0 if result.status == "success" else 2
    except Exception as e:
        logger.error("Merge error: %s", e)
        if args.json:
            print(json.dumps({"status": "error", "error": str(e)}, indent=2))
        return 1

if __name__ == "__main__":
    sys.exit(main())
