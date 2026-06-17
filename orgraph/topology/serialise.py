"""Serialisation for TopologyMap and Leiden communities.

Writes to .orgraph/topology.json and .orgraph/communities.json.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from orgraph.topology.topology import TopologyCluster, TopologyMap


def save_topology(topology: TopologyMap, orgraph_dir: Path) -> None:
    data = {
        "clusters": [_cluster_to_dict(c) for c in topology.clusters],
        "file_indegree": topology.file_indegree,
        "file_call_depth": topology.file_call_depth,
        "file_cluster_id": topology.file_cluster_id,
        "foundational_files": topology.foundational_files,
    }
    (orgraph_dir / "topology.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


def load_topology(orgraph_dir: Path) -> TopologyMap | None:
    path = orgraph_dir / "topology.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    clusters = [_dict_to_cluster(c) for c in data.get("clusters", [])]
    return TopologyMap(
        clusters=clusters,
        file_indegree=data.get("file_indegree", {}),
        file_call_depth={k: int(v) for k, v in data.get("file_call_depth", {}).items()},
        file_cluster_id=data.get("file_cluster_id", {}),
        foundational_files=data.get("foundational_files", []),
    )


def save_communities(communities: dict[int, list[str]], orgraph_dir: Path) -> None:
    # JSON keys must be strings
    serialisable = {str(k): v for k, v in communities.items()}
    (orgraph_dir / "communities.json").write_text(
        json.dumps(serialisable, indent=2), encoding="utf-8"
    )


def load_communities(orgraph_dir: Path) -> dict[int, list[str]] | None:
    path = orgraph_dir / "communities.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw.items()}


def _cluster_to_dict(c: TopologyCluster) -> dict:
    return {
        "cluster_id": c.cluster_id,
        "entry_files": c.entry_files,
        "entry_symbols": c.entry_symbols,
        "all_files": c.all_files,
        "min_depth": c.min_depth,
        "max_depth": c.max_depth,
        "side_effects": c.side_effects,
        "external_calls": c.external_calls,
        "shared_dep_files": c.shared_dep_files,
        "avg_indegree": c.avg_indegree,
        "is_foundational": c.is_foundational,
    }


def _dict_to_cluster(d: dict) -> TopologyCluster:
    return TopologyCluster(
        cluster_id=d["cluster_id"],
        entry_files=d.get("entry_files", []),
        entry_symbols=d.get("entry_symbols", []),
        all_files=d.get("all_files", []),
        min_depth=d.get("min_depth", 999),
        max_depth=d.get("max_depth", 0),
        side_effects=d.get("side_effects", []),
        external_calls=d.get("external_calls", []),
        shared_dep_files=d.get("shared_dep_files", []),
        avg_indegree=d.get("avg_indegree", 0.0),
        is_foundational=d.get("is_foundational", False),
    )
