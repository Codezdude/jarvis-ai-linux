# J.A.R.V.I.S. Project Guide

J.A.R.V.I.S. is a local-first autonomous AI assistant designed to help with research, project work, code understanding, content generation, memory continuity, browser-safe workflows, and controlled automation.

This project blends:
- local AI/provider routing
- persistent memory
- project-aware file tools
- research and web search
- browser automation controls
- social/media campaign planning
- self-improvement workflows
- voice and HUD-driven interaction

A visual overview is available in the project website: [docs/index.html](docs/index.html)

## What JARVIS does

JARVIS is not just a chatbot. It is a project-aware assistant that can:

- answer questions with provider-aware routing
- search the web with Tavily
- inspect and modify files inside the project safely
- validate Python syntax and run tests
- generate social media briefs and branding concepts
- keep user preferences in memory over time
- analyze engagement and learn from performance results
- run controlled browser tasks only on approved domains
- operate within a structured safety model

## Project structure

- `jarvis.py` – desktop app entry point and HUD runtime
- `modules/ai.py` – main orchestration layer, provider selection, memory context, tool registration
- `modules/tools.py` – safe project tools and automation wrappers
- `modules/automation.py` – browser and social automation gates
- `modules/memory.py` – persistent memory and settings storage
- `config/settings.py` – safety settings and runtime configuration
- `tests/test_secrets.py` – regression and safety checks
- `docs/index.html` – visual project documentation and overview site

## Quick start

1. Open the project folder.
2. Create or update your local environment variables in `.env`.
3. Install dependencies if needed.
4. Run:

```bash
python jarvis.py
```

## Important safety model

JARVIS is built with guardrails:

- file writes require explicit confirmation in safe mode
- browser automation is restricted to allowlisted domains
- social publishing requires approval before scheduling
- safety mode prevents risky actions unless you confirm them

This is important because the assistant is powerful, but it should stay controlled and human-reviewed where action is irreversible.

## Example use cases

### 1. Research assistant

Prompt:

> Research the latest AI trends in automation and summarize the most important opportunities for a small startup.

What JARVIS can do:
- run a live web search
- read project context
- synthesize a useful summary
- suggest next steps

### 2. Project debugging helper

Prompt:

> Review this Python file for syntax issues and fix the root cause with a safe patch.

What JARVIS can do:
- inspect the file
- validate syntax
- repair the bug
- run tests and rollback if something fails

### 3. Brand and social content planning

Prompt:

> Create a premium blue brand concept for a luxury skincare brand targeting women 25–45.

What JARVIS can do:
- generate brand concepts
- suggest colors, tone, and layout
- build a social draft
- queue it for review and approval

### 4. Memory and personalization

Prompt:

> I prefer calm blue branding and premium styling.

What JARVIS can do:
- save the preference in memory
- recall it later
- use it in future content generation and recommendations

### 5. Analytics learning loop

Prompt:

> My last Instagram post got strong engagement; tell me what content style worked and what I should do next.

What JARVIS can do:
- read analytics memory
- compare performance trends
- suggest what to keep, change, or test next

## Roadmap

### Current stage
- project-aware AI assistant with memory
- tool integration and file support
- safe browser automation
- content planning and analytics logic
- voice and UI support

### Near-term roadmap
- stronger publishing integration with approved social platforms
- better analytics models and recommendations
- deeper memory structuring and recall
- more polished UI states and control panels

### Longer-term roadmap
- improved autonomous planning and task execution
- stronger personalization and user preference learning
- proactive suggestions and project monitoring
- more robust campaign and content optimization loops

## Challenges and important realities

### 1. Safety must stay active
The biggest risk is giving the assistant too much autonomy without review. JARVIS should act safely and predictably.

### 2. Memory can become noisy
If memory is not structured well, the assistant may over-weight old data or recall the wrong preferences.

### 3. Tool use must stay scoped
JARVIS should only operate inside approved domains, approved folders, and explicitly confirmed actions.

### 4. Over-automation can reduce trust
The best system is not the most autonomous one; it is the one that is reliable, explainable, and reviewable.

## Recommendations for better use

- keep safety mode enabled until you are comfortable with an action
- treat memory like a long-term preference store, not a dump of everything
- give JARVIS clear goals and constraints
- review generated content before publishing
- use analytics memory to improve future creative decisions

## Good example prompts

- "Create a luxury blue branding direction for a wellness startup."
- "Research the latest AI workflow trends and summarize them in plain English."
- "Fix the syntax issue in this Python file and verify it with tests."
- "Build a social media campaign brief for a creative agency."
- "Remember that I prefer premium minimal branding and warm gold accents."
- "What content direction worked best in my last three campaigns?"

## Final advice

JARVIS becomes most useful when you treat it as a structured assistant, not a free-roaming agent. It performs best when:
- the goal is clear
- the boundaries are explicit
- the user stays in the approval loop for important actions
- memory is used thoughtfully

That is the balance that makes JARVIS practical, powerful, and safe.
