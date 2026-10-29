# 简历改写规则

## 1. 允许做的五件事

1. 调整求职方向与个人简介，使其对齐岗位；
2. 突出与该岗位相关的技能（顺序与措辞，不编造）；
3. 调整每段经历**内部**的重点顺序、删减与本岗位无关的细节；
4. 压缩无关内容（整段删除需写 `omit_reason`）；
5. 用"行动—任务—方法—结果"或"目的—做法—结果"重述同一件事。

## 2. 禁止做的事（会被 `rewrite validate` 拦下）

| 校验码 | 触发条件 | 例子 |
| --- | --- | --- |
| `verb_escalation` | 原文本/事实里的弱动词被换成更强动词，且强动词在任何事实里都没有依据 | 协助 → 主导 / 负责 / 统筹 / 独立 |
| `skill_inflation` | 熟练度升级且新等级无依据 | 学习过 → 熟练；了解 → 精通 |
| `skill_level_unbacked` | 写了强熟练度（熟练/精通/掌握）但事实与原文都没有该等级 | 事实只写"Python"，改写成"精通 Python" |
| `invented_number` | 出现事实与原文都没有的数字 | 原文 800ms→300ms，改写写"提升 300%" |
| `team_to_individual` | 原文是团队语境，改写出现"独立/全程负责/我主导"且依据里没有 | 团队完成 → 我独立完成 |
| `absolute_claim`（警告） | 出现"所有/唯一/首个/100%/零失误"且依据里没有 | "零失误交付" |
| `keyword_without_fact`（警告） | 新增了事实里没有的技术名词 | 凭空写入 Kubernetes |
| `identity_company_swap` | 提到了不属于所引事实的公司 | 张冠李戴 |
| `identity_degree_invented` | 出现档案里没有的学历表述 | 本科 → 硕士 |
| `no_fact_basis` | 某条改动没有任何 `fact_ids` | 无依据的自我评价 |
| `unconfirmed_as_fact` | 标 `verified` 却引用未确认事实 | 把推断当事实 |
| `unknown_requirement` | 引用了映射表里不存在的要求 | 编 R-099 |

## 3. 改写方案结构

```json
{
  "schema": "jfa.change_plan/1",
  "target_title": { "text": "后端开发工程师", "requirement_ids": ["R-001"], "fact_ids": ["F-0003"], "verification_status": "verified", "change_reason": "..." },
  "summary": { "before": "", "after": "...", "change_reason": "...", "requirement_ids": [], "fact_ids": [], "verification_status": "verified" },
  "skills": [
    { "fact_id": "F-0009", "before": "Python", "after": "Python（订单系统与后台服务中使用）", "change_reason": "...", "requirement_ids": ["R-002"], "verification_status": "verified" }
  ],
  "experiences": [
    {
      "fact_id": "F-0005", "order": 1, "omit": false, "omit_reason": "",
      "bullets": [
        { "before": "协助团队完成订单系统接口开发…", "after": "承担订单系统接口开发…",
          "change_reason": "在原文职责强度内换动词…", "requirement_ids": ["R-001"],
          "fact_ids": ["F-0005"], "verification_status": "verified" }
      ]
    }
  ],
  "greeting": { "text": "...", "requirement_ids": [], "fact_ids": [], "verification_status": "verified" },
  "open_questions": [],
  "notes": []
}
```

要点：

- `experiences[].fact_id` 必须是 `kind=employment` 的事实 ID；顺序由 `order` 决定。
- 公司、职位、时间**不写在方案里**——它们从事实档案注入，结构上没有改写入口。
- `bullets[].before` 写原文（便于对照），`after` 写新文案。
- `verification_status`：引用的全是已确认事实 → `verified`；含未确认事实 → `pending_confirmation`。

## 4. 逐字能力边界

| 原文 | 可以改成 | 不可以改成 |
| --- | --- | --- |
| 协助团队完成 X | 承担 X 中的 Y 部分（仍在协助强度内） | 主导 X |
| 参与 X 重构 | 参与 X 重构，负责其中的接口层 | 负责 X 重构 |
| 学习过 Python | 用 Python 完成课程项目 | 熟练使用 Python |
| 团队将耗时降低 60% | 配合团队将耗时降低 60% | 我独立将耗时降低 60% |
| 处理工单 60 余个 | 处理工单 60 余个 | 处理工单 200+ 个 |

## 5. 措辞原则

- 一条 bullet 只讲一件事，动词开头，避免"负责了…的工作"这类空转。
- 有数字用数字，没数字写清范围（不要造数字补位）。
- 岗位术语要用**必须**出现在所引事实里；同义替换可以（"接口开发"≈"API 开发"），但概念升级不行。
- 不堆砌关键词。一段里塞 6 个技术名词，招聘方一眼看出是关键词匹配。
- 中文简历不写"本人"，不写主观形容词（优秀的、极强的）。

## 6. 待确认内容

`verification_status = pending_confirmation` 的条目不写入正式简历（默认），而是进事实检查清单的"未写入简历的待确认内容"。

理由：没确认的内容写成事实，就是编造。用户确认对应事实后重新校验，条目会自动变成 `verified` 并进入简历。

想预览全部内容（含待确认，带【待确认】前缀）用：

```bash
jfa export <job_id> --allow-pending
```

## 7. 打招呼草稿

- 只给草稿，**不发送**。
- 由宿主 agent 撰写时可写进 `greeting`，同样受事实校验约束。
- 不给 `greeting` 时，工具用**已确认事实**生成保守版本：姓名 + 岗位 + 一条最直接的经历 + 一个诚实的追问。
- 建议 200 字以内；超长会给提示。
- 发送前让用户自己核对称呼、公司名、岗位名是否与页面一致。

## 8. 校验与保存

```bash
jfa rewrite validate <job_id> --file change_plan.json
```

- 通过 → 保存 `rewrites/<jid>/vN.json`，刷新 `latest.json`，返回 `readiness`（是否"完全定制"）。
- 不通过 → 退出码 4，列出阻断项；**先改内容**。

常见修法：

| 阻断 | 修法 |
| --- | --- |
| `verb_escalation` | 换回原文强度的动词，或补一条能证明更高职责的事实 |
| `invented_number` | 用原文数字，或删掉数字只留定性描述 |
| `skill_level_unbacked` | 删掉熟练度判断，或让用户补真实水平 |
| `no_fact_basis` | 给这条改动补 `fact_ids`，补不了就删掉 |
| `unconfirmed_as_fact` | 改状态为 `pending_confirmation`，或先确认事实 |
