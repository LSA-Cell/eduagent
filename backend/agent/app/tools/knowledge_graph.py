"""knowledge_graph_query 工具：知识点前置/关联关系查询。

当前为内存样例图谱（K12 数学主干链路），接口与数据解耦：
后续接入真实图谱存储时仅需替换 get_node 的实现。
"""

from __future__ import annotations

from typing import Any, Optional

from app.tools.base import ToolContext, registry


class KnowledgeNode(dict):
    """知识点节点：{subject, prerequisites, related}。"""


_SAMPLE_GRAPH: dict[str, KnowledgeNode] = {
    "有理数": KnowledgeNode(subject="数学", prerequisites=[], related=["数轴", "绝对值"]),
    "整式": KnowledgeNode(subject="数学", prerequisites=["有理数"], related=["因式分解"]),
    "一元一次方程": KnowledgeNode(subject="数学", prerequisites=["整式"], related=["方程的解", "等式性质"]),
    "因式分解": KnowledgeNode(subject="数学", prerequisites=["整式"], related=["一元二次方程", "分式"]),
    "一元二次方程": KnowledgeNode(
        subject="数学", prerequisites=["一元一次方程", "因式分解", "平方根"],
        related=["二次函数", "判别式", "韦达定理"],
    ),
    "二次函数": KnowledgeNode(
        subject="数学", prerequisites=["一元二次方程", "平面直角坐标系"],
        related=["函数图象", "最值问题"],
    ),
    "分式": KnowledgeNode(subject="数学", prerequisites=["因式分解"], related=["分式方程"]),
    "分式方程": KnowledgeNode(subject="数学", prerequisites=["分式", "一元一次方程"], related=["增根"]),
    "平面直角坐标系": KnowledgeNode(subject="数学", prerequisites=["数轴"], related=["二次函数", "一次函数"]),
    "一次函数": KnowledgeNode(subject="数学", prerequisites=["平面直角坐标系"], related=["二次函数"]),
    "现在进行时": KnowledgeNode(subject="英语", prerequisites=["be动词", "动词-ing形式"], related=["一般现在时"]),
    "一般过去时": KnowledgeNode(subject="英语", prerequisites=["动词过去式"], related=["现在完成时"]),
}


def get_node(concept: str) -> Optional[KnowledgeNode]:
    """查询知识点节点；样例实现为内存图谱，生产可替换为 DB/图库查询。"""
    return _SAMPLE_GRAPH.get(concept.strip())


def query_knowledge_graph(concept: str) -> dict[str, Any]:
    """返回知识点的前置/关联关系；concept 不存在时返回 found=False。"""
    node = get_node(concept)
    if node is None:
        return {"concept": concept, "found": False,
                "prerequisites": [], "related": [],
                "available_concepts": sorted(_SAMPLE_GRAPH)}
    return {
        "concept": concept.strip(),
        "found": True,
        "subject": node.get("subject", ""),
        "prerequisites": list(node.get("prerequisites", [])),
        "related": list(node.get("related", [])),
    }


KNOWLEDGE_GRAPH_SCHEMA: dict[str, Any] = {
    "name": "knowledge_graph_query",
    "description": "查询知识图谱中某知识点的前置知识与关联知识点，用于诊断学习路径。",
    "parameters": {
        "type": "object",
        "properties": {
            "concept": {"type": "string", "description": "知识点名称，如 一元二次方程"}
        },
        "required": ["concept"],
    },
}


async def handle_knowledge_graph(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    concept = str(args.get("concept", "")).strip()
    if not concept:
        return {"error": "缺少 concept 参数"}
    return query_knowledge_graph(concept)


registry.register_tool(KNOWLEDGE_GRAPH_SCHEMA, handle_knowledge_graph)
