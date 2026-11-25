# TikTok Login Kit for Web

J01「管理账号」或 J02「核验账号连接」进入指定 TikTok 账号的「只读连接」。服务器为该租户、项目和账号分配了预期 open_id 后，拥有审阅权限的操作员可点「前往 TikTok 授权」。浏览器前往官方页面，由账号本人确认。返回工作台后可以查看授权状态、刷新凭据、核验账号及视频列表读取能力，或者解除授权。授权请求仅包含 `user.info.basic`、`video.list`。本功能不申请上传、发布或消息权限。

## 部署

1. 在 TikTok for Developers 登记 Web 应用并开通 Login Kit、Display API 和上述读取权限。将静态 HTTPS 回调准确登记为 `https://<工作台域名>/api/v1/production/tiktok/login/callback`。不要加入查询、动态路径或片段。
2. 安装 `pip install -r requirements-tiktok-login.txt`。容器构建使用 `docker build --build-arg ENABLE_TIKTOK_LOGIN=true .`。该安装锁定 cryptography 50.0.0 与其依赖。已存在的环境令牌映射仍支持原来的基础安装。
3. 在服务环境注入 `AI_SHIP_TIKTOK_CLIENT_KEY`、`AI_SHIP_TIKTOK_CLIENT_SECRET`、`AI_SHIP_TIKTOK_REDIRECT_URI` 和 `AI_SHIP_TIKTOK_FERNET_KEY`。后者是 Fernet 的 32 字节 URL-safe Base64 密钥，可由 `cryptography.fernet.Fernet.generate_key()` 在密钥管理流程中生成。将密钥与 SQLite 数据库分开保存并分别备份；不要在浏览器、普通项目记录、命令参数、镜像层或记录输出的构建步骤中填写密钥。
4. 单独设置 `AI_SHIP_TIKTOK_LOGIN_ACCOUNTS`，绑定已登记的应用内 TikTok open_id。在多个 TikTok 应用之间，open_id 的作用域不同。下面内容是配置格式样例。

```json
[{"tenant_id":"team-id","project_id":"project_id","account_id":"account_id","open_id":"expected-app-specific-open-id"}]
```

TLS 终止在反向代理时，仅信任属于该代理的转发地址，并保留工作台的真实 HTTPS scheme 和 Host。在代理、CDN、WAF、APM 和错误跟踪中关闭该回调 URI 的查询串采集和请求/响应体记录。仓库默认 Uvicorn 访问日志对该端点屏蔽整个查询串，响应设置 `no-referrer`，完成处理后立即转回工作台。密钥本身或授权响应不会写入普通业务记录。

## 授权生命周期

- 单次授权 state 及独立浏览器绑定均由服务端随机生成。库内保存摘要；安全的 HttpOnly、SameSite=Lax 浏览器 cookie 用于跨站返回的顶层导航。state 十分钟到期，回调原点、浏览器、初始操作者的当前权限、预期项目账号和授权代次须一致。已消费的 state 不可再次交换。
- 平台回传的 open_id 与精确的只读授权范围匹配后，才在服务器储存访问和刷新凭据。Fernet 同时保护密文内容及完整性，密文内包含租户、项目、账号、应用与回调绑定。到期时间取自真实平台响应。操作代次与状态历史单独保留，各版凭据不得经 API 导出。
- 既有指标读取器优先使用托管授权。访问令牌将到期时在下一次读取前轮换，也可手动刷新。轮换操作带版本校验，同时到达的操作只有一项能向平台发出有效刷新请求。服务端原子存储平台返回的新访问和刷新凭据。刷新回执不明时暂停读取，不自动重放可能已失效的旧刷新令牌；可重新取得本人授权。
- 解除授权时先阻断本地读取，再向官方 revoke 接口提交当前访问令牌。收到成功的空回执后清除本地密文。平台结果不明时显示「撤销结果待核对」，保持读取暂停且允许重试。如密钥遗失或客户端撤销/旋转结果因故无法在本地核对，还可从账号本人 TikTok 的已连接应用中解除授权。轮换加密密钥前应以旧密钥解除现有授权并重新配置。
- 未进入托管授权的账号继续使用已有 `AI_SHIP_TIKTOK_ACCOUNTS` 和 `AI_SHIP_TIKTOK_TOKEN_…` 管理方式。已建立托管授权状态的账号在暂停、失效或解绑时不会回落到旧环境令牌。既有连接的「暂停读取」是本地采集开关，可通过单独的连接核验恢复。

## 验证

`python -m unittest tests.test_tiktok_login tests.test_tiktok_metrics` 使用回环 HTTP 协议桩。运行 `python scripts/browser_tiktok_login.py --output /tmp/tiktok-login-browser` 可在本机临时 HTTPS 站点中，以真实 Chromium 完成正常账号入口、授权重定向、受保护 cookie、回调、刷新和撤销。测试在访问公网之前拦截授权页，令牌服务位于回环地址。生产账号仍须使用经过平台审核的应用和真实授权单独联调。

官方资料：[Web Login Kit](https://developers.tiktok.com/doc/login-kit-web/)、[令牌生命周期](https://developers.tiktok.com/doc/oauth-user-access-token-management/)、[获取](https://developers.tiktok.com/docs/en/oauth-get-a-user-access-token)、[刷新](https://developers.tiktok.com/docs/en/oauth-refresh-a-user-access-token)、[撤销](https://developers.tiktok.com/docs/en/oauth-revoke-a-user-access-token)。
