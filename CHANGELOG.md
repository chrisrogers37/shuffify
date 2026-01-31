# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Redis Session Storage** - Migrated from filesystem to Redis-based sessions
  - Added `redis>=5.0.0` dependency for session and caching support
  - Configurable via `REDIS_URL` environment variable
  - Automatic fallback to filesystem sessions if Redis unavailable
  - Session keys prefixed with `shuffify:session:` for namespacing
  - Graceful degradation with logging when Redis connection fails
- **Redis API Caching** - Implemented caching layer for Spotify API responses
  - `SpotifyCache` class in `shuffify/spotify/cache.py` for centralized cache management
  - Configurable TTLs per data type: playlists (60s), user data (600s), audio features (24h)
  - Automatic cache invalidation after playlist modifications
  - `skip_cache` parameter for bypassing cache when fresh data needed
  - Partial cache support for audio features (fetch only uncached tracks)
  - 45 new tests for cache functionality and API caching integration
- **Flask 3.x Upgrade** - Upgraded from Flask 2.3.3 to Flask 3.1.x
  - Updated Flask-Session from 0.5.0 to 0.8.0 for Flask 3.x compatibility
  - Replaced deprecated `FLASK_ENV` config with `CONFIG_NAME` (removed in Flask 3.0)
  - Updated `datetime.utcnow()` to `datetime.now(timezone.utc)` (deprecated in Python 3.12)
  - All 303 tests passing with Flask 3.x
- **Spotify API Retry Logic** - Added exponential backoff for transient errors
  - Automatic retry on rate limits (429) with Retry-After header support
  - Automatic retry on server errors (500, 502, 503, 504)
  - Automatic retry on network errors (ConnectionError, Timeout)
  - Configurable max retries (4) with exponential backoff (2s, 4s, 8s, 16s)
  - 12 new unit tests covering all retry scenarios

### Changed
- **SpotifyAPI** - Now supports optional caching via `cache` parameter
  - Methods accept `skip_cache` parameter to bypass cache when needed
  - Cache automatically invalidated after playlist updates
- **SpotifyClient** - Updated to pass cache to internal SpotifyAPI
  - Optional `cache` parameter in constructor for caching support
- **App Factory** - Enhanced with Redis initialization and helper functions
  - `get_redis_client()` to access the global Redis connection
  - `get_spotify_cache()` to obtain configured SpotifyCache instance

### Security
- **Critical Dependency Updates** - Fixed multiple security vulnerabilities across dependencies
  - `cryptography` 41.0.7 → 46.0.4 (fixes CVE-2023-50782, CVE-2024-0727, PYSEC-2024-225, GHSA-h4gh-qq45-vh27)
  - `python-jose` 3.3.0 → 3.5.0 (fixes PYSEC-2024-232, PYSEC-2024-233)
  - `black` 23.7.0 → 26.1.0 (fixes PYSEC-2024-48)
  - `safety` 2.3.5 → 3.x (resolves dependency conflict, newer security scanning)
- **Docker Build Security** - Upgraded pip, setuptools, and wheel in Dockerfile
  - Addresses CVE-2025-8869 (pip), CVE-2024-6345 (setuptools), CVE-2026-24049 (wheel)
  - Build tools upgraded before installing project dependencies

### Added
- **Spotify Module Refactoring** (Phase 3) - Split SpotifyClient into modular components
  - `SpotifyCredentials` - Immutable dataclass for OAuth credentials, enabling dependency injection
  - `SpotifyAuthManager` - Dedicated class for OAuth flow, token exchange, and refresh
  - `SpotifyAPI` - Dedicated class for all Spotify Web API data operations
  - `TokenInfo` - Type-safe container for token data with validation and expiration checking
  - Comprehensive exception hierarchy (`SpotifyError`, `SpotifyAuthError`, `SpotifyTokenError`, etc.)
- **Comprehensive Test Suite** - 303 tests total, all passing
  - 32 new Spotify module tests (credentials, auth, API)
  - 99 new algorithm unit tests (BasicShuffle, BalancedShuffle, PercentageShuffle, StratifiedShuffle)
  - 12 integration tests covering full application flow
- **Pydantic Validation Layer** (Phase 2) - Type-safe request validation using Pydantic v2
  - `ShuffleRequest` schema with algorithm parameter validation
  - `PlaylistQueryParams` for query parameter validation
  - Algorithm-specific parameter schemas with constraints
  - `parse_shuffle_request()` utility for form data parsing
