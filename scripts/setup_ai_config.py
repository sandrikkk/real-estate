#!/usr/bin/env python3
"""
Auto-regenerate AI config pointers (GEMINI.md -> AGENTS.md, CLAUDE.md)
Ensures any AI coding assistant finds instructions cleanly.
"""

from pathlib import Path


def main() -> int:
    gemini_path = Path("GEMINI.md")
    if not gemini_path.is_file():
        return 0

    content = gemini_path.read_text(encoding="utf-8", errors="replace")

    for target_name in ["AGENTS.md", "CLAUDE.md"]:
        target_path = Path(target_name)
        if not target_path.exists():
            try:
                target_path.write_text(content, encoding="utf-8")
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    main()
