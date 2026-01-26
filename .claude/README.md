# Claude Code Configuration

This directory contains configuration and guidance files for Claude Code development.

---

## Directory Structure

```
.claude/
├── README.md              # This file - explains .claude directory
├── PROJECT_CONTEXT.md     # Quick project context for remote sessions
├── QUICK_REFERENCE.md     # Common commands and shortcuts
├── settings.json          # Claude Code settings (team-shared)
└── commands/              # Slash commands
    ├── quick-commit.md
    ├── review-changes.md
    ├── commit-push-pr.md
    ├── test-and-fix.md
    ├── algorithm-dev.md
    ├── health-check.md
    └── code-review.md
```

---

## Files Overview

### PROJECT_CONTEXT.md

**Purpose**: Concise project context for Claude web/phone sessions.

**Usage**: Copy-paste into Claude.ai/code sessions to give Claude quick context about:
- What the project does
- Architecture overview
- Key files and technologies
- Safety rules
- Common patterns

**Keep it**: Under 200 lines, highly scannable, focused on essentials.

### QUICK_REFERENCE.md

**Purpose**: Ultra-quick reference for common tasks and commands.

**Usage**: Opened frequently during development for:
- Safety rules (what NOT to run)
- Common commands
- Directory structure
- Key files
- Layer boundaries

**Keep it**: Under 100 lines, table-heavy, minimal prose.

### settings.json

**Purpose**: Team-shared Claude Code settings.

**Contains**:
- Pre-approved safe commands (git status, pytest, etc.)
- Permission settings
- File operation permissions

**Guidelines**:
- Only include truly safe, frequently-used commands
- Never add destructive commands (force push, rm -rf, etc.)
- Update when new safe patterns emerge

### commands/

**Purpose**: Reusable workflows for common development tasks.

**Guidelines for creating commands**:
- Use descriptive names (kebab-case)
- Include `description` in frontmatter
- Provide step-by-step instructions
- Include examples where helpful
- Note any prerequisites

**Current commands**:

| Command | Purpose |
|---------|---------|
| `quick-commit.md` | Stage and commit with good message |
| `review-changes.md` | Review uncommitted changes |
| `commit-push-pr.md` | Full git workflow + PR creation |
| `test-and-fix.md` | Run tests and fix failures |
| `algorithm-dev.md` | Guided workflow for new algorithms |
| `health-check.md` | Verify application health |
| `code-review.md` | Check for architecture violations |

**Usage**: Type `/command-name` in Claude Code CLI to invoke.

---

## Usage Patterns

### Local Development (CLI)

```bash
# Use slash commands for common workflows
/quick-commit
/test-and-fix
/algorithm-dev

# Settings.json pre-approves these
git status
pytest tests/ -v
flask routes
```

### Remote Development (Web/Phone)

1. Copy `.claude/PROJECT_CONTEXT.md` into the session
2. Claude now has full context about the project
3. Reference `.claude/QUICK_REFERENCE.md` as needed

---

## Maintenance

### When to Update

**PROJECT_CONTEXT.md**:
- Major architecture changes
- New core technologies
- Key safety rules change
- After major refactors

**QUICK_REFERENCE.md**:
- New common commands
- Directory structure changes
- Safety rules update

**settings.json**:
- New safe command patterns emerge
- Permission requirements change

**commands/**:
- New common workflows identified
- Existing workflows need improvement
- Team requests new automation

### Review Cadence

- **Weekly**: Check if commands are still accurate
- **Monthly**: Review PROJECT_CONTEXT for accuracy
- **Per Release**: Update after major versions

---

## Best Practices

### For PROJECT_CONTEXT.md

✅ **DO**:
- Keep under 200 lines
- Use tables for scannable info
- Include ASCII diagrams for architecture
- List safety rules prominently
- Focus on "what" not "how"

❌ **DON'T**:
- Include implementation details
- Duplicate CLAUDE.md content
- Add tutorial-style content
- Make it comprehensive (that's CLAUDE.md's job)

### For QUICK_REFERENCE.md

✅ **DO**:
- Keep under 100 lines
- Heavy use of tables
- List common commands verbatim
- Include keyboard shortcuts
- Group related info

❌ **DON'T**:
- Explain concepts
- Include long code examples
- Duplicate full documentation
- Add prose explanations

### For Commands

✅ **DO**:
- Provide clear step-by-step instructions
- Include error handling steps
- Show expected output
- Note prerequisites
- Include examples

❌ **DON'T**:
- Make them interactive (prompting user repeatedly)
- Include dangerous operations without warnings
- Assume context (be explicit)
- Skip error cases

---

## Related Files

### Root Level

- `CLAUDE.md` - Comprehensive guide for Claude Code (primary documentation)
- `.claudeignore` - Files to exclude from Claude's context

### Documentation

- `documentation/README.md` - Documentation index
- `documentation/planning/` - Architecture decisions
- `documentation/guides/` - How-to guides

---

## Contributing

When adding new files to `.claude/`:

1. **Update this README** with the new file's purpose
2. **Keep it focused** - each file has one clear purpose
3. **Maintain brevity** - Claude works best with concise docs
4. **Test it** - Try the command/doc with Claude before committing
5. **Update CHANGELOG.md** if user-facing

---

## Questions?

See `CLAUDE.md` for comprehensive project documentation, or ask the team.

Last updated: 2026-01-26
