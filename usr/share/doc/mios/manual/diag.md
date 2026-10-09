<!-- AI-hint: Manual pages distilled from the source comments of diag, sanitized, each passage anchored to the comment it came from. -->

# diag

### coredump_sanitizer.py — task_695 WS-DIAG Sanitized...

coredump_sanitizer.py — task_695 WS-DIAG
Sanitized systemd-coredump configurator and automated minidump extractor.

Extracts demangled stack minidumps into PostgreSQL bug_tracker, strips MADV_DONTDUMP
secret memory, and immediately purges raw core files.

<!-- mios-src:3b82ae896c9d from usr/libexec/mios/diag/coredump_sanitizer.py:4-10 -->
