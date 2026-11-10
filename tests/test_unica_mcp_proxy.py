#!/usr/bin/env python3
"""Тесты моста unica MCP: ответ в structuredContent доходит до модели текстом.

spec: Т5 — «живой mcp__unica__apply проходит, исход виден в tool/result»;
клиент MCP DSH показывает только `content`, Юника 0.15 отвечает только
`structuredContent`.

Запуск: python3 tests/test_unica_mcp_proxy.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib import check, summary  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROXY = ROOT / "scripts" / "unica_mcp_proxy.py"

FAKE = r'''
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        out = {"jsonrpc": "2.0", "id": message["id"], "result": {"protocolVersion": "2025-11-25", "serverInfo": {"name": "unica", "version": "0.15.0-rc.5"}, "capabilities": {"tools": {}}}}
    elif method == "tools/call":
        name = message["params"]["name"]
        if name == "unica.view":
            out = {"jsonrpc": "2.0", "id": message["id"], "result": {"content": [], "structuredContent": {"ok": True, "summary": "workspace"}}}
        elif name == "unica.docs":
            out = {"jsonrpc": "2.0", "id": message["id"], "result": {"content": [{"type": "text", "text": "готовый текст"}], "structuredContent": {"ok": True}}}
        else:
            out = {"jsonrpc": "2.0", "id": message["id"], "result": {"content": [], "structuredContent": {"ok": True, "data": {"executionToken": "t"}}}}
    else:
        continue
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
'''


def run_proxy(stdin: str) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "fake.py"
        fake.write_text(FAKE, encoding="utf-8")
        env = dict(os.environ)
        env["UNICA_PROXY_COMMAND"] = f"{sys.executable} {fake}"
        proc = subprocess.run(
            [sys.executable, str(PROXY)], input=stdin, capture_output=True,
            text=True, timeout=60, env=env,
        )
    check("мост завершился кодом 0", proc.returncode == 0, proc.stderr[:300])
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return lines


def main() -> int:
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "unica.view", "arguments": {}}}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "unica.docs", "arguments": {}}}),
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "unica.apply", "arguments": {}}}),
    ]) + "\n"
    lines = run_proxy(requests)
    by_id = {line.get("id"): line for line in lines}
    check("ответов столько же, сколько запросов", len(lines) == 4, str(len(lines)))
    check("initialize не тронут: content не появился",
          by_id[1]["result"].get("content") is None, json.dumps(by_id[1])[:200])
    view = by_id[2]["result"]
    check("view: content дописан из structuredContent",
          view.get("content") and view["content"][0]["type"] == "text"
          and json.loads(view["content"][0]["text"])["summary"] == "workspace",
          json.dumps(view, ensure_ascii=False)[:300])
    check("view: structuredContent сохранён",
          view.get("structuredContent", {}).get("ok") is True, "")
    docs = by_id[3]["result"]
    check("docs: готовый content не перезаписан",
          docs["content"][0]["text"] == "готовый текст", json.dumps(docs, ensure_ascii=False)[:200])
    apply_result = by_id[4]["result"]
    check("apply: executionToken виден модели текстом",
          json.loads(apply_result["content"][0]["text"])["data"]["executionToken"] == "t", "")
    return summary()


if __name__ == "__main__":
    sys.exit(main())
