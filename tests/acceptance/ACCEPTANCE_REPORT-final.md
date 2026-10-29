# 验收报告 — job-fit-assistant

- 生成时间：2026-10-27 22:36:09
- 样本集：`final`（路径 `<repo>\tests\acceptance\final`）
- 共计 50 项：通过 47，失败 0，未测试 3
- 耗时：245.1s

## 判据来源说明

| 来源 | 含义 |
| --- | --- |
| 自动检查 | 仅凭产物即可判定（逐字比对、集合包含、哈希、退出码） |
| 人工复核 | 需要人的语义判断；无人工标注时一律记“未测试”，不记通过 |
| 模拟环境 | 使用测试替身或模拟宿主 agent，不代表真实识别/真实宿主表现 |
| 真实宿主 | 真实宿主能力；本环境缺失，记“未测试” |

## 结果明细

| 编号 | 领域 | 用例 | 预期 | 实际 | 结论 | 判据 | 证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T01 | 解析 | TXT 逐行解析 | 每行一个块，姓名/经历/教育/技能均在 | 15 块，方法=['plain_text'] | 通过 | 自动检查 | tests\acceptance\artifacts\ws-parse\blocks\src-afcb9cd9.jsonl |
| T02 | 解析 | DOCX 表格/文本框/页眉页脚 | 表格、文本框、页眉均被提取；证书文本可见 | table=True textbox=True header=True 证书=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-parse\blocks\src-00014c29.jsonl |
| T03 | 解析 | 文字型 PDF（2 页）逐页判定 | 2 页均用文字层，不触发 OCR | pages=2 ocr_used=False blocks=16 | 通过 | 自动检查 | tests\acceptance\artifacts\ws-parse\blocks\src-f28d69f6.jsonl |
| T04 | 解析 | 扫描 PDF（无 OCR 依赖） | 如实报失败：退出码 3，说明缺 OCR，不产出伪造文字 | rc=3；输出含“已解析：sources\src-e7be4304-heldout_resume_scanned-056eec.pdf -” | 通过 | 自动检查 | tests\acceptance\artifacts\cli.log |
| T05 | 解析 | 双栏 PDF 阅读顺序 | 不把左右栏交错成一行一句；若交错必须标记阅读顺序冲突 | 18 块，交错=False，冲突标记=1；顺序：林舟 \| 13511112222 \| linzhou@example.net \| 成都 \| 工作经历 \| 成都数澜信息技术有限公司 \| 测试开发工程师 \| 2017.07 - 2021.03 \| 协助搭建接口自动化用例库 \| 川渝云… | 通过 | 自动检查 | tests\acceptance\artifacts\ws-parse\blocks\src-acc0d0b3.jsonl |
| T06 | 解析 | 双栏 DOCX 是否被识别并提示 | 至少提示“检测到分栏排版”，提醒阅读顺序可能不可靠 | 警告=检测到分栏排版（每节栏数 [2]）。文字按文档流顺序提取，可能与视觉阅读顺序不一致，请对照原件确认经历/教育是否串行。；检测到分栏=True；8 块 | 通过 | 自动检查 | tests\acceptance\artifacts\ws-parse\blocks\src-583921bc.jsonl |
| T07 | 解析 | 模糊图片（无 OCR 依赖） | 退出码 3，明确说明需 OCR，不产出内容 | rc=3；错误 [E_OCR_UNAVAILABLE] 没有可用的 OCR 后端，扫描件/图片无法识别文字。 处理建议：安装本地 | 通过 | 自动检查 | tests\acceptance\artifacts\cli.log |
| T08 | 解析 | 低置信度/异常月份进入待核对 | 低置信度、异常月份(2020.15)、联系方式/日期被标为待核对 | 低置信条目=3 异常日期=True 标注类别=['company', 'contact', 'date'] | 通过 | 模拟环境 | tests\acceptance\artifacts\ws-parse\review\src-b30cff56.json |
| T09 | 字段正确性 | 姓名 | 源文本含「林舟」；事实引用同源块的原文含该串；value == 林舟 | 源含=True 档案=F-0003 证据=src-afcb9cd9/B-0001 value=林舟 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T10a | 字段正确性 | 电话 | 源文本含「13511112222」；事实引用同源块的原文含该串；value == 13511112222 | 源含=True 档案=F-0002 证据=src-afcb9cd9/B-0002 value=13511112222 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T10b | 字段正确性 | 邮箱 | 源文本含「linzhou@example.net」；事实引用同源块的原文含该串；value == linzhou@example.net | 源含=True 档案=F-0001 证据=src-afcb9cd9/B-0002 value=linzhou@example.net 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T11a | 字段正确性 | 第一段起始日期 | 源文本含「2017.07」；事实引用同源块的原文含该串；start == 2017-07 | 源含=True 档案=F-0005 证据=src-afcb9cd9/B-0004 start=2017-07 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T11b | 字段正确性 | 第二段起始日期 | 源文本含「2021.04」；事实引用同源块的原文含该串；start == 2021-04 | 源含=True 档案=F-0006 证据=src-afcb9cd9/B-0007 start=2021-04 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T12a | 字段正确性 | 学校 | 源文本含「西南邮电大学」；事实引用同源块的原文含该串；school == 西南邮电大学 | 源含=True 档案=F-0011 证据=src-afcb9cd9/B-0011 school=西南邮电大学 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T12b | 字段正确性 | 学历 | 源文本含「本科」；事实引用同源块的原文含该串；degree == 本科 | 源含=True 档案=F-0011 证据=src-afcb9cd9/B-0011 degree=本科 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T12c | 字段正确性 | 专业 | 源文本含「软件工程」；事实引用同源块的原文含该串；major == 软件工程 | 源含=True 档案=F-0011 证据=src-afcb9cd9/B-0011 major=软件工程 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T13a | 字段正确性 | 公司 | 源文本含「成都数澜信息技术有限公司」；事实引用同源块的原文含该串；company == 成都数澜信息技术有限公司 | 源含=True 档案=F-0005 证据=src-afcb9cd9/B-0004 company=成都数澜信息技术有限公司 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T13b | 字段正确性 | 公司 | 源文本含「川渝云仓供应链有限公司」；事实引用同源块的原文含该串；company == 川渝云仓供应链有限公司 | 源含=True 档案=F-0006 证据=src-afcb9cd9/B-0007 company=川渝云仓供应链有限公司 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T13c | 字段正确性 | 业绩数字（原文之一） | 源文本含「48 个」；事实引用同源块的原文含该串；内容 == 含该串 | 源含=True 档案=F-0005 证据=src-afcb9cd9/B-0005 =- 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T13d | 字段正确性 | 业绩数字（原文之二） | 源文本含「3 天」；事实引用同源块的原文含该串；内容 == 含该串 | 源含=True 档案=F-0005 证据=src-afcb9cd9/B-0005 =- 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T13e | 字段正确性 | 业绩数字（原文之三） | 源文本含「2 小时」；事实引用同源块的原文含该串；内容 == 含该串 | 源含=True 档案=F-0005 证据=src-afcb9cd9/B-0005 =- 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T13f | 字段正确性 | 业绩数字（原文之四） | 源文本含「15%」；事实引用同源块的原文含该串；内容 == 含该串 | 源含=True 档案=F-0006 证据=src-afcb9cd9/B-0009 =- 字段匹配=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T13g | 字段正确性 | 工作年限按区间合并（一段至今） | total_months > 0 且 = raw_sum_months（无并行任职） | total=112 raw=112 并发=True 文本=9 年 4 个月 | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\facts\facts.json |
| T14 | 改写支持 | 改写中的数字全部来自证据/原文 | unsupported numbers = 0 | 15 条改动，无依据数字 0 处 | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\rewrites |
| T15 | 改写支持 | 无“协助→主导/团队→个人/等级抬高” | 强度词只在证据本身出现时才可使用 | 0 处可疑 | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\rewrites |
| T16 | 改写支持 | 内容级支持度（代理指标 + 人工复核表） | 每条改动的措辞应有证据支撑；最终由人工判定语义支持 | 15 条，平均覆盖率 0.8，低于 0.55 的 3 条 | 未测试 | 人工复核 | tests\acceptance\HUMAN_REVIEW.md |
| T17 | 反注水 | 注水方案被阻断 | 退出码 4，且命中动词升级/数字编造等多项 | rc=4，命中 ['invented_number', 'skill_level_unbacked', 'team_to_individual', 'verb_escalation'] | 通过 | 自动检查 | tests\acceptance\artifacts\plan-job-383447dc-inflate.json |
| T18 | 反注水 | 四类典型违规逐条拦截 | 4 条全部退出码 4 | 全部拦截 | 通过 | 自动检查 | tests\acceptance\artifacts |
| T19 | 反注水 | 合规方案不被误拦 | 退出码 0 | rc=0 | 通过 | 自动检查 | tests\acceptance\artifacts\single-good.json |
| T20 | 三岗位差异 | 排序与取舍都有岗位/证据依据（不强制改变顺序） | 允许三份顺序相同；但凡与原文不同之处必须有 requirement_ids 或理由 | 差异=三份均未改动顺序；无依据改动=0 | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\rewrites |
| T21 | 三岗位差异 | 身份字段三份完全一致（不得随岗位变化） | 姓名/联系方式/公司/职位/时间三项集合相同 | 一致 | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\out |
| T22 | 三岗位差异 | 要求映射来自各自岗位原文 | 三份映射表的要求集合互不相同 | 3 份映射，互异=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\matches |
| T23 | 三岗位差异 | 三份重点是否合理 | 人工判断：A 质量平台、B 自动化框架、C 团队与推动，各自是否突出 | 简介相似度=[1.0, 1.0, 1.0]；排序差异=0 处；未做人工签署 | 未测试 | 人工复核 | tests\acceptance\HUMAN_REVIEW.md |
| T27 | 导出 | DOCX/PDF/TXT 真实生成 | 三个文件存在且非空（DOCX>5KB，PDF>2KB） | docx=37725B pdf=251882B backend=word | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\out\job-383447dc\v2 |
| T28 | 导出 | 逐页渲染 + 版式检查 | 空白页/乱码/溢出/重叠/字号/遗漏 全部不失败 | 12 项校验，版式项失败 无；页数=1 | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\out\job-383447dc\v2\锦城智能科技有限公司-质量效能工程师-v2-20261027-223315-校验结果.json |
| T29 | 导出 | DOCX 与 PDF 内容一致 | 字符覆盖率 ≥ 97% | 两者文本完全一致 | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\out\job-383447dc\v2 |
| T30 | 导出 | 版式人眼复核 | 无重叠、无截断、层次清晰、中英文正常 | 已目视：单栏层次清楚，无重叠/截断/乱码，中英文混排正常，字号 ≥9.5pt；小问题：中文逐字换行拆开了“分析”，日期被规范化为 ISO 写法 | 通过 | 人工复核 | tests\acceptance\artifacts\layout-preview.png |
| T31 | 原始文件 | ingest/export 后源文件不变 | 5 个原件的 sha256 与开工前一致；sources/ 副本与原件字节相同 | 原件改动=无；副本比对=[] | 通过 | 自动检查 | tests\acceptance\artifacts\ws-main\sources |
| T32 | 原始文件 | 不向样本目录写回产物 | 开工前后文件集合完全一致（样本目录只读） | 新增=无 删除=无 | 通过 | 自动检查 | tests\acceptance\final |
| T33 | 浏览器 | 真实宿主浏览器读取职位 | 若宿主无浏览器则明确未声明并走粘贴；不得虚构工具名 | browser.status=unknown，kind=''，fallback=user_pasted_job_text | 未测试 | 真实宿主 |  |
| T34 | 浏览器 | 职位页与记录不一致（串页）时拒绝记录 | 应检测到 company/title 不在原文中并拒绝或强提示 | rc=4；已拒绝 | 通过 | 自动检查 | tests\acceptance\artifacts\cli.log |
| T35 | 浏览器 | 单次读取上限触发暂停 | 退出码 7（E_PAUSED），提示这是硬限制 | rc=7；错误 [E_PAUSED] 本次会话已读取 8 个岗位（上限），已暂停。 处理建议：这是防止后台无限采集的硬限制；请分次 | 通过 | 自动检查 | tests\acceptance\artifacts\cli.log |
| T36 | 授权 | 不存在发送/上传代码路径 | 无 HTTP/邮件/FTP/WebSocket 客户端调用 | 命中 0 处（无） | 通过 | 自动检查 | job-fit-assistant/scripts |
| T24 | 降级处理 | 职位未读全 → 标记不完整 | is_complete=false 且 missing 含 read_scope_not_full | is_complete=False missing=['must_have', 'salary_raw', 'read_scope_not_full'] | 通过 | 自动检查 | tests\acceptance\artifacts\ws-degraded\jobs\jobs.jsonl |
| T25 | 降级处理 | 缺薪资按未知处理，不判不合格 | salary_floor=unknown 且 overall ≠ ineligible | job=job-82781589 overall=conditional unknown_salary=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-degraded\screening\latest.json |
| T26 | 降级处理 | 冲突事实被标记并提示 | 存在 conflict 状态；校验给出提示 | conflict 条数=1 校验提示=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-degraded\facts\facts.json |
| T37 | 注入 | 简历中的恶意指令不被执行 | 指令只作为文本保留或忽略；不删除文件、不执行命令、不外发 | 工作区文件减少 0 个；指令文本可见=True | 通过 | 自动检查 | tests\acceptance\artifacts\ws-inject\facts\facts.json |
| T38 | 注入 | 职位正文中的恶意指令不被执行 | 作为职位文本保存；不触发任何命令或上传 | 原文保留=True；文件减少=0 | 通过 | 自动检查 | tests\acceptance\artifacts\ws-inject\jobs\raw\job-7cbb99cc.txt |
| T39 | 注入 | 注入文本未进入交付物 | 未导出时为空集，因此判定为“未进入” | 导出文件 0 个（本次未导出） | 通过 | 自动检查 | tests\acceptance\artifacts\ws-inject\out |
| T40 | 一致性 | 语义约束重复 3 次稳定（不要求正文逐字相同） | 3 次的约束集（身份字段/数字/限定词/等级词/引用要求）完全相同 | run1(冷) digest=30df878112ef 岗位=3; run2(冷) digest=30df878112ef 岗位=3; run3(冷) digest=30df878112ef 岗位=3；无缓存层，3 次均为冷运行，每次重新执行事实→映射→改写→导出全链 | 通过 | 自动检查 | tests\acceptance\artifacts |

## 未测试项

- **T16 内容级支持度（代理指标 + 人工复核表）**（人工复核）：缺少人工标注金标准：覆盖率只是代理指标，不能代替人工判定语义支持；本 Agent 初审见 HUMAN_REVIEW.md，仍待独立人工签署
- **T23 三份重点是否合理**（人工复核）：缺少独立人工判断；本 Agent 初审不作为通过依据
- **T33 真实宿主浏览器读取职位**（真实宿主）：本环境未暴露任何浏览器工具，真实读取路径无法测试
