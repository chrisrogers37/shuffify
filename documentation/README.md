# Shuffify Documentation

This directory contains all project documentation organized by purpose.

---

## Directory Structure

```
documentation/
├── README.md          # This file - documentation index
├── evaluation/        # Active system evaluations and assessments
│   ├── README.md
│   ├── 03_extensibility_evaluation.md
│   ├── 04_future_features_readiness.md
│   └── 05_brainstorm_enhancements.md
├── planning/          # Development plans and feature phases
│   └── phases/
│       ├── playlist-workshop_2026-02-10/  # Workshop Enhancement Suite (6 phases)
│       └── user-persistence_2026-02-12/  # User Persistence Suite (7 phases)
└── archive/           # Completed evaluations and legacy documents
    ├── 01_architecture_evaluation.md
    ├── 02_modularity_assessment.md
    ├── separation_of_concerns_evaluation.md
    ├── tech_debt_q1-2026_2026-02-10/  # Completed tech debt session
    └── post-workshop-cleanup_2026-02-11/  # Post-workshop tech debt (4 phases)
```

---

## System Evaluation (Active)

Active evaluation documents for ongoing development planning:

- **[Evaluation Overview](evaluation/README.md)** - Summary and reading guide
- **[Extensibility Evaluation](evaluation/03_extensibility_evaluation.md)** - Service extensibility patterns, plugin architecture proposals
- **[Future Features Readiness](evaluation/04_future_features_readiness.md)** - Readiness for planned features (database, automations, notifications, UI)
- **[Brainstorm Enhancements](evaluation/05_brainstorm_enhancements.md)** - Enhancement ideas and tracking

## Planning

Development plans for feature implementation:

- **[Playlist Workshop Enhancement Suite](planning/phases/playlist-workshop_2026-02-10/00_OVERVIEW.md)** - 6-phase plan for workshop features (all phases completed)
- **[User Persistence Enhancement Suite](planning/phases/user-persistence_2026-02-12/00_OVERVIEW.md)** - 7-phase plan: PostgreSQL, user dimension table, login tracking, settings, snapshots, activity log, dashboard

## Archived (Completed)

Evaluations and plans that have been fully implemented:

- **[Architecture Evaluation](archive/01_architecture_evaluation.md)** - All critical recommendations implemented
- **[Modularity Assessment](archive/02_modularity_assessment.md)** - All 4 refactoring phases completed
- **[Separation of Concerns Evaluation](archive/separation_of_concerns_evaluation.md)** - Service layer extracted, all recommendations done
- **[Tech Debt Q1 2026](archive/tech_debt_q1-2026_2026-02-10/00_TECH_DEBT.md)** - Completed tech debt remediation session (6 items)
- **[Post-Workshop Cleanup](archive/post-workshop-cleanup_2026-02-11/00_TECH_DEBT.md)** - Post-workshop tech debt (4 phases: tests, enums, route split, housekeeping)

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

- [dev_guides/infrastructure_critiques.md](../dev_guides/infrastructure_critiques.md) - Infrastructure improvement notes
- [dev_guides/UX_CRITIQUES.md](../dev_guides/UX_CRITIQUES.md) - UX improvement notes
- [dev_guides/FACEBOOK_OAUTH_TROUBLESHOOTING.md](../dev_guides/FACEBOOK_OAUTH_TROUBLESHOOTING.md) - OAuth troubleshooting

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

Last updated: 2026-02-12
