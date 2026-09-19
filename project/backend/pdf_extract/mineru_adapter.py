"""
MinerU PDF 解析适配器 — 将 MinerU 输出转为与 parse_standard_file 兼容的结构。

安装（独立环境推荐）:
  pip install uv
  uv pip install -U "mineru[all]"

环境变量:
  MINERU_CLI          mineru 可执行文件路径（可选）
  MINERU_ENV          conda 环境名（默认 mineru_env）
  MINERU_API_URL      若设置则走 mineru-api 异步任务（适合长文档）
  MINERU_BACKEND      pipeline | hybrid-auto-engine（默认 pipeline，CPU 友好）
  MINERU_LANG         ch | en（默认 ch）
  MINERU_TIMEOUT      秒，默认 3600
  MINERU_MODEL_SOURCE modelscope | huggingface | local
  MINERU_EFFORT       medium | high（3.3+ hybrid 后端，默认 medium）
  MINERU_IMAGE_ANALYSIS  true | false（high 模式图表分析）
  MINERU_PARSE_SERIAL 1=串行解析（本机默认）；0=允许 API 并发（5090）
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CONDA_ENV_FALLBACKS = ("agent", "mineru_env")
_MINERU_PARSE_LOCK = threading.Lock()


def _conda_base() -> str:
    return (os.environ.get("CONDA_BASE") or r"C:\Users\hyx\anaconda3").strip()


def _conda_env_candidates() -> List[str]:
    explicit = (os.environ.get("MINERU_ENV") or "").strip()
    names: List[str] = []
    if explicit:
        names.append(explicit)
    for name in _CONDA_ENV_FALLBACKS:
        if name not in names:
            names.append(name)
    return names


def _mineru_exe_in_conda_env(env_name: str) -> Optional[str]:
    base = _conda_base()
    if os.name == "nt":
        exe = os.path.join(base, "envs", env_name, "Scripts", "mineru.exe")
    else:
        exe = os.path.join(base, "envs", env_name, "bin", "mineru")
    return exe if os.path.isfile(exe) else None


def _resolve_mineru_env() -> Optional[str]:
    for env_name in _conda_env_candidates():
        if _mineru_exe_in_conda_env(env_name):
            return env_name
    return None


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _find_mineru_cli() -> Optional[str]:
    explicit = (os.environ.get("MINERU_CLI") or "").strip()
    if explicit and os.path.isfile(explicit):
        return explicit
    found = shutil.which("mineru")
    if found:
        return found
    for env_name in _conda_env_candidates():
        exe = _mineru_exe_in_conda_env(env_name)
        if exe:
            return exe
    return None


def _active_mineru_env() -> str:
    env = (os.environ.get("MINERU_ENV") or "").strip()
    if env and _mineru_exe_in_conda_env(env):
        return env
    resolved = _resolve_mineru_env()
    return resolved or env or "mineru_env"


def _conda_mineru_cmd(args: List[str]) -> List[str]:
    env_name = _active_mineru_env()
    conda_base = _conda_base()
    if os.name == "nt":
        exe = os.path.join(conda_base, "Scripts", "conda.exe")
        if os.path.isfile(exe):
            return [exe, "run", "-n", env_name, "mineru", *args]
    return ["conda", "run", "-n", env_name, "mineru", *args]


def mineru_availability_detail() -> Dict[str, Any]:
    cli = _find_mineru_cli()
    api = (os.environ.get("MINERU_API_URL") or "").strip()
    conda_env = _active_mineru_env() if cli and not shutil.which("mineru") else os.environ.get("MINERU_ENV", _active_mineru_env())
    detail: Dict[str, Any] = {
        "cli_path": cli,
        "api_url": api or None,
        "conda_env": conda_env,
        "available": bool(cli or api),
        "mode": "api" if api else ("cli" if cli else "none"),
    }
    if cli:
        try:
            proc = subprocess.run(
                [cli, "--version"],
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            detail["version"] = (proc.stdout or proc.stderr or "").strip()[:200]
        except Exception as e:
            detail["version_error"] = str(e)
    return detail


def is_mineru_available() -> bool:
    return mineru_availability_detail()["available"]


def _fetch_mineru_api_health(base_url: str, *, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """mineru-api 的 /health 会返回 version 字段；普通 FastAPI 占位服务没有。"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return None
    try:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(f"{base}/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            return payload if isinstance(payload, dict) else None
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None


def _probe_mineru_api_url(base_url: str, *, timeout: float = 3.0) -> bool:
    health = _fetch_mineru_api_health(base_url, timeout=timeout)
    if not health:
        return False
    version = health.get("version")
    return isinstance(version, str) and bool(version.strip())


def resolve_mineru_api_url(*, force_probe: bool = False) -> str:
    """
    解析可用的 mineru-api 地址。
    若 .env 指向错误端口（如 8000 被 VS Code 占用），自动探测 8001 等。
    """
    explicit = (os.environ.get("MINERU_API_URL") or "").strip().rstrip("/")
    candidates: List[str] = []
    if explicit:
        candidates.append(explicit)
    preferred_port = (os.environ.get("MINERU_API_PORT") or "8001").strip()
    # 8000 常被 VS Code / 其他 FastAPI 占用且无 /tasks，放最后且需通过 mineru health 探测
    for port in (preferred_port, "8001", "8002", "8003", "8787", "9000", "8000"):
        url = f"http://127.0.0.1:{port}"
        if url not in candidates:
            candidates.append(url)

    if not force_probe and explicit and _probe_mineru_api_url(explicit):
        return explicit

    for url in candidates:
        if _probe_mineru_api_url(url):
            if url != explicit:
                logger.warning(
                    "[MinerU] MINERU_API_URL=%s unreachable; using %s (restart Flask after fixing .env)",
                    explicit or "(unset)",
                    url,
                )
            os.environ["MINERU_API_URL"] = url
            return url

    return explicit


def ensure_mineru_api_reachable() -> str:
    url = resolve_mineru_api_url(force_probe=True)
    if not url:
        raise RuntimeError(
            "未配置 MINERU_API_URL。请先运行 EFHR/scripts/start_mineru_api.ps1，"
            "并在 .env 设置 MINERU_API_URL=http://127.0.0.1:8001"
        )
    if not _probe_mineru_api_url(url):
        raise RuntimeError(
            f"无法连接 MinerU API ({url})。请先运行 scripts/mineru/start_mineru_local.ps1 并保持窗口常开；"
            f"确认 .env 中 MINERU_API_URL 端口与脚本一致（推荐 8001）。"
            f"若 8000 被 VS Code 等占用，勿将其当作 MinerU。"
        )
    health = _fetch_mineru_api_health(url) or {}
    if str(health.get("status") or "").lower() == "unhealthy":
        err = health.get("error") or "task manager not ready"
        raise RuntimeError(
            f"MinerU API ({url}) 未就绪：{err}。请等待 mineru-api 窗口完成模型加载后重试。"
        )
    return url


def _mineru_subprocess_env() -> dict:
    env = os.environ.copy()
    model_src = (os.environ.get("MINERU_MODEL_SOURCE") or "").strip()
    if model_src:
        env["MINERU_MODEL_SOURCE"] = model_src
    no_proxy = "127.0.0.1,localhost,::1"
    env["NO_PROXY"] = no_proxy
    env["no_proxy"] = no_proxy
    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        env.pop(key, None)
    api_url = resolve_mineru_api_url()
    if api_url:
        env["MINERU_API_URL"] = api_url
    return env


def _httpx_timeout(*, read: float = 180.0) -> Any:
    import httpx

    return httpx.Timeout(connect=30.0, read=read, write=300.0, pool=60.0)


def _httpx_client(*, read: float = 180.0) -> Any:
    import httpx

    return httpx.Client(
        timeout=_httpx_timeout(read=read),
        follow_redirects=True,
        trust_env=False,
        limits=httpx.Limits(max_keepalive_connections=0, max_connections=20),
    )


def _http_request_with_retry(
    method: str,
    url: str,
    *,
    timeout_read: float = 120.0,
    max_retries: int = 180,
    sleep_sec: float = 2.0,
    **kwargs: Any,
) -> Any:
    """API 推理繁忙时连接可能被断开，需重试而非直接失败。"""
    import httpx

    retryable = (
        httpx.ConnectError,
        httpx.ReadTimeout,
        httpx.ConnectTimeout,
        httpx.RemoteProtocolError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.NetworkError,
        httpx.PoolTimeout,
    )
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_retries + 1):
        try:
            with _httpx_client(read=timeout_read) as client:
                return client.request(method, url, **kwargs)
        except retryable as exc:
            last_exc = exc
            logger.warning("[MinerU-HTTP] %s retry #%s %s: %s", method, attempt, url, exc)
            time.sleep(sleep_sec)
    raise RuntimeError(f"MinerU API 请求失败（{method} {url}）: {last_exc}") from last_exc


