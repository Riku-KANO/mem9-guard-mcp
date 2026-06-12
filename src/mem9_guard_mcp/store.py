"""MemoryStore Protocol アダプタ。

MemoryGuard はストレージ非依存 (get/set/delete/keys/items/__contains__ を満たせばよい)。
ここで mem9 のレコード指向 API (id ベース) を key/value 契約に適合させる。

マッピング規約:
  - guard のキーはレコードの metadata["amg_key"] に保存
  - guard の値は JSON エンコードして content に保存
  - key→id の対応はクライアント側 index で管理 (初回アクセス時に全件取得して構築)

この index は単一サーバープロセスが唯一の書き手である前提のキャッシュ。
別プロセスが同じ mem9 名前空間に書き込む構成にする場合は refresh() を呼ぶこと。
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from mem9_guard_mcp.client import Mem9Client, record_id

_KEY_FIELD = "amg_key"
_SENTINEL = object()


class Mem9Store:
    def __init__(self, client: Mem9Client) -> None:
        self._client = client
        self._index: dict[str, str] | None = None  # key -> memory id

    def refresh(self) -> None:
        index: dict[str, str] = {}
        for record in self._client.list_memories():
            metadata = record.get("metadata") or {}
            key = metadata.get(_KEY_FIELD)
            if key:
                index[str(key)] = record_id(record)
        self._index = index

    def _ids(self) -> dict[str, str]:
        if self._index is None:
            self.refresh()
        assert self._index is not None
        return self._index

    # ---- MemoryStore Protocol ----------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        memory_id = self._ids().get(key)
        if memory_id is None:
            return default
        record = self._client.get_memory(memory_id)
        return _decode(record.get("content"))

    def set(self, key: str, value: Any) -> None:
        content = json.dumps(value, ensure_ascii=False)
        metadata = {_KEY_FIELD: key}
        memory_id = self._ids().get(key)
        if memory_id is None:
            record = self._client.create_memory(content, metadata)
            self._ids()[key] = record_id(record)
        else:
            self._client.update_memory(memory_id, content, metadata)

    def delete(self, key: str) -> None:
        memory_id = self._ids().pop(key, None)
        if memory_id is not None:
            self._client.delete_memory(memory_id)

    def keys(self) -> Iterator[str]:
        return iter(list(self._ids().keys()))

    def items(self) -> Iterator[tuple[str, Any]]:
        for key in list(self._ids().keys()):
            value = self.get(key, _SENTINEL)
            if value is not _SENTINEL:
                yield key, value

    def __contains__(self, key: str) -> bool:
        return key in self._ids()


class LocalJsonStore:
    """mem9 の API キーがない環境向けのローカル JSON ファイルストア。

    MEM9_API_KEY 未設定時のフォールバック。インターフェースは Mem9Store と同じ
    MemoryStore Protocol なので、キーを発行したら環境変数を設定するだけで
    mem9 バックエンドへ切り替えられる。
    """

    def __init__(self, path: Any) -> None:
        from pathlib import Path

        self._path = Path(path)
        self._data: dict[str, Any] = (
            json.loads(self._path.read_text(encoding="utf-8"))
            if self._path.exists()
            else {}
        )

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._flush()

    def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self._flush()

    def keys(self) -> Iterator[str]:
        return iter(list(self._data.keys()))

    def items(self) -> Iterator[tuple[str, Any]]:
        return iter(list(self._data.items()))

    def __contains__(self, key: str) -> bool:
        return key in self._data


def _decode(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    try:
        return json.loads(content)
    except ValueError:
        return content
