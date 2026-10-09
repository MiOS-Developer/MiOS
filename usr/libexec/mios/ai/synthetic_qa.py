#!/usr/bin/env python3
# AI-hint: Synthetic training data pipeline generating Q&A pairs from local markdown documentation and architectural ADRs.
# AI-related: usr/share/doc/mios/, usr/share/doc/mios/adr/, /var/lib/mios/ai/dataset/
"""
Synthetic Training Q&A Data Pipeline (task_327 / task_2560)

Harvests architectural chapters, user guides, manual pages, and ADRs from `/usr/share/doc/mios/`
including its `adr/` records, performs hierarchical markdown parsing with context preservation, synthesizes
multi-turn reasoning and domain-specific Q&A pairs for `mios-opencode` fine-tuning,
enforces secret/token redaction (Rule 14), and emits JSONL datasets to `/var/lib/mios/ai/dataset/`.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import re
import sys
import tempfile
from typing import Any, Dict, Generator, List, Optional, Tuple

logger = logging.getLogger("mios.synthetic_qa")

@dataclasses.dataclass
class MarkdownChunk:
    doc_path: str
    doc_title: str
    section_title: str
    hierarchy: List[str]
    content: str
    code_blocks: List[Dict[str, str]] = dataclasses.field(default_factory=list)
    tables: List[str] = dataclasses.field(default_factory=list)
    word_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

class MarkdownHierarchicalParser:
    """
    Parses markdown documents into structured chunks preserving section hierarchy,
    code blocks, tables, and parent document metadata.
    """

    HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    CODE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_-]*)\n([\s\S]*?)```")
    TABLE_RE = re.compile(r"(\|[^\n]+\|\n\|[-:| ]+\|\n(?:\|[^\n]+\|\n?)+)")

    def parse_text(self, text: str, doc_path: str = "") -> List[MarkdownChunk]:
        lines = text.splitlines()
        chunks: List[MarkdownChunk] = []

        doc_title = os.path.basename(doc_path).replace(".md", "").replace("-", " ").title()
        header_stack: List[Tuple[int, str]] = []  # (level, title)

        current_lines: List[str] = []
        current_title = "Overview"

        def flush_current_chunk() -> None:
            nonlocal current_lines, current_title
            raw_content = "\n".join(current_lines).strip()
            if not raw_content or len(raw_content.split()) < 8:
                current_lines = []
                return

            # Extract code blocks
            code_blocks = []
            for match in self.CODE_BLOCK_RE.finditer(raw_content):
                lang = match.group(1).strip() or "text"
                code = match.group(2).strip()
                code_blocks.append({"lang": lang, "code": code})

            # Extract tables
            tables = [m.group(1).strip() for m in self.TABLE_RE.finditer(raw_content)]

            hierarchy = [t for _, t in header_stack] if header_stack else [doc_title]
            words = len(raw_content.split())

            chunk = MarkdownChunk(
                doc_path=doc_path,
                doc_title=doc_title,
                section_title=current_title,
                hierarchy=hierarchy,
                content=raw_content,
                code_blocks=code_blocks,
                tables=tables,
                word_count=words,
            )
            chunks.append(chunk)
            current_lines = []

        in_code_fence = False
        for line in lines:
            if line.strip().startswith("```"):
                in_code_fence = not in_code_fence

            if not in_code_fence and line.startswith("#"):
                m = self.HEADER_RE.match(line)
                if m:
                    flush_current_chunk()
                    level = len(m.group(1))
                    title = m.group(2).strip()

                    # Pop header stack to current level
                    while header_stack and header_stack[-1][0] >= level:
                        header_stack.pop()

                    header_stack.append((level, title))
                    current_title = title
                    if level == 1 and doc_title == os.path.basename(doc_path).replace(".md", "").replace("-", " ").title():
                        doc_title = title
                    continue

            current_lines.append(line)

        flush_current_chunk()
        return chunks

    def parse_file(self, file_path: str) -> List[MarkdownChunk]:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return self.parse_text(content, doc_path=file_path)
        except Exception as e:
            logger.warning("Failed to parse markdown file '%s': %s", file_path, e)
            return []

class SecretRedactor:
    """
    Redacts passwords, tokens, API keys, private keys, and credentials
    in accordance with Rule 14 (Persistence Sanitization).
    """

    PATTERNS = [
        # Standard API keys / Tokens
        (re.compile(r"\b(sk-[A-Za-z0-9-_]{20,})\b"), "<REDACTED_API_KEY>"),
        (re.compile(r"\b(ghp_[A-Za-z0-9]{36,})\b"), "<REDACTED_GITHUB_TOKEN>"),
        (re.compile(r"\b(Bearer\s+)[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE), r"\1<REDACTED_BEARER_TOKEN>"),
        # Private Keys
        (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"), "<REDACTED_PRIVATE_KEY>"),
        # Password / secret assignments in TOML/YAML/Env/JSON
        (re.compile(r"""(?i)\b(password|passwd|secret_key|client_secret)\s*[:=]\s*["']?([^,\s"'\n]+)["']?"""), r'\1: "<REDACTED_SECRET>"'),
        # Hardcoded SSH Keys
        (re.compile(r"\b(ssh-(?:rsa|ed25519)\s+AAAA[A-Za-z0-9+/]+={0,3})(?:\s+[^\n]+)?"), "<REDACTED_SSH_KEY>"),
    ]

    @classmethod
    def redact(cls, text: str) -> str:
        sanitized = text
        for pat, repl in cls.PATTERNS:
            sanitized = pat.sub(repl, sanitized)
        return sanitized

    @classmethod
    def sanitize_record(cls, record: Dict[str, Any]) -> Dict[str, Any]:
        sanitized_messages = []
        for msg in record.get("messages", []):
            role = msg.get("role", "")
            content = msg.get("content", "")
            sanitized_messages.append({
                "role": role,
                "content": cls.redact(content),
            })
        record["messages"] = sanitized_messages
        return record

class QASynthesizer:
    """
    Generates multi-turn fine-tuning Q&A dialogues from parsed markdown chunks.
    """

    SYSTEM_PROMPT = (
        "You are MiOS-Opencode, a specialized local systems programming and OS architecture assistant "
        "designed for the MiOS operating system. Adhere strictly to the six Architectural Laws, "
        "native Linux FHS structuring (USR-OVER-ETC), OpenAI-compatible endpoint contracts, "
        "and reproducible, verifiable system engineering."
    )

    @classmethod
    def synthesize_qa_pairs(cls, chunk: MarkdownChunk) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        clean_text = chunk.content.strip()
        if len(clean_text.split()) < 10:
            return records

        hierarchy_str = " > ".join(chunk.hierarchy)

        # 1. Architectural / Conceptual Q&A
        q1 = f"Explain the architectural design and purpose of {chunk.section_title} in MiOS ({hierarchy_str})."
        a1 = (
            f"In MiOS, **{chunk.section_title}** under `{hierarchy_str}` functions as follows:\n\n"
            f"{clean_text}\n\n"
            f"This aligns with MiOS system invariants and ensures strict modularity."
        )

        # Multi-turn follow-up
        q1_followup = f"What are the key technical constraints or operational rules associated with {chunk.section_title}?"
        a1_followup = (
            f"When interacting with {chunk.section_title}, the primary constraints are:\n"
            f"1. Strict adherence to FHS layout and bootc immutability (overrides in `/etc`, dynamic data in `/var`).\n"
            f"2. Unified communication via OpenAI-compatible endpoints without proprietary side-channels.\n"
            f"3. Independent reproducibility and drift verification."
        )

        record_conceptual = {
            "messages": [
                {"role": "system", "content": cls.SYSTEM_PROMPT},
                {"role": "user", "content": q1},
                {"role": "assistant", "content": a1},
                {"role": "user", "content": q1_followup},
                {"role": "assistant", "content": a1_followup},
            ],
            "metadata": {
                "source_doc": chunk.doc_path,
                "section": chunk.section_title,
                "hierarchy": chunk.hierarchy,
                "category": "architectural_qa",
            },
        }
        records.append(record_conceptual)

        # 2. Code / Implementation Q&A (if code blocks are present)
        if chunk.code_blocks:
            for idx, cb in enumerate(chunk.code_blocks[:2]):
                lang = cb.get("lang", "sh")
                code = cb.get("code", "")
                if len(code.splitlines()) < 2:
                    continue

                q_code = (
                    f"How do I implement or configure the {chunk.section_title} component in MiOS? "
                    f"Provide an example in `{lang}`."
                )
                a_code = (
                    f"Here is the standard implementation pattern for **{chunk.section_title}** in MiOS:\n\n"
                    f"```{lang}\n{code}\n```\n\n"
                    f"**Explanation:**\n"
                    f"This code integrates directly with the MiOS runtime environment, preserving system state "
                    f"and satisfying Architectural Law invariants."
                )

                record_code = {
                    "messages": [
                        {"role": "system", "content": cls.SYSTEM_PROMPT},
                        {"role": "user", "content": q_code},
                        {"role": "assistant", "content": a_code},
                    ],
                    "metadata": {
                        "source_doc": chunk.doc_path,
                        "section": chunk.section_title,
                        "hierarchy": chunk.hierarchy,
                        "category": "implementation_code",
                    },
                }
                records.append(record_code)

        # 3. Procedural / Verification Q&A
        if "test" in chunk.section_title.lower() or "verify" in clean_text.lower() or "adr" in chunk.doc_path.lower():
            q_verify = f"How is {chunk.section_title} verified and tested for correctness in MiOS CI?"
            a_verify = (
                f"To verify **{chunk.section_title}** within MiOS:\n\n"
                f"1. Run the corresponding suite registered under `[ci.tiers] unit` in `usr/share/mios/mios.toml`.\n"
                f"2. Ensure zero drift violations against `tools/drift-checks.py legibility-ratchet`.\n"
                f"3. Validate that runtime state reflects genuine execution rather than hardcoded mock outputs."
            )
            record_verify = {
                "messages": [
                    {"role": "system", "content": cls.SYSTEM_PROMPT},
                    {"role": "user", "content": q_verify},
                    {"role": "assistant", "content": a_verify},
                ],
                "metadata": {
                    "source_doc": chunk.doc_path,
                    "section": chunk.section_title,
                    "hierarchy": chunk.hierarchy,
                    "category": "verification_procedure",
                },
            }
            records.append(record_verify)

        return records

class SyntheticQAPipeline:
    """
    Coordinates document harvesting, hierarchical parsing, Q&A synthesis,
    secret redaction, and JSONL dataset generation.
    """

    def __init__(
        self,
        parser: Optional[MarkdownHierarchicalParser] = None,
        redactor: Optional[SecretRedactor] = None,
        synthesizer: Optional[QASynthesizer] = None,
    ) -> None:
        self.parser = parser or MarkdownHierarchicalParser()
        self.redactor = redactor or SecretRedactor()
        self.synthesizer = synthesizer or QASynthesizer()

    def harvest_docs(self, search_paths: List[str]) -> List[MarkdownChunk]:
        all_chunks: List[MarkdownChunk] = []
        for path in search_paths:
            if os.path.isfile(path) and path.endswith(".md"):
                chunks = self.parser.parse_file(path)
                all_chunks.extend(chunks)
            elif os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for file in sorted(files):
                        if file.endswith(".md"):
                            full_p = os.path.join(root, file)
                            chunks = self.parser.parse_file(full_p)
                            all_chunks.extend(chunks)
        return all_chunks

    def generate_dataset(
        self,
        chunks: List[MarkdownChunk],
        max_samples: Optional[int] = None,
        redact: bool = True,
    ) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for chunk in chunks:
            qa_pairs = self.synthesizer.synthesize_qa_pairs(chunk)
            for rec in qa_pairs:
                if redact:
                    rec = self.redactor.sanitize_record(rec)
                records.append(rec)
                if max_samples and len(records) >= max_samples:
                    return records
        return records

    def export_jsonl(self, records: List[Dict[str, Any]], output_path: str) -> int:
        parent = os.path.dirname(os.path.abspath(output_path))
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

        temp_dir = parent if os.path.exists(parent) else None
        fd, temp_path = tempfile.mkstemp(prefix=".syn_qa_", dir=temp_dir, text=True)
        try:
            with open(fd, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec) + "\n")
            os.replace(temp_path, output_path)
            return len(records)
        except Exception as e:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            logger.error("Failed to export JSONL dataset to '%s': %s", output_path, e)
            raise

def find_default_search_paths() -> List[str]:
    paths = []
    # Check repository root paths
    candidates = [
        "usr/share/doc/mios",
        "field",
        "doc",
        "docs",
        "/usr/share/doc/mios",
    ]
    for c in candidates:
        if os.path.exists(c):
            paths.append(c)
    return paths or ["."]

def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS Synthetic Training Q&A Data Pipeline")
    parser.add_argument("--input-dir", "-i", action="append", help="Input markdown directory or file (can specify multiple)")
    parser.add_argument("--output-file", "-o", default="/var/lib/mios/ai/dataset/synthetic_qa.jsonl", help="Output JSONL dataset path")
    parser.add_argument("--max-samples", "-n", type=int, default=None, help="Maximum number of samples to synthesize")
    parser.add_argument("--stats-only", action="store_true", help="Harvest and parse without exporting")
    parser.add_argument("--no-redact", action="store_true", help="Disable secret and token redaction filter")
    parser.add_argument("--json", action="store_true", help="Print summary statistics as JSON")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    input_paths = args.input_dir if args.input_dir else find_default_search_paths()
    pipeline = SyntheticQAPipeline()

    chunks = pipeline.harvest_docs(input_paths)
    records = pipeline.generate_dataset(chunks, max_samples=args.max_samples, redact=not args.no_redact)

    stats = {
        "input_paths": input_paths,
        "total_chunks_harvested": len(chunks),
        "total_qa_records_synthesized": len(records),
        "code_block_chunks": len([c for c in chunks if c.code_blocks]),
        "table_chunks": len([c for c in chunks if c.tables]),
        "output_file": args.output_file if not args.stats_only else None,
    }

    if args.stats_only:
        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            print(f"Synthetic Q&A Pipeline Stats (dry run):\n{json.dumps(stats, indent=2)}")
        return 0

    pipeline.export_jsonl(records, args.output_file)
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print(f"Exported {len(records)} synthetic Q&A records to '{args.output_file}'.")

    return 0

if __name__ == "__main__":
    sys.exit(main())
