---
description: "Stage all changes and commit with a descriptive message"
---

Follow these steps in order:

1. Run `git status` to see what files have changed
2. Run `git diff` to review the changes
3. Stage all relevant changes with `git add`
4. Create a commit with a clear, descriptive message following conventional commits format:
   - Format: `<type>: <description>`
   - Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`
   - Example: `feat: add smart shuffle algorithm with user preference learning`
   - Include co-author tag:
     ```
     Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
     ```
5. Run `git status` after commit to verify success

If there are any issues at any step, stop and report them.

**Do not push** - this command only commits locally.
