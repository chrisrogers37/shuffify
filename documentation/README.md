# Shuffify Documentation

This directory contains all project documentation organized by purpose.

---

## Directory Structure

```
documentation/
├── README.md          # This file - documentation index
├── production-database-setup.md  # Neon PostgreSQL setup guide
├── evaluation/        # Active system evaluations and assessments
│   ├── README.md
│   ├── 03_extensibility_evaluation.md
│   ├── 04_future_features_readiness.md
│   └── 05_brainstorm_enhancements.md
├── guides/            # How-to guides and critiques
│   ├── credential-rotation.md
│   ├── FACEBOOK_OAUTH_TROUBLESHOOTING.md
│   ├── infrastructure_critiques.md
│   └── UX_CRITIQUES.md
├── planning/          # Development plans and feature phases
│   └── phases/        # (empty — all archived)
└── archive/           # Completed evaluations, plans, and design docs
    ├── 01_architecture_evaluation.md
    ├── 02_modularity_assessment.md
    ├── separation_of_concerns_evaluation.md
    ├── raid-playlist-redesign_2026-03-23.md
    ├── swap-only-rotation-design_2026-03-10.md
    ├── tech_debt_q1-2026_2026-02-10/
    ├── post-workshop-cleanup_2026-02-11/
    ├── playlist-workshop_2026-02-10/
    ├── user-persistence_2026-02-12/
    ├── workshop-powertools_2026-02-13/
    ├── security-audit_2026-02-15/
    ├── tech-debt-cleanup_2026-02-19/
    ├── landing-page-redesign_2026-02-22/
    ├── codebase-cleanup_2026-02-22/
    ├── structural-cleanup_2026-02-25/
    ├── dashboard-enhancements_2026-02-25/
    ├── spotify-api-migration_2026-02-28/
    ├── scheduling-workshop-overhaul_2026-03-02/
    ├── dropdown-design-alignment_2026-03-28/
    ├── navigation-overhaul_2026-03-29/
    └── workshop-tab-restructure_2026-04-01/
```

---

## System Evaluation (Active)

Active evaluation documents for ongoing development planning:

- **[Evaluation Overview](evaluation/README.md)** - Summary and reading guide
- **[Extensibility Evaluation](evaluation/03_extensibility_evaluation.md)** - Service extensibility patterns, plugin architecture proposals
- **[Future Features Readiness](evaluation/04_future_features_readiness.md)** - Readiness for planned features (database, automations, notifications, UI)
- **[Brainstorm Enhancements](evaluation/05_brainstorm_enhancements.md)** - Enhancement ideas and tracking

## Guides

- **[Production Database Setup](production-database-setup.md)** - How to connect PostgreSQL (Neon, Railway, Docker, or any instance) to your Shuffify deployment
- **[Credential Rotation](guides/credential-rotation.md)** - How to rotate every secret used by the application
- **[Infrastructure Critiques](guides/infrastructure_critiques.md)** - Infrastructure improvement notes and status
- **[UX Critiques](guides/UX_CRITIQUES.md)** - UX improvement notes for landing page
- **[Facebook OAuth Troubleshooting](guides/FACEBOOK_OAUTH_TROUBLESHOOTING.md)** - Legacy OAuth troubleshooting guide

## Planning (Active)

No active plans — all sessions archived.

## Planning (Archived)

All development plans have been completed and archived:

