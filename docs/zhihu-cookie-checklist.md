# Zhihu Cookie Setup — Step-by-Step Checklist

> **TL;DR:** 准备一个**专门小号** → Chrome 登录 → 用 EditThisCookie 导出 3 个 cookie → 粘贴到 `config/cookies.json` → 完成。整过过程 5-10 分钟。

## ⚠️ 安全前置（必读）

| 规则 | 为什么 |
|---|---|
| **必须用专门小号** | 知乎有反爬风控；爬虫请求模式（高频 UA、无 referrer、并发）会触发账号风控。主账号被封不可逆，小号损失可控。 |
| **不要把这个文件 commit 到 git** | `config/cookies.json` 已经在 `.gitignore` 里，但手动检查一下。 |
| **不要把 cookie 发给别人或上传** | cookie = 账号身份。Slack / 邮件 / GitHub Issue / 截图都不行。 |
| **定期轮换** | cookie 一般 1-3 个月过期。过期前重新登录导出。 |
| **泄漏处理** | 如果文件意外泄漏：立即重新登录（让旧 cookie 失效）+ 改密码 + 考虑账号已 compromised。 |

## 步骤

### ① 注册专门小号（如果你还没有）

- 用**单独的邮箱**（不要和你其他账号关联）
- 用**单独的 Chrome profile**（Settings → Users → Add person）— 这样 cookie 隔离，不会污染主账号 session
- 知乎新号建议养 2-3 天再开始爬（立即爬容易被风控）

### ② 安装 EditThisCookie

Chrome Web Store: [EditThisCookie](https://chrome.google.com/webstore/detail/editthiscookie/)

或者用 Cookie-Editor（开源替代）。两者都行。

### ③ 在新 profile 下登录知乎

- 打开 `https://www.zhihu.com`
- 用小号登录
- 建议**先浏览 2-3 分钟**（点赞 / 看答案），模拟正常用户，避免触发风控

### ④ 导出 cookie

1. F12 打开 DevTools
2. Application → Storage → Cookies → `https://www.zhihu.com`
3. 找到 **3 个关键 cookie**：

| Cookie 名 | 作用 | 是否必须 |
|---|---|---|
| `z_c0` | 主要认证 token（HMAC 签名格式） | **必须** |
| `KLBRSID` | session 跟踪，稳定性更好 | 强烈推荐 |
| `d_c0` | 设备指纹，避免异常登录告警 | 推荐 |

4. 右键 → Export（EditThisCookie 提供 JSON / Netscape 两种格式）
5. 复制 JSON 数组

### ⑤ 填到 `config/cookies.json`

1. 复制 `config/cookies.example.json` → `config/cookies.json`
   ```bash
   cp config/cookies.example.json config/cookies.json
   ```
2. 把 `cookies.json` 里 `zhihu.cookies[]` 数组的 3 个 `value` 字段替换成真实值
3. 删掉 `_hint` 字段（仅自己看）
4. 填上 `"last_updated": "2026-08-31"`

最终结构长这样：
```json
{
  "zhihu": {
    "domain": ".zhihu.com",
    "cookies": [
      {"name": "z_c0",      "value": "HMACST|abc123...", "domain": ".zhihu.com", "path": "/"},
      {"name": "KLBRSID",   "value": "9c8d7...",         "domain": ".zhihu.com", "path": "/"},
      {"name": "d_c0",      "value": "AAE...",           "domain": ".zhihu.com", "path": "/"}
    ],
    "note": "throwaway account",
    "last_updated": "2026-08-31"
  },
  "baidu_wenku": { "...": "..." },
  "weixin":       { "...": "..." }
}
```

### ⑥ 验证 cookie 生效

跑一个能确认知乎命中的查询：

```bash
python -m deep_dive --query "知乎 热门话题 2026" --depth quick --no-report
# 或：
deep-dive --query "知乎 热门话题 2026" --depth quick
```

期望 stdout 出现：
```
[COOKIE] loaded 3 cookies across 1 sites    ← 3 = z_c0 + KLBRSID + d_c0
```

如果出现 `no cookies.json found` → 文件路径不对，看 `docs/cookies.md` 的优先级。

### ⑦ 验证抓到的内容是登录态

跑完之后看 `tmp/deep-dive/<topic>__<run-id>/raw/<task>/page.html`：

| 看到 | 含义 |
|---|---|
| 完整的答案正文（>500 字） | ✅ cookie 生效 |
| "登录查看更多" / "请登录" | ❌ cookie 没注入 / 已过期 |
| 403 状态页 | ❌ cookie 失效或被风控 |

## 失效 / 续期

Cookie 一般 1-3 个月失效。检测信号：

1. 跑已知需要登录的 query，结果出现 "登录查看更多"
2. raw html 里状态码 403
3. 知乎网页版手动访问也被踢出

**续期方法**：重新走 ③④⑤ 步骤，更新 `config/cookies.json` 里的 value 和 `last_updated` 字段。

## 多账号场景（可选）

如果需要轮换多个小号避免单账号请求过频：

```json
{
  "zhihu_a": {
    "domain": ".zhihu.com",
    "cookies": [{"name": "z_c0", "value": "AAA...", "...": "..."}]
  },
  "zhihu_b": {
    "domain": ".zhihu.com",
    "cookies": [{"name": "z_c0", "value": "BBB...", "...": "..."}]
  }
}
```

当前 deep-dive 不自动轮换，要手动切换 key 名字。具体见 `docs/cookies.md` 末尾的"多账号场景"。

## ⚠️ 如果你搞不定

如果 export cookie 遇到问题（看不懂 DevTools / EditThisCookie 不工作 / 不知道怎么填 JSON），告诉 OpenClaw 帮你：

- 给出浏览器类型和版本
- 给出具体卡在哪一步
- 提供**脱敏截图**（把 value 部分打码，让 OpenClaw 看结构）

**不要把真实 cookie value 发给 OpenClaw** — 哪怕是 throwaway 账号，这是原则问题。
