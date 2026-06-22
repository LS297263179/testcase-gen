"""测试用例生成器 - 命令行入口"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.generator import generate_testcases
from core.llm_client import build_client, load_config
from core.output import to_excel, to_markdown
from core.reader import read_requirement
from core.reviewer import review_testcases

console = Console()

logging.basicConfig(
    filename="testcase-gen.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger(__name__)


def print_summary(testcases: list[dict]):
    table = Table(title="生成的测试用例概览", show_lines=True)
    table.add_column("编号", style="cyan", width=10)
    table.add_column("模块", style="magenta", width=12)
    table.add_column("标题", style="white", width=35)
    table.add_column("优先级", style="bold", width=8)
    table.add_column("类型", width=10)

    priority_style = {"P0": "red", "P1": "yellow", "P2": "green", "P3": "blue"}

    for tc in testcases:
        p = tc.get("priority", "")
        table.add_row(
            tc.get("id", ""),
            tc.get("module", ""),
            tc.get("title", ""),
            f"[{priority_style.get(p, 'white')}]{p}[/]",
            tc.get("type", ""),
        )
    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="AI 测试用例生成器")
    parser.add_argument("source", nargs="?", default=None,
                        help="需求文档路径 (.md/.txt/.xlsx)，不指定则手动输入")
    parser.add_argument("-c", "--config", default="config.yaml",
                        help="配置文件路径 (默认: config.yaml)")
    parser.add_argument("-o", "--output", default=None,
                        help="输出目录 (覆盖配置文件)")
    parser.add_argument("-f", "--format", choices=["excel", "markdown", "all"],
                        default=None, help="输出格式 (覆盖配置文件)")
    parser.add_argument("-r", "--review", action="store_true",
                        help="启用 AI 评审用例")
    args = parser.parse_args()

    config = load_config(args.config)
    output_dir = args.output or config["output"]["dir"]
    output_format = args.format or config["output"]["format"]

    gen_model = config["generate"]["model"]
    logger.info("启动测试用例生成器")
    console.print(Panel.fit(
        f"[bold blue]AI 测试用例生成器[/]\n"
        f"生成模型: [green]{gen_model}[/]",
        border_style="blue",
    ))

    # 读取需求
    console.print("\n[bold]1. 读取需求文档...[/]")
    try:
        requirement = read_requirement(args.source)
    except FileNotFoundError as e:
        console.print(f"[red]错误: {e}[/]")
        logger.error(f"文件不存在: {e}")
        sys.exit(1)

    console.print(f"[green]  已读取 {len(requirement)} 字符的需求内容[/]")
    logger.info(f"读取需求文档: {len(requirement)} 字符")

    # 生成用例
    console.print("\n[bold]2. 调用 LLM 生成测试用例...[/]")
    gen_client = build_client(config["generate"])
    try:
        testcases = generate_testcases(
            gen_client, requirement,
            default_priority=config["testcase"]["default_priority"],
            case_types=config["testcase"]["case_types"],
            max_testcases=config["testcase"].get("max_testcases", 100),
        )
    except Exception as e:
        console.print(f"[red]LLM 调用失败: {e}[/]")
        logger.error(f"LLM 调用失败: {e}")
        sys.exit(1)

    console.print(f"[green]  成功生成 {len(testcases)} 条测试用例[/]")
    logger.info(f"生成 {len(testcases)} 条测试用例")

    print_summary(testcases)

    # 导出
    console.print("\n[bold]3. 导出测试用例...[/]")
    outputs = []
    if output_format in ("excel", "all"):
        path = to_excel(testcases, output_dir)
        outputs.append(f"  Excel:    [link={path}]{path}[/]")
        logger.info(f"导出 Excel: {path}")
    if output_format in ("markdown", "all"):
        path = to_markdown(testcases, output_dir)
        outputs.append(f"  Markdown: [link={path}]{path}[/]")
        logger.info(f"导出 Markdown: {path}")

    for o in outputs:
        console.print(o)

    # 评审
    if args.review:
        review_cfg = config.get("review", {})
        if review_cfg.get("enabled", False) and review_cfg.get("base_url") and review_cfg.get("api_key"):
            review_model = review_cfg.get("model", gen_model)
            review_client = build_client(review_cfg)
        else:
            review_model = gen_model
            review_client = gen_client

        console.print(f"\n[bold]4. AI 评审测试用例（模型: {review_model}）...[/]")
        try:
            review_result = review_testcases(review_client, requirement, testcases)
            console.print(Panel(review_result, title="评审报告", border_style="yellow"))

            report_path = Path(output_dir) / "review_report.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(f"# 测试用例评审报告\n\n{review_result}")
            console.print(f"  评审报告: [link={report_path}]{report_path}[/]")
            logger.info(f"评审报告已保存: {report_path}")
        except Exception as e:
            console.print(f"[yellow]  评审失败: {e}[/]")
            logger.error(f"评审失败: {e}")

    console.print("\n[bold green]完成![/]")
    logger.info("完成")


if __name__ == "__main__":
    main()
