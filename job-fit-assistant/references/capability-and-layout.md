# 能力检查与工作区

## 1. 为什么第一步必须做能力检查

求职流程里最常见的坑不是"写不好"，而是"以为能做到"：以为扫描件能读、以为 PDF 能导出、以为宿主有浏览器。能力检查把这三件事在开工前变成事实，而不是开工后的返工。

```bash
python scripts/jfa.py --home <ws> scan
python scripts/jfa.py --home <ws> scan --json    # 给程序读
```

## 2. 九项能力与判定口径

| 键 | 含义 | 判定方式 | 不可用时怎么向用户说 |
| --- | --- | --- | --- |
| 本地执行 | 解析/校验/计算/生成在哪个进程 | 恒为本地进程内 | — |
| 文件读写 | 工作区可写 | 真写一个探针文件再删 | 指出路径与错误 |
| DOCX 解析/生成 | python-docx | 能否 import | 给 `requirements.txt` |
| PDF 逐页检测 | PyMuPDF | 能否 import | 同上 |
| 图片读取 | Pillow | 能否 import | 同上 |
| PDF 页面渲染校验 | PyMuPDF | 能否 import | 导出后无法逐页检查 |
| OCR 文字识别 | PaddleOCR / RapidOCR / Tesseract | 逐个探测 import | 给 `requirements-ocr.txt` 与模型下载说明 |
| PDF 导出 | LibreOffice → Word COM → 内置渲染器 | 逐个探测 | 只交付 DOCX+TXT，PDF 明确标失败 |
| 浏览器 | **宿主声明** | 读 `config.host.declared` | 改用户粘贴职位文本 |

关键点：**浏览器永远不由本工具探测**。一个 Skill 无法知道宿主暴露了哪些浏览器工具，猜一个名字等于教模型调用不存在的工具。所以默认是"未声明"，由宿主用真实工具清单声明：

```bash
python scripts/jfa.py --home <ws> host declare \
  --browser-kind in-app-browser \
  --tool browser_navigate --tool browser_read_page \
  --login-scope local \
  --notes "用户在应用内浏览器自行登录"
```

## 3. 状态三值

- `available`：实测通过。
- `unavailable`：实测失败或依赖缺失。**不要暗示它可能能用。**
- `unknown`：未声明/无法判断（目前只有浏览器会落到这里）。此时走降级路径，不要假装可用。

## 4. 工作区结构

```
<ws>/
  workspace.json           schema 与创建信息
  config.json              ocr / export / screening / host / privacy 设置
  preferences.json         求职偏好
  sources/                 原始文件副本（不可变，只读引用）
  sources/_pages/          PDF 渲染页（OCR 与核对用）
  blocks/<sid>.jsonl       文本块
  blocks/index.json        来源登记表
  review/<sid>.json        OCR 核对队列
  review/corrections.jsonl 更正流水
  facts/facts.json         个人事实档案
  jobs/jobs.jsonl          职位记录
  jobs/raw/<jid>.txt       职位原文（逐字）
  matches/<jid>/vN.json    要求↔证据映射
  rewrites/<jid>/vN.json   改写方案
  screening/latest.json    筛选结果
  out/<jid>/vN/            导出产物
  logs/run.log             必要日志（不含简历正文）
```

原则：

- **原始文件只新增不修改。** `sources/` 里的副本用内容哈希命名，同名不同内容不会互相覆盖。
- 派生文件全部带版本号；`latest.json` 只是指针，版本文件永不覆盖。
- 想重建某个阶段，删对应目录即可，不会污染其他阶段。

## 5. 退出码

| 码 | 含义 | 典型来源 |
| --- | --- | --- |
| 0 | 成功 | — |
| 2 | 参数/用法错误 | 缺参数、未知枚举 |
| 3 | 依赖缺失 | 没有 OCR、没有 python-docx |
| 4 | 数据/校验失败 | 映射表不通过、事实未确认 |
| 5 | 导出失败 | 没有 PDF 后端、校验未通过 |
| 6 | 安全拦截 | 路径越界、未授权范围 |
| 7 | 已暂停 | 达到单次读取上限、登录失效 |

宿主 agent 应据退出码分流，而不是解析中文提示。

## 6. 配置项

`config.json` 里真正影响行为的键：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `ocr.provider` | `auto` | `auto` 优先 PaddleOCR |
| `ocr.max_pages_per_run` | 20 | 单次最多处理页数 |
| `ocr.dpi` | 220 | 扫描页渲染分辨率 |
| `export.max_pages` | 2 | 超过就多页，不缩小字号硬压 |
| `export.min_font_pt` | 9.5 | 正文字号硬下限 |
| `export.pdf_backends` | libreoffice → word → builtin | 转换链顺序 |
| `export.include_photo` | false | 是否允许照片 |
| `screening.max_jobs_per_run` | 8 | 单次读取岗位上限 |
| `privacy.log_raw_text` | false | 恒为 false；日志不写正文 |

改配置用 `jfa` 的默认值 + 手改 `config.json`，没有单独的 config 命令，避免多一个要维护的入口。
