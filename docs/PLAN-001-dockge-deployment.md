# PLAN-001：Dockge 全模块部署

2026-10-25，用户授权在现有 Dockge 运行本项目。

## 范围与验收

在 Ubuntu 的 Dockge 新增 `xiaozhi-esp32-server` 栈，运行语音服务、智控台、MySQL 和 Redis。配置、数据库与模型放在 `/srv/homelab` 的独立目录，凭据不入库。验证容器健康、管理端登录、OTA、WebSocket 鉴权和 Dockge 纳管，并加入 Home Manager 观察登记。真实模型对话取决于用户提供的模型配置及 ESP32 设备，不以端口通畅代替验收。

## 实施结果

已于 2026-10-25 14:28（Asia/Shanghai）完成本次部署范围。

1. 通过现有 Dockge 认证 `deployStack` 接口先部署 DB/Redis/回环管理台，再部署完整栈。Dockge 认证清单返回 `managed=true`、`status=3`（running），四个容器均为 healthy，重启策略为 `unless-stopped`。
2. 官方 `server_0.9.8` 大层下载缓慢，改经上游推荐的南京大学镜像站取得同一摘要，再核对官方引用。镜像锁定如下；具体环境值保存在运行 `.env`，无密钥入库。

   | 镜像 | 摘要 |
   | --- | --- |
   | server_0.9.8 | `sha256:cd41415f887410080dc2424b5faef651373c72fba37b3979d28e90ee548181af` |
   | web_0.9.8 | `sha256:435e04041b1cda6a980e26a188e1f44e6319b420468fd8bf62f66ecd9fb70271` |
   | mysql:8.4 | `sha256:76f0358884cb92d9a086d24e5671c38048d3fef40f2588703d73eb55392d6b69` |
   | redis:8.0 | `sha256:ae471bdc20de180beed36e347d170ec0bdf8faa327959ea1f24692f22b05b955` |

3. SenseVoiceSmall 下载自上游指定 ModelScope 地址，大小 936291369 bytes，SHA-256 为 `833ca2dcfdf8ec91bd4f31cfac36d6124e0c459074d5e909aec9cabe6204a3ea`；启动日志确认 VAD 与 ASR 初始化成功。使用默认 CPU 推理。
4. 使用正常注册、登录 API 创建唯一管理员 `admin` 并验证角色；后续用户注册关闭。管理员密码、服务连接密钥、数据库与 Redis 密码仅保存在本机私有文件，权限 0600。
5. 管理 API 保存并回读 LAN WebSocket 与 OTA 地址。验证管理页、公开配置、8000 服务探活、8003 视觉接口 GET 成功；匿名配置请求返回 API 401。临时设备 OTA 返回正确 LAN WS 地址、令牌和激活码；无令牌 WS 被拒，OTA 令牌签名与认证后的 ping/pong 通过。没有发送 hello、音频或模型请求；本次生成的两个激活缓存已精确删除，设备表未新增测试设备。
6. Home Manager 完整候选通过正式 API 校验，再单项保存并回读；登记由 56 项变为 57 项，其余登记及全局配置保持，探活 healthy。Home Manager 无需重启，生命周期仍由 Dockge 管理。

私有验证摘要：`/srv/homelab/data/xiaozhi-esp32-server/deployment-verification.json`。当前配置与操作入口见 [DOCKGE.md](DOCKGE.md)。

## 交付边界与恢复

用户已明确选择稍后自行在智控台配置模型；真实 LLM 对话、语音输出及实体 ESP32 绑定不在本次已验证范围。此次没有接入定时 NAS 备份，也未测试宿主重启。

仅操作新建的 `xiaozhi-esp32-server` 栈。停止可在 Dockge 执行 Stop；不执行删除卷或全局 prune。数据与私有配置保留，可用同一 Compose 和固定镜像重启。既有容器与其他登记保持。
