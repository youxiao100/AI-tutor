# 基于学术论文深度标注 JSON 的引导式科研思维训练系统 (v2.0)

本项目实现 **引导式 AI 科研思维导师系统 v2.0**，当前包含：
1. **真实 `lis-mas-v0.1` JSON 数据清洗与冲突降级适配器** (`backend/adapter.py`)
2. **程序内置 v2.0 黄金标准 8 关训练蓝图引擎** (`backend/golden_blueprint.py`)
3. **基于 DeepSeek API 的四步逆向动态出题与单关调优引擎** (`backend/question_builder.py`)
4. **可视化出题审校与测试工作台** (`frontend/index.html` + `backend/main.py`)

---

## 本地一键启动方式

### 1. 克隆或拉取最新代码
```bash
git clone https://github.com/youxiao100/AI-tutor.git
cd AI-tutor
```

### 2. 安装 Python 依赖
（仅需轻量级 `fastapi`、`uvicorn`、`pydantic`、`python-multipart`）
```bash
pip install -r requirements.txt
```

### 3. 启动本地 Web 服务
```bash
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

### 4. 在本地浏览器打开
先设置工作台登录密码（不要写进代码或仓库）：

```bash
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD='你的密码'
```

然后访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)，使用该账号登录。DeepSeek 出题另外读取 `DEEPSEEK_API_KEY`；未配置时仍可使用内置 8 关蓝图。

### 5. 学生答题

出题工作台右上角进入「学生答题」，或直接打开 [http://127.0.0.1:8000/answer](http://127.0.0.1:8000/answer)。答题使用当前蓝图：先自由作答，未过逻辑安检就只追问一个缺口；连续两轮笼统表述后给出等权选择题。阶段产出要学生自己确认后才写入研究状态，对照阶段才展示论文锚点。不按和论文的相似度打分，这一步也不调用 DeepSeek。

---

## 工作台功能说明

- **🎯 四步动态出题引擎 & 8 关训练蓝图**：
  - 默认自动加载《面向知识熵减的智慧图书馆：Lib3范式研究》内置黄金标准 8 关蓝图。
  - 支持切换 **全流程 (1–8关) / 破题局 (1–2关) / 架构局 (3–5关) / 挑刺局 (6–7关)** 微训练切片。
  - 支持在 **第 2–7 关** 的「动态衔接插槽模拟器」中输入任意模拟学生产出，实时预览动态题干拼装，或点击 **「⚡ DeepSeek 重写本关」** 测试单关动态协同调优。
  - 点击右上角 **「DeepSeek API 动态出题测试」** 可测试由 DeepSeek 实时逆向还原全部 8 关蓝图。
- **🧹 JSON 体检、冲突拦截与引文清洗**：
  - 展示 76 项标注点位三态统计（`reported` / `not_reported` / `not_applicable`）。
  - 展示 `GATE_POINT_CONFLICT:point_33` 等路由门控与点位冲突报警及 `v2.facts` 自动回退补齐报告。
  - 对比展示 22 处含 PDF 异常断行 `\n` 的 `raw_quote` 与清洗后的 `display_quote`。
- **🛡️ 剧透防火墙 (Spoiler Guard) 与案发现场隔离**：
  - 左侧展示第 1 关允许使用的「案发现场原料（Point 01 & 06）」，右侧支持实时增删「剧透黑名单」并自动对第 1–7 关执行零剧透审计。
