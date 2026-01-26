# Shuffify Documentation

This directory contains all project documentation organized by purpose.

---

## Directory Structure

```
documentation/
├── README.md          # This file - documentation index
├── planning/          # Design docs, architecture decisions
│   └── separation_of_concerns_evaluation.md
├── guides/            # How-to guides and tutorials (planned)
├── updates/           # Dated bug fixes, patches, hotfixes (planned)
└── operations/        # Production runbooks (planned)
```

---

## Planning & Architecture

### Existing Documents

- **[Separation of Concerns Evaluation](planning/separation_of_concerns_evaluation.md)**
  - Evaluation of current codebase architecture
  - Layer boundary analysis
  - Recommendations for maintaining separation

### Planned Documents

- Architecture Decision Records (ADRs)
- Flask 3.x upgrade planning
- Redis session storage migration plan
- Database integration design
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

Last updated: 2026-01-26
