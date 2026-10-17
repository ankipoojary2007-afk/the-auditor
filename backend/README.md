# The Auditor

**AI-assisted software test-quality auditing platform** that evaluates whether a project's existing tests can detect defects in its code — powered by mutation testing and Google Gemini foundation models.

## Architecture Overview

```text
┌─────────────────────────────────────────────────────────┐
│                    FastAPI REST API                     │
│  POST /audit  ·  POST /audit/upload  ·  GET /health     │
└────────────┬────────────────────────────┬───────────────┘
             │                            │
    ┌────────▼────────┐          ┌────────▼────────┐
    │  Audit Engine   │          │ Gemini Service  │
    │  ┌────────────┐ │          │  (Google AI)    │
    │  │AST Mutator │ │◄────────►│  • AI mutations │
    │  └────────────┘ │          │  • Analysis     │
    └────────┬────────┘          └─────────────────┘
             │
    ┌────────▼────────┐
    │    Sandbox      │
    │  subprocess │   │
    │    docker   │   │
    └─────────────────┘
