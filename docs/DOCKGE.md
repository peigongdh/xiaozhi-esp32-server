# Home Lab 的 Dockge 部署

本部署使用上游全模块镜像，包含语音服务、智控台、MySQL 和 Redis。实施与验收状态见 [PLAN-001](PLAN-001-dockge-deployment.md)。

## 入口与配置

| 用途 | 地址或路径 |
| --- | --- |
| 智控台 | http://192.168.31.109:8002/ |
| 设备 OTA | http://192.168.31.109:8002/xiaozhi/ota/ |
| 设备 WebSocket | ws://192.168.31.109:8000/xiaozhi/v1/ |
| 视觉接口 | http://192.168.31.109:8003/mcp/vision/explain |
| Dockge | http://192.168.31.109:5001/compose/xiaozhi-esp32-server |
| 运行 Compose 与私有环境 | `/srv/homelab/stacks/managed/xiaozhi-esp32-server/{compose.yaml,.env}` |
| 持久数据根 | `/srv/homelab/data/xiaozhi-esp32-server` |
| 管理员初始凭据 | 数据根的 `admin-credentials.txt`，权限 0600 |
| 服务连接配置 | 数据根的 `server/.config.yaml`，权限 0600 |

可提交的 Compose 来源为 [deploy/compose.yaml](../deploy/compose.yaml)。`.env` 保存镜像摘要、绑定地址、数据路径和随机数据库/Redis 密码，不入库。MySQL 与 Redis 不发布宿主端口；设备鉴权保持开启，首个管理员建立后关闭后续注册。

镜像版本固定为上游 `server_0.9.8`、`web_0.9.8`、MySQL `8.4`、Redis `8.0`，实际摘要以运行 `.env` 为准。SenseVoiceSmall 模型挂载为只读，其余状态分别放在数据根的 `server`、`mysql`、`redis`、`uploadfile` 目录。

## 配置模型与设备

使用 `admin` 和私有凭据文件中的密码登录智控台。在“模型配置”填写选用的大模型服务和密钥，再核对智能体使用的模型。模型配置以智控台为准；首次部署和本次 MCP 接入验收都没有发起模型对话，不能据容器健康判定真实对话可用。

ESP32 的 OTA 地址填写上表地址；它会返回已配置的 LAN WebSocket 地址与设备令牌。在智控台创建或选择智能体，按设备激活码完成绑定。全模块的 OTA 在 8002；8003 用于视觉接口。

## Home Assistant MCP

采用项目 [HA 集成指南的方法 3](homeassistant-integration.md)，通过当前客户端原生 Streamable HTTP 连接 `http://192.168.31.207:8123/api/mcp`；端点参考 [Home Assistant 官方文档](https://www.home-assistant.io/integrations/mcp_server/)，并已从运行容器实测。无需另起 `mcp-proxy` 服务。

私有配置位于 `/srv/homelab/data/xiaozhi-esp32-server/server/.mcp_server_settings.json`，已有 data 挂载会使其在容器重建后保留。配置使用 `mcpServers.Home Assistant` 的 `url`、`transport: streamable-http` 和 `headers.Authorization`，令牌只保存在该权限 0600 的文件中。不要将 HA HTTP 地址填入智控台 `server.mcp_endpoint`，后者是另一种 WebSocket 接入机制。

当前智能体已经使用 `Intent_function_call`，本次接入保持其模型和角色配置。服务端 MCP 对所有启用工具调用的智能体生效，每个设备新连接时读取配置；已有连接需要重连。可用实体与控制范围由 HA 自身的集成配置和暴露设置决定。

上游 0.9.8 的 MCP 2.3.0 客户端有工具 schema 字段兼容错误。本仓 `mcp_client.py` 已兼容 `input_schema` 和旧 `inputSchema`；运行 `.env` 的 `MCP_CLIENT_OVERRIDE` 指向 Dockge 栈目录 `compat/<SHA-256>/mcp_client.py`，Compose 将此文件只读挂载到容器中覆盖同一模块。部署或恢复时必须同时保留这个文件及环境变量。修复源码来自 `main/xiaozhi-server/core/providers/tools/server_mcp/mcp_client.py`；更新修复时复制到新的摘要目录、修改环境变量并通过 Dockge 部署。上游镜像修复经过验证后才能移除覆盖挂载。

接入与验证结果见 [PLAN-002](PLAN-002-home-assistant-mcp.md)。

## 初始化与设备握手兼容

上游 0.9.8 在连接初始化超过 1 秒时会丢弃首次 `hello`，并把未完成的绑定查询误报为“没有找到该设备的版本信息”。该提示不一定表示版本或固件错误；已绑定设备也可能遇到。本仓修复使通过鉴权的连接立即响应 `hello`，普通业务消息按原顺序等待组件就绪；真实未绑定提示只在绑定结果明确且 TTS 通道已打开后发送。

完整初始化的超时设为 60 秒，超时后取消并清理。设备配置缺失、组件初始化失败或超时会关闭连接（WebSocket 1011），不会冒充设备未绑定；对端断开会释放等待、取消初始化并清理尚未登记的 MCP client。初始化完成日志包含 `session_id` 和 `state=ready/unbound`，失败日志包含异常类型，不输出业务密钥。

运行 `.env` 的 `CONNECTION_OVERRIDE` 与 `MCP_MANAGER_OVERRIDE` 分别指向 `compat/<SHA-256>/connection.py`、`compat/<SHA-256>/mcp_manager.py`，Compose 以只读方式覆盖相同模块。它们与上节 `MCP_CLIENT_OVERRIDE` 一起保留，不更改模型、设备或 HA 配置。源码为 `main/xiaozhi-server/core/connection.py` 和 `core/providers/tools/server_mcp/mcp_manager.py`，修复与验收见 [PLAN-003](PLAN-003-initialization-handshake.md)。

独立回归测试不读取运行配置或访问外部服务；现有 pytest 全局替换计时函数，因此使用独立 unittest：

```sh
docker run --rm --network none --read-only --tmpfs /tmp \
  -v /home/zhangpei/project/xiaozhi-esp32-server/main/xiaozhi-server:/tests-source:ro \
  -w /tmp --entrypoint python \
  ghcr.io/xinnan-tech/xiaozhi-esp32-server:server_0.9.8@sha256:cd41415f887410080dc2424b5faef651373c72fba37b3979d28e90ee548181af \
  -B /tests-source/tests/test_connection_initialization.py -v
```

## 日常操作与恢复

在 Dockge 的 `xiaozhi-esp32-server` 栈执行启动、停止、重启和查看日志。修改 Compose 时同步本仓来源；私有环境值只留运行目录。固定摘要不会自动跟随 `latest` 更新，升级须有意更新镜像与摘要并先备份数据库。

只读命令：

```sh
docker compose -p xiaozhi-esp32-server \
  --env-file /srv/homelab/stacks/managed/xiaozhi-esp32-server/.env \
  -f /srv/homelab/stacks/managed/xiaozhi-esp32-server/compose.yaml ps
```

停止栈保留持久数据。不要用删除卷或全局 prune 处理启动问题；数据库一致性备份应使用 MySQL dump，或停止栈后备份整个数据根及 Dockge 栈目录（包括 `compose.yaml`、`.env` 和 `compat/`）。此次部署没有接入定时 NAS 备份，也没有测试主机重启恢复。

Home Manager 只维护观察登记和 Dockge 入口，生命周期统一由 Dockge 管理。探活只说明管理 API 可达，不等于模型、语音或实体设备已验证。
