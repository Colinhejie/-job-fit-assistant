# 个人事实档案

事实档案是**唯一**的简历内容来源。它和岗位无关，换岗位只换改写方案，不换事实。

## 1. 结构

```json
{
  "schema": "jfa.fact_archive/1",
  "person": { "display_name": "张三", "name_fact_id": "F-0003" },
  "facts": [
    {
      "fact_id": "F-0005",
      "kind": "employment",
      "label": "星河数据科技有限公司｜后端开发工程师｜2021.03–2023.06",
      "content": "星河数据科技有限公司 | 后端开发工程师 | 2021.03 - 2023.06\n协助团队完成订单系统接口开发…",
      "structured": {
        "company": "星河数据科技有限公司",
        "title": "后端开发工程师",
        "start": "2021-03",
        "end": "2023-06",
        "ongoing": false,
        "split_method": "explicit_delimiter"
      },
      "source": { "block_ids": ["B-0004","B-0005"], "page": null, "locator": {"docx_paragraph": 4} },
      "confirm_status": "source_explicit",
      "verification_basis": "self_attested",
      "version": 1,
      "revisions": []
    }
  ],
  "tenure": { "total_months": 48, "total_text": "4 年", "raw_sum_months": 48 },
  "deduped": []
}
```

## 2. 四种状态

| 状态 | 含义 | 能否写进简历 |
| --- | --- | --- |
| `source_explicit` | 原文里逐字存在 | ✔ |
| `user_confirmed` | 用户口头/文字补充并确认 | ✔ |
| `unconfirmed` | 机器推断，未经用户确认 | ✘（只能进待确认清单） |
| `conflict` | 与另一条事实矛盾 | ✘（先解决冲突） |

`verification_basis` 恒为 `self_attested`。**"已确认"只表示用户确认，不表示第三方核实。** 对外的说法也必须是"你确认过的"，而不是"已核实的"。

冲突处理：两条事实 `conflicts_with` 互相指向，两条都不进简历，直到用户选一条。

## 3. 什么算"逐字命中"

`content` 与所引块文本（去空白、小写后）是包含关系，才算 `source_explicit`。

- 多块组成的经历：对照块的**拼接结果**，不要求落在一行里。
- 只要有一个字是补的（补全公司全称、补全月份），就不是逐字命中，降为 `unconfirmed`。

## 4. 结构化字段的推断边界

| 字段 | 何时可直接采用 | 何时必须标 `needs_review` |
| --- | --- | --- |
| 公司 / 职位 | 原文有明确分隔符（`|` `·` `\t`）且两段都能在原文找到 | 无分隔符、靠规则切分 |
| 起止时间 | 原文写了日期，正则能解析 | 只写年份、或只有"至今"没起点 |
| 学校 / 学历 / 专业 | 同一行内可识别 | 需要跨行拼接 |
| 技能水平 | 原文写了"熟练/了解"等词 | 原文没写水平 |

`structured.split_method` 记录 `explicit_delimiter` 或 `inferred`，可审计。

## 5. 工作年限：区间合并，不累加

```
输入：若干 (start, end) 月区间
1. start 缺失或 end 缺失且非 ongoing → 不计入
2. end < start → 丢弃（数据错误）
3. 按 start 排序，逐个合并相交或相邻区间
4. total = 合并后各段长度之和
5. raw_sum = 合并前各段长度之和（仅用于对照展示）
```

输出同时给 `total_months` 与 `raw_sum_months`。若 `len(merged) < len(intervals)`，`concurrent_detected = true`，并提示"存在并行任职：已按区间合并，未简单累加"。

例：2019.07–2021.02 与 2020.04–2024.01 并行 → 合并 2019.07–2024.01 ≈ 4 年 7 个月，而不是 1 年 8 个月 + 3 年 10 个月 = 5 年 6 个月。

**兼职、实习、并行创业都不例外。** 用户问"我几年经验"时，答合并值并解释口径。

## 6. 跨格式去重

同一份材料的多种格式（DOCX + 导出的 PDF + 截图）会产生重复事实。去重键：

- 工作经历：`(company, title, start)`；
- 教育：`(school, degree, date_raw)`；
- 证书/荣誉：去掉前置标签后的正文（`证书：X` 与 `X` 视为同一条）；
- 其他：`(kind, 去空白小写正文)`。

被合并的记进 `deduped`，附 `duplicate_of`。**不要静默丢弃**——用户需要知道系统认出了重复。

## 7. 身份字段保护

以下字段由事实档案注入简历，改写方案**无权修改**：

姓名、学历/学位、毕业院校、专业、公司、真实职位、任职时间、技能熟练度、个人职责范围、业绩数字、证书。

实现上不是"检查是否被改"，而是**结构上不可能改**：`build_resume_doc` 只从事实档案取这些字段，改写方案只提供 `bullets` 的措辞。校验层再加一道 `identity_company_swap` / `identity_degree_invented` 兜底。

## 8. 修订记录

```bash
jfa facts confirm F-0005 --content "新的内容" --reason "用户更正公司全称"
jfa facts confirm F-0005 --status user_confirmed
```

每次修订：

- `version += 1`；
- 追加 `revisions[]`，含 `before`、`after`、`actor`、`reason`、`at`；
- `updated_at` 刷新；
- 重算 `tenure`。

**永远不要直接编辑 `facts.json` 覆盖历史**。要改就通过 `confirm`/`add`，让修订链完整。

## 9. 用户补充事实

```bash
jfa facts add --kind skill --content "Kubernetes（仅了解）"
jfa facts add --kind achievement --content "带过 2 人小组" --unconfirmed
```

- 默认 `user_confirmed`（用户刚说的）。
- 用户只是"可能吧""大概"这种语气时用 `--unconfirmed`，等确认再升级。
- `kind` 只能取：`identity` `contact` `education` `employment` `project` `skill` `achievement` `preference` `other`。

## 10. 已知限制

- 无法判断事实真伪，只能保证"用户说过"。
- 中文日期写法覆盖常见形式（`2021.03` `2021-03` `2021/03` `2021年3月` `2021年3月15日`），罕见写法可能漏解析，会体现在 `start` 为空并提示。
- 一份简历里同时出现"至今"和多段在职时，全部按 ongoing 处理，可能需要用户手工收敛。
