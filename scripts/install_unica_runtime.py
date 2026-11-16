#!/usr/bin/env python3
"""install_unica_runtime.py — глобальная установка бинарей Юники из unica.yaml.

Единый путь обновления: версии и тег читаются из `unica.yaml`, манифест
собирается упаковщиком апстрима (`scripts/ci/package-unica-plugin.py` тега),
bootstrap собирается из исходников тега (в релизные ассеты он не входит),
артефакты греются prefetch в контурный кэш. Сверка установленного с
ожидаемым — `scripts/doctor.py`, живость — `scripts/unica_ping.py`.

Запуск: python3 install_unica_runtime.py [--prune] [--workdir DIR]
  --prune    после успешной установки удалить каталоги прежних версий
             (каталог плагина и кэши артефактов); лаунчер выбирает максимум
             через sort -V, где rc-суффикс выше финала той же версии
  --workdir  рабочий каталог закачек (по умолчанию временный)

Exit: 0 — успех, 1 — ошибка (установка не завершена).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from unica_versions import load as load_unica_versions  # noqa: E402
from doctor import unica_target  # noqa: E402

REPOSITORY = "https://github.com/IngvarConsulting/unica"
RAW = "https://raw.githubusercontent.com/IngvarConsulting/unica"
TARGETS = ("darwin-arm64", "linux-x64", "win-x64")

# Контурный лаунчер: версия — UNICA_GLOBAL_VERSION или максимум sort -V.
# Ядро финала 0.15 резолвит движки через UNICA_ARTIFACT_CACHE и
# UNICA_RUNTIME_MANIFEST (handoff bootstrap при запуске через launch.sh);
# лаунчер обходит bootstrap и обязан передать те же адреса сам, иначе
# первый push/pull ответит bundled_tool_missing.
LAUNCHER = """#!/bin/sh
# Unica core launcher — глобальная установка контура 1C-DSH.
# Бинарники лежат в ~/.local/share/unica/runtimes (общие для всех проектов),
# скиллы и MCP-запись подключаются на уровне проекта.
# Ставится scripts/install_unica_runtime.py; версии — unica.yaml контура.
set -eu

root=${UNICA_GLOBAL_ROOT:-$HOME/.local/share/unica}
if [ ! -d "$root/runtimes" ]; then
\techo "unica: нет глобальной установки в $root (сначала prefetch)" >&2
\texit 66
fi

version=${UNICA_GLOBAL_VERSION:-}
if [ -z "$version" ]; then
\tversion=$(ls -1 "$root/runtimes/unica" 2>/dev/null | sed 's/--.*//' | sort -V | tail -1)
fi
if [ -z "$version" ]; then
\techo "unica: в $root/runtimes/unica нет ни одного артефакта" >&2
\texit 66
fi

entry=$(ls -d "$root"/runtimes/unica/"$version"--*/*/bin/*/unica 2>/dev/null | head -1)
if [ -z "$entry" ] || [ ! -x "$entry" ]; then
\techo "unica: ядро $version не найдено в $root/runtimes/unica" >&2
\texit 66
fi

