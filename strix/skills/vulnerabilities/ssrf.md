---
name: ssrf
description: SSRF 安全测试技能
---

# SSRF / 服务器端请求伪造

服务器端请求伪造会让服务器去访问攻击者无法直达的网络和服务。重点关注云元数据端点、服务网格、Kubernetes 和协议滥用，把一次普通抓取变成凭据获取、横向移动，甚至在某些情况下演变为 RCE。

## 攻击面

**范围**
- 对外发起 HTTP/HTTPS 请求的功能（代理、预览器、导入器、webhook 测试器）
- 通过 URL handler 触发的非 HTTP 协议（gopher、dict、file、ftp、smb 封装器）
- 通过网关和 sidecar 的服务间跳转（envoy/nginx）
- 云平台和平台元数据端点、实例服务以及控制平面

**直接 URL 参数**
- `url=`, `link=`, `fetch=`, `src=`, `webhook=`, `avatar=`, `image=`

**间接来源**
- Open Graph/link previews, PDF/image renderers
- Server-side analytics (Referer trackers), import/export jobs
- Webhooks/callback verifiers

**协议转换服务**
- PDF via wkhtmltopdf/Chrome headless, image pipelines
- Document parsers, SSO validators, archive expanders

**不那么明显的场景**
- GraphQL resolvers that fetch by URL
- Background crawlers, repository/package managers (git, npm, pip)
- Calendar (ICS) fetchers

## 高价值目标

### AWS

- IMDSv1: `http://169.254.169.254/latest/meta-data/` → `/iam/security-credentials/{role}`, `/user-data`
- IMDSv2: requires token via PUT `/latest/api/token` with header `X-aws-ec2-metadata-token-ttl-seconds`, then include `X-aws-ec2-metadata-token` on subsequent GETs
- If sink cannot set headers or methods, seek intermediaries that can
- ECS/EKS task credentials: `http://169.254.170.2$AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`

### GCP

- Endpoint: `http://metadata.google.internal/computeMetadata/v1/`
- Required header: `Metadata-Flavor: Google`
- Target: `/instance/service-accounts/default/token`

### Azure

- Endpoint: `http://169.254.169.254/metadata/instance?api-version=2021-02-01`
- Required header: `Metadata: true`
- MSI OAuth: `/metadata/identity/oauth2/token`

### Kubernetes

- Kubelet: 10250 (authenticated) and 10255 (deprecated read-only)
- Probe `/pods`, `/metrics`, exec/attach endpoints
- API server: `https://kubernetes.default.svc/`
- Authorization often needs service account token; SSRF that propagates headers/cookies may reuse them
- Service discovery: attempt cluster DNS names (`svc.cluster.local`) and default services (kube-dns, metrics-server)

### 内部服务

- Docker API: `http://localhost:2375/v1.24/containers/json` (no TLS variants often internal-only)
- Redis/Memcached: `dict://localhost:11211/stat`, gopher payloads to Redis on 6379
- Elasticsearch/OpenSearch: `http://localhost:9200/_cat/indices`
- Message brokers/admin UIs: RabbitMQ, Kafka REST, Celery/Flower, Jenkins crumb APIs
- FastCGI/PHP-FPM: `gopher://localhost:9000/` (craft records for file write/exec when app routes to FPM)

## 关键漏洞

### 协议利用

**Gopher**
- Speak raw text protocols (Redis/SMTP/IMAP/HTTP/FCGI)
- Use to craft multi-line payloads, schedule cron via Redis, or build FastCGI requests

**File and Wrappers**
- `file:///etc/passwd`, `file:///proc/self/environ` when libraries allow file handlers
- `jar:`, `netdoc:`, `smb://` and language-specific wrappers (`php://`, `expect://`) where enabled

### 地址变体

- Loopback: `127.0.0.1`, `127.1`, `2130706433`, `0x7f000001`, `::1`, `[::ffff:127.0.0.1]`
- RFC1918/link-local: 10/8, 172.16/12, 192.168/16, 169.254/16
- Test IPv6-mapped and mixed-notation forms

### URL 混淆

- Userinfo and fragments: `http://internal@attacker/` or `http://attacker#@internal/`
- Scheme-less/relative forms the server might complete internally: `//169.254.169.254/`
- Trailing dots and mixed case: `internal.` vs `INTERNAL`, Unicode dot lookalikes

