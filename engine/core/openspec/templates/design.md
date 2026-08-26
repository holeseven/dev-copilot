# Design 阶段模板

## 输出格式要求

请按以下结构输出架构设计：

### 1. 模块划分

> 系统/功能模块清单及各自职责

| 模块名称 | 职责描述 | 核心接口 |
|----------|----------|----------|
| {{module_name}} | {{module_responsibility}} | {{core_interface}} |

### 2. 接口定义

> 模块间关键接口描述

```
接口: {{interface_name}}
输入: {{input_schema}}
输出: {{output_schema}}
说明: {{interface_description}}
```

### 3. 数据流

> 核心数据流转路径（用文字描述或 Mermaid 图）

```
{{data_source}} → {{processing_module}} → {{output_target}}
```

### 4. 技术选型

| 决策点 | 选择 | 理由 |
|--------|------|------|
| {{decision_point}} | {{choice}} | {{reasoning}} |

### 5. 非功能性需求

- 性能：{{performance_requirement}}
- 安全：{{security_requirement}}
- 可扩展性：{{scalability_requirement}}

---
*提示：用户确认后将进入 Tasks（任务拆解）阶段*
