# 原神观测枢文本爬虫

一个面向《原神》观测枢文本内容的增量爬取项目。它按官方条目 ID 保存纯文本，涵盖任务、角色、NPC、地图文本、书籍、物品等分类，并提供统一的运行报告与数据校验流程。

项目的职责是**稳定抓取、整理和校验文本**。爬取结果可被其他项目继续使用，但本仓库不包含应用界面、模型训练或检索服务。

## 内容范围

- 剧情相关：任务及子任务对话、角色资料与逸闻、NPC 对话、地图交互文本、书籍、组织、委托。
- 图鉴相关：武器、圣遗物、敌人、动物、食物、物品、摆设、秘境、名片、装扮、幻想真境剧诗。
- 数据来源以官方观测枢为主；名片模块使用第三方 Wiki 数据源，输出中会保留来源类型。

## 环境要求

- Python 3.12 或更高版本
- 推荐使用 [uv](https://docs.astral.sh/uv/) 管理环境

## 从零开始

### 1. 获取项目

```bash
git clone https://github.com/rikumo-rinnku/genshin-story-crawler.git
cd genshin-story-crawler
```

如果电脑还没有安装 `uv`，请先按照 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/) 完成安装。

### 2. 创建环境并安装依赖

在项目根目录执行：

```bash
uv sync
```

该命令会创建本项目专用的 `.venv` 虚拟环境，并安装 `pyproject.toml` 中声明的依赖。首次运行需要联网下载依赖，之后通常可直接使用已有环境。

### 3. 确认环境正常

```bash
uv run python --version
uv run python -c "import bs4, lxml, requests, rich, tenacity; print('依赖安装完成')"
```

第一条命令应显示 Python 3.12 或更高版本；第二条命令输出“依赖安装完成”即表示环境可用。

## 日常使用

### 手动选择模块

```bash
uv run python run.py
```

按提示选择一个或多个分类；适合调试某个模块或单独更新某类文本。

### 全模块增量更新

```bash
uv run python scripts/run_incremental_all.py
```

爬虫会读取本地的增量状态，仅保存新增条目。运行完成后，会在 `logs/` 中生成总报告与各模块报告。

### 发布前检查

```bash
uv run python scripts/build_manifest.py --strict
uv run python scripts/validate_dataset.py
```

第一条命令会根据当前文本重新构建清单；第二条命令会检查正文是否存在、是否为空、`doc_id` 是否重复，以及是否仍有待复核记录。

## 输出结构

```text
data/
├─ cleaned/             # 按模块和稳定条目 ID 保存的 UTF-8 纯文本
├─ manifest.jsonl       # 每行一份文本的正式清单
└─ manifest.sqlite3     # 写入过程使用的本地登记库

logs/                   # 爬取日志、模块报告与运行汇总
config/
├─ channels.json        # 各分类的官方频道配置
└─ crawled.json         # 本地增量状态
```

`manifest.jsonl` 中每条记录包含 `doc_id`、模块、条目 ID、分段 ID、标题、正文路径、内容哈希、抓取时间和状态。稳定 ID 用于避免中文标题改名或重复标题造成覆盖。

## 核心目录

```text
src/core/               # 请求、解析、存储、运行日志与报告等公共能力
src/modules/            # 各类文本的爬虫实现
scripts/                # 正式运行、清单构建与数据校验脚本
run.py                  # 交互式爬虫入口
```
