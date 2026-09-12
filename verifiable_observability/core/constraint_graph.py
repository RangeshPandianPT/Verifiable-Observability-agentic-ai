"""
Cross-Agent Constraint Graph — Phase 2b.

When an agent delegates a sub-task to another agent, the child trajectory
must inherit the parent's constraint state.  This prevents circumventing a
block by simply asking a sub-agent to perform the forbidden action.

The graph maintains parent→child relationships and transitively propagates
blocked tools and accumulated constraint IDs.

Example::

    graph = ConstraintGraph()
    graph.register_delegation("parent-traj", "child-traj",
                              blocked_tools={"execute_transfer"})
    assert graph.is_tool_blocked_by_parent("child-traj", "execute_transfer")
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Constraint node
# ---------------------------------------------------------------------------


@dataclass
class ConstraintNode:
    """One node in the constraint inheritance graph."""

    trajectory_id: str
    parent_trajectory_id: str | None = None
    blocked_tools: set[str] = field(default_factory=set)
    blocked_constraint_ids: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Constraint graph
# ---------------------------------------------------------------------------


class ConstraintGraph:
    """
    Maintains parent→child constraint inheritance for delegated tasks.

    Thread-safe; all mutations are guarded by a lock.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, ConstraintNode] = {}
        self._lock = threading.Lock()

    def register_trajectory(
        self,
        trajectory_id: str,
        blocked_tools: set[str] | None = None,
        blocked_constraint_ids: set[str] | None = None,
    ) -> None:
        """Register a root trajectory (no parent)."""
        with self._lock:
            self._nodes[trajectory_id] = ConstraintNode(
                trajectory_id=trajectory_id,
                blocked_tools=blocked_tools or set(),
                blocked_constraint_ids=blocked_constraint_ids or set(),
            )

    def register_delegation(
        self,
        parent_id: str,
        child_id: str,
        blocked_tools: set[str] | None = None,
        blocked_constraint_ids: set[str] | None = None,
    ) -> None:
        """
        Register a child trajectory that inherits the parent's constraints.

        The child inherits all blocked tools and constraint IDs from the
        parent (transitively — if the parent itself inherits from a grandparent,
        those blocks propagate too).

        Additional blocks can be specified via ``blocked_tools`` and
        ``blocked_constraint_ids``.
        """
        inherited_tools = self.get_inherited_blocked_tools(parent_id)
        inherited_cids = self.get_inherited_blocked_constraint_ids(parent_id)

        with self._lock:
            self._nodes[child_id] = ConstraintNode(
                trajectory_id=child_id,
                parent_trajectory_id=parent_id,
                blocked_tools=inherited_tools | (blocked_tools or set()),
                blocked_constraint_ids=inherited_cids | (blocked_constraint_ids or set()),
            )

    def add_block(
        self,
        trajectory_id: str,
        tool_name: str | None = None,
        constraint_id: str | None = None,
    ) -> None:
        """Add a block to an existing trajectory node."""
        with self._lock:
            node = self._nodes.get(trajectory_id)
            if node is None:
                node = ConstraintNode(trajectory_id=trajectory_id)
                self._nodes[trajectory_id] = node
            if tool_name:
                node.blocked_tools.add(tool_name)
            if constraint_id:
                node.blocked_constraint_ids.add(constraint_id)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_inherited_blocked_tools(self, trajectory_id: str) -> set[str]:
        """
        Return all blocked tools for a trajectory, including inherited ones.

        Walks the parent chain transitively.
        """
        result: set[str] = set()
        visited: set[str] = set()
        current_id: str | None = trajectory_id

        with self._lock:
            while current_id and current_id not in visited:
                visited.add(current_id)
                node = self._nodes.get(current_id)
                if node is None:
                    break
                result |= node.blocked_tools
                current_id = node.parent_trajectory_id

        return result

    def get_inherited_blocked_constraint_ids(self, trajectory_id: str) -> set[str]:
        """Return all blocked constraint IDs, including inherited ones."""
        result: set[str] = set()
        visited: set[str] = set()
        current_id: str | None = trajectory_id

        with self._lock:
            while current_id and current_id not in visited:
                visited.add(current_id)
                node = self._nodes.get(current_id)
                if node is None:
                    break
                result |= node.blocked_constraint_ids
                current_id = node.parent_trajectory_id

        return result

    def is_tool_blocked_by_parent(
        self, trajectory_id: str, tool_name: str
    ) -> bool:
        """Check if a tool is blocked for this trajectory (including inheritance)."""
        return tool_name in self.get_inherited_blocked_tools(trajectory_id)

    def has_trajectory(self, trajectory_id: str) -> bool:
        """Check if a trajectory is registered in the graph."""
        with self._lock:
            return trajectory_id in self._nodes

    def clear(self) -> None:
        """Remove all nodes (useful for tests)."""
        with self._lock:
            self._nodes.clear()
