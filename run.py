#!/usr/bin/env python
"""
原神观测枢爬虫 - 统一入口
交互式选择需要爬取的模块，串行执行。
"""

import json
import sys
import time
from datetime import datetime
from typing import Dict, List, Tuple

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live
from rich.prompt import Confirm
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.text import Text

# 导入所有模块
from src.modules import (
    animal,
    artifact,
    book,
    character,
    character_anecdote,
    commission,
    domain,
    enemy,
    food,
    furnishing,
    item,
    namecard,
    npc,
    organization,
    outfit,
    phantom_theater,
    task,
    weapon,
)

console = Console()

# ========== 模块注册表 ==========
# 格式: (显示名称, 模块对象, 模块标识)
MODULES: List[Tuple[str, object, str]] = [
    ("角色", character, "character"),
    ("任务", task, "task"),
    ("角色逸闻", character_anecdote, "character_anecdote"),
    ("武器", weapon, "weapon"),
    ("圣遗物", artifact, "artifact"),
    ("组织", organization, "organization"),
    ("敌人", enemy, "enemy"),
    ("食物", food, "food"),
    ("背包物品", item, "item"),
    ("动物", animal, "animal"),
    ("书籍", book, "book"),
    ("冒险家协会", commission, "commission"),
    ("NPC", npc, "npc"),
    ("秘境", domain, "domain"),
    ("洞天", furnishing, "furnishing"),
    ("幻想真境剧诗", phantom_theater, "phantom_theater"),
    ("名片", namecard, "namecard"),
    ("装扮", outfit, "outfit"),
]


def run_module(module_name: str, module_obj, module_key: str) -> Dict:
    """
    运行单个模块，返回执行结果。
    """
    start_time = time.time()
    result = {
        "key": module_key,
        "name": module_name,
        "status": "success",
        "error": None,
        "duration": 0,
    }

    try:
        if hasattr(module_obj, "run"):
            module_obj.run()
        else:
            result["status"] = "failed"
            result["error"] = "模块没有 run 函数"
    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)

    result["duration"] = time.time() - start_time
    return result


def show_selection_menu() -> List[str]:
    """
    显示交互式选择菜单，返回用户选中的模块 key 列表。
    使用 rich 的 Console + 手动交互（因为 rich 没有内置的多选菜单组件）。
    这里使用 prompt 方式实现简单选择。
    """
    console.clear()
    console.print(
        Panel.fit(
            "[bold cyan]原神观测枢爬虫 - 模块选择[/bold cyan]",
            border_style="cyan",
        )
    )

    # 显示所有模块
    table = Table(show_header=True, header_style="bold magenta", box=None)
    table.add_column("#", style="dim", width=4)
    table.add_column("模块名称", style="white")
    table.add_column("状态", style="green")

    for idx, (name, _, key) in enumerate(MODULES, 1):
        table.add_row(str(idx), name, "✅ 可用")

    console.print(table)
    console.print("\n[dim]输入要爬取的模块编号，多个用逗号分隔 (如: 1,3,5)[/dim]")
    console.print("[dim]输入 [bold]all[/bold] 爬取全部模块[/dim]")
    console.print("[dim]输入 [bold]q[/bold] 退出[/dim]")

    while True:
        choice = console.input("\n[bold cyan]请选择: [/bold cyan]").strip().lower()

        if choice == "q":
            console.print("[yellow]已退出[/yellow]")
            sys.exit(0)

        if choice == "all":
            return [key for _, _, key in MODULES]

        try:
            indices = [int(x.strip()) for x in choice.split(",")]
            selected = []
            for idx in indices:
                if 1 <= idx <= len(MODULES):
                    selected.append(MODULES[idx - 1][2])
                else:
                    console.print(f"[red]编号 {idx} 无效，请重新输入[/red]")
                    break
            else:
                if selected:
                    return selected
        except ValueError:
            console.print("[red]输入格式无效，请用逗号分隔数字 (如: 1,3,5)[/red]")

        console.print("[dim]提示: 输入 all 爬取全部，输入 q 退出[/dim]")