- **Global Error Handlers** - Centralized exception handling for all routes
  - Consistent JSON error responses across all endpoints
  - Handlers for `ValidationError`, `AuthenticationError`, `PlaylistError`, etc.
  - HTTP status code handlers (400, 401, 404, 500)

### Changed
- **SpotifyClient Refactored** - Now a facade that delegates to SpotifyAuthManager and SpotifyAPI
  - Maintains full backward compatibility with existing code
  - Hidden Flask dependency removed (credentials now explicitly passed)
  - Token refresh bug fixed (was using disabled cache_handler)
- **Routes Simplified** - Removed try/except boilerplate, delegating to global handlers
- **ShuffleService Refactored** - Removed manual parameter parsing (now handled by Pydantic)
- **Dependencies** - Added `pydantic>=2.0.0` to requirements

### Technical Improvements
- **Modularity Score** - Increased from 7.5/10 to 8.5/10 (Phase 3 completion)
- **Test Coverage** - 303 total tests (up from 139), all passing
- **Code Quality** - Clean separation of auth, API, and facade concerns
- **Dependency Injection** - SpotifyCredentials enables proper DI patterns
- **Token Management** - Proper token refresh with auto-refresh capability

---

## [Future]

### Planned Features
- A "Refresh Playlists" button to re-fetch playlists from Spotify without losing the current undo/redo state.
- Implement Facebook and Apple authentication flows to provide more login options.

### Planned Infrastructure Improvements
- ~~**Session Security**: Migration from filesystem sessions to Redis or database-backed sessions~~ (Completed)
- ~~**Caching Strategy**: Implement Redis caching for Spotify API responses~~ (Completed)
- **CI/CD Pipeline**: Automated testing and deployment pipeline
- **Database Integration**: Lightweight database for user preferences and analytics

### [2.3.6] - 2025-08-31

#### Fixed
- **Facebook OAuth Support**: Resolved critical issue preventing Facebook-authenticated Spotify accounts from logging in
  - Enhanced OAuth error handling to detect and report authentication failures
  - Added comprehensive token validation to prevent crashes from malformed tokens
  - Improved session management with proper cleanup on authentication errors
  - Removed invalid `user-read-birthdate` scope that was causing "illegal scope" errors
  - Added detailed logging throughout the OAuth flow for better debugging

#### Changed
- **Landing Page Updates**: Updated to reflect development mode status
  - Added prominent development mode notice with contact information for user whitelisting
  - Removed misleading "Trusted by Music Lovers" social proof section
  - Updated testimonial section to "Why I Built This" with more authentic messaging
  - Changed "Enjoy" step to "Reorder" in "How It Works" section for clarity
  - Updated testimonial text to better reflect the app's purpose
  - Improved layout with proper right-alignment for testimonial attribution

#### Added
- **Enhanced Error Handling**: Comprehensive OAuth error detection and user-friendly error messages
- **Development Mode Communication**: Clear messaging about app status and whitelisting requirements
- **Improved Logging**: Detailed debug information for OAuth troubleshooting

#### Removed
- **Development Tools**: Removed `/debug/oauth` endpoint and `test_oauth.py` script (development-only tools)
- **Invalid OAuth Scope**: Removed `user-read-birthdate` scope that was causing authentication failures
- **Misleading Content**: Removed fake social proof metrics and inappropriate testimonials

#### Technical Improvements
- **Session Configuration**: Updated session security settings for better OAuth compatibility
- **Token Validation**: Added robust token structure validation before API calls
- **Error Recovery**: Improved session cleanup on authentication failures

### [2.3.5] - 2025-08-31

#### Added
- **Comprehensive UX Review**: Complete frontend landing page analysis and renovation plan
- **Enhanced Legal Consent**: Redesigned consent form with "Quick & Secure" messaging and improved visual appeal
- **Dynamic CTA Button**: Progressive enhancement with dynamic subtext that changes based on consent checkbox state
- **Social Proof Section**: Added "Trusted by Music Lovers" with realistic stats (1K+ playlists, 100+ users) and user testimonial
- **How It Works Section**: Clear 3-step process explanation (Connect, Choose, Enjoy) positioned for optimal user flow
- **Use Cases Section**: Four targeted cards for different user types (Curated Collections, Tastemaker Playlists, New Perspectives, Playlist Maintenance)
- **Trust Indicators**: Added security and privacy badges (Secure OAuth, No Data Stored, Instant Results, Free Forever)
- **User Testimonial**: Added specific testimonial from playlist curator with 5-star rating
- **Logout Functionality**: Added logout button to dashboard for better session management and user switching
- **Accessibility Improvements**: Added skip links, ARIA labels, focus states, and screen reader support
- **Scroll Animations**: Added intersection observer for smooth scroll-triggered animations
- **Custom Scrollbar**: Enhanced scrollbar styling for better visual consistency

