#!/usr/bin/env python3
"""Local Mem0 MCP backed by Oracle AI Vector Search."""
import json
import os
import pathlib

from mcp.server.fastmcp import FastMCP
from mem0 import Memory


CONFIG = pathlib.Path.home() / ".codex" / "mem0.json"
LOCAL_OPENAI_BASE_URL = "http://mac.tail651fca.ts.net:1234/v1"


def memory():
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    wallet = data["wallet_dir"]
    os.environ["TNS_ADMIN"] = wallet
    connection_params = {
        "user": data["username"], "password": data["password"], "dsn": data["tns_alias"],
        "config_dir": wallet, "wallet_location": wallet,
    }
    if data.get("wallet_password"):
        connection_params["wallet_password"] = data["wallet_password"]
    return Memory.from_config({
        "llm": {"provider": "openai", "config": {
            "model": "qwen3.8-27b-mlx", "openai_base_url": LOCAL_OPENAI_BASE_URL,
            "api_key": "local-no-key",
        }},
        "embedder": {"provider": "openai", "config": {
            "model": "text-embedding-bge-m3", "openai_base_url": LOCAL_OPENAI_BASE_URL,
            "api_key": "local-no-key", "embedding_dims": 1024,
        }},
        "vector_store": {"provider": "oracledb", "config": {
            "collection_name": "CODEX_MEMORIES", "embedding_model_dims": 1024,
            "connection_params": connection_params,
        }},
    })


mcp = FastMCP("mem0")


@mcp.tool()
def search_memory(query: str, user_id: str = "codex") -> str:
    """Search durable project/user memories before repeating prior investigation."""
    return json.dumps(memory().search(query, filters={"user_id": user_id}), ensure_ascii=False, default=str)


@mcp.tool()
def save_memory(content: str, user_id: str = "codex") -> str:
    """Extract and save durable facts from the supplied text as memories."""
    return json.dumps(memory().add(content, user_id=user_id), ensure_ascii=False, default=str)


@mcp.tool()
def list_memories(user_id: str = "codex") -> str:
    """List stored memories for the given user namespace."""
    return json.dumps(memory().get_all(filters={"user_id": user_id}), ensure_ascii=False, default=str)


@mcp.tool()
def delete_memory(memory_id: str) -> str:
    """Permanently delete one memory by its Mem0 identifier."""
    memory().delete(memory_id)
    return json.dumps({"deleted": memory_id})


if __name__ == "__main__":
    mcp.run(transport="stdio")
