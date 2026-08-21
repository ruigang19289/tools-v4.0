"""Web API for the storage node pre-check utility."""
import json
import os
import shutil
import subprocess
import sys
import uuid
import tempfile
from pathlib import Path

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_TOOL_DIR = PROJECT_ROOT / "tools" / "node_precheck"
ALLOWED_MODES = {"all", "collect-info", "disk-perf", "network-perf", "steady-perf", "dry-run"}
ALLOWED_ONLY = {"bios", "os", "hardware", "network", "disk", "disk-perf", "network-perf"}


def get_tool_dir():
    return Path(os.environ.get("TOOLS_NODE_PRECHECK_DIR", DEFAULT_TOOL_DIR)).resolve()


def error_response(message, status=400):
    return JsonResponse({"status": "error", "error": message}, status=status)


@csrf_exempt
@require_http_methods(["POST"])
def run_precheck(request):
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError as exc:
        return error_response(f"请求 JSON 格式错误: {exc}")

    config_text = str(data.get("config_text") or "")
    mode = str(data.get("mode") or "all")
    only = str(data.get("only") or "").strip()
    if not config_text.strip():
        return error_response("请提供节点预检配置")
    if len(config_text) > 200000:
        return error_response("配置文件过大")
    if mode not in ALLOWED_MODES:
        return error_response("不支持的执行模式")
    if only:
        selected = [item.strip() for item in only.split(",") if item.strip()]
        invalid = [item for item in selected if item not in ALLOWED_ONLY]
        if invalid:
            return error_response(f"检查项不支持: {', '.join(invalid)}")
    if mode == "steady-perf" and not data.get("confirmed_destructive"):
        return error_response("稳态磁盘测试属于破坏性操作，请确认后再执行")

    tool_dir = get_tool_dir()
    entrypoint = tool_dir / "node_precheck.py"
    if not entrypoint.is_file():
        return error_response(f"节点预检工具不存在: {entrypoint}", 500)

    try:
        timeout = max(10, min(int(data.get("timeout", 30)), 3600))
        parallel = max(1, min(int(data.get("parallel", 10)), 50))
    except (TypeError, ValueError):
        return error_response("超时时间或并行数格式错误")

    command = [sys.executable, str(entrypoint), "--conf", "config.conf", "--timeout", str(timeout), "--parallel", str(parallel)]
    if data.get("verbose"):
        command.append("--verbose")
    if mode == "collect-info":
        command.append("--collect-info")
    elif mode == "disk-perf":
        command.append("--disk-perf")
    elif mode == "network-perf":
        command.extend(["--only", "network-perf"])
    elif mode == "steady-perf":
        command.append("--steady-perf")
        if data.get("steady_node"):
            command.extend(["--steady-node", str(data["steady_node"])])
        if data.get("steady_disk"):
            command.extend(["--steady-disk", str(data["steady_disk"])])
    elif mode == "dry-run":
        command.append("--dry-run")
        if only:
            command.extend(["--only", only])
    elif only:
        command.extend(["--only", only])

    run_timeout = 3700 if mode == "steady-perf" else max(300, timeout * 20)
    with tempfile.TemporaryDirectory(prefix="node-precheck-") as workdir:
        config_path = Path(workdir) / "config.conf"
        config_path.write_text(config_text, encoding="utf-8")
        os.chmod(config_path, 0o600)
        try:
            completed = subprocess.run(
                command,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=run_timeout,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except subprocess.TimeoutExpired:
            return error_response(f"执行超时（上限 {run_timeout} 秒）", 504)
        except OSError as exc:
            return error_response(f"启动节点预检工具失败: {exc}", 500)

        output = completed.stdout or ""
        if completed.stderr:
            output += ("\n" if output else "") + completed.stderr
        run_id = uuid.uuid4().hex
        artifact_root = PROJECT_ROOT / "data" / "node-precheck" / run_id
        artifact_root.mkdir(parents=True, exist_ok=True)
        artifacts = []
        for path in sorted(Path(workdir).rglob("*")):
            if path.is_file() and path.name != "config.conf":
                relative = path.relative_to(workdir)
                target = artifact_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                artifacts.append({
                    "name": relative.as_posix(),
                    "url": f"/api/v1/system/node-precheck/artifact/{run_id}/{relative.as_posix()}",
                })
        return JsonResponse({
            "status": "success" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "output": output[-500000:],
            "run_id": run_id,
            "artifacts": artifacts,
        })


@require_http_methods(["GET"])
def download_artifact(request, run_id, artifact_path):
    from django.http import FileResponse, Http404

    root = (PROJECT_ROOT / "data" / "node-precheck" / run_id).resolve()
    target = (root / artifact_path).resolve()
    if root not in target.parents or not target.is_file():
        raise Http404("报告文件不存在")
    return FileResponse(open(target, "rb"), as_attachment=True, filename=target.name)
