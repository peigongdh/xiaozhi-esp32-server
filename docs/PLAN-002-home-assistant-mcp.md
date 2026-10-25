# PLAN-002：Home Assistant MCP 接入

2026-10-25，用户授权为现有小智服务接入 NAS 上已安装的 Home Assistant MCP，提供 LAN 地址与长期访问令牌。

范围：验证实际 MCP 端点，将鉴权配置写入已挂载的私有 `server/.mcp_server_settings.json`，核对现有智能体函数调用模式，用运行容器的实际 MCP 客户端验证初始化与工具发现。验证不控制设备，不发送模型对话，不修改 HA 集成或实体暴露范围。

实施中确认 HA 2026.8.3 认证通过，`/api/mcp` 初始化和工具发现成功，但现有小智 0.9.8 转换工具定义时读取旧字段 `Tool.inputSchema`，实际 MCP SDK 使用 `input_schema`，导致无法注册工具。已通过下述新旧字段兼容修复解决，并以只读挂载固定修复文件部署；保留其余镜像内容。当前唯一智能体已使用 `Intent_function_call`，无需修改它或模型配置。

已完成：

- `mcp_client.py` 兼容 MCP 2.3.0 的 `input_schema` 和旧 `inputSchema`；使用真实 SDK Tool 验证嵌套参数、必填列表及额外属性约束保留，旧对象和空 schema 也通过。
- 私有 MCP 配置已保存，使用原生 Streamable HTTP、`/api/mcp` 与 Bearer 鉴权，权限 0600；无需新增服务或 `mcp-proxy`。
- 固定补丁 SHA-256：`abd9a9035595d493f87354dff6b515c29c34e6c1064bd3035f3cfa486ef9968e`。运行 `.env` 指向持久栈目录的同摘要文件，通过 Compose 只读挂载；Dockge 部署成功。
- 从重建后的容器使用实际 `ServerMCPManager` 读取持久配置，初始化、完整工具发现及注册成功，共 16 个工具，确认没有剩余分页。包含 `GetLiveContext`、`HassTurnOn`、`HassTurnOff`、`HassClimateSetTemperature`、窗帘、待办与扫地机相关工具。
- 此次不执行 `tools/call`，不发送真实模型对话或家居控制。现有智能体已经启用函数调用，未修改智能体、设备、聊天历史、LLM 或 HA 实体暴露设置。

验收摘要保存于 `/srv/homelab/data/xiaozhi-esp32-server/home-assistant-mcp-verification.json`，恢复前配置保存在同数据根的 `mcp-setup-20261025/`。运行说明见 [DOCKGE.md](DOCKGE.md)。本次接入范围已完成，真实语音控制未验证。

恢复：移走新增私有 MCP 配置并使设备重新连接即可撤销 HA 接入。兼容修复通过 `MCP_CLIENT_OVERRIDE` 指定的只读文件挂载；撤销补丁需恢复此次私有备份中的 Compose/.env 并仅重建语音服务。数据库、智能体和模型密钥不改动。
