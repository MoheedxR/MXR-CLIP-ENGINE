# MXR CLIP ENGINE

**Turn long videos into short clips people actually watch.**

MXR CLIP ENGINE runs on your own computer. Paste a YouTube or Vimeo link, or drop in a video file. It transcribes the speech, scores every 30 to 120 second window for hook strength, energy and clarity, then renders the best moments as 9:16 clips with optional captions. Each clip appears as soon as it's ready, and an activity log shows what the engine is doing at every step.

![Welcome screen](docs/screenshot-welcome.png)

![A clip rendering while the engine works](docs/screenshot-working.png)

## Features

- **One-click start.** `python run.py` checks your setup and opens the app in your browser.
- **Live progress.** A progress bar, step tracker, and elapsed timer show where the job is.
- **Clips as they're made.** Each clip appears with a live preview as it finishes, with an engagement score.
- **Activity log.** A running, timestamped log of downloads, transcription, scoring, and rendering.
- **Engagement-first picking.** Clips are chosen by hook, energy, pace, completeness and length, with no overlaps.
- **Ready to post.** 9:16 vertical crop, optional word-level captions burned in, `.srt` files and thumbnails.
- **Private by default.** Transcription runs locally with Whisper. Nothing is sent to the cloud unless you turn on AI ranking.
- **Optional AI ranking.** Use any OpenAI-compatible model to rate hooks and write titles.

## Requirements

- Python 3.10 or newer
- FFmpeg (video processing)
- About 2 GB of disk space for the speech model and temporary files
- A modern browser

## Install

### Windows

1. Install Python from [python.org](https://www.python.org/downloads/). Tick **Add Python to PATH**.
2. Install FFmpeg in a terminal: `winget install Gyan.FFmpeg`, then reopen the terminal.
3. Download or clone this repository, open a terminal in the folder, and run:
   ```bash
   pip install -r requirements.txt
   ```
4. Double-click **`run.bat`**, or run `python run.py`.

### macOS and Linux

```bash
# macOS
brew install ffmpeg
# Ubuntu / Debian
sudo apt install ffmpeg

git clone https://github.com/YOUR-USER/mxr-clip-engine.git
cd mxr-clip-engine
pip install -r requirements.txt
./run.sh          # or: python3 run.py
```

The app opens at **http://localhost:8000**. Press `Ctrl+C` in the terminal to stop it.

The first transcription downloads the Whisper speech model once. The default `base` model is about 150 MB.

## Configuration

Copy `.env.example` to `.env` and edit it. You don't need to change anything to get started.

| Variable | Default | What it does |
| --- | --- | --- |
| `WHISPER_MODEL` | `base` | Speech model: `tiny` (fastest), `base`, `small`, `medium`, `large-v3` (most accurate). |
| `WHISPER_DEVICE` | `auto` | Use `cuda` if you have an NVIDIA GPU. |
| `LLM_API_KEY` | empty | Enables AI ranking. Any OpenAI-compatible provider works. |
| `LLM_BASE_URL` | OpenAI | Change for OpenRouter, Groq, Ollama, or other providers. |
| `LLM_MODEL` | `gpt-4o-mini` | Model used for AI ranking. |
| `MAX_DOWNLOAD_HEIGHT` | `1080` | Highest video resolution to download from links. |
| `CLIPPER_DATA_DIR` | `./data` | Where projects and clips are stored. |
| `PORT` | `8000` | Port to run on. |
| `HOST` | `127.0.0.1` | Set to `0.0.0.0` to open the app to other devices on your network. |
| `NO_BROWSER` | unset | Set to `1` to stop the browser from opening automatically. |

## How the engine picks clips

It isn't a crystal ball. It uses signals that tend to hold attention in short-form video:

| Signal | Weight | What it looks for |
| --- | --- | --- |
| Hook | 28% | A question, a number, or a provocative word in the first five seconds, or a punchy opening line |
| Energy | 18% | Loudness compared with the rest of the video, and how much it varies |
| Completeness | 14% | Starts on a new sentence or after a pause, and ends on a finished thought |
| Emotional language | 12% | Density of charged words |
| Pace | 10% | A lively but clear speaking rate |
| Length | 10% | Sweet spot 45 to 90 seconds |
| Closure | 8% | A question that gets answered inside the clip |

Long silences and quiet stretches are penalised. With AI ranking on, the top moments are reviewed by the model and the final score blends both views. Weights live in `clipper/scoring.py` and are easy to tune.

Scores are a guide, not a guarantee. Real performance depends on your audience, topic and platform.

## Project layout

```
run.py / run.bat / run.sh   one-click launcher
clipper/
  config.py       settings (reads .env)
  download.py     video links via yt-dlp
  media.py        FFmpeg: audio, loudness, cutting, thumbnails, captions
  transcribe.py   Whisper speech-to-text, grouped into sentences
  candidates.py   all valid 30–120 s windows
  scoring.py      engagement heuristics and overlap-free selection
  llm.py          optional AI ranking and titles
  pipeline.py     the whole job, reporting progress and log lines as it goes
  jobs.py         background worker, JSON persistence, live activity log
  server.py       FastAPI web app and API
static/           the web interface (HTML, CSS, JS, no build step)
tests/            unit tests
```

## API

The web app talks to a small JSON API, which you can also use from scripts.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/jobs` | Start a project from a link (JSON body: `url`, `count`, `min_seconds`, `max_seconds`, `vertical`, `captions`, `use_llm`) |
| `POST` | `/api/jobs/upload` | Start a project from an uploaded file (multipart form) |
| `GET` | `/api/jobs` | List projects |
| `GET` | `/api/jobs/{id}` | Project detail: progress, activity log, clips, warnings |
| `DELETE` | `/api/jobs/{id}` | Delete a finished project |
| `GET` | `/api/config` | Engine settings and what's enabled |

## Tests

```bash
pip install pytest numpy httpx
pytest -q
```

The tests cover word grouping, candidate boundaries, scoring, overlap-free selection, LLM response parsing, caption timing, and project storage. The full pipeline has been run end to end on a test video, including through the browser.

## Limitations

- **Speed depends on your computer.** Transcription is the slowest step on a CPU. A GPU or a smaller model helps.
- **Scores are heuristics.** They approximate what holds attention. They don't predict results.
- **Crop is centred.** Talking-head footage works well. Wide or multi-speaker shots may need face tracking, which isn't included yet.
- **Use content you have rights to.** Downloading from platforms may breach their terms. You are responsible for how you use the output.

## Contributing

Issues and pull requests are welcome. Please run `pytest -q` before submitting.

## License

MIT. See [LICENSE](LICENSE).
