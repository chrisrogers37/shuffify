# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Future]

### Added
- A "Refresh Playlists" button to re-fetch playlists from Spotify without losing the current undo/redo state.
- A "Logout" button to securely end the user's session.

### To Do / In Progress
- Implement Facebook and Apple authentication flows to provide more login options.
- Complete redesign of the pre-login landing page to improve aesthetics and user experience.

## [Unreleased]

### [2.3.1] - 2025-06-22

#### Fixed
- Resolved a critical bug preventing the multi-level "Undo" feature from working correctly. The session state is now managed robustly, allowing users to undo multiple shuffles in a row.
- Addressed a frontend issue where the "Undo" button would incorrectly disappear after a single use.
- Fixed a CSS regression where legal links on the index page were incorrectly styled.
- Replaced the hover-to-open mechanic on playlist tiles with a more stable click-to-open system to improve UX and prevent visual bugs.

### [2.3.0] - 2025-06-22

#### Added
- `LEGAL_GUIDE.md` to outline the process for adding legal documentation.

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