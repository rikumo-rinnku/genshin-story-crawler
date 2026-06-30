#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成项目目录树结构，排除无关目录。
排序规则：文件夹优先，然后按字母序排列（不区分大小写）。
用法：python generate_tree.py
输出：project_structure.txt
"""

import os
import sys

# 需要排除的目录名称
EXCLUDE_DIRS = {
    '.venv', 'venv', 'env',
    '__pycache__',
    'logs', 'log',
    'data', 'chroma_db', 'node_modules',
    '.git', '.vscode', '.idea',
    '.pytest_cache', '.mypy_cache',
    'dist', 'build', '*.egg-info'
}

# 需要排除的文件扩展名
EXCLUDE_EXTENSIONS = {'.pyc', '.pyo', '.pyd', '.so', '.dll', '.exe'}

# 需要排除的完整文件名
EXCLUDE_FILES = {'.DS_Store', 'Thumbs.db', 'desktop.ini'}


def should_exclude(name, is_dir=False):
    """判断是否排除"""
    name_lower = name.lower()
    if is_dir:
        for pattern in EXCLUDE_DIRS:
            if pattern.startswith('*') and pattern.endswith('*'):
                if pattern[1:-1] in name_lower:
                    return True
            elif pattern.startswith('*'):
                if name_lower.endswith(pattern[1:]):
                    return True
            elif pattern.endswith('*'):
                if name_lower.startswith(pattern[:-1]):
                    return True
            elif name_lower == pattern.lower():
                return True
        return False
    else:
        if name in EXCLUDE_FILES:
            return True
        ext = os.path.splitext(name)[1].lower()
        if ext in EXCLUDE_EXTENSIONS:
            return True
        return False


def sorted_items(path):
    """获取目录下的条目，按文件夹优先、字母序排列"""
    items = []
    try:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if should_exclude(name, is_dir=os.path.isdir(full)):
                continue
            items.append((name, os.path.isdir(full)))
    except PermissionError:
        return []
    # 排序：先按 is_dir 降序（文件夹 True 在前），再按名称升序（忽略大小写）
    items.sort(key=lambda x: (not x[1], x[0].lower()))
    return items


def print_tree(startpath, prefix="", is_last=False, output_lines=None):
    if output_lines is None:
        output_lines = []

    items = sorted_items(startpath)
    for idx, (name, is_dir) in enumerate(items):
        path = os.path.join(startpath, name)
        is_last_item = (idx == len(items) - 1)

        # 前缀
        if prefix == "":
            line = "├── " if not is_last_item else "└── "
        else:
            line = prefix + ("└── " if is_last_item else "├── ")

        line += name
        output_lines.append(line)

        if is_dir:
            new_prefix = prefix + ("    " if is_last_item else "│   ")
            print_tree(path, new_prefix, is_last_item, output_lines)

    return output_lines


def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root_dir)

    print(f"正在生成 {root_dir} 的目录树结构...")
    print("排除目录：", ", ".join(sorted(EXCLUDE_DIRS)))
    lines = print_tree(root_dir)

    output_file = "project_structure.txt"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"项目根目录：{root_dir}\n")
        f.write("=" * 50 + "\n\n")
        f.write("\n".join(lines))
        f.write("\n")

    print(f"✅ 结构已保存至 {output_file}")
    print("\n--- 预览（前20行）---")
    for line in lines[:20]:
        print(line)
    if len(lines) > 20:
        print(f"...（共 {len(lines)} 行）")


if __name__ == "__main__":
    main()