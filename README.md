# Social Crazy Dr. Dev — Ultimate Video Downloader
**Created by Dr. Hamza**

---

## Features
- Download Instagram posts, reels, IGTV videos
- Download YouTube videos up to 4K UHD
- Audio-only MP3 extraction from YouTube
- Thumbnail downloader for both platforms
- Video quality selector (default = highest)
- Full video metadata display (title, uploader, date, views, likes)
- Day / Night mode
- Fully responsive on all devices
- Unlimited downloads — no sign-up required

---

## Files
```
app.py            ← Main Flask app (all routes + embedded HTML)
requirements.txt  ← Python dependencies
render.yaml       ← Render deployment config (installs ffmpeg)
```

---

## Local Development

```bash
pip install -r requirements.txt
python app.py
```
Open http://localhost:5000

---

## Deploy on Render (Free Tier)

1. Push all files to a GitHub repository
2. Go to https://render.com → New → Web Service
3. Connect your GitHub repo
4. Render will auto-detect `render.yaml`
5. **Important**: In Render settings, set Build Command to:
   ```
   apt-get update && apt-get install -y ffmpeg && pip install -r requirements.txt
   ```
   Or just use the provided `render.yaml` — it handles this automatically.
6. Start Command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 120`
7. Deploy!

### Why ffmpeg is needed
yt-dlp uses ffmpeg to merge the best video + audio streams for YouTube downloads.
Without it, only pre-merged (lower quality) streams can be downloaded.
The `render.yaml` installs ffmpeg automatically during the build step.

---

## Notes
- **Instagram**: Only public accounts/posts are supported.
- **YouTube**: Large files may take time on Render's free tier (limited CPU).
- Render free tier spins down after 15 min inactivity — first request after sleep may be slow.
- For heavy usage, consider upgrading to Render Starter ($7/month).

---

## Tech Stack
- **Backend**: Python / Flask
- **Instagram**: instaloader
- **YouTube**: yt-dlp
- **Frontend**: Vanilla HTML/CSS/JS (embedded in Python)
- **Fonts**: Orbitron + Exo 2 (Google Fonts)
- **Icons**: Font Awesome 6
