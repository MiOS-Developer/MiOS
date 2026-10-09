<!-- AI-hint: Manual pages distilled from the source comments of prompt, sanitized, each passage anchored to the comment it came from. -->

# prompt

### MiOS Contextual Prompt Compression & Token Pruning Engine...

MiOS Contextual Prompt Compression & Token Pruning Engine (PROMPT-01 / task_324 / task_2557).

Prunes conversational boilerplate, pleasantries, redundant markdown headers/spacers,
and low-information syntactic filler while strictly preserving code syntax, code blocks,
and semantic clarity.

<!-- mios-src:86a8f82e5120 from usr/libexec/mios/prompt/pruning.py:4-10 -->

### Compresses input text through linguistic pruning and...

Compresses input text through linguistic pruning and contextual compaction.

        Args:
            text: Input prompt or context block to compress.
            target_ratio: Desired token/character reduction ratio (e.g. 0.25 for 25% savings).
            preserve_code: Whether to strictly preserve code blocks and syntax structures.

        Returns:
            Tuple of (compressed_text, statistics_dict)

<!-- mios-src:8cc237729ab5 from usr/libexec/mios/prompt/pruning.py:223-233 -->

### Compresses a list of chat/system messages...

Compresses a list of chat/system messages (OpenAI-compatible format).

        Args:
            messages: List of message dicts (e.g. [{"role": "user", "content": "..."}]).
            target_ratio: Desired reduction ratio.
            preserve_code: Whether to protect code blocks.

        Returns:
            Tuple of (pruned_messages, aggregate_statistics_dict)

<!-- mios-src:2aad9dd9d841 from usr/libexec/mios/prompt/pruning.py:297-307 -->
