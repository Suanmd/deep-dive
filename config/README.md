# `config/` — Configuration directory

This directory holds non-secret configuration that ships with the repo.

| File | Committed? | Purpose |
|------|------------|---------|
| `defaults.yaml` | yes | Built-in defaults (overridden by `~/.deep-dive/config.yaml`) |
| `cookies.example.json` | yes | **Template only** — copy to `cookies.json` and fill in |
| `cookies.json` | **no** (gitignored) | Your real cookies — never commit |

## How config resolution works

`deep_dive.config.load_config()` resolves values in this priority order
(highest first):

1. **CLI flags** (e.g. `--output ./research`)
2. **Environment variables** (e.g. `DEEP_DIVE_OUTPUT_DIR=./research`)
3. **User config** at `~/.deep-dive/config.yaml` (if it exists)
4. **`config/defaults.yaml`** (this directory)
5. **Built-in hardcoded defaults** (lowest priority)

## Cookie file format

See `cookies.example.json` for the schema. Quick recap:

```json
{
  "<site-key>": {
    "domain": ".<your-domain>.com",
    "cookies": [
      {"name": "...", "value": "...", "domain": ".<your-domain>.com", "path": "/"}
    ]
  }
}
```

`deep-dive` matches cookies to URLs by suffix (e.g. cookie domain
`.zhihu.com` matches `https://www.zhihu.com/question/123`).

## Environment variables

| Variable | Effect |
|----------|--------|
| `TAVILY_API_KEY` | Primary Tavily API key |
| `TAVILY_API_KEY_BACKUP` | Secondary Tavily API key (auto-fallback) |
| `DEEP_DIVE_OUTPUT_DIR` | Override `--output` |
| `DEEP_DIVE_CONFIG` | Override YAML config file path |
| `DEEP_DIVE_COOKIE_FILE` | Override cookies.json path |
| `DEEP_DIVE_DEBUG` | Set to `1` to enable `--debug` |
| `PYTHONIOENCODING` | Set to `utf-8` (auto-applied) |
| `PYTHONUNBUFFERED` | Set to `1` (auto-applied) |
