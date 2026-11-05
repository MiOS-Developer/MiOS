# Engineering Goals & Acceptance Invariants

## 1. High-Level Objective
[Define the primary capability, refactor, or architectural milestone here]

## 2. Non-Goals
- [Explicitly declare what is out of scope for this iteration]

## 3. Core Acceptance Criteria (Invariants)
- [ ] Invariant 1: [Functional invariant that must hold true]
- [ ] Invariant 2: [Two-sided validation: Positive control passes, negative control fails]
- [ ] Invariant 3: [Performance, throughput, or memory latency bounds]
- [ ] Invariant 4: [Zero regression on existing test suites]

## 4. Value Provenance & Data Origin Table
All data fields produced by this milestone must have a verified, non-invented source (`reference/provenance.py`).

| Field Name | Target Surface | Source Type | Origin / Key | Validation Rule |
| :--- | :--- | :--- | :--- | :--- |
| `status` | API_RESPONSE | DB_COLUMN | `orders.status` | Enum [PENDING, PAID, SHIPPED] |
| `total` | UI_SCREEN | COMPUTED | `order.subtotal + order.tax` | Decimal(10,2) |
