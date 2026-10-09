#!/usr/bin/env python3
# AI-hint: Knowledge Graph triple storage and recursive CTE traversal engine for MiOS RAG and dependency resolution.
# AI-related: tests/test-knowledge-graph.py, usr/share/mios/postgres/schema-init.sql, usr/share/doc/mios/manual/graph.md
"""
MiOS Knowledge Graph Traversal Engine (GRAPH-01 / task_323 / task_2556).
Implements triple ingestion (subject-predicate-object), metadata properties,
recursive CTE multi-hop dependency resolution, cycle detection, and SQL generation.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

class KnowledgeGraph:
    """Knowledge Graph store supporting recursive CTE traversals, dependency resolution, and cycle prevention."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        db_uri: Optional[str] = None,
    ) -> None:
        """Initialize the KnowledgeGraph instance with SQLite or optional PostgreSQL connection."""
        self.db_path = db_path
        self.db_uri = db_uri
        self._pg_conn = None
        self._sqlite_conn: Optional[sqlite3.Connection] = None

        if db_uri and (db_uri.startswith("postgresql://") or db_uri.startswith("postgres://")):
            try:
                import psycopg2
                self._pg_conn = psycopg2.connect(db_uri)
                self._init_pg_schema()
            except Exception:
                # Fallback to in-memory SQLite if PostgreSQL driver/server is unavailable
                self._sqlite_conn = sqlite3.connect(":memory:")
                self._init_sqlite_schema()
        else:
            target = db_path if db_path else ":memory:"
            self._sqlite_conn = sqlite3.connect(target)
            self._sqlite_conn.row_factory = sqlite3.Row
            self._init_sqlite_schema()

    def _init_sqlite_schema(self) -> None:
        """Create knowledge_graph table and indexes in SQLite."""
        if not self._sqlite_conn:
            return
        cursor = self._sqlite_conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_graph (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                properties TEXT DEFAULT '{}',
                embedding TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_subject ON knowledge_graph(subject);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_object ON knowledge_graph(object);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_kg_predicate ON knowledge_graph(predicate);")
        self._sqlite_conn.commit()

    def _init_pg_schema(self) -> None:
        """Create knowledge_graph table and indexes in PostgreSQL."""
        if not self._pg_conn:
            return
        with self._pg_conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_graph (
                    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    properties JSONB DEFAULT '{}'::jsonb,
                    emb vector(768),
                    ts TIMESTAMPTZ DEFAULT now()
                );
                CREATE INDEX IF NOT EXISTS knowledge_graph_subj ON knowledge_graph (subject);
                CREATE INDEX IF NOT EXISTS knowledge_graph_obj  ON knowledge_graph (object);
                CREATE INDEX IF NOT EXISTS knowledge_graph_pred ON knowledge_graph (predicate);
                """
            )
        self._pg_conn.commit()

    def add_triple(
        self,
        subject: str,
        predicate: str,
        object_: str,
        properties: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
    ) -> int:
        """Add a single triple with optional properties dict and vector embedding. Returns inserted ID."""
        props_json = json.dumps(properties or {})
        emb_json = json.dumps(embedding) if embedding is not None else None

        if self._sqlite_conn:
            cursor = self._sqlite_conn.cursor()
            cursor.execute(
                """
                INSERT INTO knowledge_graph (subject, predicate, object, properties, embedding)
                VALUES (?, ?, ?, ?, ?);
                """,
                (subject, predicate, object_, props_json, emb_json),
            )
            self._sqlite_conn.commit()
            return cursor.lastrowid or 0
        elif self._pg_conn:
            with self._pg_conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO knowledge_graph (subject, predicate, object, properties)
                    VALUES (%s, %s, %s, %s::jsonb)
                    RETURNING id;
                    """,
                    (subject, predicate, object_, props_json),
                )
                row_id = cursor.fetchone()[0]
            self._pg_conn.commit()
            return int(row_id)
        return 0

    def add_triples(self, triples: List[Tuple[Any, ...]]) -> int:
        """
        Batch add multiple triples.
        Each tuple can be:
          - (subject, predicate, object)
          - (subject, predicate, object, properties)
          - (subject, predicate, object, properties, embedding)
        Returns the count of successfully added triples.
        """
        count = 0
        for item in triples:
            subj = item[0]
            pred = item[1]
            obj = item[2]
            props = item[3] if len(item) > 3 and isinstance(item[3], dict) else None
            emb = item[4] if len(item) > 4 and isinstance(item[4], list) else None
            self.add_triple(subj, pred, obj, properties=props, embedding=emb)
            count += 1
        return count

    def get_dependencies(
        self,
        subject: str,
        predicate: Optional[str] = None,
    ) -> List[str]:
        """Get direct (1-hop) dependencies (objects) of a given subject."""
        if not self._sqlite_conn:
            return []
        cursor = self._sqlite_conn.cursor()
        if predicate:
            cursor.execute(
                """
                SELECT DISTINCT object
                FROM knowledge_graph
                WHERE subject = ? AND predicate = ?
                ORDER BY id ASC;
                """,
                (subject, predicate),
            )
        else:
            cursor.execute(
                """
                SELECT DISTINCT object
                FROM knowledge_graph
                WHERE subject = ?
                ORDER BY id ASC;
                """,
                (subject,),
            )
        return [row[0] for row in cursor.fetchall()]

    def get_dependents(
        self,
        object_: str,
        predicate: Optional[str] = None,
    ) -> List[str]:
        """Get direct (1-hop) dependents (subjects) that depend on a given object."""
        if not self._sqlite_conn:
            return []
        cursor = self._sqlite_conn.cursor()
        if predicate:
            cursor.execute(
                """
                SELECT DISTINCT subject
                FROM knowledge_graph
                WHERE object = ? AND predicate = ?
                ORDER BY id ASC;
                """,
                (object_, predicate),
            )
        else:
            cursor.execute(
                """
                SELECT DISTINCT subject
                FROM knowledge_graph
                WHERE object = ?
                ORDER BY id ASC;
                """,
                (object_,),
            )
        return [row[0] for row in cursor.fetchall()]

    def get_recursive_dependencies(
        self,
        root: str,
        max_depth: int = 5,
        predicate: Optional[str] = None,
    ) -> List[str]:
        """
        Get all recursive multi-hop dependencies starting from root up to max_depth.
        Returns deduplicated list of reachable node names in traversal order.
        """
        steps = self.traverse(
            root=root,
            max_depth=max_depth,
            direction="forward",
            predicate=predicate,
        )
        seen: List[str] = []
        for step in steps:
            obj = step["object"]
            if obj != root and obj not in seen:
                seen.append(obj)
        return seen

    def traverse(
        self,
        root: str,
        max_depth: int = 5,
        direction: str = "forward",
        predicate: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Full recursive walk returning path, depth, triple metadata, and properties.
        Cycles are detected and prevented from infinite-looping.
        """
        if not self._sqlite_conn:
            return []

        cursor = self._sqlite_conn.cursor()
        results: List[Dict[str, Any]] = []

        if direction == "forward":
            # Forward walk: subject -> object
            if predicate:
                query = """
                WITH RECURSIVE graph_walk(id, subject, predicate, object, properties, embedding, depth, path) AS (
                    SELECT id, subject, predicate, object, properties, embedding, 1,
                           ',' || subject || ',' || object || ','
                    FROM knowledge_graph
                    WHERE subject = ? AND predicate = ?
                    UNION ALL
                    SELECT kg.id, kg.subject, kg.predicate, kg.object, kg.properties, kg.embedding,
                           gw.depth + 1,
                           gw.path || kg.object || ','
                    FROM knowledge_graph kg
                    JOIN graph_walk gw ON kg.subject = gw.object
                    WHERE gw.depth < ?
                      AND kg.predicate = ?
                      AND instr(gw.path, ',' || kg.object || ',') = 0
                )
                SELECT id, subject, predicate, object, properties, embedding, depth, path
                FROM graph_walk
                ORDER BY depth ASC, id ASC;
                """
                params = (root, predicate, max_depth, predicate)
            else:
                query = """
                WITH RECURSIVE graph_walk(id, subject, predicate, object, properties, embedding, depth, path) AS (
                    SELECT id, subject, predicate, object, properties, embedding, 1,
                           ',' || subject || ',' || object || ','
                    FROM knowledge_graph
                    WHERE subject = ?
                    UNION ALL
                    SELECT kg.id, kg.subject, kg.predicate, kg.object, kg.properties, kg.embedding,
                           gw.depth + 1,
                           gw.path || kg.object || ','
                    FROM knowledge_graph kg
                    JOIN graph_walk gw ON kg.subject = gw.object
                    WHERE gw.depth < ?
                      AND instr(gw.path, ',' || kg.object || ',') = 0
                )
                SELECT id, subject, predicate, object, properties, embedding, depth, path
                FROM graph_walk
                ORDER BY depth ASC, id ASC;
                """
                params = (root, max_depth)
        else:
            # Backward walk: object -> subject (reverse traversal)
            if predicate:
                query = """
                WITH RECURSIVE graph_walk(id, subject, predicate, object, properties, embedding, depth, path) AS (
                    SELECT id, subject, predicate, object, properties, embedding, 1,
                           ',' || object || ',' || subject || ','
                    FROM knowledge_graph
                    WHERE object = ? AND predicate = ?
                    UNION ALL
                    SELECT kg.id, kg.subject, kg.predicate, kg.object, kg.properties, kg.embedding,
                           gw.depth + 1,
                           gw.path || kg.subject || ','
                    FROM knowledge_graph kg
                    JOIN graph_walk gw ON kg.object = gw.subject
                    WHERE gw.depth < ?
                      AND kg.predicate = ?
                      AND instr(gw.path, ',' || kg.subject || ',') = 0
                )
                SELECT id, subject, predicate, object, properties, embedding, depth, path
                FROM graph_walk
                ORDER BY depth ASC, id ASC;
                """
                params = (root, predicate, max_depth, predicate)
            else:
                query = """
                WITH RECURSIVE graph_walk(id, subject, predicate, object, properties, embedding, depth, path) AS (
                    SELECT id, subject, predicate, object, properties, embedding, 1,
                           ',' || object || ',' || subject || ','
                    FROM knowledge_graph
                    WHERE object = ?
                    UNION ALL
                    SELECT kg.id, kg.subject, kg.predicate, kg.object, kg.properties, kg.embedding,
                           gw.depth + 1,
                           gw.path || kg.subject || ','
                    FROM knowledge_graph kg
                    JOIN graph_walk gw ON kg.object = gw.subject
                    WHERE gw.depth < ?
                      AND instr(gw.path, ',' || kg.subject || ',') = 0
                )
                SELECT id, subject, predicate, object, properties, embedding, depth, path
                FROM graph_walk
                ORDER BY depth ASC, id ASC;
                """
                params = (root, max_depth)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        for row in rows:
            raw_path = row["path"] if "path" in row.keys() else row[7]
            path_nodes = [p for p in raw_path.split(",") if p]
            raw_props = row["properties"] if "properties" in row.keys() else row[4]
            raw_emb = row["embedding"] if "embedding" in row.keys() else row[5]

            props_dict: Dict[str, Any] = {}
            if raw_props:
                try:
                    props_dict = json.loads(raw_props)
                except (json.JSONDecodeError, TypeError):
                    props_dict = {}

            emb_list: Optional[List[float]] = None
            if raw_emb:
                try:
                    emb_list = json.loads(raw_emb)
                except (json.JSONDecodeError, TypeError):
                    emb_list = None

            results.append(
                {
                    "id": row["id"] if "id" in row.keys() else row[0],
                    "subject": row["subject"] if "subject" in row.keys() else row[1],
                    "predicate": row["predicate"] if "predicate" in row.keys() else row[2],
                    "object": row["object"] if "object" in row.keys() else row[3],
                    "depth": row["depth"] if "depth" in row.keys() else row[6],
                    "path": path_nodes,
                    "properties": props_dict,
                    "embedding": emb_list,
                }
            )

        return results

    def generate_recursive_cte_sql(
        self,
        root: str,
        max_depth: int = 5,
        predicate: Optional[str] = None,
        direction: str = "forward",
        dialect: str = "postgres",
    ) -> str:
        """
        Generate recursive CTE statement for PostgreSQL or SQLite.
        Used for embedding directly into pgvector pipelines or analytical queries.
        """
        pred_filter = f"AND predicate = '{predicate}'" if predicate else ""
        pred_join_filter = f"AND kg.predicate = '{predicate}'" if predicate else ""

        if dialect.lower() == "postgres":
            if direction == "forward":
                return f"""WITH RECURSIVE graph_walk AS (
    SELECT id, subject, predicate, object, properties, emb, 1 AS depth, ARRAY[subject, object] AS path
    FROM knowledge_graph
    WHERE subject = '{root}' {pred_filter}
    UNION ALL
    SELECT kg.id, kg.subject, kg.predicate, kg.object, kg.properties, kg.emb, gw.depth + 1, gw.path || kg.object
    FROM knowledge_graph kg
    JOIN graph_walk gw ON kg.subject = gw.object
    WHERE gw.depth < {max_depth} {pred_join_filter}
      AND NOT (kg.object = ANY(gw.path))
)
SELECT * FROM graph_walk;"""
            else:
                return f"""WITH RECURSIVE graph_walk AS (
    SELECT id, subject, predicate, object, properties, emb, 1 AS depth, ARRAY[object, subject] AS path
    FROM knowledge_graph
    WHERE object = '{root}' {pred_filter}
    UNION ALL
    SELECT kg.id, kg.subject, kg.predicate, kg.object, kg.properties, kg.emb, gw.depth + 1, gw.path || kg.subject
    FROM knowledge_graph kg
    JOIN graph_walk gw ON kg.object = gw.subject
    WHERE gw.depth < {max_depth} {pred_join_filter}
      AND NOT (kg.subject = ANY(gw.path))
)
SELECT * FROM graph_walk;"""
        else:
            # SQLite dialect
            if direction == "forward":
                return f"""WITH RECURSIVE graph_walk(id, subject, predicate, object, properties, embedding, depth, path) AS (
    SELECT id, subject, predicate, object, properties, embedding, 1, ',' || subject || ',' || object || ','
    FROM knowledge_graph
    WHERE subject = '{root}' {pred_filter}
    UNION ALL
    SELECT kg.id, kg.subject, kg.predicate, kg.object, kg.properties, kg.embedding, gw.depth + 1, gw.path || kg.object || ','
    FROM knowledge_graph kg
    JOIN graph_walk gw ON kg.subject = gw.object
    WHERE gw.depth < {max_depth} {pred_join_filter}
      AND instr(gw.path, ',' || kg.object || ',') = 0
)
SELECT * FROM graph_walk;"""
            else:
                return f"""WITH RECURSIVE graph_walk(id, subject, predicate, object, properties, embedding, depth, path) AS (
    SELECT id, subject, predicate, object, properties, embedding, 1, ',' || object || ',' || subject || ','
    FROM knowledge_graph
    WHERE object = '{root}' {pred_filter}
    UNION ALL
    SELECT kg.id, kg.subject, kg.predicate, kg.object, kg.properties, kg.embedding, gw.depth + 1, gw.path || kg.subject || ','
    FROM knowledge_graph kg
    JOIN graph_walk gw ON kg.object = gw.subject
    WHERE gw.depth < {max_depth} {pred_join_filter}
      AND instr(gw.path, ',' || kg.subject || ',') = 0
)
SELECT * FROM graph_walk;"""

    def export_graph(self) -> Dict[str, Any]:
        """Export all triples, nodes, and edges in the knowledge graph."""
        if not self._sqlite_conn:
            return {"nodes": [], "edges": []}
        cursor = self._sqlite_conn.cursor()
        cursor.execute("SELECT id, subject, predicate, object, properties, embedding, created_at FROM knowledge_graph ORDER BY id ASC;")
        rows = cursor.fetchall()

        nodes: set[str] = set()
        edges: List[Dict[str, Any]] = []

        for row in rows:
            subj = row[1]
            pred = row[2]
            obj = row[3]
            nodes.add(subj)
            nodes.add(obj)
            props = {}
            if row[4]:
                try:
                    props = json.loads(row[4])
                except Exception:
                    props = {}
            edges.append(
                {
                    "id": row[0],
                    "source": subj,
                    "target": obj,
                    "predicate": pred,
                    "properties": props,
                }
            )

        return {
            "nodes": sorted(list(nodes)),
            "edges": edges,
            "count": len(edges),
        }

    def close(self) -> None:
        """Close database connection."""
        if self._sqlite_conn:
            self._sqlite_conn.close()
            self._sqlite_conn = None
        if self._pg_conn:
            self._pg_conn.close()
            self._pg_conn = None

    def __enter__(self) -> KnowledgeGraph:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

def main(argv: Optional[List[str]] = None) -> int:
    """CLI execution entrypoint for Knowledge Graph operations."""
    parser = argparse.ArgumentParser(
        description="MiOS Knowledge Graph Recursive CTE Traversal Engine (GRAPH-01)"
    )
    parser.add_argument("--db", default=None, help="Database file path (default: in-memory)")
    parser.add_argument(
        "--add-triple",
        nargs=3,
        metavar=("SUBJECT", "PREDICATE", "OBJECT"),
        help="Add a single triple to the knowledge graph",
    )
    parser.add_argument("--properties", default=None, help="JSON string of properties metadata")
    parser.add_argument("--embedding", default=None, help="JSON array of float embedding vector")
    parser.add_argument("--import-json", help="JSON file with triples list to import")
    parser.add_argument("--dependencies", metavar="SUBJECT", help="Query direct dependencies of subject")
    parser.add_argument("--dependents", metavar="OBJECT", help="Query direct dependents of object")
    parser.add_argument("--recursive-deps", metavar="ROOT", help="Query recursive multi-hop dependencies")
    parser.add_argument("--traverse", metavar="ROOT", help="Perform recursive CTE traversal from root")
    parser.add_argument(
        "--direction",
        choices=["forward", "backward"],
        default="forward",
        help="Traversal direction (default: forward)",
    )
    parser.add_argument("--max-depth", type=int, default=5, help="Maximum traversal depth (default: 5)")
    parser.add_argument("--predicate", default=None, help="Filter traversal by predicate")
    parser.add_argument(
        "--generate-cte",
        metavar="ROOT",
        help="Generate recursive CTE SQL statement for root",
    )
    parser.add_argument(
        "--dialect",
        choices=["postgres", "sqlite"],
        default="postgres",
        help="SQL dialect for CTE generation (default: postgres)",
    )
    parser.add_argument("--json", action="store_true", help="Format CLI output as JSON")
    parser.add_argument("--dump", action="store_true", help="Dump entire knowledge graph as JSON")

    args = parser.parse_args(argv)

    with KnowledgeGraph(db_path=args.db) as kg:
        if args.add_triple:
            subj, pred, obj = args.add_triple
            props = json.loads(args.properties) if args.properties else None
            emb = json.loads(args.embedding) if args.embedding else None
            row_id = kg.add_triple(subj, pred, obj, properties=props, embedding=emb)
            if args.json:
                print(json.dumps({"status": "added", "id": row_id, "subject": subj, "predicate": pred, "object": obj}))
            else:
                print(f"Added triple [{row_id}]: {subj} --({pred})--> {obj}")

        if args.import_json:
            with open(args.import_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            triples = data if isinstance(data, list) else data.get("triples", [])
            count = kg.add_triples([tuple(t) if isinstance(t, list) else (t["subject"], t["predicate"], t["object"]) for t in triples])
            if args.json:
                print(json.dumps({"status": "imported", "count": count}))
            else:
                print(f"Imported {count} triples.")

        if args.dependencies:
            deps = kg.get_dependencies(args.dependencies, predicate=args.predicate)
            if args.json:
                print(json.dumps({"subject": args.dependencies, "dependencies": deps}, indent=2))
            else:
                print(f"Dependencies for '{args.dependencies}': {', '.join(deps) if deps else '(none)'}")

        if args.dependents:
            deps = kg.get_dependents(args.dependents, predicate=args.predicate)
            if args.json:
                print(json.dumps({"object": args.dependents, "dependents": deps}, indent=2))
            else:
                print(f"Dependents for '{args.dependents}': {', '.join(deps) if deps else '(none)'}")

        if args.recursive_deps:
            deps = kg.get_recursive_dependencies(
                args.recursive_deps,
                max_depth=args.max_depth,
                predicate=args.predicate,
            )
            if args.json:
                print(
                    json.dumps(
                        {
                            "root": args.recursive_deps,
                            "max_depth": args.max_depth,
                            "predicate": args.predicate,
                            "recursive_dependencies": deps,
                        },
                        indent=2,
                    )
                )
            else:
                print(f"Recursive dependencies for '{args.recursive_deps}' (max_depth={args.max_depth}):")
                for d in deps:
                    print(f"  - {d}")

        if args.traverse:
            steps = kg.traverse(
                args.traverse,
                max_depth=args.max_depth,
                direction=args.direction,
                predicate=args.predicate,
            )
            if args.json:
                print(json.dumps(steps, indent=2))
            else:
                print(f"Traversal from '{args.traverse}' (direction={args.direction}, max_depth={args.max_depth}):")
                for s in steps:
                    path_str = " -> ".join(s["path"])
                    print(f"  [depth={s['depth']}] {s['subject']} --({s['predicate']})--> {s['object']} (path: {path_str})")

        if args.generate_cte:
            sql = kg.generate_recursive_cte_sql(
                args.generate_cte,
                max_depth=args.max_depth,
                predicate=args.predicate,
                direction=args.direction,
                dialect=args.dialect,
            )
            print(sql)

        if args.dump:
            dump_data = kg.export_graph()
            print(json.dumps(dump_data, indent=2))

    return 0

if __name__ == "__main__":
    sys.exit(main())
