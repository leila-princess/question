import argparse
import os
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

try:
    from neo4j import GraphDatabase
except ImportError:
    GraphDatabase = None

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.config import Config

STOP_INPUTS = {"n", "no", "q", "quit", "stop"}


def init_runtime_clients():
    if getattr(Config, "OPENAI_CLIENT", None) is None:
        api_key = getattr(Config, "OPENAI_API_KEY", None)
        base_url = getattr(Config, "OPENAI_BASE_URL", None)

        if not api_key:
            raise RuntimeError("Config.OPENAI_API_KEY 未配置，请检查 backend/config.py")

        Config.OPENAI_CLIENT = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )

    if getattr(Config, "NEO4J_DRIVER", None) is None and GraphDatabase is not None:
        neo4j_uri = getattr(Config, "NEO4J_URI", None)
        neo4j_user = getattr(Config, "NEO4J_USER", None)
        neo4j_password = getattr(Config, "NEO4J_PASSWORD", None)

        if neo4j_uri and neo4j_user is not None and neo4j_password is not None:
            try:
                Config.NEO4J_DRIVER = GraphDatabase.driver(
                    neo4j_uri,
                    auth=(neo4j_user, neo4j_password),
                )
                with Config.NEO4J_DRIVER.session() as session:
                    session.run("RETURN 1")
            except Exception as e:
                print(f"[WARN] Neo4j 初始化失败，后续将跳过图库召回：{e}")
                Config.NEO4J_DRIVER = None

    if not hasattr(Config, "TESTMETHOD_RETRIEVER"):
        Config.TESTMETHOD_RETRIEVER = None

    if not hasattr(Config, "BGE_MODEL_PATH"):
        Config.BGE_MODEL_PATH = r"./models/bge-small-zh-v1.5"

    if not getattr(Config, "OPENAI_MODEL", None):
        raise RuntimeError("Config.OPENAI_MODEL 未配置，请检查 backend/config.py")



def parse_topk(args) -> dict:
    return {
        "equipment": args.topk_equipment,
        "environment": args.topk_environment,
        "test_project": args.topk_test_project,
        "indicator": args.topk_indicator,
    }



def print_methods_preview(extraction_data: dict) -> None:
    methods = extraction_data.get("methods", []) or []
    print(f"[INFO] 当前共抽取到 {len(methods)} 个方法")

    for i, method in enumerate(methods, start=1):
        name = method.get("name", "未命名方法")
        type_hint = method.get("type", "")
        summary = (method.get("summary", "") or "").replace("\n", " ").strip()
        if len(summary) > 80:
            summary = summary[:80] + "..."

        print(f"  [{i}] {name}")
        if type_hint:
            print(f"      Type: {type_hint}")
        if summary:
            print(f"      Summary: {summary}")



def prompt_continue() -> bool:
    user_input = input(
        "\n回车继续召回并生成最终 JSON，输入 n 后回车则停止并保留当前进度："
    ).strip().lower()
    return user_input not in STOP_INPUTS



def close_runtime_clients():
    if getattr(Config, "NEO4J_DRIVER", None) is not None:
        try:
            Config.NEO4J_DRIVER.close()
        except Exception:
            pass



def _import_pipeline_extract_only():
    from GetTestMethod.pipeline import run_extract_only
    return run_extract_only



def _import_pipeline_resume():
    from GetTestMethod.pipeline import run_pipeline_resume
    return run_pipeline_resume



def _run_resume_subprocess(args, checkpoint_path: str) -> int:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "run_get_testmethod.py"),
        "resume",
        checkpoint_path,
        "--source-file-id",
        args.source_file_id,
        "--user-id",
        args.user_id,
        "--method-id-prefix",
        args.method_id_prefix,
        "--topk-equipment",
        str(args.topk_equipment),
        "--topk-environment",
        str(args.topk_environment),
        "--topk-test-project",
        str(args.topk_test_project),
        "--topk-indicator",
        str(args.topk_indicator),
    ]
    if args.output_root is not None:
        cmd.extend(["--output-root", args.output_root])

    print("[INFO] 将在新进程中继续召回与组装，避免 Paddle 与 Torch 在同进程冲突")
    completed = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return completed.returncode