### 重定向滥用

- Allowlist only applied pre-redirect: 302 from attacker → internal host
- Test multi-hop and protocol switches (http→file/gopher via custom clients)

### 头部与方法控制

- Some sinks reflect or allow CRLF-injection into the request line/headers
- If arbitrary headers/methods are possible, IMDSv2, GCP, and Azure become reachable

## 绕过技巧

**地址编码**
- Decimal, hex, octal representations of IP addresses
- IPv6 variants, IPv4-mapped IPv6, mixed notation

**DNS Rebinding**
- First resolution returns allowed IP, second returns internal target
- Use short TTL DNS records under attacker control

**URL 解析器差异**
- Different parsing between allowlist checker and actual fetcher
- Exploit inconsistencies in scheme, host, port, path handling

**重定向链**
- Initial URL passes allowlist, redirect targets internal host
- Protocol downgrade/upgrade through redirects

## 盲 SSRF

- 使用 OAST（DNS/HTTP）确认外联
- 通过时延、响应大小、TLS 错误和 ETag 差异推断内网可达性
- 通过二分超时构建端口映射（较短的连接/读取超时会带来更清晰的差异）

## 链式攻击

- SSRF → Metadata creds → cloud API access (list buckets, read secrets)
- SSRF → Redis/FCGI/Docker → file write/command execution → shell
- SSRF → Kubelet/API → pod list/logs → token/secret discovery → lateral movement

## 测试方法

1. **识别攻击面** - Web/移动/API 和后台任务中所有受用户影响的 URL/主机/路径
2. **建立判定器** - 先使用安静的 OAST DNS/HTTP 回调
3. **内网地址尝试** - 转向 loopback、RFC1918、link-local、IPv6、主机名
4. **协议变体** - 在支持时测试 gopher、file、dict
5. **解析器差异** - 在框架、CDN 和语言库之间测试
6. **重定向行为** - 单跳、多跳、协议切换
7. **头部/方法控制** - 能否影响请求头或 HTTP 方法？
8. **高价值目标** - 元数据、kubelet、Redis、FastCGI、Docker、Vault、内部管理面板

## 验证

1. 证明确实发生了服务器发起的对外请求（OAST 交互或仅内网可见的响应差异）
2. 展示从漏洞服务访问了非公开资源（元数据、内部管理接口、服务端口）
3. 在可能时，演示最小影响的凭据访问（短生命周期令牌）或无害的内部数据读取
4. 确认可复现，并记录控制 scheme/host/headers/method 与重定向行为的请求参数

## 误报

- 仅有客户端发起的 fetch（没有服务器请求）
- 严格白名单、DNS pinning 且不跟随重定向
- SSRF 模拟器/Mock 仅返回预设响应，没有真实外联
- 所有目标和协议都返回一致错误，证明外联被阻断
- OAST 回调的源 IP 是测试机而不是服务器时，说明是浏览器或客户端 fetch 发起了请求，而不是后端

## 影响

- 云端凭据泄露，随后可访问控制平面/API
- 访问未公开的内部控制面板和数据存储
- 横向移动到 Kubernetes、服务网格和 CI/CD
- 通过协议滥用（FCGI、Redis）、Docker 守护进程访问或可脚本化的管理接口实现 RCE

## 实战技巧

1. 先优先使用 OAST 回调；然后再迭代内网地址和协议
2. 测试 IPv6 和混合记法地址；过滤器经常忽略它们
3. 观察不同库/客户端（curl、Java HttpClient、Node、Go）的差异；行为在不同服务和任务中会变化
4. 重定向很关键：同时控制起始的白名单主机和下一跳
5. 元数据端点需要头部/方法；确认落点是否能设置，或者中间件是否会自动添加
6. 用小载荷和紧超时来以最少噪声绘制端口图
7. 当响应被掩盖时，通过长度/ETag/status 和 TLS 错误类别的差异推断可达性
8. 快速链到持久影响（短生命周期令牌、无害内部读取）后就停止

## 总结

任何代表用户抓取远程内容的功能，都可能成为通往内网和控制平面的隧道。请显式绑定 scheme/host/port/headers，否则就要准备面对攻击者借道转发。
