"""
GraphitiStorage — Graphiti-core implementation of GraphStorage.

Uses graphiti-core for knowledge graph construction with built-in
NER, entity resolution, embedding, and hybrid search.
Replaces the custom NER/embedding pipeline in Neo4jStorage.

Graphiti-core imports are lazy (inside methods) so this module can be
imported even when graphiti-core is not installed.
"""

import asyncio
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Callable

from neo4j import GraphDatabase

from ..config import Config
from .graph_storage import GraphStorage

logger = logging.getLogger('miroshark.graphiti_storage')


class GraphitiStorage(GraphStorage):
    """Graphiti-core backed implementation of GraphStorage."""

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self._uri = uri or Config.NEO4J_URI
        self._user = user or Config.NEO4J_USER
        self._password = password or Config.NEO4J_PASSWORD

        # Neo4j driver for direct queries (graph info, legacy reads)
        self._driver = GraphDatabase.driver(
            self._uri, auth=(self._user, self._password)
        )

        # Graphiti instance — lazily initialised
        self._default_graphiti = None

        # Dedicated event loop running in a background thread.
        # All async Graphiti operations are dispatched here so that
        # the asyncio.Lock, Futures, and the Graphiti client all
        # share the *same* loop — regardless of which thread calls
        # the synchronous wrapper methods.
        import threading
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True, name="graphiti-loop"
        )
        self._loop_thread.start()

        # Lock lives on the dedicated loop so it never conflicts
        self._init_lock = asyncio.Lock()

        # In-memory ontology cache (also persisted in the Neo4j Graph node)
        self._ontologies: Dict[str, Dict] = {}

    # ----------------------------------------------------------------
    # Graphiti lifecycle helpers
    # ----------------------------------------------------------------

    async def _get_graphiti(self):
        """Get or create the singleton Graphiti instance."""
        from graphiti_core import Graphiti
        from graphiti_core.llm_client import OpenAIClient

        async with self._init_lock:
            if self._default_graphiti is None:
                import os
                from graphiti_core.llm_client.config import LLMConfig

                # Ensure OPENAI_API_KEY is set (Graphiti may also read it from env)
                api_key = Config.LLM_API_KEY or os.environ.get('OPENAI_API_KEY', '')
                if api_key:
                    os.environ['OPENAI_API_KEY'] = api_key

                # Pass base URL as-is — Graphiti/OpenAI SDK handles it correctly
                base_url = Config.LLM_BASE_URL or None

                llm_config = LLMConfig(
                    api_key=api_key,
                    base_url=base_url,
                    model=Config.LLM_MODEL_NAME or 'gpt-4o-mini',
                )
                llm_client = OpenAIClient(config=llm_config)

                self._default_graphiti = Graphiti(
                    uri=self._uri,
                    user=self._user,
                    password=self._password,
                    llm_client=llm_client,
                )

                # Build indices on first init (try both old and new API names)
                try:
                    await self._default_graphiti.build_indices()
                except AttributeError:
                    try:
                        await self._default_graphiti.build_indices_and_constraints()
                    except AttributeError:
                        logger.warning("Could not build Graphiti indices (method not found)")
                logger.info("Graphiti initialised with Neo4j at %s", self._uri)

        return self._default_graphiti

    def _run_async(self, coro):
        """
        Run an async coroutine from synchronous code.

        Always dispatches to the dedicated background event loop so that
        all Graphiti/asyncio objects (Locks, Futures, connections) share
        the same loop.  Safe to call from any thread.
        """
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def close(self):
        """Close connections."""
        self._driver.close()
        if self._default_graphiti:
            try:
                self._run_async(self._default_graphiti.close())
            except Exception:
                logger.warning("Error closing Graphiti instance", exc_info=True)
        # Shut down the dedicated event loop
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)

    # ----------------------------------------------------------------
    # Graph lifecycle
    # ----------------------------------------------------------------

    def create_graph(self, name: str, description: str = "") -> str:
        """Create a new graph namespace.

        Returns graph_id which doubles as the Graphiti ``group_id``.
        """
        graph_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        # Store graph metadata in Neo4j (same schema as Neo4jStorage for compat)
        with self._driver.session() as session:
            session.run(
                """
                CREATE (g:Graph {
                    graph_id: $graph_id,
                    name: $name,
                    description: $description,
                    ontology_json: '{}',
                    created_at: $created_at
                })
                """,
                graph_id=graph_id,
                name=name,
                description=description,
                created_at=now,
            )

        logger.info("Created graph '%s' with id %s", name, graph_id)
        return graph_id

    def delete_graph(self, graph_id: str) -> None:
        """Delete all data for a graph namespace."""
        with self._driver.session() as session:
            # Delete Graphiti nodes with this group_id
            session.run(
                "MATCH (n {group_id: $gid}) DETACH DELETE n",
                gid=graph_id,
            )
            # Delete legacy Entity/Episode nodes
            session.run(
                "MATCH (n {graph_id: $gid}) DETACH DELETE n",
                gid=graph_id,
            )
            # Delete graph metadata node
            session.run(
                "MATCH (g:Graph {graph_id: $gid}) DELETE g",
                gid=graph_id,
            )
        logger.info("Deleted graph %s", graph_id)

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]) -> None:
        """Store ontology for a graph."""
        self._ontologies[graph_id] = ontology
        with self._driver.session() as session:
            session.run(
                """
                MATCH (g:Graph {graph_id: $gid})
                SET g.ontology_json = $ontology_json
                """,
                gid=graph_id,
                ontology_json=json.dumps(ontology, ensure_ascii=False),
            )

    def get_ontology(self, graph_id: str) -> Dict[str, Any]:
        """Retrieve stored ontology."""
        if graph_id in self._ontologies:
            return self._ontologies[graph_id]

        with self._driver.session() as session:
            result = session.run(
                "MATCH (g:Graph {graph_id: $gid}) RETURN g.ontology_json AS oj",
                gid=graph_id,
            )
            record = result.single()
            if record and record["oj"]:
                ontology = json.loads(record["oj"])
                self._ontologies[graph_id] = ontology
                return ontology
        return {}

    # ----------------------------------------------------------------
    # Add data via Graphiti episodes
    # ----------------------------------------------------------------

    def add_text(self, graph_id: str, text: str) -> str:
        """Process text via Graphiti's ``add_episode``.

        Graphiti handles NER, entity resolution, embedding, and dedup
        internally.  Returns an episode id for tracking.
        """
        episode_id = str(uuid.uuid4())

        async def _add():
            graphiti = await self._get_graphiti()
            await graphiti.add_episode(
                name=f"chunk_{episode_id[:8]}",
                episode_body=text,
                source_description="document_chunk",
                group_id=graph_id,
                reference_time=datetime.now(timezone.utc),
            )

        self._run_async(_add())

        logger.info("[add_text] Added episode %s to graph %s", episode_id, graph_id)
        return episode_id

    async def add_text_async(self, graph_id: str, text: str) -> str:
        """Async version of :meth:`add_text` for use in async contexts."""
        episode_id = str(uuid.uuid4())

        graphiti = await self._get_graphiti()
        await graphiti.add_episode(
            name=f"chunk_{episode_id[:8]}",
            episode_body=text,
            source_description="document_chunk",
            group_id=graph_id,
            reference_time=datetime.now(timezone.utc),
        )

        logger.info("[add_text_async] Added episode %s to graph %s", episode_id, graph_id)
        return episode_id

    def add_text_batch(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 3,
        progress_callback: Optional[Callable] = None,
    ) -> List[str]:
        """Batch-add text chunks.  Synchronous wrapper."""
        episode_ids: List[str] = []
        total = len(chunks)

        for i, chunk in enumerate(chunks):
            if not chunk or not chunk.strip():
                continue
            episode_id = self.add_text(graph_id, chunk)
            episode_ids.append(episode_id)

            if progress_callback:
                progress = (i + 1) / total
                progress_callback(progress)

            logger.info("Processed chunk %d/%d", i + 1, total)

        return episode_ids

    async def add_text_batch_async(
        self,
        graph_id: str,
        chunks: List[str],
        max_concurrency: int = 5,
        progress_callback: Optional[Callable] = None,
    ) -> List[str]:
        """Async batch-add with bounded parallelism via :class:`asyncio.Semaphore`."""
        semaphore = asyncio.Semaphore(max_concurrency)
        total = len(chunks)
        completed = 0

        async def _process_chunk(idx: int, chunk: str) -> Optional[str]:
            nonlocal completed
            if not chunk or not chunk.strip():
                return None
            async with semaphore:
                eid = await self.add_text_async(graph_id, chunk)
                completed += 1
                if progress_callback:
                    progress_callback(
                        f"Processing chunk {completed}/{total}...",
                        completed / total,
                    )
                logger.info("Processed chunk %d/%d", completed, total)
                return eid

        tasks = [_process_chunk(i, chunk) for i, chunk in enumerate(chunks)]
        results = await asyncio.gather(*tasks)
        return [eid for eid in results if eid is not None]

    def wait_for_processing(
        self,
        episode_ids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = 600,
    ) -> None:
        """No-op — Graphiti processes synchronously within ``add_episode``."""
        if progress_callback:
            progress_callback(1.0)

    # ----------------------------------------------------------------
    # Read nodes — query Graphiti EntityNode data in Neo4j
    # ----------------------------------------------------------------

    def get_all_nodes(self, graph_id: str, limit: int = 2000) -> List[Dict[str, Any]]:
        """Get all entity nodes for a graph."""
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (n:Entity {group_id: $gid})
                RETURN n, labels(n) AS labels
                ORDER BY n.created_at DESC
                LIMIT $limit
                """,
                gid=graph_id,
                limit=limit,
            )
            nodes = [
                self._entity_node_to_dict(record["n"], record["labels"])
                for record in result
            ]

            # Fallback: also check legacy Entity nodes with graph_id property
            if not nodes:
                result = session.run(
                    """
                    MATCH (n:Entity {graph_id: $gid})
                    RETURN n, labels(n) AS labels
                    ORDER BY n.created_at DESC
                    LIMIT $limit
                    """,
                    gid=graph_id,
                    limit=limit,
                )
                nodes = [
                    self._legacy_node_to_dict(record["n"], record["labels"])
                    for record in result
                ]

            return nodes

    def get_node(self, uuid_str: str) -> Optional[Dict[str, Any]]:
        """Get a single entity node by UUID."""
        with self._driver.session() as session:
            result = session.run(
                "MATCH (n:Entity {uuid: $uuid}) RETURN n, labels(n) AS labels",
                uuid=uuid_str,
            )
            record = result.single()
            if record:
                return self._entity_node_to_dict(record["n"], record["labels"])
            return None

    def get_node_edges(self, node_uuid: str) -> List[Dict[str, Any]]:
        """Get all edges connected to a node."""
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (n:Entity {uuid: $uuid})-[r]-(m:Entity)
                WHERE type(r) <> 'MENTIONS'
                RETURN r,
                       startNode(r).uuid AS src_uuid,
                       endNode(r).uuid   AS tgt_uuid,
                       type(r)           AS rel_type
                """,
                uuid=node_uuid,
            )
            return [
                self._edge_to_dict(
                    record["r"],
                    record["src_uuid"],
                    record["tgt_uuid"],
                    rel_type=record["rel_type"],
                )
                for record in result
            ]

    def get_nodes_by_label(self, graph_id: str, label: str) -> List[Dict[str, Any]]:
        """Get nodes filtered by entity-type label."""
        with self._driver.session() as session:
            # Graphiti stores entity types in labels and/or summary
            result = session.run(
                """
                MATCH (n:Entity {group_id: $gid})
                WHERE n.summary CONTAINS $label
                   OR any(l IN labels(n) WHERE l = $label)
                RETURN n, labels(n) AS labels
                """,
                gid=graph_id,
                label=label,
            )
            nodes = [
                self._entity_node_to_dict(record["n"], record["labels"])
                for record in result
            ]

            if not nodes:
                # Fallback to legacy format with dynamic label
                query = (
                    "MATCH (n:Entity:`" + label + "` {graph_id: $gid}) "
                    "RETURN n, labels(n) AS labels"
                )
                result = session.run(query, gid=graph_id)
                nodes = [
                    self._legacy_node_to_dict(record["n"], record["labels"])
                    for record in result
                ]

            return nodes

    # ----------------------------------------------------------------
    # Read edges
    # ----------------------------------------------------------------

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        """Get all edges for a graph."""
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (src:Entity)-[r]->(tgt:Entity)
                WHERE (r.group_id = $gid OR r.graph_id = $gid)
                  AND type(r) <> 'MENTIONS'
                RETURN r,
                       src.uuid AS src_uuid,
                       tgt.uuid AS tgt_uuid,
                       type(r)  AS rel_type
                ORDER BY r.created_at DESC
                """,
                gid=graph_id,
            )
            return [
                self._edge_to_dict(
                    record["r"],
                    record["src_uuid"],
                    record["tgt_uuid"],
                    rel_type=record["rel_type"],
                )
                for record in result
            ]

    # ----------------------------------------------------------------
    # Search via Graphiti hybrid search
    # ----------------------------------------------------------------

    def search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
    ):
        """Search using Graphiti's hybrid (vector + keyword) search."""

        async def _search():
            graphiti = await self._get_graphiti()
            return await graphiti.search(
                query=query,
                group_ids=[graph_id],
                num_results=limit,
            )

        graphiti_results = self._run_async(_search())

        # Convert Graphiti search results to MiroShark format
        edges: List[Dict[str, Any]] = []
        for edge in graphiti_results:
            edges.append({
                "uuid": getattr(edge, "uuid", str(uuid.uuid4())),
                "name": getattr(edge, "name", ""),
                "fact": getattr(edge, "fact", str(edge)),
                "source_node_uuid": getattr(edge, "source_node_uuid", ""),
                "target_node_uuid": getattr(edge, "target_node_uuid", ""),
                "score": getattr(edge, "score", 0.0),
                "created_at": (
                    edge.created_at.isoformat()
                    if getattr(edge, "created_at", None)
                    else None
                ),
            })

        return {"edges": edges, "nodes": [], "query": query}

    async def search_async(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
        scope: str = "edges",
    ) -> Dict[str, Any]:
        """Async version of :meth:`search`."""
        graphiti = await self._get_graphiti()
        results = await graphiti.search(
            query=query,
            group_ids=[graph_id],
            num_results=limit,
        )

        edges: List[Dict[str, Any]] = []
        for edge in results:
            edges.append({
                "uuid": getattr(edge, "uuid", str(uuid.uuid4())),
                "name": getattr(edge, "name", ""),
                "fact": getattr(edge, "fact", str(edge)),
                "source_node_uuid": getattr(edge, "source_node_uuid", ""),
                "target_node_uuid": getattr(edge, "target_node_uuid", ""),
                "score": getattr(edge, "score", 0.0),
                "created_at": (
                    edge.created_at.isoformat()
                    if getattr(edge, "created_at", None)
                    else None
                ),
            })

        return {"edges": edges, "nodes": [], "query": query}

    # ----------------------------------------------------------------
    # Graph info
    # ----------------------------------------------------------------

    def get_graph_info(self, graph_id: str) -> Dict[str, Any]:
        """Get graph metadata: counts and entity types."""
        with self._driver.session() as session:
            node_result = session.run(
                """
                MATCH (n:Entity)
                WHERE n.group_id = $gid OR n.graph_id = $gid
                RETURN count(n) AS cnt
                """,
                gid=graph_id,
            )
            node_count = node_result.single()["cnt"]

            edge_result = session.run(
                """
                MATCH ()-[r]->()
                WHERE (r.group_id = $gid OR r.graph_id = $gid)
                  AND type(r) <> 'MENTIONS'
                RETURN count(r) AS cnt
                """,
                gid=graph_id,
            )
            edge_count = edge_result.single()["cnt"]

            label_result = session.run(
                """
                MATCH (n:Entity)
                WHERE n.group_id = $gid OR n.graph_id = $gid
                UNWIND labels(n) AS lbl
                WITH lbl WHERE lbl NOT IN ['Entity', 'EntityNode']
                RETURN DISTINCT lbl
                """,
                gid=graph_id,
            )
            entity_types = [record["lbl"] for record in label_result]

            return {
                "graph_id": graph_id,
                "node_count": node_count,
                "edge_count": edge_count,
                "entity_types": entity_types,
            }

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """Full graph dump for frontend visualisation."""
        with self._driver.session() as session:
            # Nodes
            node_result = session.run(
                """
                MATCH (n:Entity)
                WHERE n.group_id = $gid OR n.graph_id = $gid
                RETURN n, labels(n) AS labels
                """,
                gid=graph_id,
            )
            nodes: List[Dict[str, Any]] = []
            for record in node_result:
                nd = self._entity_node_to_dict(record["n"], record["labels"])
                nodes.append(nd)

            # Edges
            edge_result = session.run(
                """
                MATCH (src:Entity)-[r]->(tgt:Entity)
                WHERE (r.group_id = $gid OR r.graph_id = $gid)
                  AND type(r) <> 'MENTIONS'
                RETURN r,
                       src.uuid AS src_uuid, tgt.uuid AS tgt_uuid,
                       src.name AS src_name, tgt.name AS tgt_name,
                       type(r)  AS rel_type
                """,
                gid=graph_id,
            )
            edges: List[Dict[str, Any]] = []
            for record in edge_result:
                ed = self._edge_to_dict(
                    record["r"],
                    record["src_uuid"],
                    record["tgt_uuid"],
                    rel_type=record["rel_type"],
                )
                ed["fact_type"] = ed["name"]
                ed["source_node_name"] = record["src_name"] or ""
                ed["target_node_name"] = record["tgt_name"] or ""
                ed["episodes"] = ed.get("episode_ids", [])
                edges.append(ed)

            return {
                "graph_id": graph_id,
                "nodes": nodes,
                "edges": edges,
                "node_count": len(nodes),
                "edge_count": len(edges),
            }

    # ----------------------------------------------------------------
    # Dict conversion helpers
    # ----------------------------------------------------------------

    @staticmethod
    def _entity_node_to_dict(node, labels: List[str]) -> Dict[str, Any]:
        """Convert a Graphiti EntityNode to MiroShark node format."""
        props = dict(node)

        # Remove bulky internal fields
        props.pop("embedding", None)
        props.pop("name_lower", None)

        # Parse attributes JSON
        attrs_json = props.pop("attributes_json", "{}")
        try:
            attributes = json.loads(attrs_json) if attrs_json else {}
        except (json.JSONDecodeError, TypeError):
            attributes = {}

        filtered_labels = [
            l for l in (labels or []) if l not in ("Entity", "EntityNode")
        ]

        return {
            "uuid": props.get("uuid", ""),
            "name": props.get("name", ""),
            "labels": filtered_labels,
            "summary": props.get("summary", ""),
            "attributes": attributes,
            "created_at": props.get("created_at"),
        }

    @staticmethod
    def _legacy_node_to_dict(node, labels: List[str]) -> Dict[str, Any]:
        """Convert legacy Neo4jStorage node format."""
        props = dict(node)
        attrs_json = props.pop("attributes_json", "{}")
        try:
            attributes = json.loads(attrs_json) if attrs_json else {}
        except (json.JSONDecodeError, TypeError):
            attributes = {}
        props.pop("embedding", None)
        props.pop("name_lower", None)

        return {
            "uuid": props.get("uuid", ""),
            "name": props.get("name", ""),
            "labels": [l for l in labels if l != "Entity"] if labels else [],
            "summary": props.get("summary", ""),
            "attributes": attributes,
            "created_at": props.get("created_at"),
        }

    @staticmethod
    def _edge_to_dict(
        rel,
        source_uuid: str,
        target_uuid: str,
        rel_type: str = "RELATION",
    ) -> Dict[str, Any]:
        """Convert Neo4j relationship to MiroShark edge format."""
        props = dict(rel)
        attrs_json = props.pop("attributes_json", "{}")
        try:
            attributes = json.loads(attrs_json) if attrs_json else {}
        except (json.JSONDecodeError, TypeError):
            attributes = {}
        props.pop("fact_embedding", None)
        props.pop("embedding", None)

        episode_ids = props.get("episode_ids", [])
        if episode_ids and not isinstance(episode_ids, list):
            episode_ids = [str(episode_ids)]

        return {
            "uuid": props.get("uuid", ""),
            "name": props.get("name", rel_type),
            "fact": props.get("fact", ""),
            "source_node_uuid": source_uuid,
            "target_node_uuid": target_uuid,
            "attributes": attributes,
            "created_at": props.get("created_at"),
            "valid_at": props.get("valid_at"),
            "invalid_at": props.get("invalid_at"),
            "expired_at": props.get("expired_at"),
            "episode_ids": episode_ids,
        }
