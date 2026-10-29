# 发布前检查清单（v1.0.0-rc.1）

> 本文件记录「这个仓库现在能不能公开」的判断依据与操作步骤。发布完成后可删除。

## 1. 已清理（相对初始目录）

| 处理 | 对象 | 原因 |
|---|---|---|
| 移出仓库 | `杭州电商运营岗位清单_梁嘉俊.xlsx` | 测试期真实求职数据 + 姓名 |
| 移出仓库 | `make_job_list.py` | 生成上述文件的脚本，含本机路径 |
| 删除 | `jfa-com.log` | 本机报错残留（`No module named 'pythoncom'`） |
| 不发布 | `.venv-ocr/`（370 MB） | Python 3.11 虚拟环境，`.gitignore` 已排除 |
| 不发布 | `tests/acceptance/artifacts/`（5.1 MB） | JSON 内含 `C:\Users\...` 绝对路径 |
| 不发布 | `review/`、`tests/acceptance/heldout|final/` | 过程材料，README 已改为“未随仓库发布” |
| 不发布 | 405 个 `__pycache__/*.pyc` | 两套解释器字节码 |

移出的两个文件存放在仓库同级的 `boss-skill-test-artifacts\` 目录，保留以便复现，不在仓库内。

## 2. 已修正

- **4 处死链**：`review/README.md`、`review/05-逐条语义审计.md`、`tests/acceptance/OCR-REAL-RESULTS.md`（README 2 处 + TESTING 2 处）→ 改为“属本地过程材料，未随仓库发布”，不再指向被 `.gitignore` 排除的文件。
- **版本号**：`v1.0.0-internal` → `v1.0.0-rc.1`；CHANGELOG 三个“未发布”标题改为预发布轮次，顶部还原为 `1.0.0-rc.1`。
- **版权人**：`job-fit-assistant contributors` → `梁嘉俊`。

## 3. 发布前仍需你确认（2 项）

1. **`review/` 是否确为合成数据**：该目录已被排除，但如果以后要放行，请先逐页确认无真实简历、真实联系方式。
2. **CI 是否要开**：建议加 `.github/workflows/ci.yml` 跑 `python tests/test_units.py`（纯逻辑，不需要网络与 OCR）。若要开，注意 `requirements.txt` 装完才能跑端到端。

## 4. GitHub 发布步骤

```bash
git init
git add -A
git status --short            # 确认没有 xlsx / .venv-ocr / __pycache__ / acceptance artifacts
git commit -m "chore: release v1.0.0-rc.1 (preview)"
git branch -M main
git remote add origin <你的仓库地址>
git push -u origin main        # 这一步由你执行，本清单不代为推送
```

## 5. Skill 市场发布步骤（Claude Skills / Agent Skills 类）

市场要的是 **skill 目录的 zip**，不是整个仓库：

```
job-fit-assistant-v1.0.0-rc.1.zip
└── job-fit-assistant/
    ├── SKILL.md            # frontmatter: name / description / license
    ├── references/         # 13 份规则
    ├── scripts/            # 纯 Python，无 .pyc
    ├── assets/  evals/
```

- 打包来源：`release/` 下的 staging 目录，已剔除 `__pycache__`。
- 依赖需用户自行 `pip install -r scripts/requirements.txt`（市场不会代装）。
- 不建议投 Smithery / MCP 市场：本包不是 MCP server；Coze/Dify 插件格式也不适用。

## 6. 必须随发布保留的声明（不要删）

README 首屏的这句是发布诚信的基础，也是这个项目的卖点：

> 未验证、且是"完整版"发布阻断项：真实宿主浏览器、简历语义质量。

因此对外口径统一为 **pre-release / preview**，不要宣称 1.0 正式版或生产可用。

## 7. 体积参考

- 仓库（按 `.gitignore` 实际入库）：约 8 MB
- skill 包（去 `.pyc`）：62 个文件、0.5 MB
