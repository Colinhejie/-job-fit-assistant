# 命令速查

所有命令都支持 `--json`（除少数纯文本输出）与全局 `--home <工作区>`。工作区可用 `JFA_HOME` 环境变量代替。

```
python scripts/jfa.py [--home <ws>] <命令> [参数]
```

## 能力与初始化

| 命令 | 作用 |
| --- | --- |
| `scan [--json]` | 九项能力检查；浏览器为"未声明"属正常 |
| `init [--force]` | 建立工作区 |
| `host declare --browser-kind K [--tool T]... [--login-scope S] [--notes N]` | 声明宿主真实浏览器能力 |
| `host show` | 查看声明 |
| `schema list` | 列出随包 JSON schema |
| `schema check <kind> <file>` | 用 schema 校验一个 JSON 文件 |

## 解析与核对

| 命令 | 作用 |
| --- | --- |
| `ingest <file> [--ocr auto\|paddleocr\|rapidocr\|fixture\|none] [--lang ch] [--dpi 220] [--max-pages N] [--structure]` | 解析简历 |
| `sources` | 列出已解析来源 |
| `ocr list [source_id]` | 列出待核对条目 |
| `ocr show <source_id> [--limit N]` | 输出核对清单（含重点字段） |
| `ocr accept <sid> <item_id>` | 确认无误 |
| `ocr correct <sid> <item_id> --text "..." [--note N]` | 更正（保留原文） |
| `ocr reject <sid> <item_id> [--note N]` | 判为无效内容 |

## 事实档案

| 命令 | 作用 |
| --- | --- |
| `facts build [--source-id S] [--force]` | 从文本块生成候选档案；已存在时不覆盖，写 `facts.candidate.json` |
| `facts validate` | 结构校验；有阻断项退出码 4 |
| `facts show [--kind K] [--status S]` | 查看事实 |
| `facts confirm <id> [--content "..."] [--status S] [--reason R]` | 确认/更正，写修订记录 |
| `facts add --kind K --content "..." [--unconfirmed] [--label L]` | 加入用户口述事实 |
| `facts tenure` | 年限（合并值与直接相加值） |

## 偏好与职位

| 命令 | 作用 |
| --- | --- |
| `prefs set --file <json>` 或 `--json-patch '<json>'` | 写入偏好 |
| `prefs show` | 查看偏好 |
| `job add --company C --title T (--text "..." \| --file F) [--url U] [--job-ref ID] [--read-at T] [--detail-region R] [--location L] [--source-kind K] [--host-tool T] [--read-scope S] [--session S] [--index N] [--sections '<json>']` | 记录职位 |
| `job list [--limit N]` | 列出职位 |
| `job show <job_id>` | 职位详情、分栏结果、身份状态 |
| `job confirm-identity <job_id> --statement "<用户原话>" --evidence "<证据>"` | 记录用户对身份的确认（非系统核对，`human_authorised=false`） |
| `job session --status running\|paused\|cancelled\|done [--session S] [--reason R]` | 会话状态 |

## 筛选、映射、改写

| 命令 | 作用 |
| --- | --- |
| `screen [--limit N] [--job-id J]...` | 硬条件 + 软偏好 |
| `match skeleton <job_id>` | 生成映射骨架 |
| `match validate <job_id> [--file F]` | 校验并保存映射 |
| `match show <job_id>` | 输出匹配报告 |
| `rewrite template <job_id>` | 生成改写方案模板到 `change_plan.json` |
| `rewrite validate <job_id> [--file F]` | 反注水校验并保存 |
| `rewrite show <job_id> [--text]` | 查看方案 / 文本预览 |

## 导出与维护

| 命令 | 作用 |
| --- | --- |
| `export <job_id> [--version N] [--style F] [--photo P] [--max-pages N] [--min-font-pt N] [--allow-pending]` | 导出全部产物并校验 |
| `purge [--yes]` | 删除本地资料 |
| `selftest` | 内置冒烟测试（不涉及真实简历） |

## 退出码

`0` 成功 · `2` 用法错误 · `3` 依赖缺失 · `4` 数据/校验失败 · `5` 导出失败 · `6` 安全拦截 · `7` 已暂停

## 典型串联

```bash
WS="--home ./job-fit-workspace"
python scripts/jfa.py $WS scan
python scripts/jfa.py $WS init
python scripts/jfa.py $WS ingest resume.docx
python scripts/jfa.py $WS ocr show src-xxxx
python scripts/jfa.py $WS facts build
python scripts/jfa.py $WS facts show
python scripts/jfa.py $WS prefs set --file prefs.json
python scripts/jfa.py $WS job add --company "示例科技" --title "后端开发工程师" --text "$(cat jd.txt)" --url "https://..."
python scripts/jfa.py $WS screen
python scripts/jfa.py $WS match skeleton job-xxxxxxxx
python scripts/jfa.py $WS match validate job-xxxxxxxx --file mapping.json
python scripts/jfa.py $WS rewrite template job-xxxxxxxx
python scripts/jfa.py $WS rewrite validate job-xxxxxxxx --file change_plan.json
python scripts/jfa.py $WS export job-xxxxxxxx
```
