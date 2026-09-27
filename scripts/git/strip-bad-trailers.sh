#!/bin/sh
# Strip disallowed Co-authored-by: trailers from commit message
# Argument $1 is the commit message file path

COMMIT_MSG_FILE="$1"

if [ -n "$COMMIT_MSG_FILE" ] && [ -f "$COMMIT_MSG_FILE" ]; then
    grep -v -i -E '^Co-authored-by:' "$COMMIT_MSG_FILE" > "${COMMIT_MSG_FILE}.tmp" && mv "${COMMIT_MSG_FILE}.tmp" "$COMMIT_MSG_FILE"
fi

exit 0