#### Changed
- **Hero Section Copy**: Updated to target "tastemakers" and "reorder carefully curated Spotify playlists" instead of generic shuffling
- **CTA Button Text**: Changed from "Connect with Spotify" to "Start Reordering Now" with dynamic subtext
- **Section Ordering**: Moved "How It Works" above "Trusted by Music Lovers" for better information architecture
- **Spacing Optimization**: Reduced excessive padding between sections for better visual flow and scrolling experience
- **Feature Descriptions**: Updated to emphasize "reordering" instead of "shuffling" for clarity
- **Color Scheme**: Enhanced legal links with Spotify green color for better brand consistency
- **Responsive Design**: Improved spacing and layout across all screen sizes

#### Fixed
- **Legal Consent UX**: Transformed required consent from friction point to positive security feature
- **Information Architecture**: Reordered sections for more logical user journey
- **Visual Hierarchy**: Improved spacing and typography for better content digestion
- **Mobile Experience**: Optimized touch targets and spacing for mobile devices
- **Session Management**: Added proper logout functionality for user switching

#### Technical Improvements
- **Tailwind Config**: Extended with custom animations (fade-in, slide-up, scale-in)
- **Global CSS**: Added accessibility styles, focus states, and custom scrollbar
- **JavaScript**: Added dynamic CTA updates and scroll-triggered animations
- **Template Structure**: Improved semantic HTML with proper ARIA labels and skip links

### [2.3.4] - 2025-08-31

#### Security
- **Environment variable validation**: Added fail-fast validation for required environment variables in production
- **Dependency security updates**: Updated non-breaking packages to latest versions for security improvements
- **Security scanning tools**: Added safety and bandit to development environment for vulnerability detection

#### Added
- **Environment validation**: Added startup validation for SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET
- **Development security tools**: Added safety==2.3.5 and bandit==1.7.5 for automated security scanning
- **Improved error handling**: Better logging and error messages for missing environment variables

#### Changed
- **Package updates**: Updated spotipy (2.23.0 → 2.25.1), requests (2.31.0 → 2.32.5), python-dotenv (1.0.0 → 1.1.1), gunicorn (21.2.0 → 23.0.0), numpy (1.24.3 → >=1.26.0)
- **Conservative approach**: Kept Flask 2.3.3 for compatibility while updating other packages
- **Production safety**: Application now fails fast in production with missing environment variables

#### Notes
- **Flask 3.x consideration**: Flask was kept at 2.3.3 for compatibility. A future major version upgrade to Flask 3.x is planned with comprehensive testing and migration guide.

### [2.3.3] - 2025-08-31

#### Security
- **Fixed critical security vulnerability**: Changed session directory permissions from `chmod 777` to `chmod 755` with proper ownership (`nobody:nogroup`)
- **Added Docker security hardening**: Implemented proper file permissions and ownership for production containers

#### Added
- **Health check endpoint**: Added `/health` route returning JSON status for monitoring and container orchestration
- **Docker health checks**: Added `HEALTHCHECK` directive with curl-based monitoring
- **Container monitoring**: Added curl dependency for health check functionality

#### Fixed
- **Tailwind CSS configuration**: Fixed content paths from `./app/templates/**/*.html` to `./shuffify/templates/**/*.html` for proper asset optimization
- **Build optimization**: Ensured Tailwind classes are properly purged in production builds

#### Changed
- **Dockerfile improvements**: Enhanced container security and operational readiness
- **Infrastructure documentation**: Updated `dev_guides/infrastructure_critiques.md` to track completed fixes

### [2.3.2] - 2025-01-27

#### Fixed
- Fixed shuffle algorithm inheritance issues - all algorithms now properly inherit from ShuffleAlgorithm base class
- Corrected Balanced Shuffle description in global README to accurately reflect playlist position-based shuffling (not artist/genre-based)
- Fixed CHANGELOG contradictions - moved unimplemented "Refresh Playlists" and "Logout" features to "Planned Features" section

