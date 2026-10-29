# 简历解析与 OCR

## 1. 支持矩阵

| 格式 | 处理方式 | 页码 | 备注 |
| --- | --- | --- | --- |
| `.docx` | 段落 + 表格 + 文本框 + 页眉页脚 + 嵌入图片 | **无**（用段落/行号定位） | 不编造页码 |
| `.pdf` 文字层 | 逐页取文本块 + bbox | 有 | 不强制 OCR |
| `.pdf` 扫描页 | 逐页渲染 → OCR | 有 | 只对需要的页 OCR |
| `.png/.jpg/.jpeg/.webp/.bmp/.tif` | 整图 OCR | 记为第 1 页 | 没有文字层，必须 OCR |
| `.txt/.md` | 逐行切块 | 无（用行号） | 阶段 1 主路径 |

明确不支持并给出替代方案：`.doc`、`.wps`、`.pages`、`.rtf`、`.heic`。不要尝试用二进制猜测，直接让用户另存。

## 2. PDF 逐页判定

对每一页只看该页自己的文字层，判断记录在 `report.page_reports[]`，含 `use_ocr` 与 `reason`：

| 条件 | 结论 | reason 示例 |
| --- | --- | --- |
| 去空白后 0 字 | 需要 OCR | 该页没有文字层（纯扫描或纯图片页） |
| 少于 20 字且含图片 | 需要 OCR | 文字层仅 N 字且含图片，疑似扫描页 |
| 少于 20 字无图片 | 需要 OCR | 文字层仅 N 字，信息不足 |
| 不可显示字符 > 2% | 需要 OCR | 疑似字体子集编码损坏 |
| 字母/汉字占比 < 35% | 需要 OCR | 疑似符号化乱码 |
| 其余 | 不 OCR | 文字层可用，未启用 OCR |

**为什么不做整份判断**：一份简历常见「第 1 页是文字、第 2 页贴了张证书扫描图」。整份判断会要么漏掉证书，要么把本来能读的页拿去 OCR 制造误差。

没有可用 OCR 后端时，需要的页会写进 `report.ocr_failures`，并在 `errors` 里说明"这些页的内容缺失"。**不要**降级成空文本继续，那会让下游以为这页是空白。

## 3. DOCX 的四类隐形内容

直接 `python-docx` 读 `document.paragraphs` 会漏掉：

1. **表格**：按行抽取，单元格用 ` | ` 连接，locator 记 `{table, row}`。
2. **文本框**（`w:txbxContent`）：单独抽取为 `docx_textbox`，并强制 `needs_review`。联系方式经常就藏在这里。
3. **页眉页脚**：单独抽取，locator 记 `{docx_header|docx_footer}`，并在事实分段时归入 `header`，避免被最后一个正文栏目吞掉。
4. **嵌入图片**：每张图片生成一个占位块并报告数量。图片里的文字不会被自动识别——必须告诉用户"这里可能有信息，需要你确认或单独提交图片"。

另有读不了的内容会进 `report.unhandled`：OLE 对象、图表、脚注尾注、批注、SmartArt。这些必须以警告形式出现在 ingest 输出里，不能静默丢弃。

## 4. OCR 依赖（固定版本）

```
paddlepaddle==3.2.0   （CPU 源）
paddleocr==3.4.1
```

安装：

```bash
pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install paddleocr==3.4.1
```

- **模型首次下载**：PP-OCRv5 检测+识别模型、PP-StructureV3 版面模型，合计数百 MB，需要联网一次；缓存在 `~/.paddlex/official_models`，之后离线可用。
- **资源**：CPU 路径建议 ≥ 2GB 可用内存；整页 A4 在普通 CPU 上约数秒到十几秒。PP-StructureV3 更重。
- **复杂版式**：用 `--structure` 走 PP-StructureV3。它给出版面块（标题/表格/正文），适合多栏、带表格的简历；纯文字简历用 PP-OCRv5 更快。
- 轻量替代：`rapidocr_onnxruntime`（无版面结构分析），Tesseract（需系统中文语言包，效果一般）。

## 5. 可替换接口

`scripts/jfa/extract/ocr/base.py` 定义 `OcrProvider`：

```python
class OcrProvider:
    name: str
    supports_structure: bool
    @classmethod
    def available(cls) -> bool: ...
    def describe(self) -> dict: ...
    def recognize(self, image_path, *, page=None, lang="ch", structure=False) -> OcrResult: ...
```

规则：

- `available()` 只做真实探测（能否 import），不做"大概能用"的猜测。
- 识别失败抛 `OcrUnavailable`，附带安装指引，**不允许返回空结果假装成功**。
- 新增后端：实现该接口 + 在 `providers()` 注册，不要改调用方。

## 6. 测试替身 `fixture`

`fixture` 提供者回放人工编写的 `.ocr.json` 旁车文件，仅用于在没有 OCR 的机器上测核对流程。三重防误用：

1. 必须显式 `JFA_ALLOW_TEST_PROVIDER=1`；
2. 每个块的 `method` 记为 `fixture_test_double`；
3. 抽取报告里带显著警告。

它永远不算"真实识别结果"。发布包中保留它，是为了让测试可复现，不是为了在生产里凑数。

## 7. 三份输入的关系

同一份简历可能同时以 DOCX、PDF、截图出现。全部 ingest 是允许的：

- 来源之间**串行拼接**，不交错（交错会把一段经历拆散）。
- 事实档案按「公司+职位+起始时间」「学校+学历+日期」去重，因此同一份材料多格式上传不会让工作年限翻倍。
- 去重结果记在 `facts.deduped`，可审计。
