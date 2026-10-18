ECONOMIC NEWS ALERT — FREE GITHUB ACTIONS VERSION

This version runs on GitHub's hosted runners, so your own computer can be off.
It checks feeds on a scheduled basis (currently every 15 minutes) and sends high-scoring
headlines to Telegram. GitHub schedule timing can be delayed; this is not guaranteed
real-time breaking-news coverage.

FILES
- news_alert.py: one-pass feed checker
- requirements.txt: Python dependencies
- .github/workflows/news-alert.yml: scheduled GitHub Actions workflow
- news_seen.json: created/updated automatically to reduce duplicate alerts

SETUP
1. Create a Telegram bot with @BotFather; keep the bot token secret.
2. Start a chat with your bot by pressing Start.
3. Get your chat ID using:
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   Replace YOUR_BOT_TOKEN locally. Never share the URL if it contains your token.
4. Create a GitHub account at https://github.com/ if needed.
5. Create a NEW repository. For easiest setup, make it PUBLIC; the code contains no
   credentials, but you must still add the token only as a GitHub Actions secret.
6. Upload all files/folders from this ZIP, including the hidden .github folder, to the
   repository's default branch.
7. In the repository go to Settings > Secrets and variables > Actions > New repository secret.
   Add:
     Name: TELEGRAM_BOT_TOKEN
     Secret: your bot token
   Add another:
     Name: TELEGRAM_CHAT_ID
     Secret: your numeric chat ID
8. Go to Actions. If prompted, enable workflows.
9. Open "Economic news alerts" and click "Run workflow" to test it.
10. Check your Telegram chat and the workflow run logs.

IMPORTANT GITHUB SETTINGS
The workflow commits news_seen.json so that previously processed stories are remembered.
The workflow has contents: write permission. If the push fails, check Settings > Actions >
General > Workflow permissions and allow read and write permissions.

FREE PLAN NOTES
GitHub Actions has included usage for standard runners, subject to current plan limits.
Scheduled runs may be delayed and repositories with no activity may have schedules disabled.
Check GitHub's current documentation for exact limits:
https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#schedule
https://docs.github.com/en/billing/managing-billing-for-your-products/managing-billing-for-github-actions/about-billing-for-github-actions

LIMITATIONS
- This is a starter prototype using Google News RSS discovery plus a TCMB RSS feed.
- Keyword scoring can miss important stories or flag unimportant ones.
- RSS and scheduled workflows can be delayed or unavailable.
- Do not make investment decisions based on an alert alone.
- Never commit bot tokens into source code or files. Use GitHub Actions secrets.
