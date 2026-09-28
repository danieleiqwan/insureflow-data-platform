# CLAUDE.md — InsureFlow

@AGENTS.md

The imported `AGENTS.md` holds the shared project rules (phase gate, setup commands, code/data/database/Docker standards, verification, final report format). Follow it fully. The notes below are Claude-specific additions only.

## Working style

- **Current phase is 1.** Do not build anything from later phases, even if it looks like a small helpful extra.
- Read `docs/architecture/ARCHITECTURE_ESSENTIAL.md` before starting. Open `ARCHITECTURE.md` only when you need detail.
- For multi-file work, state a short plan first, then execute. Do not stop to ask permission for steps that are clearly in scope.
- Prefer editing existing files over creating new ones. Do not create files that the current phase does not need.
- Run commands and show real output as evidence. Never write "verified" for something you did not run.
- If a command fails, diagnose and fix it, then re-run. Report only after it works or after you have hit a genuine blocker.
- Ask before adding any dependency beyond the four listed in `AGENTS.md`.

## Communication

- The user writes casual English/Malay (Manglish). Reply in the same register and keep it direct, but write **code, comments, commit messages, and documentation in English**.
- Be concise. Lead with the result, then the details.
- Give honest pushback if a request conflicts with the phase rules or introduces risk, and explain briefly why.

## Do not

- Commit `.env` or any credential.
- Push to a remote unless asked.
- Modify `docs/PRD.md` scope or the roadmap without being asked.
- Mark anything as implemented in the README or docs unless it exists and has been run.
- Start Phase 2 after finishing Phase 1. Deliver the final report from `AGENTS.md` and stop.

## Keeping docs in sync

When you change the schema, structure, conventions, or phase status, update `ARCHITECTURE.md`, `ARCHITECTURE_ESSENTIAL.md`, and `AGENTS.md` in the same change.
