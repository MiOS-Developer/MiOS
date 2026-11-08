# Changelog

All notable changes to the `dev-loop` framework are documented here.
Format conforms to [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [2.1.0] - 2026-09-17
### Added
- Artifact lifecycle management module (`reference/artifacts.py`) with SHA-256 integrity verification.
- Complete contract template suite (`reference/templates/`) for repository-native planning.
- GitHub Copilot Agent definition (`commands/copilot/dev-loop.agent.md`).
- Official Cursor rule MDC format (`commands/cursor/dev-loop.mdc`).

## [2.0.0] - 2026-09-16
### Added
- Native cross-harness slash commands for Claude, Codex, Gemini, Copilot, Antigravity, OpenCode, and Cursor.
- Unified Python adapter layer (`reference/adapters.py`) supporting 10 harnesses.
- Automated cross-platform installation scripts (`install.sh` and `install.ps1`).
