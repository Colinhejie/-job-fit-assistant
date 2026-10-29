# 宿主适配

## 1. 铁律：不虚构工具名

本 Skill **不提供**通用的浏览器工具名。不同宿主暴露的能力完全不同：

- 有的给 `browser_navigate` + `browser_read_page`；
- 有的只有 Playwright MCP；
- 有的只能让用户粘贴；
- 有的给的是"打开 URL 并返回正文"的单一封装。

所以流程是：**宿主先照自己的真实工具清单声明，再按声明结果执行**。

```bash
python scripts/jfa.py --home <ws> host declare \
  --browser-kind <类型> \
  --tool <工具名> [--tool <工具名> ...] \
  --login-scope <local|cloud|none> \
  --notes "<补充说明>"
```

`browser-kind` 建议取值（按实际情况选，不要硬套）：

| 值 | 含义 |
| --- | --- |
| `host-tool` | 宿主内置的浏览器工具 |
| `in-app-browser` | 应用内嵌浏览器，用户在其中登录 |
| `playwright` | Playwright MCP 之类的自动化驱动 |
| `extension` | 浏览器扩展代理 |
| `none` | 确实没有，走粘贴 |

## 2. 三种典型接法

### A. 宿主有浏览器工具

1. `jfa scan` → 浏览器一项显示"未声明"；
2. `jfa host declare --browser-kind host-tool --tool <真实工具名> --login-scope local`；
3. 用宿主工具打开用户给的链接，**请用户自己完成登录**；
4. 每次导航后核对职位、公司、地点；
5. 把页面正文交给 `jfa job add --source-kind host_browser --url ... --host-tool <工具名>`；
6. 读不全就 `--read-scope partial`；
7. 登录失效/验证码 → `jfa job session --status paused` 并告知用户。

### B. 宿主用 Playwright 之类的自动化

同样的流程，区别在于：

- 你的工具名写进 `--host-tool`；
- 不注入用户 Cookie，不保存 `storageState` 到工作区；
- 不写死坐标、不用固定延时模拟点击绕过风控；
- 遇到反自动化拦截就停，报告"该站点限制访问"。

### C. 没有浏览器

直接说清，不要暗示"我可以试试"：

> 当前宿主没有可用的浏览器工具。请把职位页面内容复制粘贴给我（或另存为文本文件），我用同一套流程处理。

然后 `--source-kind user_paste`。这条路的功能不打折，只是多一步复制。

## 3. 能力声明与执行的一致性

| 声明 | 可实现 | 不可实现 |
| --- | --- | --- |
| 有工具名 + `local` 登录 | 读本机浏览器已登录页面 | 读云端浏览器会话 |
| 有工具名 + `cloud` 登录 | 读云端会话 | 声称"我看到你本机的页面" |
| 无工具名 | 粘贴文本 | 任何自动读取 |

**不要**在没声明的情况下调用任何"看起来像浏览器"的工具。

## 4. 职位读取的可移植写法

无论哪个宿主，最终都要落到这些字段：

```
company, title, location, url, raw_text, read_scope, host_tool, session_id, index
```

建议宿主侧的处理顺序：

1. 打开用户指定 URL；
2. 等页面稳定（用宿主自身的等待能力，不用固定 sleep 数值）；
3. **优先取结构化文本**（DOM 文本 / 可读文本导出）；
4. 结构化文本拿不到关键栏目时，才考虑截图识别（并说明这是降级路径）；
5. 核对职位名、公司名、地点三项是否与用户给的一致；
6. 调用 `jfa job add`。

## 5. 会话与限流

- 每个 `session_id` 有独立游标，`jobs/session.json` 记录已抓岗位与最后索引。
- 达到 `max_jobs_per_run` 会 `E_PAUSED`。这是**设计上的刹车**，不是 bug。
- 暂停原因必须写清（`--reason`），因为恢复时要靠它判断是"读完了"还是"被打断了"。

## 6. 兼容性矩阵

| 宿主 | 浏览器 | 本地脚本 | DOCX | PDF | OCR | 验证状态 |
| --- | --- | --- | --- | --- | --- | --- |
| 本仓库测试环境（Windows / DeepSeek Harness CLI 工具） | 无（走粘贴） | ✔ | ✔ | ✔（builtin） | ✘ 未安装 | **已实测** |
| 任意有 Python 3.10+ 的宿主 | 视宿主 | ✔ | ✔ | 视后端 | 视安装 | 未验证 |
| 带浏览器工具的宿主 | 需宿主声明 | ✔ | ✔ | 视后端 | 视安装 | **未验证**（本环境无浏览器工具可测） |
| 云端浏览器宿主 | 需宿主声明 | ✔ | ✔ | 视后端 | 视安装 | **未验证** |

**没有实测过的组合，一律标"未验证"**，不要写成"支持"。
