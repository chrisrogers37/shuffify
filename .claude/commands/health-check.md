---
description: "Check application health and configuration"
---

Run these health checks in order:

## 1. Environment Variables

Check required environment variables are set:
```bash
# Check if variables are set (don't show values for security)
python -c "
import os
required = ['SPOTIFY_CLIENT_ID', 'SPOTIFY_CLIENT_SECRET', 'SECRET_KEY', 'FLASK_ENV']
missing = [var for var in required if not os.getenv(var)]
if missing:
    print(f'❌ Missing: {missing}')
else:
    print('✅ All required environment variables set')
"
```

## 2. Dependencies

Check Python dependencies:
```bash
pip check
```

## 3. Application Structure

Verify key files exist:
```bash
ls -1 shuffify/__init__.py shuffify/routes.py shuffify/spotify/client.py config.py run.py 2>/dev/null | wc -l
# Should output: 5
```

## 4. Health Endpoint (if running)

If the application is running:
```bash
curl -s http://localhost:5000/health
# Should return: {"status": "healthy"}
```

## 5. Flask Routes

List all registered routes:
```bash
flask routes
```

## Summary

Report:
- ✅ What's working correctly
- ⚠️ Any warnings or concerns
- ❌ What's broken or missing
- 💡 Suggested fixes for any issues
