---
title: Agent 职责体系综述 14 - 评测与优化 Agent
source: paper-02-Agent职责体系综述.md
chapter: 十三、评测与优化 Agent：让改进围绕误差信号发生
type: 拆分扩充稿
---

# 评测与优化 Agent：让改进围绕误差信号发生

在自动评测和优化系统中，Agent 职责可以分成评测和优化两类。

评测 Agent 负责执行评测步骤、观察系统结果、判断是否符合预期。它像传感器，把系统状态转化为误差信号。

优化 Agent 负责根据失败信息定位问题、修改系统、触发下一轮评测。它像控制器和执行器的一部分，目标是减少偏差。

两类职责不能混在一起。如果优化 Agent 可以随意修改评测标准，系统就会自我欺骗。评测必须相对独立，优化才有意义。

文章中的自动评测平台展示了完整闭环：先创建评测任务，明确目标和验收标准；再创建评测集，写清步骤和预期结果；然后自动运行评测，根据失败优化，再次评测。这个循环可以持续多轮，甚至一晚上自动推进。

这里的关键不是“AI 会自动改”，而是“改动被评测信号牵引”。没有评测，优化只是猜；有评测，优化才可能收敛。

因此，评测 Agent 和优化 Agent 的职责边界，是自动改进系统的基础。

## 双 Agent 闭环图

```mermaid
sequenceDiagram
    participant Eval as 评测 Agent
    participant Harness as 评测平台
    participant Opt as 优化 Agent
    participant App as 被测系统

    Eval->>Harness: 读取评测任务和评测集
    Harness->>App: 执行步骤
    App-->>Harness: 返回 UI/API/日志结果
    Harness-->>Eval: 生成评测报告
    Eval-->>Opt: 失败 case + 证据
    Opt->>App: 修改系统
    Opt->>Harness: 请求重新评测
```

## 评测报告示例

```json
{
  "suite": "login-flow-regression",
  "case_id": "login-timeout",
  "status": "failed",
  "observed": {
    "status": 504,
    "latency_ms": 10012
  },
  "expected": {
    "status": 200,
    "latency_ms_lt": 3000
  },
  "artifacts": [
    "logs/gateway-login-timeout.log",
    "screenshots/login-timeout.png"
  ],
  "handoff_to_optimizer": {
    "suspected_area": "auth token service timeout/retry policy",
    "must_not_change": ["public API response schema"]
  }
}
```

评测报告必须比“失败了”更具体，否则优化 Agent 无法定位问题，只能随机试错。
