# 参与贡献

感谢你帮助改进“小瑶开发助手”。项目当前定位为可信机器上的本地单用户开发助手；提交变更时，请继续维护这一安全边界。

## 开发环境

需要 Python 3.12、uv、Node.js 20.19.x、Java 17 和仓库内的 Maven Wrapper。安装 Web 与 Python 的冻结依赖后，从仓库根目录运行统一门禁：

```bash
npm ci --prefix web
uv sync --project engine --frozen --extra dev
uv run --project engine --frozen --extra dev python scripts/verify.py
```

门禁实际入口是 `python scripts/verify.py`，会依次检查 Python 锁文件、编译与测试 Engine、测试并构建 Web，以及测试 Java 服务。

## 安全开发约束

- Workspace 默认只读。只有需要验证受控修改闭环时，才显式设置 `WORKSPACE_WRITE_ENABLED=true`。
- 写入能力必须继续经过服务端提案、Diff 展示、单次人工审批、内容哈希校验、原子替换和固定测试配置；不要加入任意 shell 参数入口。
- 新增文件访问能力时，必须复用 Workspace 路径守卫，并覆盖绝对路径、父目录逃逸、symlink/junction 与竞态测试。
- 不要提交 API Key、密码、真实 `.env`、本地数据库、用户代码、内部计划或 Agent 交接材料。

## 提交与评审

请保持提交聚焦，并为修复或行为变更先增加可复现测试。Pull Request 应说明用户可见结果、安全影响和实际执行过的验证命令。涉及协议字段时，需要同时检查 Python、Java 与 Web 三端兼容性。

