---
description: "Review uncommitted changes and suggest improvements"
---

Follow these steps in order:

1. Run `git status` to see what files have changed
2. Run `git diff` to see the detailed changes
3. Review the changes for:
   - **Code Quality**: Check for proper error handling, logging, type hints
   - **Security**: Look for exposed secrets, SQL injection, XSS vulnerabilities
   - **Style**: Ensure consistency with existing codebase patterns
   - **Tests**: Check if tests are needed for new functionality
   - **Documentation**: Verify docstrings, comments, and CHANGELOG.md updates
   - **Spotify API**: Ensure OAuth tokens are handled securely
   - **Session Management**: Check undo stack is maintained correctly

4. Provide a summary with:
   - **Good**: What's working well
   - **Issues**: Any problems or concerns (with file:line references)
   - **Suggestions**: Improvements to consider
   - **Missing**: Tests, docs, or CHANGELOG entries needed

5. If issues found, ask if user wants to address them before committing

**Do not commit** - this command only reviews changes.
