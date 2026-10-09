<!-- AI-hint: Manual pages distilled from the source comments of graph, sanitized, each passage anchored to the comment it came from. -->

# graph

### MiOS Knowledge Graph Traversal Engine (GRAPH-01 / task_323 /...

MiOS Knowledge Graph Traversal Engine (GRAPH-01 / task_323 / task_2556).
Implements triple ingestion (subject-predicate-object), metadata properties,
recursive CTE multi-hop dependency resolution, cycle detection, and SQL generation.

<!-- mios-src:e9b5f3446c6f from usr/libexec/mios/graph/traversal.py:4-8 -->

### Batch add multiple triples. Each tuple can be: - (subject...

Batch add multiple triples.
        Each tuple can be:
          - (subject, predicate, object)
          - (subject, predicate, object, properties)
          - (subject, predicate, object, properties, embedding)
        Returns the count of successfully added triples.

<!-- mios-src:939de397513c from usr/libexec/mios/graph/traversal.py:134-141 -->