def show_summary_report(results: List[Dict], total_time: float):
    """
    显示运行汇总报告。
    """
    success_count = sum(1 for r in results if r["status"] == "success")
    fail_count = len(results) - success_count

    console.clear()
    console.print(
        Panel.fit(
            "[bold green]✅ 全部模块运行完成！[/bold green]",
            border_style="green",
        )
    )

    # 结果表格
    table = Table(show_header=True, header_style="bold")
    table.add_column("模块", style="white")
    table.add_column("状态", style="green")
    table.add_column("耗时", style="cyan")
    table.add_column("错误信息", style="red")

    for r in results:
        status = "✅ 成功" if r["status"] == "success" else "❌ 失败"
        error = r["error"][:50] + "..." if r["error"] and len(r["error"]) > 50 else (r["error"] or "")
        table.add_row(
            r["name"],
            status,
            f"{r['duration']:.1f}s",
            error if error else "-",
        )

    console.print(table)

    # 汇总信息
    console.print(
        Panel(
            f"[bold]成功: {success_count}/{len(results)}[/bold]  "
            f"[bold red]失败: {fail_count}/{len(results)}[/bold red]  "
            f"[bold cyan]总耗时: {total_time:.1f}s[/bold cyan]"
            + (f"\n\n[red]失败模块: {', '.join(r['name'] for r in results if r['status'] == 'failed')}[/red]" if fail_count > 0 else ""),
            border_style="green" if fail_count == 0 else "red",
        )
    )

    console.print(f"\n[dim]详细报告: logs/run_all_report.json[/dim]")
    console.print("[dim]完整日志: logs/run_all.log[/dim]")


def save_report(results: List[Dict]):
    """
    保存详细报告到 JSON 文件。
    """
    report = {
        "run_time": datetime.now().isoformat(),
        "total_modules": len(results),
        "success": sum(1 for r in results if r["status"] == "success"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "failed_list": [r["name"] for r in results if r["status"] == "failed"],
        "total_duration": sum(r["duration"] for r in results),
        "details": results,
    }

    import os
    os.makedirs("logs", exist_ok=True)
    with open("logs/run_all_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)


def main():
    """主流程"""
    # 1. 显示选择菜单
    selected_keys = show_selection_menu()

    if not selected_keys:
        console.print("[yellow]未选择任何模块，退出[/yellow]")
        return

    # 2. 确认选择
    selected_names = [name for name, _, key in MODULES if key in selected_keys]
    console.print(f"\n[cyan]已选择 {len(selected_names)} 个模块:[/cyan] {', '.join(selected_names)}")

    if not Confirm.ask("\n[bold yellow]确认开始爬取？[/bold yellow]"):
        console.print("[yellow]已取消[/yellow]")
        return

    # 3. 执行爬取（带进度显示）
    total = len(selected_keys)
    console.print(f"\n[bold green]开始执行 {total} 个模块...[/bold green]\n")

    results = []
    total_start = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    ) as progress:

        task = progress.add_task("[cyan]总体进度", total=total)

        for idx, key in enumerate(selected_keys, 1):
            # 找到对应的模块
            module_name = ""
            module_obj = None
            for name, obj, k in MODULES:
                if k == key:
                    module_name = name
                    module_obj = obj
                    break

            if module_obj is None:
                results.append({
                    "key": key,
                    "name": key,
                    "status": "failed",
                    "error": "模块未找到",
                    "duration": 0,
                })
                progress.advance(task)
                continue

            # 更新进度描述
            progress.update(task, description=f"[cyan][{idx}/{total}] {module_name}[/cyan]")

            # 运行模块
            result = run_module(module_name, module_obj, key)
            results.append(result)

            # 显示即时结果
            if result["status"] == "success":
                progress.console.print(f"  [green]✅ {module_name}[/green] ({result['duration']:.1f}s)")
            else:
                progress.console.print(f"  [red]❌ {module_name}[/red] - {result['error']}")

            progress.advance(task)

    # 4. 显示汇总报告
    total_time = time.time() - total_start
    save_report(results)
    show_summary_report(results, total_time)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]用户中断，已退出[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[red]程序异常: {e}[/red]")
        import traceback
        traceback.print_exc()
        sys.exit(1)