# 引导式科研思维训练系统

整个流程分成两块，共用一次登录。题库负责把论文变成可审校的关卡；答题只读取这份题库，不另出一套题。

```text
论文标注 JSON
      │
      ▼
后端题库   backend/question_bank
      │     8 关训练蓝图，在 / 审校
      ▼
前端答题   frontend/answering
            /answer 按蓝图作答，确认后的产出进入下一关
```

| | 后端题库 | 前端答题 |
|---|---|---|
| 做什么 | 清洗论文 JSON，生成并审校 8 关蓝图 | 学生按当前蓝图作答 |
| 代码 | `backend/question_bank/` | `frontend/answering/answer.html`、`backend/answering.py` |
| 页面 | [http://127.0.0.1:8000](http://127.0.0.1:8000) | [http://127.0.0.1:8000/answer](http://127.0.0.1:8000/answer) |
| 输入 | `data/lib3_sample.json` 或上传的标注 JSON | 题库里当前这篇论文的蓝图 |
| 不做什么 | 不保存学生答卷 | 不调用 DeepSeek，不按和论文的相似度打分 |

登录密码只来自环境变量 `WORKSHOP_PASSWORD`，不要写进代码或仓库。

---

## 本地启动

```bash
git clone https://github.com/youxiao100/AI-tutor.git
cd AI-tutor
pip install -r requirements.txt
export ADMIN_USERNAME=admin
export WORKSHOP_PASSWORD='你的密码'
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

DeepSeek 出题另外读取 `DEEPSEEK_API_KEY`。没配置时，题库使用内置 8 关蓝图，答题不受影响。

---

## 1. 后端题库

目录：`backend/question_bank/`

- `adapter.py`：把 `lis-mas-v0.1` 标注 JSON 清洗成统一文档，拦截门控和点位冲突。
- `golden_blueprint.py`：内置《Lib3 范式研究》8 关黄金蓝图。离线可用。
- `question_builder.py`：四步逆向出题。配置了 `DEEPSEEK_API_KEY` 时可以重写整套或单关。
- `models.py`：文档、蓝图、选项和安检门的数据结构。
- `cached_lib3_blueprint.json`：默认加载的蓝图缓存。
- 样例输入在 `data/lib3_sample.json`。

审校页面是 `frontend/question_bank/index.html`。右上角可以进入前端答题。

题库接口（都要先登录）：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/state` | 当前论文和蓝图 |
| POST | `/api/upload-json` | 上传标注 JSON 并适配 |
| POST | `/api/generate-blueprint` | 用 DeepSeek 生成 8 关 |
| POST | `/api/regenerate-stage` | 重写单关 |
| POST | `/api/save-stage` | 保存审校后的一关 |
| POST | `/api/update-blacklist` | 修改剧透黑名单并复查 |
| POST | `/api/reset-golden-blueprint` | 恢复内置黄金蓝图 |

题库页面上可以：

- 切换全流程、破题局（1–2 关）、架构局（3–5 关）、挑刺局（6–7 关）。
- 在第 2–7 关用模拟学生产出预览动态题干，或重写单关。
- 查看 76 个点位的三态统计、门控冲突，以及清洗前后的引文。
- 编辑剧透黑名单，审计第 1–7 关是否泄漏专有名词。

## 2. 前端答题

页面：`frontend/answering/answer.html`  
会话逻辑：`backend/answering.py`，只消费题库给出的 `TrainingBlueprint`。

作答规则：

- 先自由作答。没过本关逻辑安检时，只追问一个缺口。
- 连续两轮笼统或重复，才给出蓝图里的等权选项，没有标准答案。
- 阶段产出要学生自己确认，才写入研究状态，并填进下一关题干。
- 论文标题和锚点在对照之前不出现。方向与论文不同时，对照论证骨架，不要求改成论文结论。
- 报告按每关安检过没过给 0–3 的描述，不按相似度打分。

答题接口（都要先登录）：

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/answer` | 当前答题进度；还没开始时返回可选切片 |
| POST | `/api/answer/start` | 选择全流程或一个微训练切片 |
| POST | `/api/answer/submit` | 提交自由作答、追问或理由 |
| POST | `/api/answer/choose` | 选择一个等权方向 |
| POST | `/api/answer/inspiration` | 第 3、5 关抽取灵感卡片 |
| POST | `/api/answer/confirm` | 确认本关产出 |
| POST | `/api/answer/acknowledge` | 看完对照，进入下一关或生成报告 |
| POST | `/api/answer/reset` | 回到选题 |

设计说明仍在 `docs_v2_0.md`。上面两块是现在仓库里的实现边界。
