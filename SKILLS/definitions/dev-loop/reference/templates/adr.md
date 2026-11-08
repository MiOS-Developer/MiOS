# Architecture Decision Record (ADR): [Title]

- **Status:** [Proposed | In Progress | Accepted | Superseded | Deprecated]
- **Date:** [YYYY-MM-DD]
- **Deciders:** [List collaborators and agent personas]
- **Scope Reference:** `docs/scope.md` or `GOALS.md`

## 1. Context and Problem Statement
[Describe the architectural issue, constraint, or trade-off requiring a decision]

## 2. Decision Outcome
[Chosen option: e.g., Adopt relational PostgreSQL schema with isolated git worktrees]

### Positive Consequences
- Prevents file collision between concurrent worker lanes.
- Clear data ownership and relational constraints.

### Negative Consequences / Trade-offs
- Requires migration scripts for existing records.
- Slightly higher initial configuration overhead.

## 3. Value Provenance Map (Decision-Owed Gate)
Every output attribute displayed on screen or returned by an API must have a declared architectural source.

| Field / Property | Target Surface | Source Type | Origin / Key | Formula / Validation Rule |
| :--- | :--- | :--- | :--- | :--- |
| `id` | API_RESPONSE | DB_COLUMN | `table.id` | UUIDv4 |
| `totalAmount` | UI_SCREEN | COMPUTED | `items.price * items.qty` | Sum + tax calculation |
| `taxRate` | API_RESPONSE | EXTERNAL_API | TaxJar `/v2/taxes` | Cached 1 hour |

## 4. Alternatives Considered & Rejection Rationale
- **Option A:** [Description of alternative]. *(Rejected: [Exact reason why it lost])*
- **Option B:** [Description of alternative]. *(Rejected: [Exact reason why it lost])*
