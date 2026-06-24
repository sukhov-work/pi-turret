#!/bin/bash
# Session end hook — reminds Claude to persist important context to Serena memories.

cat << 'EOF'
Before this session ends, you MUST do the following:
1. Review what was accomplished in this session.
2. If any significant work was done (new component implemented, architecture decision made, non-obvious pattern discovered, tricky bug fixed), call mcp__serena__write_memory to persist it.
   - Use naming: architecture/<component>, decisions/<topic>, patterns/<pattern>, bugs/<issue>
   - Include: tags, key facts, file paths, gotchas. Code over prose.
3. If an existing memory is now outdated by this session's work, update it via mcp__serena__edit_memory.
4. Summarize what was done and what's next in 2-3 sentences for the user.
Do the memory operations silently — only show the summary to the user.
EOF
