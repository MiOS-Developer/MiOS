# Technical Investigation & Research Spike: [Title]

**Spike ID:** `SPIKE-[YYYYMMDD]-[Identifier]`  
**Author / Driver:** [Name or Agent ID]  
**Status:** [PROPOSED | IN_FLIGHT | CONCLUDED | ABANDONED]  
**Timebox:** [e.g., 2 hours, 1 day]  
**Target Completion Date:** [YYYY-MM-DD]  
**Related Epics / Lanes:** `lane-[name]`, `#issue-id`  

---

## 1. Context & Motivation
Describe the architectural dilemma, unknown API surface, performance bottleneck, or dependency risk that motivated this spike.
- What assumption is currently unverified?
- What happens if we make the wrong decision without this research?

---

## 2. Research Questions & Hypotheses
List the specific, falsifiable questions this spike seeks to answer.

| Question ID | Research Question | Hypothesis (Initial Assumption) |
| :--- | :--- | :--- |
| **Q1** | Can [Library X] handle [Requirement Y] within [Limit Z]? | [Yes / No, because...] |
| **Q2** | Does upstream release [vX] break backwards compatibility with [Module]? | [Anticipate breaking changes in...] |
| **Q3** | What is the performance overhead of [Approach A] vs [Approach B]? | [Approach A will have lower latency...] |

---

## 3. Experimental Methodology & Prototypes
Detail the tests, benchmarks, or minimal reproduction scripts created to gather empirical evidence.

- **Sandbox / Worktree:** `worktrees/spike-[topic]`
- **Benchmark / Probe Script:** `scripts/probe_[name].py`
- **Execution Command:**
  ```bash
  python3 scripts/probe_[name].py --benchmark --iterations 100
  ```

---

## 4. Empirical Findings & Benchmarks

### 4.1 Quantitative Data
| Metric | Baseline / Approach A | Candidate / Approach B | Delta (%) |
| :--- | :--- | :--- | :--- |
| **Latency (p95)** | `12.4 ms` | `3.1 ms` | `-75.0%` |
| **Throughput (req/s)** | `1,200` | `4,850` | `+304.1%` |
| **Memory Resident** | `120 MB` | `185 MB` | `+54.1%` |

### 4.2 Qualitative Discoveries & Trade-offs
- **Finding 1:** [Detail behavior discovered in documentation or runtime debugging]
- **Finding 2:** [Edge-case limitations, missing typing stubs, or undocumented invariants]

---

## 5. Architectural Implications
- **Contract Impact:** Will schemas, API signatures, or storage formats need to change?
- **Dependency Footprint:** Does this introduce heavy transitive dependencies or native build tools?
- **Operational Complexity:** What observability, logging, or maintenance requirements are introduced?

---

## 6. Final Decision & Actionable Next Steps

**Verdict:** [ADOPT | REJECT | DEFER]

### Rationale
[Provide concise, evidence-backed justification based on findings above.]

### Action Items
- [ ] Task 1: Create lane configuration `lanes/lane-[feature].json`
- [ ] Task 2: Draft Architecture Decision Record in `docs/adr/ADR-[NNN].md`
- [ ] Task 3: Update `GOALS.md` and `ROADMAP.md`
