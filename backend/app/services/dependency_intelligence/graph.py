"""
Lightweight in-memory dependency graph.

Built from the deterministic taxonomy in ``taxonomy.py``. This is NOT a graph
database and adds no persistence: topology/relationship data stays in PostgreSQL
and is consumed through existing repositories by the caller (see ``analysis.py``).

Traversal is bounded and cycle-protected.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.dependency_intelligence import taxonomy

#: Hard ceilings so a malformed taxonomy can never cause unbounded work.
MAX_DEPTH = 12
MAX_NODES = 200


@dataclass(frozen=True)
class Edge:
    """parent -> depends_on (required), or parent -> optional_depends_on."""

    parent: str
    dependency: str
    required: bool = True

    def to_dict(self) -> dict:
        return {"parent": self.parent, "dependency": self.dependency, "required": self.required}


@dataclass
class DependencyGraph:
    """In-memory protocol dependency graph with cycle protection."""

    nodes: set[str] = field(default_factory=set)
    edges: list[Edge] = field(default_factory=list)
    _required: dict[str, tuple[str, ...]] = field(default_factory=dict)
    _optional: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def from_taxonomy(cls) -> "DependencyGraph":
        graph = cls()
        for proto in taxonomy.all_protocols():
            graph.add_protocol(proto.identifier)
        return graph

    def add_protocol(self, identifier: str) -> None:
        key = str(identifier).strip().lower()
        proto = taxonomy.get_protocol(key)
        if proto is None or key in self.nodes:
            return
        self.nodes.add(key)
        self._required[key] = tuple(proto.dependencies)
        self._optional[key] = tuple(proto.optional_dependencies)
        for dep in proto.dependencies:
            self.edges.append(Edge(parent=key, dependency=dep, required=True))
        for dep in proto.optional_dependencies:
            self.edges.append(Edge(parent=key, dependency=dep, required=False))

    # -- lookup ----------------------------------------------------------

    def direct_dependencies(self, identifier: str, *, required_only: bool = True) -> list[str]:
        """What ``identifier`` needs. Order is taxonomy declaration order."""
        key = str(identifier).strip().lower()
        if required_only:
            return list(self._required.get(key, ()))
        return list(self._required.get(key, ())) + list(self._optional.get(key, ()))

    def direct_dependents(self, identifier: str) -> list[str]:
        """What BREAKS if ``identifier`` fails (reverse edges, required only)."""
        key = str(identifier).strip().lower()
        return sorted({e.parent for e in self.edges if e.dependency == key and e.required})

    # -- traversal -------------------------------------------------------

    def traverse(self, identifier: str, *, max_depth: int = MAX_DEPTH) -> list[str]:
        """Depth-first transitive closure of dependencies, cycle-protected.

        Returns dependencies in a deterministic, breadth-first-ish order
        (sorted), excluding the starting node.
        """
        start = str(identifier).strip().lower()
        if start not in self.nodes:
            return []
        seen: set[str] = {start}
        ordered: list[str] = []
        frontier = [start]
        depth = 0
        while frontier and depth < max_depth and len(seen) <= MAX_NODES:
            nxt: list[str] = []
            for node in frontier:
                for dep in self.direct_dependencies(node):
                    if dep in seen:
                        continue  # cycle / already visited - never revisited
                    seen.add(dep)
                    ordered.append(dep)
                    nxt.append(dep)
                    if len(seen) > MAX_NODES:
                        break
            frontier = sorted(nxt)
            depth += 1
        return ordered

    def dependency_path(self, start: str, target: str, *, max_depth: int = MAX_DEPTH) -> list[str] | None:
        """Shortest dependency path start -> target, or None. Cycle-protected."""
        s = str(start).strip().lower()
        t = str(target).strip().lower()
        if s not in self.nodes or t not in self.nodes:
            return None
        if s == t:
            return None  # a node is not its own dependency
        queue: list[list[str]] = [[s]]
        visited: set[str] = {s}
        while queue:
            path = queue.pop(0)
            if len(path) > max_depth:
                return None
            for dep in self.direct_dependencies(path[-1]):
                if dep == t:
                    return path + [dep]
                if dep not in visited:
                    visited.add(dep)
                    queue.append(path + [dep])
        return None

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "nodes": sorted(self.nodes),
            "edges": [e.to_dict() for e in self.edges],
        }