def _run_mineru_via_http_api(
    pdf_path: str,
    output_dir: str,
    *,
    timeout: int,
    method: Optional[str] = None,
) -> None:
    """
    直接调用 mineru-api（绕过 mineru.exe 子进程）。
    trust_env=False + 轮询重试，避免 API 推理繁忙时连接断开。
    """
    backend = (os.environ.get("MINERU_BACKEND") or "pipeline").strip()
    lang = (os.environ.get("MINERU_LANG") or "ch").strip()
    parse_method = (method or detect_pdf_parse_method(pdf_path)).strip().lower()
    if parse_method not in ("txt", "ocr", "auto"):
        parse_method = "auto"

    base_url = ensure_mineru_api_reachable().rstrip("/")
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    pdf = Path(pdf_path)

    from mineru.cli.api_client import UploadAsset, safe_extract_zip, submit_parse_task_sync
    import click

    form_data = _build_mineru_form_data(
        lang=lang,
        backend=backend,
        parse_method=parse_method,
    )

    logger.info(
        "[MinerU-HTTP] submit pdf=%s api=%s method=%s backend=%s effort=%s",
        pdf.name, base_url, parse_method, backend, _mineru_effort() or "(default)",
    )

    try:
        submit = submit_parse_task_sync(
            base_url,
            [UploadAsset(path=pdf, upload_name=pdf.name)],
            form_data,
        )
    except click.ClickException as exc:
        msg = str(exc)
        if "404" in msg or "Not Found" in msg:
            raise RuntimeError(
                f"MinerU API 提交失败（404）：{base_url} 不是 mineru-api（常见原因：8000 被 VS Code 占用）。"
                f"请运行 scripts/mineru/start_mineru_local.ps1，并在 .env 设置 MINERU_API_URL=http://127.0.0.1:8001"
            ) from exc
        raise RuntimeError(f"MinerU API 提交失败：{msg}") from exc

    task_id = submit.task_id
    status_url = submit.status_url
    result_url = submit.result_url
    if not task_id:
        raise RuntimeError("MinerU API invalid task payload (missing task_id)")

    logger.info("[MinerU-HTTP] task submitted id=%s", task_id)

    deadline = time.time() + timeout
    last_status = "unknown"
    while time.time() < deadline:
        sr = _http_request_with_retry("GET", status_url, timeout_read=120.0, max_retries=5, sleep_sec=1.0)

        if sr.status_code != 200:
            raise RuntimeError(f"MinerU status poll failed ({sr.status_code}): {sr.text[:500]}")

        body = sr.json()
        last_status = str(body.get("status") or "")
        if last_status in ("pending", "processing"):
            time.sleep(1.0)
            continue
        if last_status == "failed":
            raise RuntimeError(_mineru_task_error_message(body))
        if last_status == "completed":
            break
        raise RuntimeError(f"MinerU unknown task status: {last_status}")
    else:
        raise RuntimeError(f"MinerU task timeout after {timeout}s (last_status={last_status})")

    zip_tmp = out_path / f"_mineru_result_{task_id}.zip"
    try:
        dl = _http_request_with_retry(
            "GET",
            result_url,
            timeout_read=max(600.0, float(timeout)),
            max_retries=10,
            sleep_sec=3.0,
        )
        if dl.status_code != 200:
            raise RuntimeError(f"MinerU result download failed ({dl.status_code})")
        zip_tmp.write_bytes(dl.content)
        safe_extract_zip(zip_tmp, out_path)
    finally:
        zip_tmp.unlink(missing_ok=True)

    logger.info("[MinerU-HTTP] done task_id=%s out=%s", task_id, out_path)


