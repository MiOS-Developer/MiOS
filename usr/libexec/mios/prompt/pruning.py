#!/usr/bin/env python3
# AI-hint: Contextual prompt compression and selective linguistic token pruning engine.
# AI-related: tests/test-context-processing.py, usr/share/doc/mios/manual/prompt.md
"""
MiOS Contextual Prompt Compression & Token Pruning Engine (PROMPT-01 / task_324 / task_2557).

Prunes conversational boilerplate, pleasantries, redundant markdown headers/spacers,
and low-information syntactic filler while strictly preserving code syntax, code blocks,
and semantic clarity.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

class PromptPruner:
    """
    Contextual prompt compressor performing selective linguistic token pruning
    and AST/syntax-safe code block preservation.
    """

    # Conversational boilerplate, pleasantries, and polite introductory/closing phrases
    BOILERPLATE_PATTERNS = [
        # Polite introductory phrases
        r"(?i)\b(?:please\s+be\s+advised\s+that|it\s+is\s+critically\s+necessary\s+to\s+ensure\s+that|it\s+is\s+critically\s+important\s+to\s+note\s+that|it\s+is\s+important\s+to\s+note\s+that|it\s+is\s+worth\s+noting\s+that|it\s+is\s+worth\s+mentioning\s+that|furthermore,\s+we\s+would\s+like\s+to\s+note\s+that|furthermore,\s+it\s+should\s+be\s+noted\s+that|please\s+note\s+that|as\s+(?:an?\s+)?(?:ai\s+language\s+model|ai\s+assistant|ai|helpful\s+assistant|assistant)[,:]?\s*|i\s+am\s+(?:an?\s+)?(?:ai\s+language\s+model|ai\s+assistant|ai|helpful\s+assistant|assistant)[,:]?\s*|i'd\s+be\s+happy\s+to\s+help(?:\s+you)?(?:\s+with\s+that)?|i\s+would\s+be\s+happy\s+to\s+help(?:\s+you)?(?:\s+with\s+that)?|thank\s+you\s+for\s+asking(?:\s+me)?|thank\s+you\s+for\s+your\s+question|without\s+further\s+ado,\s*|it\s+should\s+be\s+emphasized\s+that|needless\s+to\s+say,\s*|at\s+the\s+end\s+of\s+the\s+day,\s*|it\s+goes\s+without\s+saying\s+that|as\s+a\s+matter\s+of\s+fact,\s*|for\s+all\s+intents\s+and\s+purposes,\s*|in\s+light\s+of\s+the\s+above,\s*|taking\s+all\s+of\s+this\s+into\s+consideration,\s*|as\s+previously\s+mentioned,\s*|as\s+stated\s+above,\s*|as\s+you\s+may\s+already\s+know,\s*|it\s+is\s+clear\s+that|it\s+is\s+obvious\s+that|as\s+you\s+can\s+see,\s*|to\s+make\s+a\s+long\s+story\s+short,\s*)\b",
        # Conversational closers and pleasantries
        r"(?i)\b(?:feel\s+free\s+to\s+ask(?:\s+if\s+you\s+have\s+any\s+questions)?|let\s+me\s+know\s+if\s+you\s+need\s+anything\s+else|if\s+you\s+have\s+any(?:\s+further)?\s+questions,\s*(?:please\s+)?let\s+me\s+know|don't\s+hesitate\s+to\s+reach\s+out|i\s+hope\s+this\s+helps(?:\s+you)?(?:\s+out)?|hope\s+this\s+helps(?:\s+you)?|hope\s+this\s+information\s+is\s+useful|best\s+regards,?|sincerely,?|cheers,?)\b[.!]*",
    ]

    # Verbose phrases mapped to concise equivalents
    PHRASE_REPLACEMENTS = [
        (r"(?i)\bin\s+order\s+to\b", "to"),
        (r"(?i)\bdue\s+to\s+the\s+fact\s+that\b", "because"),
        (r"(?i)\bfor\s+the\s+purpose\s+of\b", "for"),
        (r"(?i)\bin\s+the\s+event\s+that\b", "if"),
        (r"(?i)\bwith\s+regard\s+to\b", "regarding"),
        (r"(?i)\bwith\s+regards\s+to\b", "regarding"),
        (r"(?i)\bwith\s+respect\s+to\b", "regarding"),
        (r"(?i)\bin\s+accordance\s+with\b", "per"),
        (r"(?i)\bat\s+this\s+point\s+in\s+time\b", "now"),
        (r"(?i)\bat\s+the\s+present\s+time\b", "currently"),
        (r"(?i)\bprior\s+to\b", "before"),
        (r"(?i)\bsubsequent\s+to\b", "after"),
        (r"(?i)\bis\s+able\s+to\b", "can"),
        (r"(?i)\bhas\s+the\s+ability\s+to\b", "can"),
        (r"(?i)\ba\s+large\s+number\s+of\b", "many"),
        (r"(?i)\ba\s+majority\s+of\b", "most"),
        (r"(?i)\bin\s+spite\s+of\s+the\s+fact\s+that\b", "although"),
        (r"(?i)\bgive\s+consideration\s+to\b", "consider"),
        (r"(?i)\bmake\s+an\s+assumption\b", "assume"),
        (r"(?i)\bconduct\s+an\s+investigation\b", "investigate"),
        (r"(?i)\bdraw\s+to\s+a\s+close\b", "end"),
        (r"(?i)\bin\s+the\s+near\s+future\b", "soon"),
        (r"(?i)\bby\s+means\s+of\b", "via"),
        (r"(?i)\bfirst\s+and\s+foremost\b", "first"),
        (r"(?i)\butilize\b", "use"),
        (r"(?i)\butilizes\b", "uses"),
        (r"(?i)\butilizing\b", "using"),
        (r"(?i)\butilization\b", "use"),
        (r"(?i)\bdemonstrates\s+the\s+presence\s+of\b", "shows"),
    ]

    # Non-essential filler adverbs/modifiers that can be pruned when compression target requires it
    FILLER_WORDS = [
        r"(?i)\b(?:basically|essentially|literally|actually|honestly|clearly|obviously|definitely|certainly|simply|just|totally|completely|practically)\b",
    ]

    # Regex for fenced code blocks (```lang ... ```)
    FENCED_CODE_PATTERN = re.compile(r"```[\w\-]*\n[\s\S]*?\n```|```[\s\S]*?```", re.MULTILINE)
    # Regex for inline code (`...`)
    INLINE_CODE_PATTERN = re.compile(r"`[^`\n]+`")

    def __init__(self) -> None:
        self._compiled_boilerplate = [re.compile(p, re.MULTILINE) for p in self.BOILERPLATE_PATTERNS]
        self._compiled_phrases = [(re.compile(p), repl) for p, repl in self.PHRASE_REPLACEMENTS]
        self._compiled_fillers = [re.compile(p) for p in self.FILLER_WORDS]

    @staticmethod
    def _approx_tokens(text: str) -> int:
        """
        Calculates an approximate token count for text using standard word/char heuristics.
        """
        if not text:
            return 0
        # Roughly 1 token per 4 characters or ~0.75 words + punctuation tokens
        char_based = len(text) / 4.0
        words = len(re.findall(r"\w+|[^\w\s]", text))
        approx = int(round((char_based + words) / 2.0))
        return max(1, approx)

    def _extract_code_blocks(self, text: str) -> Tuple[str, List[str]]:
        """
        Extracts fenced and inline code blocks, replacing them with placeholders
        to guarantee syntax and structure preservation during linguistic pruning.
        """
        placeholders: List[str] = []

        def _replace_fenced(match: re.Match) -> str:
            idx = len(placeholders)
            placeholders.append(match.group(0))
            return f"__MIOS_CODE_BLOCK_{idx}__"

        def _replace_inline(match: re.Match) -> str:
            idx = len(placeholders)
            placeholders.append(match.group(0))
            return f"__MIOS_INLINE_CODE_{idx}__"

        # 1. Extract fenced multi-line code blocks
        protected = self.FENCED_CODE_PATTERN.sub(_replace_fenced, text)

        # 2. Extract inline backticked code
        protected = self.INLINE_CODE_PATTERN.sub(_replace_inline, protected)

        return protected, placeholders

    def _restore_code_blocks(self, text: str, placeholders: List[str]) -> str:
        """
        Restores extracted code blocks from placeholders.
        """
        for idx, original_block in enumerate(placeholders):
            text = text.replace(f"__MIOS_CODE_BLOCK_{idx}__", original_block)
            text = text.replace(f"__MIOS_INLINE_CODE_{idx}__", original_block)
        return text

    def _clean_markdown_formatting(self, text: str) -> str:
        """
        Normalizes markdown structure, deduplicates horizontal rules and blank lines,
        and trims trailing whitespace.
        """
        # Collapse multiple horizontal rules
        text = re.sub(r"(?:^|\n)(?:[-*_]\s*){3,}(?:\n(?:[-*_]\s*){3,})+", "\n---", text)

        # Deduplicate consecutive identical markdown headings
        lines = text.split("\n")
        deduped_lines: List[str] = []
        last_heading = None

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                if stripped == last_heading:
                    continue
                last_heading = stripped
            else:
                if stripped != "":
                    last_heading = None
            deduped_lines.append(line)

        text = "\n".join(deduped_lines)

        # Remove trailing whitespace per line
        text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)

        # Collapse 3+ consecutive newlines to 2
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text

    def _clean_sentence_syntax(self, text: str) -> str:
        """
        Cleans up orphaned punctuation, leading commas/colons, and ensures correct capitalization.
        """
        # Remove orphaned leading punctuation after phrases were stripped
        # e.g., " . Something" -> "Something" or ": something" -> "something"
        lines = text.split("\n")
        cleaned_lines: List[str] = []

        for line in lines:
            trimmed = line.strip()
            # Clean leading punctuation on the line if it was left by a stripped introductory phrase
            trimmed = re.sub(r"^[,\s;:\-]+", "", trimmed)
            # Remove double spaces
            trimmed = re.sub(r"[ \t]{2,}", " ", trimmed)
            # Fix punctuation spacing: " , " -> ", " or " . " -> ". "
            trimmed = re.sub(r"\s+([,.:;?!])", r"\1", trimmed)

            # Ensure first letter is capitalized if starting a standard sentence
            if trimmed and trimmed[0].islower() and not trimmed.startswith(("http", "www", "/", ".")):
                # Don't capitalize if it looks like code placeholder or list marker
                if not trimmed.startswith("__MIOS_"):
                    trimmed = trimmed[0].upper() + trimmed[1:]

            cleaned_lines.append(trimmed if not line.startswith("  ") else line)

        out = "\n".join(cleaned_lines)
        return re.sub(r"\n{3,}", "\n\n", out)

    def _prune_linguistics(self, text: str, aggressive: bool = False) -> str:
        """
        Applies linguistic pruning: removes pleasantries, replaces verbose phrases,
        and optionally prunes filler words.
        """
        # 1. Strip conversational boilerplate
        for pattern in self._compiled_boilerplate:
            text = pattern.sub("", text)

        # 2. Apply phrase contractions / replacements
        for pattern, replacement in self._compiled_phrases:
            text = pattern.sub(replacement, text)

        # 3. If aggressive mode requested or high compression ratio needed, remove filler words
        if aggressive:
            for pattern in self._compiled_fillers:
                text = pattern.sub("", text)

            # Prune redundant demonstratives and filler clauses:
            text = re.sub(r"(?i)\b(?:it\s+should\s+be\s+pointed\s+out\s+that|it\s+is\s+evident\s+that)\b", "", text)

        return text

    def compress(
        self,
        text: str,
        target_ratio: float = 0.25,
        preserve_code: bool = True,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Compresses input text through linguistic pruning and contextual compaction.

        Args:
            text: Input prompt or context block to compress.
            target_ratio: Desired token/character reduction ratio (e.g. 0.25 for 25% savings).
            preserve_code: Whether to strictly preserve code blocks and syntax structures.

        Returns:
            Tuple of (compressed_text, statistics_dict)
        """
        if not text or not text.strip():
            orig_len = len(text)
            return text, {
                "original_chars": orig_len,
                "compressed_chars": orig_len,
                "original_tokens_approx": self._approx_tokens(text),
                "compressed_tokens_approx": self._approx_tokens(text),
                "reduction_ratio": 0.0,
                "saved_tokens_approx": 0,
            }

        original_chars = len(text)
        original_tokens = self._approx_tokens(text)

        # Step 1: Protect code blocks if enabled
        placeholders: List[str] = []
        working_text = text
        if preserve_code:
            working_text, placeholders = self._extract_code_blocks(working_text)

        # Step 2: Primary linguistic pruning
        pruned = self._prune_linguistics(working_text, aggressive=False)
        pruned = self._clean_markdown_formatting(pruned)
        pruned = self._clean_sentence_syntax(pruned)

        # Step 3: Check current reduction; if below target_ratio and target >= 0.20, run aggressive pass
        curr_saved_ratio = (len(working_text) - len(pruned)) / max(1, len(working_text))
        if curr_saved_ratio < target_ratio and target_ratio >= 0.15:
            pruned = self._prune_linguistics(pruned, aggressive=True)
            pruned = self._clean_sentence_syntax(pruned)

        # Step 4: Restore code blocks
        if preserve_code:
            compressed_text = self._restore_code_blocks(pruned, placeholders)
        else:
            compressed_text = pruned

        compressed_text = compressed_text.strip()
        compressed_chars = len(compressed_text)
        compressed_tokens = self._approx_tokens(compressed_text)

        # Step 5: Compute reduction stats
        saved_chars = max(0, original_chars - compressed_chars)
        reduction_ratio = max(0.0, round(saved_chars / original_chars, 4)) if original_chars > 0 else 0.0
        saved_tokens = max(0, original_tokens - compressed_tokens)

        stats: Dict[str, Any] = {
            "original_chars": original_chars,
            "compressed_chars": compressed_chars,
            "original_tokens_approx": original_tokens,
            "compressed_tokens_approx": compressed_tokens,
            "reduction_ratio": reduction_ratio,
            "saved_tokens_approx": saved_tokens,
        }

        return compressed_text, stats

    def prune_messages(
        self,
        messages: List[Dict[str, Any]],
        target_ratio: float = 0.25,
        preserve_code: bool = True,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Compresses a list of chat/system messages (OpenAI-compatible format).

        Args:
            messages: List of message dicts (e.g. [{"role": "user", "content": "..."}]).
            target_ratio: Desired reduction ratio.
            preserve_code: Whether to protect code blocks.

        Returns:
            Tuple of (pruned_messages, aggregate_statistics_dict)
        """
        pruned_list: List[Dict[str, Any]] = []
        total_orig_chars = 0
        total_comp_chars = 0
        total_orig_tokens = 0
        total_comp_tokens = 0

        for msg in messages:
            msg_copy = dict(msg)
            content = msg_copy.get("content")

            if isinstance(content, str):
                comp_text, s = self.compress(content, target_ratio=target_ratio, preserve_code=preserve_code)
                msg_copy["content"] = comp_text
                total_orig_chars += s["original_chars"]
                total_comp_chars += s["compressed_chars"]
                total_orig_tokens += s["original_tokens_approx"]
                total_comp_tokens += s["compressed_tokens_approx"]
            elif isinstance(content, list):
                # Multimodal or multi-part message parts
                new_parts: List[Any] = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                        part_copy = dict(part)
                        comp_text, s = self.compress(part["text"], target_ratio=target_ratio, preserve_code=preserve_code)
                        part_copy["text"] = comp_text
                        new_parts.append(part_copy)
                        total_orig_chars += s["original_chars"]
                        total_comp_chars += s["compressed_chars"]
                        total_orig_tokens += s["original_tokens_approx"]
                        total_comp_tokens += s["compressed_tokens_approx"]
                    else:
                        new_parts.append(part)
                msg_copy["content"] = new_parts
            else:
                # Non-text content untouched
                pass

            pruned_list.append(msg_copy)

        saved_chars = max(0, total_orig_chars - total_comp_chars)
        agg_ratio = max(0.0, round(saved_chars / total_orig_chars, 4)) if total_orig_chars > 0 else 0.0
        agg_saved_tokens = max(0, total_orig_tokens - total_comp_tokens)

        agg_stats: Dict[str, Any] = {
            "original_chars": total_orig_chars,
            "compressed_chars": total_comp_chars,
            "original_tokens_approx": total_orig_tokens,
            "compressed_tokens_approx": total_comp_tokens,
            "reduction_ratio": agg_ratio,
            "saved_tokens_approx": agg_saved_tokens,
        }

        return pruned_list, agg_stats

def main(argv: Optional[List[str]] = None) -> int:
    """CLI driver for prompt pruning."""
    parser = argparse.ArgumentParser(
        description="MiOS Prompt Pruner - Contextual Prompt Compression and Token Pruning"
    )
    parser.add_argument("file", nargs="?", default=None, help="Input prompt file (or stdin if omitted)")
    parser.add_argument("-i", "--input", dest="input_file", help="Explicit input file path")
    parser.add_argument("-o", "--output", dest="output_file", help="Output file path (default: stdout)")
    parser.add_argument("-r", "--ratio", type=float, default=0.25, help="Target reduction ratio (default: 0.25)")
    parser.add_argument("--no-preserve-code", action="store_true", help="Disable code block preservation")
    parser.add_argument("--stats", action="store_true", help="Print statistics JSON to stderr")
    parser.add_argument("--stats-only", action="store_true", help="Print only statistics JSON to stdout")
    parser.add_argument("--json", dest="is_json", action="store_true", help="Process input as JSON message array")

    args = parser.parse_args(argv)

    # Determine input source
    input_path = args.input_file or args.file
    if input_path and input_path != "-":
        if not os.path.isfile(input_path):
            sys.stderr.write(f"Error: Input file not found: {input_path}\n")
            return 1
        with open(input_path, "r", encoding="utf-8") as f:
            raw_input = f.read()
    else:
        raw_input = sys.stdin.read()

    pruner = PromptPruner()
    preserve_code = not args.no_preserve_code

    if args.is_json:
        try:
            data = json.loads(raw_input)
            if isinstance(data, list):
                result_messages, stats = pruner.prune_messages(
                    data, target_ratio=args.ratio, preserve_code=preserve_code
                )
                output_str = json.dumps(result_messages, indent=2)
            elif isinstance(data, dict) and "messages" in data:
                result_messages, stats = pruner.prune_messages(
                    data["messages"], target_ratio=args.ratio, preserve_code=preserve_code
                )
                data["messages"] = result_messages
                output_str = json.dumps(data, indent=2)
            else:
                sys.stderr.write("Error: Expected JSON list of messages or object with 'messages' key.\n")
                return 1
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"Error decoding JSON input: {exc}\n")
            return 1
    else:
        output_str, stats = pruner.compress(
            raw_input, target_ratio=args.ratio, preserve_code=preserve_code
        )

    if args.stats_only:
        sys.stdout.write(json.dumps(stats, indent=2) + "\n")
        return 0

    if args.stats:
        sys.stderr.write(json.dumps(stats, indent=2) + "\n")

    if args.output_file:
        with open(args.output_file, "w", encoding="utf-8") as f:
            f.write(output_str + "\n")
    else:
        sys.stdout.write(output_str + "\n")

    return 0

if __name__ == "__main__":
    sys.exit(main())
