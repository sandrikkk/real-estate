#!/bin/sh
# Auto-sync AI config pointers (GEMINI.md -> AGENTS.md / CLAUDE.md)
if [ -f "GEMINI.md" ] && [ ! -f "AGENTS.md" ]; then
    cp "GEMINI.md" "AGENTS.md" 2>/dev/null || true
fi
if [ -f "GEMINI.md" ] && [ ! -f "CLAUDE.md" ]; then
    cp "GEMINI.md" "CLAUDE.md" 2>/dev/null || true
fi
exit 0