#### Changed
- Enhanced shuffle algorithm documentation with detailed examples, use cases, and comparison table
- Updated shuffle algorithms README to reflect current implementations without audio features
- Improved algorithm descriptions and parameter documentation

#### Added
- Comprehensive infrastructure critiques and recommendations document in `dev_guides/infrastructure_critiques.md`
- Algorithm comparison table to help users choose appropriate shuffle methods
- Detailed use cases for all shuffle algorithms

### [2.3.1] - 2025-06-22

#### Fixed
- Resolved a critical bug preventing the multi-level "Undo" feature from working correctly. The session state is now managed robustly, allowing users to undo multiple shuffles in a row.
- Addressed a frontend issue where the "Undo" button would incorrectly disappear after a single use.
- Fixed a CSS regression where legal links on the index page were incorrectly styled.
- Replaced the hover-to-open mechanic on playlist tiles with a more stable click-to-open system to improve UX and prevent visual bugs.

### [2.3.0] - 2025-06-22

### Added
- Terms of Service and Privacy Policy pages for Spotify compliance
- Legal consent checkbox on login page
- Required user agreement before Spotify authentication
- Legal document routes and templates

### Changed
- Updated login flow to require explicit legal consent
- Enhanced UI for compliance with Spotify Developer Policy

## [2.2.4] - 2025-01-27

### Changed
- UI updates for Spotify compliance
- Removed follower count display from playlist cards
- Updated menu dropdown functionality
- Improved playlist model to handle follower visibility

### Fixed
- Spotify API compliance issues with follower data display

## [2.2.3] - 2025-01-26

### Changed
- Simplified feature logic for better performance
- Removed complex feature calculations that were causing issues

## [2.2.2] - 2025-01-25

### Added
- Playlist class implementation for better data management
- Improved playlist data handling and structure

### Changed
- Refactored playlist handling to use dedicated class
- Enhanced playlist model architecture

## [2.2.1] - 2025-01-24

### Changed
- Consolidated code structure for better maintainability
- Improved code organization and efficiency

## [2.2.0] - 2025-01-23

### Added
- Enhanced vibe-based shuffle method with improved audio feature analysis
- Better audio feature weighting and transition calculations

### Changed
- Improved vibe shuffle algorithm performance
- Enhanced audio feature processing

## [2.1.0] - 2025-01-22

### Added
- Stratified shuffle method for section-based shuffling
- New shuffle algorithm for maintaining playlist structure

### Changed
- Updated algorithm descriptions and documentation
- Improved algorithm package structure

## [2.0.3] - 2025-01-21

### Changed
- Updated text descriptors throughout the application
- Improved user interface text and descriptions
- Enhanced algorithm documentation

## [2.0.2] - 2025-01-20

### Changed
- Updated algorithm README to reflect current functionality
- Improved documentation for available shuffle methods

## [2.0.1] - 2024-04-21

### Changed
- Updated README.md to reflect current project structure
- Removed references to deprecated features (Vibe Shuffle, Docker directory)
- Updated documentation for current shuffle algorithms
- Improved project structure documentation
- Updated development workflow instructions

## [2.0.0] - 2024-04-20

### Added
- Multiple shuffle algorithms:
  - Basic Shuffle: Standard random shuffle with fixed start option
  - Balanced Shuffle: Ensures fair representation from all playlist parts
  - Percentage Shuffle: Allows shuffling specific portions of playlists
- Decorative music note patterns in background
- Smooth hover and transition effects
- Improved form input styling
- Better visual feedback for interactive elements
- Undo functionality for shuffle operations
- Detailed algorithm documentation
- Requirements management structure (base.txt, dev.txt, prod.txt)

### Changed
- Completely redesigned UI with modern glassmorphism effects
- Extended gradient background across all pages
- Improved visual hierarchy and spacing
- Enhanced playlist card interactions
- Updated color scheme for better contrast and readability
- Streamlined navigation by removing unnecessary elements
- Restructured project organization
- Improved error handling and logging
- Enhanced algorithm parameter validation

### Fixed
- Inconsistent styling between landing and dashboard pages
- Full-width background coverage issues
- Visual hierarchy in playlist cards
- Form input contrast and accessibility
- Default values for algorithm parameters
- Session handling and caching issues

### Removed
- Temporarily hidden Vibe Shuffle algorithm for future development
- Redundant configuration files
- Unused dependencies

## [1.0.0] - 2024-04-19

### Added
- Initial stable release
- Core playlist shuffling functionality
- Basic UI with Tailwind CSS
- Spotify integration 