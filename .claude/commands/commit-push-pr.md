---
description: "Commit, push, and open a PR"
---

Follow these steps in order:

1. **Review Changes**:
   - Run `git status` to see what files have changed
   - Run `git diff` to review the detailed changes
   - Run `git log --oneline -5` to see recent commits for message style

2. **Verify Quality**:
   - Check that CHANGELOG.md is updated under `## [Unreleased]`
   - Verify no secrets or credentials in changes
   - Ensure OAuth/session handling is secure if modified

3. **Stage and Commit**:
   - Stage relevant files with `git add`
   - Create commit with clear message following conventional commits format
   - Include co-author tag:
     ```
     Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>
     ```

4. **Push**:
   - Push to remote branch (create with `-u origin <branch>` if needed)
   - Run `git status` to verify push success

5. **Create Pull Request**:
   - Use `gh pr create` with:
     - **Title**: Clear summary of changes
     - **Description**:
       ```markdown
       ## Summary
       - [List of key changes]

       ## Testing
       - [ ] Manual testing completed
       - [ ] Unit tests pass
       - [ ] No regressions in OAuth flow

       ## Notes
       - [Any additional context for reviewers]

       🤖 Generated with [Claude Code](https://claude.com/claude-code)
       ```

6. Return the PR URL when complete

If there are any issues at any step, stop and report them.