- **[Playlist Workshop Enhancement Suite](archive/playlist-workshop_2026-02-10/00_OVERVIEW.md)** - 6-phase plan for workshop features (all phases completed, PRs #43-48)
- **[User Persistence Enhancement Suite](archive/user-persistence_2026-02-12/00_OVERVIEW.md)** - 7-phase plan: PostgreSQL, user dimension, login tracking, settings, snapshots, activity log, dashboard (all phases completed, PRs #56-62)
- **[Workshop Powertools Enhancement Suite](archive/workshop-powertools_2026-02-13/00_OVERVIEW.md)** - 5-phase plan: sidebar framework, snapshot browser, archive pairing, smart raid, scheduled rotation (all phases completed, PRs #64-68)
- **[Tech Debt Cleanup](archive/tech-debt-cleanup_2026-02-19/00_TECH_DEBT.md)** - 5-phase plan: route cleanup, service deduplication, function decomposition, route tests, schema tests (all phases completed, PRs #83-87)

## Archived (Completed)

Evaluations and plans that have been fully implemented:

- **[Architecture Evaluation](archive/01_architecture_evaluation.md)** - All critical recommendations implemented
- **[Modularity Assessment](archive/02_modularity_assessment.md)** - All 4 refactoring phases completed
- **[Separation of Concerns Evaluation](archive/separation_of_concerns_evaluation.md)** - Service layer extracted, all recommendations done
- **[Tech Debt Q1 2026](archive/tech_debt_q1-2026_2026-02-10/00_TECH_DEBT.md)** - Completed tech debt remediation session (6 items)
- **[Post-Workshop Cleanup](archive/post-workshop-cleanup_2026-02-11/00_TECH_DEBT.md)** - Post-workshop tech debt (4 phases: tests, enums, route split, housekeeping)
- **[Playlist Workshop Plans](archive/playlist-workshop_2026-02-10/00_OVERVIEW.md)** - Workshop feature implementation plans (6 phases)
- **[User Persistence Plans](archive/user-persistence_2026-02-12/00_OVERVIEW.md)** - User persistence implementation plans (7 phases)
- **[Workshop Powertools Plans](archive/workshop-powertools_2026-02-13/00_OVERVIEW.md)** - Workshop powertools implementation plans (5 phases)
- **[Security Audit](archive/security-audit_2026-02-15/00_SECURITY_AUDIT.md)** - Security audit and remediation (5 phases)
- **[Tech Debt Cleanup](archive/tech-debt-cleanup_2026-02-19/00_TECH_DEBT.md)** - Tech debt remediation (5 phases, PRs #83-87)
- **[Landing Page Redesign](archive/landing-page-redesign_2026-02-22/00_OVERVIEW.md)** - Dark theme, animated hero, scroll motion, mobile responsive
- **[Codebase Cleanup](archive/codebase-cleanup_2026-02-22/00_TECH_DEBT.md)** - DB commits, route auth, executor split, template decomposition
- **[Structural Cleanup](archive/structural-cleanup_2026-02-25/00_TECH_DEBT.md)** - Test fixtures, function decomposition, route helpers, JS extraction
- **[Dashboard Enhancements](archive/dashboard-enhancements_2026-02-25/00_OVERVIEW.md)** - Error handling, tile layout, shuffle overlay, playlist management
- **[Spotify API Migration](archive/spotify-api-migration_2026-02-28/00_OVERVIEW.md)** - Direct HTTP client, search fix, raid pivot, graceful degradation
- **[Scheduling Workshop Overhaul](archive/scheduling-workshop-overhaul_2026-03-02/00_OVERVIEW.md)** - Schedule creation fix, raid/rotation config, scheduler scaling
- **[Swap-Only Rotation Design](archive/swap-only-rotation-design_2026-03-10.md)** - Rotation mode design document
- **[Raid Playlist Redesign](archive/raid-playlist-redesign_2026-03-23.md)** - Raid system redesign document
- **[Dropdown Design Alignment](archive/dropdown-design-alignment_2026-03-28/00_OVERVIEW.md)** - Dropdown UI consistency
- **[Navigation Overhaul](archive/navigation-overhaul_2026-03-29/00_OVERVIEW.md)** - Navigation bar, workshop hub, activity log, settings sidebar
- **[Workshop Tab Restructure](archive/workshop-tab-restructure_2026-04-01/00_OVERVIEW.md)** - Horizontal tabs, raids/rotation/schedules/snapshots tabs, sidebar removal

---

## Related Project Documentation

### Root-Level Documents

| File | Description |
|------|-------------|
| [README.md](../README.md) | Project overview and quick start |
| [CHANGELOG.md](../CHANGELOG.md) | Version history |
| [CLAUDE.md](../CLAUDE.md) | Developer guide for AI assistants |

### Algorithm Documentation

- [shuffify/shuffle_algorithms/README.md](../shuffify/shuffle_algorithms/README.md) - All 7 shuffle algorithms with usage details

### Development Guides

- [documentation/guides/infrastructure_critiques.md](guides/infrastructure_critiques.md) - Infrastructure improvement notes
- [documentation/guides/UX_CRITIQUES.md](guides/UX_CRITIQUES.md) - UX improvement notes
- [documentation/guides/FACEBOOK_OAUTH_TROUBLESHOOTING.md](guides/FACEBOOK_OAUTH_TROUBLESHOOTING.md) - Legacy OAuth troubleshooting

---

## Contributing to Documentation

### Where to Put New Documentation

| Document Type | Location | Example |
|--------------|----------|---------|
| System evaluations | `evaluation/` | `evaluation/06_performance_review.md` |
| Completed evaluations | `archive/` | `archive/01_architecture_evaluation.md` |
| Project overview | Root | `README.md` |
| Version history | Root | `CHANGELOG.md` |

### Documentation Standards

1. **Use Markdown** for all documentation
2. **Date Updates** - Use `YYYY-MM-DD` format for dated documents
3. **Link Liberally** - Cross-reference related documents
4. **Keep Root Clean** - Only critical docs in project root
5. **Update This Index** - Add new docs to this README
6. **Development Plans** - Mark phases as PENDING, IN PROGRESS, or COMPLETED

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
- Documentation is deprecated or archived

Last updated: 2026-04-05