def _run_subprocess(cmd: List[str], *, timeout: int, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    env = _mineru_subprocess_env()
    logger.info("[MinerU] run: %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=cwd,
        env=env,
        encoding="utf-8",
        errors="replace",
    )


def _env_bool(key: str, default: bool) -> bool:
    raw = (os.environ.get(key) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _mineru_effort() -> Optional[str]:
    """hybrid 后端解析强度：medium（快）| high（准，含 image analysis）。"""
    raw = (os.environ.get("MINERU_EFFORT") or "").strip().lower()
    if raw in ("medium", "high"):
        return raw
    return None


def _mineru_image_analysis() -> bool:
    explicit = (os.environ.get("MINERU_IMAGE_ANALYSIS") or "").strip().lower()
    if explicit:
        return explicit in ("1", "true", "yes", "on")
    effort = _mineru_effort()
    if effort == "high":
        return True
    return False


def _parse_serial_enabled() -> bool:
    return _env_bool("MINERU_PARSE_SERIAL", True)


def _build_mineru_form_data(
    *,
    lang: str,
    backend: str,
    parse_method: str,
) -> Dict[str, Any]:
    from mineru.cli.client import build_request_form_data

    kwargs: Dict[str, Any] = {
        "lang": lang,
        "backend": backend,
        "method": parse_method,
        "formula_enable": _env_bool("MINERU_FORMULA", True),
        "table_enable": _env_bool("MINERU_TABLE", True),
        "server_url": None,
        "start_page_id": 0,
        "end_page_id": None,
        "image_analysis": _mineru_image_analysis(),
    }
    effort = _mineru_effort()
    sig = inspect.signature(build_request_form_data)
    if effort and "effort" in sig.parameters:
        kwargs["effort"] = effort
    return build_request_form_data(**kwargs)


def _mineru_cli_extra_args() -> List[str]:
    """3.3+ 追加 --effort / --image-analysis 等 CLI 参数。"""
    extra: List[str] = []
    effort = _mineru_effort()
    if effort:
        extra.extend(["--effort", effort])
    if not _mineru_image_analysis():
        extra.extend(["--image-analysis", "false"])
    return extra


def _mineru_task_error_message(body: Dict[str, Any]) -> str:
    err = str(body.get("error") or body.get("message") or "").strip()
    if err:
        if "PDFium" in err or "Data format error" in err:
            return f"PDF 文件损坏或无效，MinerU 无法打开（{err}）"
        return f"MinerU 解析失败：{err}"
    return f"MinerU 解析失败：{json.dumps(body, ensure_ascii=False)[:400]}"


def validate_pdf_for_mineru(pdf_path: str) -> None:
    """上传/解析前检查 PDF 是否可读，避免 MinerU 返回难懂的 PDFium 错误。"""
    p = Path(pdf_path)
    if not p.is_file():
        raise FileNotFoundError(f"PDF 不存在: {pdf_path}")
    size = p.stat().st_size
    if size < 1024:
        raise RuntimeError(
            f"PDF 文件过小（{size} 字节），可能不是有效文件。"
            f"请确认上传的是完整 PDF（当前文件: {p.name}）"
        )
    head = b""
    try:
        with open(pdf_path, "rb") as f:
            head = f.read(8)
    except OSError as e:
        raise RuntimeError(f"无法读取 PDF（{p.name}）：{e}") from e
    if not head.startswith(b"%PDF"):
        raise RuntimeError(
            f"文件不是有效 PDF（缺少 %PDF 头，{p.name}，{size} 字节）。"
            "请确认上传的是完整 PDF 而非重命名后的其他格式。"
        )
    try:
        import fitz
    except ImportError:
        logger.warning(
            "[mineru] PyMuPDF 未安装，跳过本地页数校验（pip install pymupdf）；"
            "将直接提交 MinerU API 解析: %s",
            p.name,
        )
        return
    try:
        doc = fitz.open(pdf_path)
        try:
            if len(doc) < 1:
                raise RuntimeError(
                    f"PDF 无有效页面，文件可能已损坏（{p.name}，{size} 字节）"
                )
        finally:
            doc.close()
    except RuntimeError:
        raise
    except Exception as e:
        err = str(e).strip()
        if "no module named" in err.lower() or "fitz" in err.lower():
            logger.warning("[mineru] PyMuPDF 打开失败，跳过本地校验: %s", err)
            return
        raise RuntimeError(f"无法读取 PDF（{p.name}）：{e}") from e


def detect_pdf_parse_method(pdf_path: str) -> str:
    """
    有文字层的 PDF 用 txt 比 auto/ocr 快很多（国标 PDF 多为可选中文字的电子版）。
    可通过 MINERU_METHOD=txt|ocr|auto 强制指定。
    """
    explicit = (os.environ.get("MINERU_METHOD") or "").strip().lower()
    if explicit in ("txt", "ocr", "auto"):
        return explicit
    try:
        import fitz

        doc = fitz.open(pdf_path)
        sample = ""
        for i in range(min(3, len(doc))):
            sample += doc[i].get_text()
        doc.close()
        if len(sample.strip()) > 400:
            return "txt"
    except Exception:
        pass
    return "auto"


def _run_mineru_cli(
    pdf_path: str,
    output_dir: str,
    *,
    timeout: int,
    method: Optional[str] = None,
) -> None:
    api_url = resolve_mineru_api_url(force_probe=True)
    if api_url:
        _run_mineru_via_http_api(
            pdf_path, output_dir, timeout=timeout, method=method,
        )
        return

    backend = (os.environ.get("MINERU_BACKEND") or "pipeline").strip()
    lang = (os.environ.get("MINERU_LANG") or "ch").strip()
    parse_method = (method or detect_pdf_parse_method(pdf_path)).strip().lower()
    if parse_method not in ("txt", "ocr", "auto"):
        parse_method = "auto"

    args = [
        "-p", pdf_path,
        "-o", output_dir,
        "-b", backend,
        "-l", lang,
        "-m", parse_method,
        "-f", "true" if _env_bool("MINERU_FORMULA", True) else "false",
        "-t", "true" if _env_bool("MINERU_TABLE", True) else "false",
        *_mineru_cli_extra_args(),
    ]

    cli = _find_mineru_cli()
    if cli:
        cmd = [cli, *args]
    else:
        cmd = _conda_mineru_cmd(args)

    proc = _run_subprocess(cmd, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1200:]
        raise RuntimeError(f"MinerU CLI 失败 (code={proc.returncode}): {tail}")


def _discover_mineru_artifacts(output_dir: Path, pdf_stem: str) -> Dict[str, Optional[Path]]:
    """在 MinerU 输出目录中查找 md / content_list / middle_json。"""
    candidates_md: List[Path] = []
    candidates_cl: List[Path] = []
    candidates_mj: List[Path] = []
    images_dir: Optional[Path] = None

    for p in output_dir.rglob("*"):
        if not p.is_file():
            if p.is_dir() and p.name.lower() in ("images", "figures", "img"):
                images_dir = images_dir or p
            continue
        low = p.name.lower()
        if low.endswith(".md") and pdf_stem.lower() in low:
            candidates_md.append(p)
        elif low.endswith("_content_list.json") or low == "content_list.json":
            candidates_cl.append(p)
        elif low.endswith("_middle.json") or low == "middle.json":
            candidates_mj.append(p)

    def _pick(paths: List[Path]) -> Optional[Path]:
        if not paths:
            return None
        paths.sort(key=lambda x: len(str(x)))
        return paths[0]

    return {
        "markdown": _pick(candidates_md) or _pick(list(output_dir.rglob("*.md"))),
        "content_list": _pick(candidates_cl),
        "middle_json": _pick(candidates_mj),
        "images_dir": images_dir,
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _blocks_from_content_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("content_list", "blocks", "pdf_info", "pages"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
    return []


def _block_text(block: Dict[str, Any]) -> str:
    for key in ("text", "content", "md", "markdown"):
        v = block.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _block_table_html(block: Dict[str, Any]) -> str:
    for key in ("html", "table_body", "table_html", "content"):
        v = block.get(key)
        if isinstance(v, str) and ("<table" in v.lower() or "|" in v):
            if "<table" in v.lower():
                return v
    return ""


def _page_idx(block: Dict[str, Any], default: int = 0) -> int:
    for key in ("page_idx", "page_index", "page_number", "page"):
        v = block.get(key)
        if v is not None:
            try:
                return int(v) + (0 if key != "page_number" else -1 if int(v) >= 1 else 0)
            except (TypeError, ValueError):
                pass
    return default


def _markdown_to_plain(md: str) -> str:
    s = md or ""
    s = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"^#+\s*", "", s, flags=re.M)
    s = re.sub(r"\$\$[^$]+\$\$", " [公式] ", s)
    s = re.sub(r"\$[^$]+\$", " [公式] ", s)
    return s.strip()


def _build_pages_from_blocks(
    blocks: List[Dict[str, Any]],
    images_dir: Optional[Path],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """由 content_list 构建 pages / all_tables / full_text。"""
    by_page: Dict[int, Dict[str, Any]] = {}
    all_tables: List[Dict[str, Any]] = []

    for block in blocks:
        btype = str(block.get("type") or block.get("block_type") or "text").lower()
        page_no = max(1, _page_idx(block, 0) + 1)
        page = by_page.setdefault(page_no, {
            "page_number": page_no,
            "text": "",
            "tables_html": [],
            "figures": [],
        })

        if btype in ("table",):
            th = _block_table_html(block)
            if th:
                page["tables_html"].append(th)
                all_tables.append({"page": page_no, "html": th})
        elif btype in ("image", "figure"):
            img = block.get("img_path") or block.get("image_path") or block.get("path") or ""
            fig: Dict[str, Any] = {"page": page_no, "ocr_text": _block_text(block)}
            if img and images_dir:
                ip = Path(str(img))
                if not ip.is_absolute():
                    ip = images_dir / ip.name
                if ip.is_file():
                    fig["path"] = str(ip)
                    fig["filename"] = ip.name
            page["figures"].append(fig)
        else:
            txt = _block_text(block)
            if txt:
                page["text"] = (page["text"] + "\n" + txt).strip()

    if not by_page and blocks:
        joined = "\n".join(_block_text(b) for b in blocks if _block_text(b))
        by_page[1] = {"page_number": 1, "text": joined, "tables_html": [], "figures": []}

    pages = [by_page[k] for k in sorted(by_page.keys())]
    full_text = "\n\n".join(
        f"===== Page {p['page_number']} =====\n{p['text']}"
        for p in pages
        if (p.get("text") or "").strip()
    )
    return pages, all_tables, full_text


def parse_pdf_with_mineru(
    file_path: str,
    output_dir: Optional[str] = None,
    *,
    timeout: Optional[int] = None,
    method: Optional[str] = None,
) -> Dict[str, Any]:
    """
    使用 MinerU 解析 PDF，返回与 parse_standard_file 兼容的字典（不含 sections，由调用方 split）。
    """
    if not is_mineru_available():
        raise RuntimeError(
            "MinerU 未安装或未配置。请安装 mineru 或设置 MINERU_API_URL。"
            f" detail={mineru_availability_detail()}"
        )

    pdf_path = Path(file_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(file_path)

    validate_pdf_for_mineru(str(pdf_path))

    timeout = timeout or _env_int("MINERU_TIMEOUT", 3600)
    stem = pdf_path.stem
    base_out = Path(output_dir or os.path.join(os.getcwd(), "mineru_output", f"{stem}_{uuid.uuid4().hex[:8]}"))
    if not base_out.is_absolute():
        from pdf_extract.mineru_wjgl_service import project_root

        base_out = (project_root() / base_out).resolve()
    else:
        base_out = base_out.resolve()
    base_out.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    chosen_method = method or detect_pdf_parse_method(str(pdf_path))
    logger.info(
        "[MinerU] method=%s backend=%s effort=%s api=%s serial=%s",
        chosen_method,
        os.environ.get("MINERU_BACKEND", "pipeline"),
        _mineru_effort() or "(default)",
        resolve_mineru_api_url() or "(cli embedded)",
        _parse_serial_enabled(),
    )
    api_url = resolve_mineru_api_url(force_probe=True)
    if api_url:
        ensure_mineru_api_reachable()

    def _do_parse() -> None:
        _run_mineru_cli(str(pdf_path), str(base_out), timeout=timeout, method=chosen_method)

    if _parse_serial_enabled():
        lock_wait_t0 = time.perf_counter()
        _MINERU_PARSE_LOCK.acquire()
        lock_wait = time.perf_counter() - lock_wait_t0
        try:
            if lock_wait > 0.5:
                logger.info("[MinerU] acquired parse lock after %.1fs for %s", lock_wait, pdf_path.name)
            _do_parse()
        finally:
            _MINERU_PARSE_LOCK.release()
    else:
        _do_parse()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    artifacts = _discover_mineru_artifacts(base_out, stem)
    blocks: List[Dict[str, Any]] = []

    if artifacts.get("content_list") and artifacts["content_list"].is_file():
        blocks = _blocks_from_content_list(_load_json(artifacts["content_list"]))
    elif artifacts.get("middle_json") and artifacts["middle_json"].is_file():
        blocks = _blocks_from_content_list(_load_json(artifacts["middle_json"]))

    pages, all_tables, full_text = _build_pages_from_blocks(blocks, artifacts.get("images_dir"))

    if artifacts.get("markdown") and artifacts["markdown"].is_file():
        md = artifacts["markdown"].read_text(encoding="utf-8", errors="replace")
        if len(md.strip()) > len(full_text.strip()):
            plain = _markdown_to_plain(md)
            full_text = plain
            if not pages:
                pages = [{"page_number": 1, "text": plain, "tables_html": [], "figures": []}]

    if not full_text.strip():
        raise RuntimeError(f"MinerU 解析结果为空，输出目录: {base_out}")

    table_count = len(all_tables) + sum(len(p.get("tables_html") or []) for p in pages)
    figure_count = sum(len(p.get("figures") or []) for p in pages)

    return {
        "file_path": str(pdf_path),
        "file_name": pdf_path.name,
        "file_ext": ".pdf",
        "parse_mode": "pdf_mineru",
        "ocr_used": True,
        "full_text": full_text,
        "pages": pages,
        "all_tables": all_tables,
        "page_table_map": {},
        "mineru_output_dir": str(base_out),
        "mineru_artifacts": {k: str(v) if v else None for k, v in artifacts.items()},
        "mineru_elapsed_ms": elapsed_ms,
        "stats": {
            "page_count": len(pages),
            "text_length": len(full_text),
            "table_count": table_count,
            "figure_count": figure_count,
        },
    }


def finalize_mineru_parse(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """补全 sections / section_catalogue（与 parser.parse_standard_file 后半段一致）。"""
    from blueprints.FLGL.GetTestMethod.parser import (
        split_standard_sections,
        build_section_catalogue,
        _build_page_table_map,
    )

    full_text = (parsed.get("full_text") or "").strip()
    sections = split_standard_sections(full_text)
    pages = list(parsed.get("pages") or [])
    page_table_map = _build_page_table_map(pages)
    stats = dict(parsed.get("stats") or {})
    stats.update({
        "section_count": len(sections),
        "text_length": len(full_text),
        "page_count": len(pages),
        "table_count": stats.get("table_count") or sum(len(v) for v in page_table_map.values()),
    })
    parsed = dict(parsed)
    parsed["sections"] = sections
    parsed["section_catalogue"] = build_section_catalogue(sections)
    parsed["page_table_map"] = {str(k): v for k, v in page_table_map.items()}
    parsed["stats"] = stats
    return parsed