: "${UNICA_RUNTIME_CACHE_DIR:=$root/runtimes}"
export UNICA_RUNTIME_CACHE_DIR
: "${UNICA_ARTIFACT_CACHE:=$root/runtimes}"
export UNICA_ARTIFACT_CACHE
: "${UNICA_RUNTIME_MANIFEST:=$root/$version/runtime-manifest.json}"
export UNICA_RUNTIME_MANIFEST
: "${UNICA_PROVIDER_STATE_DIR:=$root/provider-state}"
export UNICA_PROVIDER_STATE_DIR
exec "$entry" "$@"
"""


def fetch(url: str) -> bytes:
	print(f"закачка {url}")
	with urllib.request.urlopen(url) as response:
		return response.read()


def sha256(data: bytes) -> str:
	return hashlib.sha256(data).hexdigest()


def engine_metadata(lock: dict, version: str, workdir: Path) -> None:
	"""Метаданные движков schema 2: lock тега + хеши файлов архивов."""
	for tool in lock["tools"]:
		if tool["name"] == "unica":
			continue  # ядро — релизные core-ассеты
		for target in TARGETS:
			spec = lock["targets"][target]
			asset = tool["assets"][target]
			data = {
				"schemaVersion": 2,
				"artifact": tool["name"],
				"target": target,
				"targetTriple": spec["targetTriple"],
				"role": "engine",
				"version": tool["version"],
				"pluginVersion": version,
				"asset": {"name": asset["assetName"], "sha256": asset["sha256"]},
				"assetOrigin": {
					"repository": tool["assetRepository"],
					"tag": tool["assetTag"],
				},
			}
			url = (f"{tool['assetRepository']}/releases/download/"
			       f"{tool['assetTag']}/{asset['assetName']}")
			if tool["assetStrategy"] == "direct-release-asset":
				data["asset"]["mediaType"] = "application/octet-stream"
				data["files"] = [{
					"executable": True,
					"path": tool["binaryName"] + spec["exe"],
					"sha256": asset["sha256"],
				}]
			else:  # upstream-archive-release-asset
				blob = fetch(url)
				if sha256(blob) != asset["sha256"]:
					raise RuntimeError(f"sha256 ассета не сошёлся с lock: {url}")
				files = []
				if asset["assetName"].endswith(".tar.gz"):
					data["asset"]["mediaType"] = "application/gzip"
					with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
						for member in archive.getmembers():
							if member.isfile():
								files.append({
									"executable": bool(member.mode & 0o111),
									"path": member.name,
									"sha256": sha256(archive.extractfile(member).read()),
								})
				else:
					data["asset"]["mediaType"] = "application/zip"
					with zipfile.ZipFile(io.BytesIO(blob)) as archive:
						for info in archive.infolist():
							if not info.is_dir():
								files.append({
									"executable": bool((info.external_attr >> 16) & 0o111),
									"path": info.filename,
									"sha256": sha256(archive.read(info)),
								})
				data["files"] = sorted(files, key=lambda item: item["path"])
			out = workdir / "metadata" / f"{tool['name']}-runtime-{target}.json"
			out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
			               encoding="utf-8")


def build_bootstrap(version: str, tag: str, target: str, workdir: Path) -> Path:
	"""Bootstrap из исходников тега; готовый бинарь той же версии — повтор."""
	share = Path.home() / ".local" / "share" / "unica"
	exe = "unica-bootstrap.exe" if target == "win-x64" else "unica-bootstrap"
	existing = share / version / "bootstrap" / "bin" / target / exe
	if existing.is_file():
		out = subprocess.run([str(existing), "--version"], capture_output=True, text=True)
		if out.returncode == 0 and out.stdout.strip() == f"unica-bootstrap {version}":
			print(f"bootstrap {version} уже стоит — повтор")
			return existing
	src = workdir / "src"
	if not (src / "Cargo.toml").is_file():
		blob = fetch(f"https://codeload.github.com/IngvarConsulting/unica/tar.gz/refs/tags/{tag}")
		src.mkdir(parents=True, exist_ok=True)
		with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
			archive.extractall(src, filter="data")
		# архив распаковывается в unica-<tag>/ — поднимаем содержимое
		inner = next(src.iterdir())
		for item in inner.iterdir():
			shutil.move(str(item), src / item.name)
		inner.rmdir()
	done = subprocess.run(
		["cargo", "build", "--release", "--locked", "-p", "unica-bootstrap"],
		cwd=src, capture_output=True, text=True,
	)
	if done.returncode != 0:
		raise RuntimeError(f"cargo build unica-bootstrap: {done.stderr.strip()[-800:]}")
	return src / "target" / "release" / exe


def prune(share: Path, version: str) -> None:
	"""Снести каталоги прежних версий: лаунчеру sort -V rc выше финала."""
	for path in sorted(share.iterdir()):
		if path.is_dir() and path.name not in (version, "runtimes", "skills") \
				and not path.name.startswith("."):
			print(f"снос прежней версии: {path}")
			shutil.rmtree(path)
	runtimes = share / "runtimes"
	for artifact in sorted(runtimes.iterdir()):
		if not artifact.is_dir() or artifact.name.startswith("."):
			continue
		for entry in sorted(artifact.iterdir()):
			if entry.is_dir() and entry.name.split("--", 1)[0] != version \
					and not entry.name.startswith("."):
				# движки живут своей версией: сносятся только каталоги,
				# которых нет в манифесте текущей версии
				manifest = json.loads((share / version / "runtime-manifest.json")
				                      .read_text(encoding="utf-8"))
				spec = (manifest.get("artifacts") or {}).get(artifact.name) or {}
				keep = {f"{spec.get('version')}--"
				        f"{((spec.get('targets') or {}).get(unica_target()) or {}).get('asset', {}).get('sha256')}"}
				if entry.name not in keep:
					print(f"снос прежнего артефакта: {entry}")
					shutil.rmtree(entry)


def main(argv: list[str]) -> int:
	parser = argparse.ArgumentParser(description="Бинарники Юники из unica.yaml")
	parser.add_argument("--prune", action="store_true")
	parser.add_argument("--workdir", type=Path, default=None)
	args = parser.parse_args(argv)
	try:
		values = load_unica_versions()
		version, tag = values["unica"], values["tag"]
		if tag != f"v{version}":
			raise RuntimeError(f"unica.yaml: тег {tag} не согласован с версией {version}")
		target = unica_target()
		if not target:
			raise RuntimeError("платформа хоста не опознана")
		share = Path(os.environ.get("UNICA_GLOBAL_ROOT")
		             or Path.home() / ".local" / "share" / "unica")
		cache = Path(os.environ.get("UNICA_RUNTIME_CACHE_DIR") or share / "runtimes")

		raw = args.workdir
		temp = tempfile.TemporaryDirectory(prefix="unica-runtime-") if raw is None else None
		workdir = Path(temp.name) if temp is not None else raw
		workdir.mkdir(parents=True, exist_ok=True)
		try:
			(workdir / "metadata").mkdir(exist_ok=True)
			# ядро: релизные метаданные всех целей
			for item in TARGETS:
				blob = fetch(f"{REPOSITORY}/releases/download/{tag}/unica-runtime-{item}.json")
				(workdir / "metadata" / f"unica-runtime-{item}.json").write_bytes(blob)
			# lock тега — канон версий движков; сверка с unica.yaml
			lock = json.loads(fetch(f"{RAW}/{tag}/plugins/unica/third-party/tools.lock.json"))
			for tool in lock["tools"]:
				if tool["name"] in values and tool["version"] != values[tool["name"]]:
					raise RuntimeError(
						f"unica.yaml: {tool['name']} {values[tool['name']]} расходится "
						f"с lock тега {tag} ({tool['version']})")
			engine_metadata(lock, version, workdir)
			# манифест собирает упаковщик апстрима того же тега
			packager = workdir / "package-unica-plugin.py"
			packager.write_bytes(fetch(f"{RAW}/{tag}/scripts/ci/package-unica-plugin.py"))
			spec = importlib.util.spec_from_file_location("pup", packager)
			pup = importlib.util.module_from_spec(spec)
			spec.loader.exec_module(pup)
			# коммит тега: аннотированный тег разыменовывается, лёгкий — сам refs
			refs = subprocess.run(
				["git", "ls-remote", REPOSITORY,
				 f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
				capture_output=True, text=True, check=True,
			).stdout.splitlines()
			by_ref = {line.split()[1]: line.split()[0] for line in refs if line.strip()}
			commit = by_ref.get(f"refs/tags/{tag}^{{}}") or by_ref.get(f"refs/tags/{tag}")
			if not commit:
				raise RuntimeError(f"тег {tag} не найден в {REPOSITORY}")
			plugin = workdir / "plugin"
			plugin.mkdir(exist_ok=True)
			metadata = pup.load_runtime_metadata(workdir / "metadata", plugin_version=version)
			pup.write_release_runtime_manifest(
				plugin, metadata, plugin_version=version, release_tag=tag,
				source_commit=commit)
			# установка каталога плагина
			root = share / version
			(root / "bootstrap" / "bin" / target).mkdir(parents=True, exist_ok=True)
			shutil.copyfile(plugin / "runtime-manifest.json", root / "runtime-manifest.json")
			launch = root / "bootstrap" / "launch.sh"
			launch.write_bytes(fetch(f"{RAW}/{tag}/plugins/unica/bootstrap/launch.sh"))
			launch.chmod(0o755)
			exe = "unica-bootstrap.exe" if target == "win-x64" else "unica-bootstrap"
			binary = build_bootstrap(version, tag, target, workdir)
			destination = root / "bootstrap" / "bin" / target / exe
			if binary.resolve() != destination.resolve():
				shutil.copyfile(binary, destination)
			destination.chmod(0o755)
			# контурный лаунчер: единый корень кэша и состояния провайдеров
			launcher = Path.home() / ".local" / "bin" / "unica"
			launcher.parent.mkdir(parents=True, exist_ok=True)
			if not launcher.is_file() or launcher.read_text(encoding="utf-8") != LAUNCHER:
				launcher.write_text(LAUNCHER, encoding="utf-8")
				launcher.chmod(0o755)
				print(f"лаунчер обновлён: {launcher}")
			# прогрев артефактов в контурный кэш
			env = dict(os.environ, UNICA_RUNTIME_CACHE_DIR=str(cache))
			done = subprocess.run(
				[str(destination), "prefetch", "--plugin-root", str(root)],
				env=env, capture_output=True, text=True,
			)
			if done.returncode != 0:
				raise RuntimeError(f"prefetch: {done.stderr.strip()[-800:]}")
			summary = (done.stdout.strip() or done.stderr.strip()).splitlines()
			print(summary[-1] if summary else "prefetch: артефакты на месте")
			if args.prune:
				prune(share, version)
			# контроль факта: лаунчер обязан отдавать установленную версию,
			# иначе обновление молча не действует (sort -V: rc выше финала)
			launcher = Path(os.environ.get("UNICA_LAUNCHER")
			                or Path.home() / ".local" / "bin" / "unica")
			out = subprocess.run([str(launcher), "--help"],
			                     capture_output=True, text=True)
			first = (out.stdout or "").splitlines()[0] if out.stdout.strip() else ""
			if out.returncode != 0 or first != f"unica {version}":
				raise RuntimeError(
					f"лаунчер отдаёт «{first or 'ничего'}» вместо unica {version}: "
					"прежняя версия не снесена — повтор с --prune или UNICA_GLOBAL_VERSION")
		finally:
			if temp is not None:
				temp.cleanup()
	except (RuntimeError, OSError, subprocess.SubprocessError) as error:
		print(f"install_unica_runtime: {error}", file=sys.stderr)
		return 1
	print(f"Юника {version} установлена: {share / version}; кэш {cache}")
	print("дальше: scripts/doctor.py <проект> и scripts/install_unica_skills.py")
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
