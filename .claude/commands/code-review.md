---
description: "Review code for architecture violations and best practices"
---

Review the codebase for common issues. Check the following:

## 1. Layer Violation Check

Search for imports that violate the 3-layer architecture:

**Routes should NOT import Spotify API directly (must use client):**
```bash
grep -r "import spotipy" shuffify/routes.py
grep -r "from spotipy" shuffify/routes.py
```

**Templates should NOT contain business logic:**
```bash
grep -rE "{% if.*\.shuffle|{% for.*algorithm" shuffify/templates/ --include="*.html"
```

**Algorithms should NOT make Spotify API calls:**
```bash
grep -r "spotipy\|spotify" shuffify/shuffle_algorithms/ --include="*.py" | grep -v "test" | grep -v "README"
```

## 2. Security Check

**Hardcoded secrets:**
```bash
grep -rE "(client_id|client_secret|secret_key)\s*=\s*['\"]" shuffify/ --include="*.py" | grep -v "config.py" | grep -v "test"
```

**Session token exposure:**
```bash
grep -rE "session\['access_token'\]" shuffify/templates/ --include="*.html"
```

**OAuth redirect URI hardcoded:**
```bash
grep -rE "redirect_uri\s*=\s*['\"]http" shuffify/ --include="*.py" | grep -v "config.py"
```

## 3. Session Management Check

**Undo stack manipulation:**
```bash
grep -r "session\['undo_stack'\]" shuffify/ --include="*.py" | grep -v "\.append\|\.pop\|\.get"
```

**Missing session.modified:**
```bash
# Check for session modifications without session.modified = True
grep -r "session\[" shuffify/routes.py -B2 -A2 | grep -v "session.modified"
```

## 4. Error Handling Check

**Bare except clauses:**
```bash
grep -rE "except:" shuffify/ --include="*.py"
```

**Missing error handling in Spotify API calls:**
```bash
grep -rE "sp\.(current_user|playlist|reorder)" shuffify/ --include="*.py" -A2 | grep -v "try:\|except"
```

**Flask routes without error handling:**
```bash
grep -rE "@main\.route" shuffify/routes.py -A10 | grep -v "try:\|except\|flash"
```

## 5. Algorithm Registration Check

**Algorithms not using decorator:**
```bash
grep -r "class.*Algorithm" shuffify/shuffle_algorithms/ --include="*.py" | grep -v "@register_algorithm"
```

**Algorithms not imported in __init__.py:**
```bash
ls shuffify/shuffle_algorithms/*.py | grep -v __init__ | grep -v registry | xargs -I {} basename {} .py | while read alg; do grep -q "$alg" shuffify/shuffle_algorithms/__init__.py || echo "Missing import: $alg"; done
```

## 6. Test Coverage Check

```bash
pytest tests/ --cov=shuffify --cov-report=term-missing
```

## 7. Documentation Check

**CHANGELOG not updated:**
```bash
# Check if CHANGELOG has [Unreleased] section
grep -q "## \[Unreleased\]" CHANGELOG.md && echo "✅ CHANGELOG has Unreleased section" || echo "❌ CHANGELOG missing Unreleased section"
```

**Algorithms missing documentation:**
```bash
ls shuffify/shuffle_algorithms/*.py | grep -v __init__ | grep -v registry | xargs -I {} basename {} .py | while read alg; do grep -q "$alg" shuffify/shuffle_algorithms/README.md || echo "Missing docs: $alg"; done
```

## 8. Code Quality Check

**Missing type hints on public functions:**
```bash
grep -rE "def [a-z_]+\(" shuffify/ --include="*.py" | grep -v "__" | grep -v "test" | grep -v " ->" | head -10
```

**Missing docstrings on classes:**
```bash
grep -rE "^class [A-Z]" shuffify/ --include="*.py" -A1 | grep -v '"""' | grep "class " | head -5
```

## 9. Report Format

Summarize findings:
- **Critical**: Layer violations, security issues, exposed tokens
- **Warning**: Missing error handling, bare excepts, session management issues
- **Info**: Style improvements, documentation gaps, missing type hints

Provide specific file:line references for each issue found.

If critical or warning issues found, recommend fixes before committing.
