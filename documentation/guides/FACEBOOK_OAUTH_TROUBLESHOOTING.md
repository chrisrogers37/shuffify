# Facebook OAuth Troubleshooting Guide

This guide helps resolve issues with Facebook login for Spotify OAuth in Shuffify.

## Common Issues and Solutions

### 1. OAuth Callback Errors

**Symptoms:** User authenticates with Facebook but gets redirected back to login page without being logged in.

**Debugging Steps:**
1. Check application logs for OAuth errors
2. Visit `/debug/oauth` endpoint to verify configuration
3. Run `python test_oauth.py` to test OAuth setup

**Common Causes:**
- Incorrect redirect URI in Spotify Developer Dashboard
- Missing or incorrect environment variables
- Session cookie issues
- Facebook login flow interruption

### 2. Redirect URI Mismatch

**Solution:** Ensure your redirect URI in Spotify Developer Dashboard exactly matches your application's callback URL.

**For Development:**
```
http://localhost:8000/callback
```

**For Production:**
```
https://yourdomain.com/callback
```

**Important:** The URI must match exactly, including protocol (http/https), port number, and path.

### 3. Session Issues

**Symptoms:** User appears to authenticate but session is lost immediately.

**Solutions:**
- Check browser cookie settings
- Ensure SameSite cookie policy is set to 'Lax'
- Verify session storage is working

### 4. Facebook-Specific Issues

**Facebook Login Flow:**
1. User clicks "Login with Facebook" on Spotify
2. Facebook authenticates user
3. Facebook redirects back to Spotify
4. Spotify processes Facebook authentication
5. Spotify redirects to your app with authorization code
6. Your app exchanges code for access token

**Common Facebook Issues:**
- Facebook app not configured for web OAuth
- Missing Facebook app domain configuration
- Facebook login permissions not granted

## Debugging Steps

### Step 1: Check Environment Variables

Ensure these are set correctly:
```bash
SPOTIFY_CLIENT_ID=your_client_id
SPOTIFY_CLIENT_SECRET=your_client_secret
SPOTIFY_REDIRECT_URI=http://localhost:8000/callback
```

### Step 2: Verify Spotify App Configuration

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Select your app
3. Go to "Edit Settings"
4. Verify redirect URI matches exactly
5. Ensure app is not in development mode (if testing with non-registered users)

### Step 3: Test OAuth Flow

1. Run the test script:
   ```bash
   python test_oauth.py
   ```

2. Check the debug endpoint:
   ```
   http://localhost:8000/debug/oauth
   ```

### Step 4: Monitor Application Logs

Look for these log messages:
- `"Callback request - Args:"` - Shows what Spotify sends back
- `"OAuth error received:"` - Indicates OAuth errors
- `"Token received successfully"` - Confirms successful token exchange
- `"User X successfully authenticated"` - Confirms successful login

### Step 5: Browser Developer Tools

1. Open browser developer tools (F12)
2. Go to Network tab
3. Attempt Facebook login
4. Look for:
   - Failed requests to `/callback`
   - Cookie issues
   - Redirect chain problems

## Enhanced Error Handling

The updated code includes:

1. **OAuth Error Detection:** Checks for `error` parameter in callback
2. **Token Validation:** Validates token structure before using
3. **Session Management:** Properly clears and manages session data
4. **Detailed Logging:** Comprehensive logging for debugging
5. **Graceful Fallbacks:** Handles partial failures gracefully

## Testing Facebook Login

### Manual Test:
1. Clear browser cookies and cache
2. Visit your application
3. Click "Login with Spotify"
4. Choose "Continue with Facebook"
5. Complete Facebook authentication
6. Verify you're redirected to dashboard

### Automated Test:
```bash
# Test OAuth configuration
python test_oauth.py

# Check application logs
tail -f logs/app.log
```

## Production Considerations

1. **HTTPS Required:** Use HTTPS in production
2. **Secure Cookies:** Set `SESSION_COOKIE_SECURE = True`
3. **Domain Configuration:** Update redirect URI for production domain
4. **Facebook App Settings:** Configure Facebook app for production domain

## Common Error Messages

- `"No code received in callback"` - OAuth flow interrupted
- `"Invalid token structure"` - Token exchange failed
- `"Failed to get user data with token"` - Token validation failed
- `"OAuth Error: access_denied"` - User denied permissions
- `"illegal scope"` - Invalid OAuth scope requested (check scope configuration)

## Getting Help

If issues persist:

1. Check application logs for detailed error messages
2. Verify all environment variables are set correctly
3. Test with a regular Spotify account (not Facebook)
4. Check Spotify Developer Dashboard for app status
5. Verify Facebook app configuration if using Facebook login

## Recent Improvements

The code has been updated with:

- Enhanced OAuth error handling
- Better session management
- Improved logging for debugging
- Token validation
- Debug endpoints for troubleshooting

**Note:** The `user-read-birthdate` scope was removed as it's not a valid Spotify API scope and was causing "illegal scope" errors.
