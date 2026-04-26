# PR: feat(context): 添加上下文架构设计和四级压缩管线

### 总结

实现完整的 Harness 上下文架构，包括**模块化提示词加载系统**和**四级上下文压缩管线**。

### 文件结构

```
01_agent module/context/
├── code/                                 # 实战代码
│   ├── .harness/                         # Harness 工作空间（自动创建）
│   │   ├── data/
│   │   │   ├── memory/                    # 持久化记忆存储
│   │   │   └── session/                   # 会话数据存储
│   │   ├── skills/                       # 技能定义
│   │   │   ├── browser/
│   │   │   ├── docx/
│   │   │   ├── pdf/
│   │   │   ├── pptx/
│   │   │   ├── skill-creator/
│   │   │   └── xlsx/
│   │   └── workspace/                    # 工作区配置
│   │
│   ├── compact/                          # 上下文压缩模块
│   │   ├── __init__.py
│   │   ├── compaction.py                # 完整压缩实现
│   │   ├── microcompact.py              # 微压缩实现
│   │   ├── models.py                    # 数据模型
│   │   ├── prompt.py                    # 压缩提示词
│   │   ├── session_memory.py           # 会话记忆压缩
│   │   ├── token_estimation.py         # Token 估算
│   │
│   ├── template/                        # 模板目录（skills）
│   │   ├── docx/
│   │   ├── pdf/
│   │   ├── pptx/
│   │   ├── skill-creator/
│   │   └── xlsx/
│   │
│   ├── .gitignore
│   ├── environment.py                   # 获取运行时环境
│   ├── prompt.py                        # 提示词构建
│   ├── runtime.py                       # Agent Loop
│   ├── session.py                       # 会话管理
│   └── requirements.txt                 # 依赖环境
│
└── harness_context.md                    # 上下文架构教程文档
```

### 教程内容
一、引言
1.1 问题背景：当上下文成为瓶颈
1.2 上下文工程的诞生
二、 Harness 中的上下文架构设计
2.1 模块化提示词加载
2.1.1 提示词组成
2.1.2 模块加载顺序
2.2 四级上下文压缩管线
2.2.1 微压缩
2.2.2 会话记忆压缩
2.2.3 完整压缩
三、在 Good Harness 中的实践:上下文架构
3.1 代码文件结构
3.2 快速启动
3.3 核心数据类定义
3.4 提示词加载
3.5 上下文压缩

---

**提交范围**: `01_agent module/context/` 目录下 158 个文件

**来源分支**: `main` (WOWCharlotte/good-harness)
**目标分支**: `main` (Fyuan0206/good-harness)

---

## 提交历史

| 类别 | 说明 |
|------|------|
| **新增** | 四级压缩管线（microcompact、session_memory、compaction）、会话管理系统、Message 数据类、Anthropic 格式支持 |
| **重构** | 移除 OpenAI 支持、简化 API 接口、移除废弃代码、调整压缩配置 |
| **文档** | Harness 上下文架构设计文档、压缩管线文档 |
