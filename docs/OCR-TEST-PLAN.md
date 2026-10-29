# 真实 OCR 测试计划（未执行）

本文件用于在一台**能装上 OCR 依赖**的机器上补齐第四部分。当前环境装不上，原因见
`docs/DELIVERY.md` 第 3 节。这里的 5 个场景是必须补测的最小集。

## 0. 安装（Python 3.11 或 3.12 更稳；3.13 目前缺 opencv/shapely wheel）

```bash
# 方案 A：PaddleOCR（推荐，含 PP-StructureV3 可测双栏）
pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install paddleocr==3.4.1
python scripts/jfa.py scan    # 应显示 OCR 可用

# 方案 B：RapidOCR（轻量，无版面结构）
pip install rapidocr-onnxruntime
```

首次运行会下载模型（数百 MB），之后离线可用。

## 1. 必须测的 5 个场景

| # | 样本 | 构造方式 | 期望 | 记录什么 |
|---|---|---|---|---|
| O1 | 清晰扫描 PDF | 把文字型 PDF 以 200–300dpi 渲染成图片再封装为 PDF | 识别出姓名/电话/公司/日期，置信度多在 0.9 以上 | 每行文本、bbox、置信度 |
| O2 | 双栏扫描 PDF | 用两栏排版渲染 | 阅读顺序不乱；若乱必须触发阅读顺序冲突标记 | 块顺序 + 冲突标记 |
| O3 | 中英混排 | 简历里放 `Python/Pytest/JMeter` 与中文混排 | 英文不被拆成乱码、大小写保留 | 原文对照 |
| O4 | 模糊照片 | 手机拍摄/降采样+压缩（本仓库 `heldout_resume_blurry.jpg` 可作起点） | 低置信度被标记；异常字段进待核对；**不静默采信** | 置信度、标记原因 |
| O5 | 文字+扫描混合 PDF | 第 1 页文字层、第 2 页图片 | 第 1 页不触发 OCR，第 2 页触发 | 逐页 `use_ocr` 与 `reason` |

## 2. 必须保存的证据

对每个场景保存：

```
ocr-evidence/
  O1/
    page-01.png            渲染图（核对用）
    blocks.jsonl           每块的 text / bbox / confidence / method
    review.json            待核对队列
    report.json            ingest 的 report（page_reports / ocr_failures）
```

**置信度是工具返回的原值**：`OcrProvider` 返回什么就记什么；后端不返回置信度时
字段留空（`null`），**不要填 0 或 1 假装有**。

## 3. 人工纠正要能进入最终简历

固定动作：

1. `jfa ocr correct <sid> <item> --text "更正后"` → 队列状态 `corrected`，`revisions` 保留原文；
2. `jfa facts build`（会应用更正）；
3. `jfa facts confirm <fid> --content "..."` 确认结构化字段；
4. `jfa rewrite validate` → `jfa export`；
5. 打开导出的 DOCX/PDF，确认更正后的文字出现在简历里，**原始识别错误没有残留**。

断言：

- `review/corrections.jsonl` 有对应记录；
- `facts.json` 的 `source.block_ids` 指向被更正的块；
- 导出文本里出现更正后的字符串，且**不出现**原错误串。

## 4. 冲突未解决时不得当事实导出

构造两条冲突事实（同一技能两种说法），都标 `conflict`：

- `jfa facts validate` 应给出冲突提示；
- `jfa rewrite validate` 若引用这两条事实，应被 `unconfirmed_as_fact` 阻断；
- 导出必须失败（退出码 4），**不能**把冲突内容当已确认事实写进简历。

## 5. 结果怎么记

```markdown
| # | 后端与版本 | 识别字符数 | 平均置信度 | 关键字段正确率 | 结果 |
|---|---|---|---|---|---|
| O1 | paddleocr 3.4.1 | 412 | 0.94 | 姓名✓ 电话✓ 公司✓ 日期✓ | 通过 |
```

> 注意：「缺依赖时如实失败」的测试（T04/T07）**不计入**这里的识别成功率。
> 它们只证明"装不上时不撒谎"，不证明"装上了能认对"。
