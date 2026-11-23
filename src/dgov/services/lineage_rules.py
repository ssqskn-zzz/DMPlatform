"""Deterministic lineage checks.

These are cheap, explainable and hallucination-free, so they always run even
when the agent is unavailable.  Agent findings are merged on top and de-duped.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from dgov.services.metadata_rules import layer_for

LAYER_ORDER = {"ODS": 0, "STG": 1, "DWD": 2, "DIM": 3, "DWS": 3, "ADS": 4, "TMP": -1, "OTHER": -1}
TEMP_PATTERN = re.compile(r"^(tmp_|dq_monitor_|temp_|stg_)", re.IGNORECASE)


def infer_layer(name: str, schema: str, hint: str = "") -> str:
    if hint and hint.upper() in LAYER_ORDER:
        return hint.upper()
    raw = (schema or "").strip().upper()
    if raw in LAYER_ORDER:
        return raw
    return layer_for(name or "")


def dedupe_key(code: str, affected: list[Any]) -> str:
    parts = [str(item).strip().lower() for item in affected if item is not None]
    return f"{code}:{','.join(sorted(set(parts)))}"


def _issue(
    code: str,
    *,
    severity: str,
    confidence: float,
    title: str,
    affected: list[Any],
    description: str,
    evidence: dict[str, Any],
    suggestion: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "category": code,
        "severity": severity,
        "confidence": confidence,
        "source": "rule",
        "title": title,
        "description": description,
        "affected": affected,
        "evidence": evidence,
        "suggestion": suggestion,
        "dedupe_key": dedupe_key(code, affected),
    }


def run_rules(
    edges: list[dict[str, Any]],
    *,
    known_table_ids: set[str] | None = None,
    changed_ids: set[str] | None = None,
    raw_edges: list[dict[str, Any]] | None = None,
    max_dangling: int = 20,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    known_table_ids = known_table_ids or set()
    changed_ids = changed_ids or set()

    # 1. duplicate edges -------------------------------------------------
    # The persisted graph is de-duplicated by the ingestion pipeline, so use
    # the original API rows when available to detect duplicate reporting.
    duplicate_source = raw_edges if raw_edges else edges
    counts: Counter[tuple[str, str, str]] = Counter()
    for edge in duplicate_source:
        counts[
            (
                str(edge.get("source_table_id") or ""),
                str(edge.get("target_table_id") or ""),
                str(edge.get("type") or "0"),
            )
        ] += 1
    for (src, tgt, edge_type), count in counts.items():
        if count > 1:
            issues.append(
                _issue(
                    "duplicate_edge",
                    severity="medium",
                    confidence=1.0,
                    title=f"重复血缘边 {src} → {tgt}",
                    affected=[src, tgt],
                    description=f"同一组 (源表, 目标表, 类型) 出现 {count} 次，可能是重复上报。",
                    evidence={
                        "source_table_id": src,
                        "target_table_id": tgt,
                        "type": edge_type,
                        "count": count,
                    },
                    suggestion="按 (源表, 目标表, 血缘类型) 唯一约束去重。",
                )
            )

    # 2. self loops and dangling ids -------------------------------------
    dangling: set[str] = set()
    for edge in edges:
        src = str(edge.get("source_table_id") or "")
        tgt = str(edge.get("target_table_id") or "")
        if src and src == tgt:
            issues.append(
                _issue(
                    "self_loop",
                    severity="high",
                    confidence=1.0,
                    title=f"自环依赖 {src}",
                    affected=[src],
                    description="任务依赖自身，通常意味着血缘数据错误。",
                    evidence={"table_id": src},
                    suggestion="核对该任务的上下游解析结果。",
                )
            )
        if known_table_ids:
            for table_id in (src, tgt):
                if table_id and table_id not in known_table_ids:
                    dangling.add(table_id)
    for table_id in sorted(dangling)[:max_dangling]:
        issues.append(
            _issue(
                "dangling_id",
                severity="low",
                confidence=0.95,
                title=f"血缘节点 {table_id} 未在元数据目录中登记",
                affected=[table_id],
                description="血缘边引用的表 id 在表级元数据中不存在，链路无法完整回溯。",
                evidence={"table_id": table_id, "registered": False},
                suggestion="补采该表元数据或修正血缘接口中的 table_id。",
            )
        )

    # 3. layer direction and temp tables ---------------------------------
    seen_temp: set[str] = set()
    for edge in edges:
        src_name = str(edge.get("source_table") or "")
        tgt_name = str(edge.get("target_table") or "")
        src_layer = infer_layer(
            src_name,
            str(edge.get("source_schema") or ""),
            str(edge.get("source_layer") or ""),
        )
        tgt_layer = infer_layer(
            tgt_name,
            str(edge.get("target_schema") or ""),
            str(edge.get("target_layer") or ""),
        )
        s_order, t_order = LAYER_ORDER.get(src_layer, -1), LAYER_ORDER.get(tgt_layer, -1)
        if s_order >= 0 and t_order >= 0 and t_order < s_order:
            issues.append(
                _issue(
                    "invalid_layer_dependency",
                    severity="high",
                    confidence=0.9,
                    title=f"分层依赖逆向 {src_layer} → {tgt_layer}",
                    affected=[edge.get("source_table_id"), edge.get("target_table_id")],
                    description=(
                        f"{src_name or edge.get('source_table_id')} → "
                        f"{tgt_name or edge.get('target_table_id')} "
                        "违反 ods→dwd→dws→ads 方向。"
                    ),
                    evidence={"source_layer": src_layer, "target_layer": tgt_layer},
                    suggestion="按血缘开发规范调整分层与任务依赖。",
                )
            )
        for name in (src_name, tgt_name):
            if name and TEMP_PATTERN.match(name) and name not in seen_temp:
                seen_temp.add(name)
                issues.append(
                    _issue(
                        "temp_table_in_chain",
                        severity="medium",
                        confidence=0.85,
                        title=f"临时/监控表进入依赖链 {name}",
                        affected=[name],
                        description="临时表或质量监控表不应作为正式任务依赖链的节点。",
                        evidence={"table_name": name},
                        suggestion="改用符合分层前缀规范的正式表名。",
                    )
                )

    # 4. cycles ----------------------------------------------------------
    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        src = str(edge.get("source_table_id") or "")
        tgt = str(edge.get("target_table_id") or "")
        if src and tgt and src != tgt:
            adjacency[src].append(tgt)
    visited: set[str] = set()
    on_stack: set[str] = set()
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        on_stack.add(node)
        for nxt in adjacency.get(node, []):
            if nxt in on_stack:
                start = path.index(nxt) if nxt in path else 0
                cycles.append(path[start:] + [nxt])
            elif nxt not in visited:
                dfs(nxt, path + [nxt])
        on_stack.discard(node)

    for node in list(adjacency):
        if node not in visited:
            dfs(node, [node])
    for cycle in cycles[:10]:
        issues.append(
            _issue(
                "circular_dependency",
                severity="high",
                confidence=1.0,
                title="任务依赖存在环",
                affected=cycle,
                description=" → ".join(cycle),
                evidence={"cycle": cycle},
                suggestion="拆解循环依赖，必要时引入中间层表。",
            )
        )

    # 5. broken chain for nodes touched by the incremental batch ---------
    if changed_ids:
        targets_with_downstream = {str(e.get("source_table_id")) for e in edges}
        for node in sorted(changed_ids):
            if node in targets_with_downstream:
                continue
            terminal = [e for e in edges if str(e.get("target_table_id")) == node]
            if not terminal:
                continue
            edge = terminal[0]
            layer = infer_layer(
                str(edge.get("target_table") or ""),
                str(edge.get("target_schema") or ""),
                str(edge.get("target_layer") or ""),
            )
            if layer in {"ODS", "DWD", "DWS", "DIM", "STG"}:
                issues.append(
                    _issue(
                        "broken_chain",
                        severity="medium",
                        confidence=0.75,
                        title=f"增量节点 {node} 链路疑似中断",
                        affected=[node],
                        description=f"节点 {node} 当前无下游，但其分层为 {layer}，链路未延伸至 ADS。",
                        evidence={"node": node, "layer": layer},
                        suggestion="确认下游任务是否漏采血缘。",
                    )
                )

    return issues
