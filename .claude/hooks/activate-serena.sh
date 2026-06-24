#!/bin/bash
# Serena MCP initialization hook — activates project + loads memories on session start.

PROJECT_PATH=$(pwd)

cat << EOF
You MUST do the following silently before responding to the user:
1. Call mcp__serena__activate_project with project "${PROJECT_PATH}" to initialize Serena.
2. Call mcp__serena__list_memories to see available memories from prior sessions.
3. Read any memories relevant to the current conversation context.
4. If onboarding was not performed, call mcp__serena__onboarding.
Do all of this silently — do not narrate these steps to the user.
EOF
