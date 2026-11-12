# AGENTS.md

# OpenCode Project Instructions

This project uses three persistent knowledge mechanisms:

1. `README.md` — project entry point and operational overview
2. Obsidian — durable human-readable project knowledge
3. Knowledge Graph Memory MCP — structured agent memory

The goal is to keep project knowledge persistent, discoverable, accurate, and synchronized without unnecessarily storing transient information.

---

# 1. README.md

`README.md` is the primary entry point for the repository.

Use it to document information that a developer needs to understand or operate the project.

The README should contain:

* Project purpose
* Main capabilities
* Technology stack
* Repository structure
* How to run the project
* How to configure the project
* Important environment requirements
* Development commands
* Testing commands
* Main integrations
* Links to important documentation

Keep the README concise.

Do not turn the README into a detailed knowledge base.

Detailed architecture, decisions, procedures, and historical reasoning belong in Obsidian.

## README maintenance

When a significant change affects:

* installation
* configuration
* architecture
* development commands
* project structure
* dependencies
* external integrations

check whether `README.md` needs to be updated.

Do not leave the README describing an obsolete project state.

---

# 2. Obsidian Knowledge Base

The project has an Obsidian knowledge base available at:

```text
.opencode/obsidian/
```

This is a symbolic link to the actual Obsidian vault.

Treat the Markdown files in this directory as persistent project documentation.

Do not assume that knowledge exists only in the current conversation or source code.

Before making significant architectural changes, inspect the relevant Obsidian documentation.

---

## Obsidian structure

The vault normally follows this structure:

```text
.opencode/obsidian/
├── Architecture/
├── Decisions/
├── Knowledge/
├── Projects/
├── Procedures/
├── Tasks/
└── Sessions/
```

### Architecture/

Use for the current architecture of the system.

Examples:

```text
Architecture/System Architecture.md
Architecture/Backend.md
Architecture/Frontend.md
Architecture/Workflow Engine.md
Architecture/Database.md
```

Document:

* components
* responsibilities
* integrations
* data flows
* architectural constraints
* important interfaces

---

### Decisions/

Use for significant architectural or technical decisions.

Create an ADR when a decision has meaningful consequences for the project.

Example:

```text
Decisions/001-Async-Workflow-Execution.md
```

Prefer documenting:

* Context
* Problem
* Decision
* Alternatives
* Consequences

Do not create an ADR for trivial implementation choices.

---

### Knowledge/

Use for durable technical knowledge.

Examples:

```text
Knowledge/Django.md
Knowledge/Celery.md
Knowledge/ServiceNow.md
Knowledge/React Flow.md
Knowledge/PostgreSQL.md
```

Use this section for knowledge that is useful across multiple tasks.

---

### Projects/

Use for descriptions of major project areas or subsystems.

Examples:

```text
Projects/ServiceNow Automation.md
Projects/Workflow Engine.md
Projects/SEOL.md
```

---

### Procedures/

Use for repeatable operational procedures.

Examples:

```text
Procedures/Deploying.md
Procedures/Debugging Celery.md
Procedures/Adding a Workflow Node.md
```

Procedures should contain actionable steps.

---

### Tasks/

Use for durable task documentation when a task requires more context than a normal issue or TODO.

Do not use this directory as a replacement for the project's issue tracker.

---

### Sessions/

Use sparingly.

Session notes may contain useful historical context, discoveries, or investigation results.

Do not create a session note for every OpenCode interaction.

---

# 3. Obsidian Documentation Rules

When modifying Obsidian documentation:

* Use standard Markdown.
* Keep documents focused.
* Prefer one subject per document.
* Use descriptive filenames.
* Use relative Markdown/Obsidian links when useful.
* Avoid duplicating the same information across many files.
* Prefer updating an existing document over creating a duplicate.
* Preserve existing useful documentation.
* Do not delete documentation merely because it is not immediately relevant.
* Do not store secrets.

Use links such as:

```markdown
[[System Architecture]]
[[Workflow Engine]]
[[Ticket Classification]]
```

when they improve navigation.

---

# 4. Knowledge Graph Memory MCP

A Knowledge Graph Memory MCP is available to the project.

Use it for structured information that should persist across OpenCode sessions.

The memory system is intended for:

* important project facts
* relationships between components
* architectural constraints
* significant decisions
* recurring project conventions
* important discoveries
* lessons learned
* stable implementation preferences

