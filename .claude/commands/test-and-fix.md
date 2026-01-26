---
description: "Run tests and fix any failures"
---

Follow these steps in order:

1. **Run Tests**:
   ```bash
   pytest tests/ -v
   ```

2. **Analyze Results**:
   - If all tests pass:
     - Report success
     - Show test count and coverage if available
     - Stop here

   - If tests fail:
     - Identify which tests failed
     - Read the error messages and stack traces carefully
     - Note the file and line numbers

3. **Investigate Failures**:
   - Read the failing test code to understand what it expects
   - Read the code being tested to find the bug
   - Determine root cause (don't guess)

4. **Fix Issues**:
   - Make targeted fixes to the failing code
   - Explain what was wrong and how you fixed it
   - Run tests again to verify the fix

5. **Iterate**:
   - If tests still fail, repeat steps 3-4
   - If new tests fail, investigate those too
   - Continue until all tests pass

6. **Final Verification**:
   - Run full test suite one more time
   - Verify no regressions introduced
   - Report final status

**Important**:
- Only fix real bugs, don't modify tests to pass
- Don't skip tests or mark them as xfail
- Preserve existing test coverage
- Add new tests if functionality changed
