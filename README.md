# 登阶

面向上海交通大学本科生的长期目标执行助手。

核心闭环：目标拆解 / 手填 / 校园数据 → 任务池（待确认 → 已确认）→ 周视图与容量缺口 → 每日复盘 → 记账与重排；小组页在旁边看见彼此。

## 技术栈

- 后端：Python + FastAPI + SQLite (SQLModel)
- 前端：HTML + JavaScript
- 本地同步器：Python + Playwright
- 大模型：DeepSeek 官方接口

## 目录结构

```
demo3/
  backend/        FastAPI 后端
    app/
      main.py     入口，挂载路由与静态前端
      config.py   集中读取 .env
      db.py       SQLModel engine 与 get_session
  frontend/       纯 HTML/JS 前端
  data/           运行期数据（资源目录、缓存等）
  syncer/         本地 Playwright 同步器
  docs/           文档
  scripts/        启动脚本
  requirements.txt
  .env.example
```

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env          # Windows: copy .env.example .env
bash scripts/dev.sh           # Windows: powershell scripts/dev.ps1
```

启动后访问 http://127.0.0.1:8000/ 。

## 当前进度

- [x] H0 项目骨架
- [ ] H1a 数据模型 + 账号系统
- [ ] H1b DeepSeek 接口 + 降级
- [ ] H2 目标 + AI 拆解
- [ ] …后续见任务卡