#!/usr/bin/env python3
"""
Strip disallowed Co-authored-by: trailers from commit message.
Git passes the commit message file as the first argument.
"""

import re
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        return 0

    msg_file = Path(sys.argv[1])
    if not msg_file.is_file():
        return 0

    content = msg_file.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines(keepends=True)

    cleaned_lines = [
        line for line in lines if not re.match(r"^\s*co-authored-by:\s*", line, re.IGNORECASE)
    ]

    msg_file.write_text("".join(cleaned_lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
