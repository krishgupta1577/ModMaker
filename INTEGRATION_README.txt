FF AUTOLIKE — SINGLE PYTHON FILE BUILD

This package combines the website and Telegram bot runtime into ONE app.py.
There is no separate likess.py. The bot runs in an isolated namespace inside
app.py so its variables do not overwrite Flask website variables. Its secondary
Flask server and standalone main loop are removed.

INSTALL
1. Back up the existing database.db.
2. Upload app.py and requirements.txt to the same Wispbyte application directory.
3. Do not delete or replace database.db, templates/, or static/.
4. Keep the Wispbyte Environment variable BOT_TOKEN (exact uppercase key).
5. Install: pip install -r requirements.txt
6. Start with: python app.py
   Or if Wispbyte requires WSGI: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1
7. Restart once.

CHECK
Open /health. It reports bot_token_configured true/false and env_files_loaded count,
but never reveals the token value. Expected bot status is running.

IMPORTANT
Merging two Python files does NOT create a Telegram token. If /health reports
bot_token_configured=false, Wispbyte is not exposing its Environment /.env file
to this process. The hosting Environment setting must be saved with key BOT_TOKEN
and a non-empty token value. Do not paste token into chat or screenshots.
Use one WSGI worker/replica to avoid duplicate Telegram polling.

Existing database.db must be preserved. This build has syntax-checked modules;
live Wispbyte and Telegram authentication still need deployment verification.
