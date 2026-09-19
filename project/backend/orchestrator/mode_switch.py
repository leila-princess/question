import json
import shutil
import subprocess
from pathlib import Path


def load_config():
    cfg_path = Path("config/execution_modes.json")
    if not cfg_path.exists():
        return {"mode": "agent", "ohomopencode_enabled": False}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"mode": "agent", "ohomopencode_enabled": False}


def ohomopencode_available():
    return shutil.which("ohomopencode") is not None


def run_cmd(cmd, cwd=None):
    try:
        res = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        return {
            "success": res.returncode == 0,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "code": res.returncode,
        }
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": str(e), "code": -1}


def run_opencode_build():
    if not ohomopencode_available():
        return {"success": False, "reason": "ohomopencode not installed or not in PATH."}
    return run_cmd(["ohomopencode", "build"])


def run_opencode_plan():
    if not ohomopencode_available():
        return {"success": False, "reason": "ohomopencode not installed or not in PATH."}
    return run_cmd(["ohomopencode", "plan"])


def select_mode():
    cfg = load_config()
    mode = cfg.get("mode", "agent")
    if mode == "agent":
        return {"mode": "agent"}
    if mode in ("opencode_build", "opencode_plan"):
        return {"mode": mode, "ohomopencode_enabled": cfg.get("ohomopencode_enabled", False)}
    return {"mode": "agent"}


def execute_opencode_path():
    cfg = load_config()
    # Explicit switch to specific mode if requested
    if cfg.get("mode") == "opencode_build":
        return run_opencode_build()
    if cfg.get("mode") == "opencode_plan":
        return run_opencode_plan()

    # If ohomopencode is enabled and available, run a pipeline of build + plan
    if cfg.get("ohomopencode_enabled") and ohomopencode_available():
        build_res = run_opencode_build()
        plan_res = run_opencode_plan()
        return {"mode": "opencode_pipeline", "build": build_res, "plan": plan_res}
    return {"mode": "agent", "reason": "opencode not available or not enabled"}
