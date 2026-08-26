# Tasks 阶段模板

## 输出格式要求

请按以下结构输出任务清单：

### 任务总览

- 总任务数：{{total_tasks}}
- 预估总工时：{{total_hours}} 小时
- 执行策略：{{execution_strategy}}

### 任务清单

#### Task {{task_id}}: {{task_title}}

- **描述**：{{task_description}}
- **负责 Agent**：{{assigned_agent}}
- **依赖**：{{depends_on}}
- **预估耗时**：{{estimated_hours}} 小时
- **验收标准**：
  - [ ] {{acceptance_criteria_1}}
  - [ ] {{acceptance_criteria_2}}

---

### 依赖关系图

```
{{task_1}} → {{task_2}} → {{task_3}}
                ↘ {{task_4}}
```

### 关键路径

最长依赖链：{{critical_path}}
关键路径预估耗时：{{critical_path_hours}} 小时

---
*提示：用户确认后将进入 Apply（执行落地）阶段，Supervisor 将按此计划逐个执行*
