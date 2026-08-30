# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | ✅        |

## Reporting a Vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

Use [GitHub Security Advisories](https://github.com/Suanmd/deep-dive/security/advisories/new)
to report privately. We will:

- Acknowledge within 72 hours
- Triage within 7 days
- Patch + release within 30 days for high-severity issues

## ⚠️ Secrets — What NEVER Goes Into This Repo

| Secret | Where it goes | Why |
|--------|---------------|-----|
| `TAVILY_API_KEY` | Environment variable only | Avoid committed-key breaches |
| `TAVILY_API_KEY_BACKUP` | Environment variable only | Same |
| Cookie values (`z_c0`, `session`, `token`, etc.) | `config/cookies.json` (gitignored) | Cookies authenticate *you* |
| `mmx` CLI auth tokens | `mmx`'s own config (`~/.mmx/`) | Out of repo scope |

`config/cookies.json` is in `.gitignore`. A safe template is committed at
`config/cookies.example.json` with empty arrays — no real credentials.

## ✅ Sanitisation Before Publishing

When sharing log output, `summary.json`, or `report.md` that may contain
sensitive URLs or query strings, scrub them first:

```bash
# Remove query-string tokens
python -c "
import json, re
with open('summary.json') as f:
    data = json.load(f)
def scrub(u):
    return re.sub(r'([?&])(token|sid|key|signature|password)=[^&]+',
                  r'\1\2=REDACTED', u, flags=re.I)
for task in data.get('task_results', []):
    if task.get('output_dir'):
        task['output_dir'] = scrub(task['output_dir'])
print(json.dumps(data, indent=2, ensure_ascii=False))
" > summary.sanitized.json
```

## 🛡️ Hardening Checklist

When running deep-dive in production:

- [ ] Use a dedicated / throwaway account for cookie-based crawling.
- [ ] Rotate cookies every 30–90 days (most sites invalidate stale sessions).
- [ ] Set `--max-workers` ≤ 3 (higher values may trigger anti-crawl throttling).
- [ ] Don't run crawls from the same IP that holds sensitive accounts.
- [ ] Log `summary.json` and `metadata.json` only to private storage.
- [ ] Respect `robots.txt` for sites that publish it (this tool does not, by design).

## 📚 Known Anti-Crawl Behaviours

Some sites actively block scrapers. deep-dive works around them via:

- `cloudscraper` for Cloudflare-protected sites.
- Cookie injection for login-walled sites (Zhihu, Baidu Wenku, WeChat).
- Human-behaviour simulation (random scroll + click) for sites that fingerprint.

If a site starts blocking you:

1. Wait 24–48 hours (rate-limit cooldown).
2. Reduce `--max-workers` to 1.
3. Add fresh cookies to `config/cookies.json`.
4. Consider asking the site owner for API access.

## 🔗 Dependency Security

Minimum versions are pinned in `requirements.txt`. To audit:

```bash
pip install pip-audit
pip-audit -r requirements.txt
```

Please report any high/critical findings via the vulnerability reporting
channel above.
