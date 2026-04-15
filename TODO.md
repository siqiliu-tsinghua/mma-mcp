# TODO: URL-based Image Delivery

## Problem

MCP `ImageContent` 在主流 Web 客户端上体验不佳：
- Claude.ai 把图片折叠在 result 面板里，需要点击展开
- ChatGPT 完全不渲染 MCP 返回的图片

## Idea

将生成的图片存储在服务器上（如 `https://mma.<domain>/img/<random>.png`），
工具返回 Markdown 图片语法 `![plot](url)` 作为文本结果。如果客户端将
tool result 当 Markdown 渲染，图片就会内联显示。

## 验证步骤（零成本）

在 Claude.ai / ChatGPT 中让 `evaluate` 返回一个包含公开图片 URL 的
Markdown 图片标记，观察客户端是否渲染。

## 实现方案（验证通过后）

1. Caddy 添加 `/img/` 静态文件路由，指向图片存储目录
2. `evaluate_image` 将 PNG 存入该目录（随机文件名，如 UUID）
3. 返回 `![result](https://mma.<domain>/img/<uuid>.png)` 文本
4. 定期清理过期图片（cron 或进程内定时器）

## 待考虑

- **认证**：图片 URL 公开可访问（文件名不可猜测）vs 签名 URL（带过期时间）
- **清理策略**：TTL 多长合适（5 分钟？1 小时？）
- **回退**：如果客户端不渲染 Markdown 图片，仍需保留 `ImageContent` 路径
- **MCP Resource**：另一条路，服务器暴露 `resource://` 链接供客户端拉取，
  但目前主流客户端对 MCP Resource 的支持不明确
