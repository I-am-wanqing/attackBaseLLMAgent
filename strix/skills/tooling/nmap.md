---
name: nmap
description: Nmap 扫描工具的使用指南
---

# Nmap 工具指南

官方文档：
- https://nmap.org/book/man-briefoptions.html
- https://nmap.org/book/man.html
- https://nmap.org/book/man-performance.html

标准语法：
`nmap [Scan Type(s)] [选项] {target specification}`

高信号参数：
- `-n` 跳过 DNS 解析
- `-Pn` 在 ICMP/ping 被过滤时跳过主机发现
- `-sS` SYN 扫描（需要 root/特权）
- `-sT` TCP 连接扫描（不需要原始套接字权限）
- `-sV` 探测服务版本
- `-sC` 运行默认 NSE 脚本
- `-p <ports>` 指定端口（`-p-` 表示所有 TCP 端口）
- `--top-ports <n>` 快速扫描常见端口
- `--open` 只显示有开放端口的主机
- `-T<0-5>` 时间模板（常用 `-T4`）
- `--max-retries <n>` 限制重传次数
- `--host-timeout <time>` 对过慢主机放弃等待
- `--script-timeout <time>` 限制 NSE 脚本运行时长
- `-oA <prefix>` 以 normal/XML/grepable 格式输出

适合自动化的安全基线：
`nmap -n -Pn --open --top-ports 100 -T4 --max-retries 1 --host-timeout 90s -oA nmap_quick <host>`

常用模式：
- 快速首轮：
  `nmap -n -Pn --top-ports 100 --open -T4 --max-retries 1 --host-timeout 90s <host>`
- 小范围关键端口扫描：
  `nmap -n -Pn -p 22,80,443,8080,8443 --open -T4 --max-retries 1 --host-timeout 90s <host>`
- 对已发现端口做服务/脚本增强：
  `nmap -n -Pn -sV -sC -p <comma_ports> --script-timeout 30s --host-timeout 3m -oA nmap_services <host>`
- 无 root 备选：
  `nmap -n -Pn -sT --top-ports 100 --open --host-timeout 90s <host>`

关键正确性规则：
- 始终显式设置目标范围。
- 优先采用两轮扫描：先发现，再增强。
- 始终使用 `--host-timeout` 设定超时边界；只要涉及 NSE 脚本，就加上 `--script-timeout`。
- 发现扫描要保持收敛：除非明确需要更广覆盖，否则使用明确的重要端口或较小的 `--top-ports` 组合。
- 在沙箱运行中，除非明确要求，不要做穷举式扫描（`-p-`、很高的 `--top-ports` 或大范围主机段）。
- 不要刷流量；先从最小的端口集合开始，足以回答问题即可。
- 广泛端口发现优先用 `naabu`；有范围的验证/增强再用 `nmap`。

使用规则：
- 在自动化中默认加 `-n`，避免 DNS 延迟。
- 使用 `-oA` 生成可复用产物。
- 在考虑更大范围扫描前，优先使用 `-p 22,80,443,8080,8443` 或 `--top-ports 100`。
- 日常使用不要随意调用 `-h`/`--help`，除非确实有必要。

故障恢复：
- 如果主机意外显示离线，改用 `-Pn` 重新运行。
- 如果扫描卡住，缩小范围（`-p` 或更小的 `--top-ports`）并降低重试次数。
- 如果脚本运行过久，添加 `--script-timeout`。

如果不确定，可用 web_search 查询：
`site:nmap.org/book nmap <flag>`
