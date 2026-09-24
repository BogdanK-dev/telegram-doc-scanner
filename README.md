<img src=".github/banner.svg" width="100%" alt="Doc Scanner. Document photo in. Clean PDF out.">

Telegram bot. Send a photo of a document — get a scanned PDF back.

It finds the page edges, fixes the perspective and removes shadows. On its own.

## What it does

- **Scan.** Page edges, perspective, shadows — automatic.
- **Filters.** B/W, Gray, Color, Original.
- **PDF.** Several photos or an album — one A4 file.
- **ID card.** Both sides on one A4 sheet, true size.
- **Text.** Reads Russian, Kazakh, English. Searchable PDF.
- **Language.** Interface in English and Russian.

## Privacy

Everything runs on your machine. No third-party services.
Documents live in memory only and are wiped after 24 hours.
One thing is written to disk: the chosen language (`prefs.json`).

## Run (Windows)

1. Create a bot with [@BotFather](https://t.me/BotFather): `/newbot`. Keep the token.
2. Install [Python](https://www.python.org/downloads/). Tick **Add python.exe to PATH**.
3. `install_ocr.bat` — text recognition. Optional.
4. `start.bat` — paste the token. The bot is live.
5. `autostart.bat` — start with Windows. Run it again to turn it off.

The bot works while its window is open and the machine is awake.

## Settings — `.env`

| Key | What it does |
|---|---|
| `BOT_TOKEN` | Token from @BotFather |
| `ALLOWED_USERS` | Who gets access: IDs or @usernames, comma-separated. Empty — everyone. Your ID: `/myid` |
| `OCR_LANG` | Recognition languages. Default `rus+kaz+eng` |
| `TESSERACT_CMD` | Path to `tesseract.exe` if it is not found automatically |
| `DEFAULT_FILTER` | Filter for new pages: `bw`, `gray`, `color`, `orig` |

Restart the bot after any change.

## Troubleshooting

| Problem | Fix |
|---|---|
| Bot is silent | Window open? Machine awake? Internet up? Log: `bot.log` |
| No "Recognize text" button | Run `install_ocr.bat`, restart the bot |
| "Page borders not detected" | Dark plain background, all four corners in frame |
| "The file exceeds 20 MB" | Telegram limit. Send it as a photo |

## Stack

Python · aiogram 3 · OpenCV · Pillow · pypdf · Tesseract