Examples of useful memory:

```text
OrgService uses Django as its backend framework.

Workflow execution is asynchronous and uses Celery.

React Flow is used for workflow visualization.

Ticket classification must not automatically label tickets
unless measured accuracy exceeds 90%.

TestingSwitch is the node type used for workflow branching.
```

---

# 5. What NOT to store in Memory

Never store:

* passwords
* API keys
* access tokens
* private keys
* credentials
* session cookies
* secrets
* personal authentication information

Do not store:

* temporary debugging output
* transient errors
* one-off shell commands
* irrelevant conversation details
* speculative statements
* information that is likely to become obsolete immediately

When uncertain, prefer not storing the information.

---

# 6. Memory vs Obsidian

Use the following rule:

## Knowledge Graph Memory

Use memory for:

> "What facts and relationships should the agent remember?"

Examples:

```text
Django → uses → PostgreSQL

Workflow Engine → executes through → Celery

ServiceNow → provides → Incidents

TestingSwitch → supports → branching
```

---

## Obsidian

Use Obsidian for:

> "What knowledge should a human developer be able to read and understand?"

Examples:

```text
Architecture documentation
Architecture decisions
Implementation explanations
Procedures
Integration documentation
Technical investigations
Project context
```

---

## Both

Important architectural decisions may belong in both systems.

For example:

### Obsidian

Create:

```text
Decisions/001-Async-Workflow-Execution.md
```

with the detailed reasoning.

### Memory

Store a concise fact:

```text
Workflow execution is asynchronous and is performed through Celery.
```

Do not duplicate the entire Obsidian document into the Knowledge Graph.

---

# 7. Before Starting Significant Work

Before implementing a significant feature:

1. Read `README.md`.
2. Inspect the relevant source code.
3. Search relevant Obsidian documentation.
4. Search Knowledge Graph Memory when relevant.
5. Determine whether an existing architectural decision affects the task.
6. Follow existing project conventions unless there is a documented reason to change them.

Do not redesign an existing subsystem without first checking its documented architecture.

---

# 8. During Development

When discovering important information, classify it:

```text
Transient
    ↓
Do not persist

Useful project fact
    ↓
Knowledge Graph Memory

Detailed durable documentation
    ↓
Obsidian

Public project operation/configuration information
    ↓
README.md

Important architectural decision
    ↓
Obsidian + concise Memory entry
```

Do not automatically persist every discovery.

Use judgment.

---

# 9. After Significant Changes

Before finishing a significant task:

### Check README

Ask:

> Does this change alter how a developer installs, runs, configures, or understands the project?

If yes, update `README.md`.

### Check Obsidian

Ask:

> Did this change introduce or modify durable architecture, a procedure, or an important technical decision?

If yes, update the appropriate Obsidian document.

### Check Memory

Ask:

> Did I learn a stable fact or relationship that will be useful in future sessions?

If yes, store a concise memory.

---

# 10. Source Code Is the Authority

Documentation and memory must not override the actual implementation.

When there is a conflict:

```text
Current source code
    ↓
Current configuration
    ↓
Tests
    ↓
README
    ↓
Obsidian
    ↓
Memory
```

Investigate discrepancies rather than blindly trusting old documentation.

When a documented fact becomes incorrect, update the documentation and memory.

---

# 11. Avoid Documentation Drift

Do not create documentation merely because a file does not exist.

Before creating a new document:

1. Search the Obsidian vault.
2. Determine whether an existing document covers the subject.
3. Update the existing document if appropriate.
4. Create a new document only when the subject deserves independent documentation.

Avoid multiple documents containing contradictory versions of the same architecture.

---

# 12. Security

Never place secrets in:

* `README.md`
* Obsidian
* Knowledge Graph Memory
* `AGENTS.md`
* source-controlled Markdown files

Use environment variables or appropriate secret-management mechanisms.

If a secret appears during investigation, do not persist it.

---

# 13. General Principle

The purpose of persistent knowledge is not to remember everything.

The purpose is to prevent the agent and developers from repeatedly rediscovering important information.

Prefer:

```text
small
accurate
structured
durable
up-to-date
```

over:

```text
large
redundant
speculative
temporary
```

When making a significant architectural decision, leave behind enough information that another developer or agent can understand:

1. What was decided
2. Why it was decided
3. What alternatives were considered
4. What consequences the decision has
5. Where the implementation lives
