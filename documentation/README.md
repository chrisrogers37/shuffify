# Shuffify Documentation

This directory contains all project documentation organized by purpose.

---

## Directory Structure

```
documentation/
├── README.md          # This file - documentation index
├── evaluation/        # System evaluations and assessments
│   ├── README.md
│   ├── 01_architecture_evaluation.md
│   ├── 02_modularity_assessment.md
│   ├── 03_extensibility_evaluation.md
│   ├── 04_future_features_readiness.md
│   └── 05_brainstorm_enhancements.md
├── planning/          # Design docs, architecture decisions
│   ├── phase_0_test_foundation.md    # ✅ Complete
│   └── separation_of_concerns_evaluation.md
├── guides/            # How-to guides and tutorials (planned)
├── updates/           # Dated bug fixes, patches, hotfixes (planned)
└── operations/        # Production runbooks (planned)
```

---

## System Evaluation

Comprehensive system review documents for development planning:

- **[Evaluation Overview](evaluation/README.md)** - Summary and reading guide
- **[Architecture Evaluation](evaluation/01_architecture_evaluation.md)** - Three-layer architecture analysis
- **[Modularity Assessment](evaluation/02_modularity_assessment.md)** - Code modularity and coupling analysis
- **[Extensibility Evaluation](evaluation/03_extensibility_evaluation.md)** - Service extensibility patterns
- **[Future Features Readiness](evaluation/04_future_features_readiness.md)** - Readiness for planned features
- **[Brainstorm Enhancements](evaluation/05_brainstorm_enhancements.md)** - Additional enhancement ideas

---

## Planning & Architecture

### Development Phases

| Phase | Status | Document |
|-------|--------|----------|
| **Phase 0: Test Foundation** | ✅ Complete | [phase_0_test_foundation.md](planning/phase_0_test_foundation.md) |
| **Phase 1A: Service Layer** | 🔜 Next | Planned |
| **Phase 1B: Database** | 🔜 Next | Planned |
| **Phase 1C: UI Improvements** | 🔜 Next | Planned |

### Existing Documents

- **[Phase 0: Test Foundation](planning/phase_0_test_foundation.md)** ✅ Complete
  - Comprehensive test suite (176 tests)
  - 100% coverage on shuffle algorithms, models, config
  - Prerequisite for all future phases

- **[Separation of Concerns Evaluation](planning/separation_of_concerns_evaluation.md)**
  - Evaluation of current codebase architecture
  - Layer boundary analysis
  - Recommendations for maintaining separation

### Planned Documents

- Phase 1A: Service Layer Extraction
- Phase 1B: Database Integration Design
- Architecture Decision Records (ADRs)
- Flask 3.x upgrade planning
- Redis session storage migration plan
- Algorithm performance comparison framework

---

## Guides (Planned)

Future how-to guides and tutorials:

- **Spotify Setup Guide** - OAuth app configuration on Spotify Developer Dashboard
- **Algorithm Development Guide** - Step-by-step for creating new shuffle algorithms
- **Deployment Guide** - Production deployment procedures
- **Docker Setup Guide** - Container configuration and deployment
- **Testing Guide** - Running and writing tests

---

## Updates (Planned)

Dated bug fixes, patches, and hotfixes:

Format: `YYYY-MM-DD-description.md`

Examples:
- `2026-02-15-oauth-security-patch.md`
- `2026-03-10-session-storage-bugfix.md`

---

## Operations (Planned)

Production runbooks and procedures:

- **Monitoring** - Health checks, metrics, alerting
- **Backups** - Session data, configuration
- **Troubleshooting** - Common issues and solutions
- **Incident Response** - Procedures for outages
- **Rollback Procedures** - How to revert deployments

---

## Quick Links

### Root-Level Documentation

- [README.md](../README.md) - Project overview and quick start
- [CHANGELOG.md](../CHANGELOG.md) - Version history
- [CLAUDE.md](../CLAUDE.md) - Developer guide for AI assistants
- [LICENSE](../LICENSE) - MIT License

### Development Guides

- [dev_guides/infrastructure_critiques.md](../dev_guides/infrastructure_critiques.md)
- [dev_guides/UX_CRITIQUES.md](../dev_guides/UX_CRITIQUES.md)
- [dev_guides/FACEBOOK_OAUTH_TROUBLESHOOTING.md](../dev_guides/FACEBOOK_OAUTH_TROUBLESHOOTING.md)

### Algorithm Documentation

- [shuffify/shuffle_algorithms/README.md](../shuffify/shuffle_algorithms/README.md)

---

## Contributing to Documentation

### Where to Put New Documentation

| Document Type | Location | Example |
|--------------|----------|---------|
| System evaluations | `evaluation/` | `evaluation/01_architecture_evaluation.md` |
| Architecture decisions | `planning/` | `planning/redis-migration-adr.md` |
| How-to guides | `guides/` | `guides/spotify-oauth-setup.md` |
| Bug fixes (dated) | `updates/` | `updates/2026-02-15-session-bug.md` |
| Operations | `operations/` | `operations/deployment.md` |
| Project overview | Root | `README.md` |
| Version history | Root | `CHANGELOG.md` |

### Documentation Standards

1. **Use Markdown** for all documentation
2. **Date Updates** - Use `YYYY-MM-DD` format for dated documents
3. **Link Liberally** - Cross-reference related documents
4. **Keep Root Clean** - Only critical docs in project root
5. **Update This Index** - Add new docs to this README

### Documentation Review Checklist

- [ ] Document has clear title and purpose
- [ ] Content is organized with headers
- [ ] Code examples are syntax-highlighted
- [ ] Links to related documents included
- [ ] Added to this README index
- [ ] CHANGELOG updated if user-facing

---

## Maintenance

This documentation index should be updated whenever:
- New documentation is added
- Documentation is reorganized
- Documentation is deprecated or removed

Last updated: 2026-01-29
