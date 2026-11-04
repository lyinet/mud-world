"""W5 部署资产静态校验：compose 可解析、数据卷/健康检查齐备、镜像不 baked .env。"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def test_dockerfile_has_no_baked_env_and_runs_nonroot():
    text = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY .env" not in text
    assert "COPY . /app" not in text
    assert "USER app" in text
    assert "HEALTHCHECK" in text
    assert "deploy/healthcheck.py" in text
    assert "deploy/entrypoint.sh" in text


def test_dockerignore_excludes_secrets_and_world_state():
    lines = {line.strip() for line in (DEPLOY / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()}
    for required in (".env", ".env.*", "runs", "*.sqlite", ".git", ".venv"):
        assert required in lines, required


def test_compose_declares_volume_healthcheck_and_optional_env():
    data = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))
    service = data["services"]["world"]
    assert service["healthcheck"]["test"][0] == "CMD"
    assert any("deploy/healthcheck.py" in str(arg) for arg in service["healthcheck"]["test"])
    assert service["volumes"] == ["xkx-data:/app/runs"]
    assert data["volumes"]["xkx-data"]["name"] == "xkx-w5-data"
    env_files = service["env_file"]
    assert env_files and env_files[0]["path"] == "../.env"
    assert env_files[0]["required"] is False
    ports = service["ports"]
    assert any(str(port).endswith("8020") for port in ports)
    assert any(str(port).endswith("8030") for port in ports)


def test_entrypoint_syntax_and_executable():
    path = DEPLOY / "entrypoint.sh"
    assert os.access(path, os.X_OK), "entrypoint.sh 必须可执行（git mode 100755）"
    bash = shutil.which("bash")
    if bash:
        result = subprocess.run([bash, "-n", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_compose_config_renders_when_cli_available():
    cli = shutil.which("docker-compose")
    if not cli:
        return
    result = subprocess.run(
        [cli, "-f", str(DEPLOY / "docker-compose.yml"), "config"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert "xkx-w5-data" in result.stdout
    assert "/app/runs" in result.stdout
