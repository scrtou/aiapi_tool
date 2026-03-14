from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi.responses import FileResponse

from libs.core.config import env_str


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_artifacts_root() -> str:
    root = env_str("ARTIFACTS_STORAGE_PATH", "/tmp/aiapi_tool_artifacts")
    os.makedirs(root, exist_ok=True)
    return root


def sanitize_name(name: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", name.strip())
    return safe or "artifact"


def artifact_extension(artifact: dict[str, Any]) -> str:
    mime = (artifact.get("mime_type") or "").lower()
    art_type = (artifact.get("type") or "").lower()
    if mime == "text/html" or art_type == "html_snapshot":
        return ".html"
    if mime == "image/png" or art_type == "screenshot":
        return ".png"
    if mime == "text/plain":
        return ".txt"
    return ".json"


def persist_artifact(store_name: str, task_id: str, artifact: dict[str, Any], index: int) -> dict[str, Any]:
    root = Path(get_artifacts_root()) / store_name / task_id
    root.mkdir(parents=True, exist_ok=True)

    artifact_type = artifact.get("type", "unknown")
    artifact_name = sanitize_name(artifact.get("name", f"artifact_{index}"))
    ext = artifact_extension(artifact)
    file_path = root / f"{artifact_name}{ext}"

    mime_type = artifact.get("mime_type")
    content = artifact.get("content")
    content_base64 = artifact.get("content_base64")

    if content_base64:
        data = base64.b64decode(content_base64)
        file_path.write_bytes(data)
        size_bytes = len(data)
        if not mime_type:
            mime_type = "application/octet-stream"
    elif isinstance(content, str):
        file_path.write_text(content, encoding="utf-8")
        size_bytes = len(content.encode("utf-8"))
        if not mime_type:
            mime_type = "text/plain"
    else:
        serialized = json.dumps(artifact, ensure_ascii=False, indent=2)
        file_path.write_text(serialized, encoding="utf-8")
        size_bytes = len(serialized.encode("utf-8"))
        if not mime_type:
            mime_type = "application/json"

    return {
        "artifact_id": f"{store_name}:{task_id}:{index}",
        "type": artifact_type,
        "name": artifact_name,
        "storage_path": str(file_path),
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "created_at": utcnow_iso(),
        "meta": artifact.get("meta", {}),
    }


def build_file_response(path: str, filename: str | None = None, media_type: str | None = None):
    return FileResponse(path=path, filename=filename, media_type=media_type)
