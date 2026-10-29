# 要求 ↔ 证据映射

## 1. 目的

把"岗位要什么"和"我有什么"逐条对上，并明确区分「我真的做过」「可以迁移」「我没有」「我不符合」。这份表是改写方案的依据，也是匹配报告的骨架。

## 2. 骨架从哪来

```bash
jfa match skeleton <job_id>
```

从职位的 `must_have`、`nice_to_have`、`responsibilities` 生成 `R-001` 起的要求列表，**全部初始为 `missing_evidence`**——这是诚实的默认值：还没做映射，就不能声称匹配。

## 3. 四种状态与验收规则

| 状态 | 含义 | 校验要求 |
| --- | --- | --- |
| `direct` | 直接匹配 | 必须至少引用 1 条**已确认**事实；措辞与事实应有共同术语（否则告警复核） |
| `transferable` | 可迁移经验 | 必须引用已确认事实，且 `explanation` 非空说明迁移逻辑 |
| `missing_evidence` | 缺少证据 | 引用已确认事实会被拦下（自相矛盾）；解释可空 |
| `clear_mismatch` | 明确不符合 | 必须引用能证明不符合的事实，并写清原因 |

其他阻断项：

- `requirement_id` 重复或缺失；
- `importance` 不是 `must`/`nice`；
- 引用了不存在的事实（`unknown_fact`）；
- 引用了未确认事实却声称 `direct`（`evidence_unconfirmed`）。

## 4. 三类证据举例

**direct**（岗位要 Python，事实里明确写了用过 Python 做项目）
> 要求：3 年以上后端开发经验
> fact：`F-0005`（星河数据 2021.03–2023.06 后端开发）、`F-0007`（云帆网络 2019.07–2021.02）
> 状态：`direct`，解释：两段后端经历合计 4 年（区间合并后）

**transferable**（岗位要支付系统，做过订单系统）
> 要求：有支付或电商业务经验
> fact：`F-0005`（订单系统接口开发与查询链路重构）
> 状态：`transferable`，解释：做过订单链路，支付领域知识需补；迁移的是交易链路经验，不是支付合规经验

**clear_mismatch**（岗位要 Kubernetes 运维，事实里完全没有）
> 要求：熟悉 Kubernetes 集群运维
> fact：`F-0009`（技能清单里只有 Docker）
> 状态：`clear_mismatch`，解释：会用 Docker，但没有 K8s 集群运维经历

注意 `missing_evidence` 与 `clear_mismatch` 的区别：前者是"档案里没有，可能用户有但没说"，后者是"档案明确显示不具备"。**默认用前者**，不要轻易判定用户不符合。

## 5. 覆盖率

```
must_ratio = Σ credit(must) / len(must)
nice_ratio = Σ credit(nice) / len(nice)
score = 0.7 × must_ratio + 0.3 × nice_ratio
credit: direct 1.0 / transferable 0.6 / missing 0 / mismatch 0
```

必备条件权重更高：一个必备缺口比三个加分缺口更致命。

`gaps` 按重要性排序输出，必备在前。

## 6. 追问策略

工具生成最多 3 个高价值问题，只针对 **must 且 missing** 的要求。原则：

- 一次问不超过 3 个，避免变成填表；
- 问题必须可回答、可验证："你是否有真实做过相关工作的经历？做过什么、你负责哪部分、用了什么方法、结果如何"；
- 每个问题都允许用户说"没有"，并说明跳过后的处理（按缺口如实标注）；
- **不要问已经知道答案的问题**（档案里有的不要再问一遍）。

## 7. 用户不补充时

允许，但要遵守：

1. 该要求保持 `missing_evidence`；
2. 简历不写相关内容；
3. 匹配报告列出缺口；
4. 完成度结论降级为"仅生成保守草稿：必备条件覆盖率低于 50%"之类，**不能**说"已完成全面定制"。

宁要一份诚实但薄的简历，不要一份好看但站不住的。

## 8. 校验命令

```bash
jfa match validate <job_id> --file mapping.json
```

- 有阻断项 → 退出码 4，逐条列出 `code` 与原因；
- 通过 → 保存 `matches/<jid>/vN.json` 并刷新 `latest.json`，同时写回 `coverage`、`gaps`、`open_questions`。

**校验不过就改内容，不要改标准。** 把 `direct` 降成 `transferable` 可以，但要有迁移解释；把 `missing_evidence` 硬改成 `direct` 会在下一次校验继续被拦。

## 9. 常见错误

| 错误 | 后果 | 正确做法 |
| --- | --- | --- |
| 所有要求都标 `direct` | 被 `evidence_unconfirmed`/`direct_without_evidence` 拦下 | 按实际证据分级 |
| `transferable` 不写解释 | 被拦下 | 写清"迁移的是什么、缺的是什么" |
| 引用未确认事实 | 被拦下 | 先 `facts confirm`，或降级 |
| 要求文本原样照抄长句 | 报告难读 | 压缩到 60–120 字，保留关键约束 |
| 漏掉某种明显不匹配 | 报告失真 | `clear_mismatch` 也要如实写 |
