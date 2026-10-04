Export DeepSeek shared conversation to Markdown.

Requires uv — the fast Python package installer.
Install: curl -LsSf https://astral.sh/uv/install.sh | sh

Usage (just run it — venv is created automatically):
```
python deepseek_export.py <share_url_or_id> [-o output.md] [--json] [--txt] [--token TOKEN]
```
Examples:
```
python deepseek_export.py https://chat.deepseek.com/share/nvy7v2ps1r6e2wyoyj

python deepseek_export.py nvy7v2ps1r6e2wyoyj -o my_chat
python deepseek_export.py nvy7v2ps1r6e2wyoyj -o my_chat.md 

python deepseek_export.py nvy7v2ps1r6e2wyoyj --json -o chat
python deepseek_export.py nvy7v2ps1r6e2wyoyj --json -o chat.json

python deepseek_export.py nvy7v2ps1r6e2wyoyj --txt
```

`-o` without an extension gets the extension of the chosen format:
`my_chat` → `my_chat.md`, with `--json` → `my_chat.json`, with `--txt` → `my_chat.txt`.
An explicit extension is always kept as-is.

First run will create .venv/ and install requests via uv.

See CHANGELOG.md for the history.