def main():
    parser = argparse.ArgumentParser(description="GetTestMethod: 标准文件多方法抽取与召回")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_extract = sub.add_parser("extract", help="只做抽取并保存 checkpoint")
    p_extract.add_argument("file", help="输入文件路径")
    p_extract.add_argument("--source-file-id", default="WJ_std0001")
    p_extract.add_argument("--user-id", default="admin")
    p_extract.add_argument("--output-root", default="output_json")
    p_extract.add_argument("--max-methods", type=int, default=None)

    p_interactive = sub.add_parser("interactive", help="先抽取，回车继续；输入 n 则仅保存当前进度")
    p_interactive.add_argument("file", help="输入文件路径")
    p_interactive.add_argument("--source-file-id", default="WJ_std0001")
    p_interactive.add_argument("--user-id", default="admin")
    p_interactive.add_argument("--output-root", default="output_json")
    p_interactive.add_argument("--max-methods", type=int, default=None)
    p_interactive.add_argument("--topk-equipment", type=int, default=3)
    p_interactive.add_argument("--topk-environment", type=int, default=3)
    p_interactive.add_argument("--topk-test-project", type=int, default=3)
    p_interactive.add_argument("--topk-indicator", type=int, default=3)
    p_interactive.add_argument("--method-id-prefix", default="FF")

    p_full = sub.add_parser("full", help="抽取 + 召回 + 生成最终 json（两进程模式）")
    p_full.add_argument("file", help="输入文件路径")
    p_full.add_argument("--source-file-id", default="WJ_std0001")
    p_full.add_argument("--user-id", default="admin")
    p_full.add_argument("--output-root", default="output_json")
    p_full.add_argument("--max-methods", type=int, default=None)
    p_full.add_argument("--topk-equipment", type=int, default=3)
    p_full.add_argument("--topk-environment", type=int, default=3)
    p_full.add_argument("--topk-test-project", type=int, default=3)
    p_full.add_argument("--topk-indicator", type=int, default=3)
    p_full.add_argument("--method-id-prefix", default="FF")

    p_resume = sub.add_parser("resume", help="从 extraction_checkpoint.json 继续召回")
    p_resume.add_argument("checkpoint", help="extraction_checkpoint.json 路径")
    p_resume.add_argument("--source-file-id", default="WJ_std0001")
    p_resume.add_argument("--user-id", default="admin")
    p_resume.add_argument("--output-root", default=None)
    p_resume.add_argument("--topk-equipment", type=int, default=3)
    p_resume.add_argument("--topk-environment", type=int, default=3)
    p_resume.add_argument("--topk-test-project", type=int, default=3)
    p_resume.add_argument("--topk-indicator", type=int, default=3)
    p_resume.add_argument("--method-id-prefix", default="FF")

    args = parser.parse_args()
    init_runtime_clients()

    try:
        if args.mode == "extract":
            run_extract_only = _import_pipeline_extract_only()
            result = run_extract_only(
                file_path=os.path.abspath(args.file),
                source_file_id=args.source_file_id,
                user_id=args.user_id,
                output_root=args.output_root,
                max_methods=args.max_methods,
            )
            print("[INFO] 已完成抽取并暂停")
            print(f"[INFO] 输出目录：{result['document_dir']}")
            print(f"[INFO] checkpoint：{result['extraction_checkpoint_path']}")

        elif args.mode == "interactive":
            run_extract_only = _import_pipeline_extract_only()
            paused = run_extract_only(
                file_path=os.path.abspath(args.file),
                source_file_id=args.source_file_id,
                user_id=args.user_id,
                output_root=args.output_root,
                max_methods=args.max_methods,
            )
            print("[INFO] 已完成抽取")
            print(f"[INFO] 输出目录：{paused['document_dir']}")
            print(f"[INFO] checkpoint：{paused['extraction_checkpoint_path']}")
            print_methods_preview(paused.get("extraction_data", {}))

            if not prompt_continue():
                print("[INFO] 已停止在抽取阶段，你之后可以使用 resume 命令继续。")
                return

            code = _run_resume_subprocess(args, paused["extraction_checkpoint_path"])
            if code != 0:
                raise SystemExit(code)

        elif args.mode == "full":
            run_extract_only = _import_pipeline_extract_only()
            paused = run_extract_only(
                file_path=os.path.abspath(args.file),
                source_file_id=args.source_file_id,
                user_id=args.user_id,
                output_root=args.output_root,
                max_methods=args.max_methods,
            )
            print("[INFO] 已完成抽取，准备在新进程中继续召回与组装")
            print(f"[INFO] 输出目录：{paused['document_dir']}")
            print(f"[INFO] checkpoint：{paused['extraction_checkpoint_path']}")
            code = _run_resume_subprocess(args, paused["extraction_checkpoint_path"])
            if code != 0:
                raise SystemExit(code)

        elif args.mode == "resume":
            run_pipeline_resume = _import_pipeline_resume()
            result = run_pipeline_resume(
                extraction_checkpoint_path=os.path.abspath(args.checkpoint),
                source_file_id=args.source_file_id,
                user_id=args.user_id,
                output_root=args.output_root,
                topk_config=parse_topk(args),
                method_id_prefix=args.method_id_prefix,
            )
            print("[INFO] 已从 checkpoint 继续完成召回与组装")
            print(f"[INFO] 输出目录：{result['document_dir']}")
            print(f"[INFO] manifest：{result['manifest_path']}")
            for path in result.get("method_json_paths", []):
                print(f"  - {path}")

    finally:
        close_runtime_clients()


if __name__ == "__main__":
    main()
