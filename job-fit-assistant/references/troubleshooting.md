# 故障排查

## 1. 按错误码

| 码 | 含义 | 常见原因 | 处理 |
| --- | --- | --- | --- |
| `E_USAGE` | 参数不对 | 缺 `--company`/`--title`、同时给了 `--text` 和 `--file`、未知枚举 | 看错误里的 `hint`，补参数或改取值 |
| `E_NOT_FOUND` | 文件/记录不存在 | 工作区没 init、`job_id` 打错、方案没保存 | `jfa sources` / `jfa job list` 核对 ID |
| `E_DATA` | 数据损坏 | 手改 JSON 改坏、JSONL 半行 | 用 `.jfa`/备份恢复，或重跑该阶段 |
| `E_VALIDATION` | 校验不通过 | 映射表/改写方案不合规、空岗位 | 按列出的 `code` 改内容，不改标准 |
| `E_DEPENDENCY` | 依赖缺失 | 没装 python-docx / PyMuPDF / Pillow | `pip install -r scripts/requirements.txt` |
| `E_OCR_UNAVAILABLE` | 无 OCR | 扫描件/图片但没有 OCR 后端 | 装 PaddleOCR 或改提供 DOCX/文字型 PDF |
| `E_EXPORT` | 导出失败 | 没有 PDF 后端、PDF 是空文件、字号低于下限 | 看 `attempts`；仍可交付 DOCX+TXT |
| `E_SAFETY` | 越界 | 路径跑出工作区 | 用 `--home` 指定工作区内路径 |
| `E_PAUSED` | 主动暂停 | 达到单次上限、登录失效 | `jfa job session --status running` 后继续 |

## 2. 按现象

### `scan` 很慢（十几秒）

`PDF 导出` 那一项在探测 Word COM，超时上限 12 秒。Word 卡在首次运行/修复弹窗时就是这个表现。

- 临时跳过：`set JFA_SKIP_WORD_PROBE=1`（Windows）或 `export $env:JFA_SKIP_WORD_PROBE=1`；
- 长期解决：装 LibreOffice，或直接用内置渲染器（只需 PyMuPDF）。

### 扫描件读不出字

1. `jfa scan` 看 OCR 是否"不可用"；
2. 装依赖：`paddlepaddle==3.2.0`（CPU 源）+ `paddleocr==3.4.1`；
3. 首次运行会下载模型（数百 MB），需联网；
4. 仍失败看 `report.ocr_failures` 的 `reason`：分辨率过低先 `--dpi 300` 重试；
5. 文档太复杂（多栏、表格）加 `--structure` 走 PP-StructureV3。

### DOCX 里有内容没被读到

`ingest` 的 warnings 会列出：文本框数量、嵌入图片数量、未解析内容（图表/OLE/脚注/批注）。

- 文本框：已单独提取并标待核对，去 `ocr show` 看；
- 嵌入图片：**不会自动 OCR**。让用户把图片单独存出来再 `ingest`，或口述内容用 `facts add`；
- 图表/脚注：让用户提供等价文字。

### 工作年限算少了

检查：

1. `jfa facts show --kind employment` 看每段的 `start`/`end` 是否解析出来；
2. `jfa facts tenure` 看 `raw_sum_months` 与 `total_months` 是否不同（不同说明有并行任职，合并是对的）；
3. `inferred` 拆分的事实需要 `facts confirm` 才会被计入；
4. 只写年份（如 "2021-2023"）时按月精度会偏保守。

### 映射表怎么改都不过

按错误码对症：

| code | 修法 |
| --- | --- |
| `evidence_unconfirmed` | 先 `facts confirm`，或把状态降到 `missing_evidence` |
| `direct_without_evidence` | 补 `fact_ids` |
| `transferable_without_explanation` | 写清迁移逻辑 |
| `missing_but_has_evidence` | 删掉 `fact_ids`，或改状态 |
| `mismatch_without_evidence` | 补能证明不符合的事实 |
| `unknown_requirement` | 用骨架里真实存在的 `R-xxx` |

### 改写被拦下

见 `references/rewrite-rules.md` 的第 2 节与第 8 节。核心：**改内容，不改标准**。

### PDF 导出失败

```bash
jfa export <job_id> --json
```

看 `pdf_error` 与 `manifest.fit_attempts`：

- "没有可用的转换后端" → 装 LibreOffice，或 `pip install PyMuPDF` 用内置渲染器；
- "文件过小，疑似空文件" → 转换器异常，换后端；
- "PDF 中没有可选中的文字层" → 转换器把文字转成了图形，换后端。

### 导出校验没通过

`-校验结果.json` 里逐项有 `detail` 与 `pages`。优先看 `pdf_line_coverage` 的 `missing` 字段，它直接告诉你是哪几行没进 PDF。

### 第二次导出报错

检查是否用了同一个 `--version`。同名文件不会覆盖，会追加 `-2`；但 `out/<jid>/vN/manifest.json` 是覆盖式的（那是索引，不是交付物）。

## 3. 自检

```bash
python scripts/jfa.py selftest
```

覆盖：块切分、事实构建、年限、薪资解析、筛选、映射校验、反注水拦截、文档模型、文本渲染、DOCX 生成。全绿说明本地核心本身没问题，问题在数据或参数。

## 4. 完整回归

```bash
python tests/make_fixtures.py     # 生成合成测试文件
python tests/run_e2e.py           # 全链路（含导出与校验）
python tests/run_e2e.py --keep    # 保留产物到 examples/demo
```

## 5. 仍然卡住时

需要向用户收集的最小信息：

1. `jfa scan --json` 输出；
2. 失败命令的完整输出（含错误码与 `detail`）；
3. `-校验结果.json`（若是导出问题）；
4. **不要**索取简历正文或职位原文的完整副本，除非用户主动提供。
