# n8n workflow setup

This project intentionally does not ship a complex n8n JSON workflow in v1 because FreshRSS API credentials, feed import state, and Telegram credentials are environment-specific. Importing a fake workflow would be brittle.

Manual workflow outline:

1. Create a Schedule Trigger node.
   - Mode: every week
   - Day: Monday
   - Time: 09:00
   - Timezone: Asia/Tokyo
2. Add RSS/FreshRSS collection.
   - Option A: use FreshRSS API after you finish FreshRSS web setup and import `feeds/japan-antiwar-starter.opml`.
   - Option B: add several n8n RSS Feed Read nodes for the OPML URLs.
3. Map each item into this shape:

```json
{
  "articles": [
    {
      "title": "{{$json.title}}",
      "url": "{{$json.link}}",
      "source_name": "source name",
      "source_type": "news_agency",
      "published_at": "{{$json.isoDate}}",
      "raw_text": "{{$json.contentSnippet || $json.content || $json.title}}"
    }
  ]
}
```

4. Add an HTTP Request node:
   - Method: POST
   - URL: `http://analysis-worker:8090/ingest`
   - Body: JSON from step 3
5. Add another HTTP Request node:
   - Method: POST
   - URL: `http://analysis-worker:8090/analyze/weekly`
   - Body:

```json
{
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD"
}
```

6. Add Telegram delivery.
   - If the report is long, send only `summary` and `report_path`.
   - Telegram variables are available in the n8n container as `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` if you set them in `.env`.

## Japan anti-war specific notes

- Feed sources focus on publicly available Japanese and international news covering peace movements, Article 9, US military bases, and anti-war activism.
- All sources are public RSS/Atom feeds; no login, no CAPTCHA bypass, no private content.
- GDELT queries should target Japan-related anti-war and peace movement keywords (e.g., "Japan anti-war", "Japan peace movement", "Okinawa base protest", "Article 9 Japan").
- Reddit public discussion sources default to `r/japan`, `r/okinawa`, `r/japanlife`, `r/newsokur`.
