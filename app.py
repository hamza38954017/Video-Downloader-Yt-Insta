"""
Social Crazy Dr. Dev - Ultimate Instagram & YouTube Video Downloader
Created by Dr. Hamza
Deploy on Render Free Web Service
"""

import os
import re
import tempfile
import json
import threading
import shutil
from datetime import datetime
from urllib.parse import urlparse

import requests as http_req
from flask import Flask, render_template_string, request, jsonify, Response, stream_with_context

import instaloader
import yt_dlp

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB

# ─────────────────────────────────────────────
# UTILITY HELPERS
# ─────────────────────────────────────────────

def extract_instagram_shortcode(url):
    patterns = [
        r'instagram\.com/p/([A-Za-z0-9_-]+)',
        r'instagram\.com/reel/([A-Za-z0-9_-]+)',
        r'instagram\.com/reels/([A-Za-z0-9_-]+)',
        r'instagram\.com/tv/([A-Za-z0-9_-]+)',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def safe_filename(s, max_len=60):
    s = re.sub(r'[^\w\s-]', '', str(s)).strip()
    s = re.sub(r'[\s]+', '_', s)
    return s[:max_len] or 'download'


def format_date(d):
    if not d:
        return ''
    try:
        return datetime.strptime(str(d), '%Y%m%d').strftime('%d %b %Y')
    except Exception:
        return str(d)


def cleanup_dir(path):
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# ─────────────────────────────────────────────
# INSTAGRAM FETCH INFO
# ─────────────────────────────────────────────

def fetch_instagram_info(url):
    shortcode = extract_instagram_shortcode(url)
    if not shortcode:
        return {'error': 'Invalid Instagram URL. Please use a post, reel, or IGTV link.'}
    try:
        L = instaloader.Instaloader(
            quiet=True,
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
        )
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        info = {
            'platform': 'instagram',
            'title': (post.title or f'Instagram post by @{post.owner_username}'),
            'description': (post.caption or '')[:600],
            'uploader': post.owner_username,
            'uploader_full': post.owner_profile.full_name if post.owner_profile else post.owner_username,
            'date': post.date_utc.strftime('%d %b %Y, %H:%M UTC'),
            'likes': post.likes,
            'comments': post.comments,
            'is_video': post.is_video,
            'thumbnail': post.url,
            'url': url,
            'shortcode': shortcode,
            'typename': post.typename,
            'formats': [{'quality': '🏆 Original Quality (Best)', 'format_id': 'best'}],
        }
        if post.is_video:
            info['video_url'] = post.video_url
        return {'success': True, 'info': info}
    except instaloader.exceptions.InstaloaderException as e:
        return {'error': f'Instagram error: {str(e)}'}
    except Exception as e:
        return {'error': f'Could not fetch post. Make sure it is a public post. ({str(e)})'}


# ─────────────────────────────────────────────
# YOUTUBE FETCH INFO
# ─────────────────────────────────────────────

def fetch_youtube_info(url):
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            raw = ydl.extract_info(url, download=False)

        formats = []
        seen = set()
        # Collect combined formats first
        for f in sorted(raw.get('formats', []), key=lambda x: x.get('height') or 0, reverse=True):
            height = f.get('height')
            vcodec = f.get('vcodec', 'none')
            acodec = f.get('acodec', 'none')
            ext = f.get('ext', 'mp4')
            if vcodec == 'none' or acodec == 'none':
                continue
            if height and height not in seen:
                seen.add(height)
                size = f.get('filesize') or f.get('filesize_approx') or 0
                formats.append({
                    'quality': f'{height}p',
                    'format_id': f['format_id'],
                    'ext': ext,
                    'filesize': size,
                })

        # Always prepend the best merged option
        formats.insert(0, {
            'quality': '🏆 Best Quality (Auto)',
            'format_id': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best',
            'ext': 'mp4',
            'filesize': 0,
        })

        # Audio-only
        formats.append({'quality': '🎵 Audio Only (MP3)', 'format_id': 'bestaudio/best', 'ext': 'mp3', 'filesize': 0})

        result = {
            'platform': 'youtube',
            'title': raw.get('title', 'YouTube Video'),
            'description': (raw.get('description', '') or '')[:600],
            'uploader': raw.get('uploader', raw.get('channel', 'Unknown')),
            'uploader_url': raw.get('uploader_url', ''),
            'date': format_date(raw.get('upload_date', '')),
            'duration': raw.get('duration', 0),
            'view_count': raw.get('view_count', 0),
            'like_count': raw.get('like_count', 0),
            'comment_count': raw.get('comment_count', 0),
            'thumbnail': raw.get('thumbnail', ''),
            'url': url,
            'is_video': True,
            'formats': formats[:12],
            'channel_follower_count': raw.get('channel_follower_count', 0),
            'categories': ', '.join(raw.get('categories', [])[:3]),
            'tags': ', '.join((raw.get('tags', []) or [])[:5]),
        }
        return {'success': True, 'info': result}
    except yt_dlp.utils.DownloadError as e:
        return {'error': f'YouTube error: {str(e)[:200]}'}
    except Exception as e:
        return {'error': f'Could not fetch video info. ({str(e)[:200]})'}


# ─────────────────────────────────────────────
# FLASK ROUTES
# ─────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/fetch-info', methods=['POST'])
def fetch_info():
    data = request.get_json(silent=True) or {}
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': 'URL is required.'})
    if 'youtube.com' in url or 'youtu.be' in url:
        return jsonify(fetch_youtube_info(url))
    elif 'instagram.com' in url:
        return jsonify(fetch_instagram_info(url))
    else:
        return jsonify({'error': 'Unsupported URL. Paste an Instagram or YouTube link.'})


@app.route('/download', methods=['POST'])
def download():
    data = request.get_json(silent=True) or {}
    url = data.get('url', '').strip()
    format_id = data.get('format_id', 'bestvideo+bestaudio/best')
    platform = data.get('platform', '')
    title = data.get('title', 'video')
    fname = safe_filename(title)

    if not url:
        return jsonify({'error': 'URL missing.'}), 400

    if platform == 'instagram':
        return _stream_instagram(url, fname, data.get('video_url'))
    else:
        is_audio = 'bestaudio' in format_id and 'bestvideo' not in format_id
        ext = 'mp3' if is_audio else 'mp4'
        return _stream_youtube(url, format_id, fname, ext, is_audio)


def _stream_instagram(url, fname, video_url=None):
    """Stream Instagram video/image directly."""
    try:
        if not video_url:
            shortcode = extract_instagram_shortcode(url)
            L = instaloader.Instaloader(quiet=True)
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            if post.is_video:
                video_url = post.video_url
            else:
                video_url = post.url  # image

        resp = http_req.get(video_url, stream=True, timeout=60, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        content_type = resp.headers.get('Content-Type', 'video/mp4')
        file_ext = 'mp4' if 'video' in content_type else 'jpg'

        def generate():
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        return Response(
            stream_with_context(generate()),
            headers={
                'Content-Disposition': f'attachment; filename="{fname}.{file_ext}"',
                'Content-Type': content_type,
            }
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _stream_youtube(url, format_id, fname, ext, is_audio):
    """Download YouTube video to temp then stream."""
    tmpdir = tempfile.mkdtemp(prefix='scd_')
    out_tpl = os.path.join(tmpdir, '%(title)s.%(ext)s')

    pp = []
    if is_audio:
        pp.append({'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'})
    else:
        pp.append({'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'})

    ydl_opts = {
        'format': format_id,
        'outtmpl': out_tpl,
        'quiet': True,
        'no_warnings': True,
        'merge_output_format': 'mp4' if not is_audio else None,
        'postprocessors': pp,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        # Find downloaded file
        files = [f for f in os.listdir(tmpdir)]
        if not files:
            cleanup_dir(tmpdir)
            return jsonify({'error': 'Download produced no file.'}), 500

        # Prefer mp4/mp3
        preferred = [f for f in files if f.endswith(('.mp4', '.mp3', '.webm', '.mkv'))]
        chosen = preferred[0] if preferred else files[0]
        filepath = os.path.join(tmpdir, chosen)
        file_ext = os.path.splitext(chosen)[1].lstrip('.') or ext
        content_type = 'audio/mpeg' if file_ext == 'mp3' else 'video/mp4'

        def generate():
            try:
                with open(filepath, 'rb') as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        yield chunk
            finally:
                cleanup_dir(tmpdir)

        return Response(
            stream_with_context(generate()),
            headers={
                'Content-Disposition': f'attachment; filename="{fname}.{file_ext}"',
                'Content-Type': content_type,
            }
        )
    except Exception as e:
        cleanup_dir(tmpdir)
        return jsonify({'error': str(e)[:300]}), 500


@app.route('/download-thumbnail', methods=['POST'])
def download_thumbnail():
    data = request.get_json(silent=True) or {}
    thumb_url = data.get('thumbnail_url', '').strip()
    fname = safe_filename(data.get('filename', 'thumbnail'))

    if not thumb_url:
        return jsonify({'error': 'No thumbnail URL provided.'}), 400
    try:
        resp = http_req.get(thumb_url, stream=True, timeout=30, headers={
            'User-Agent': 'Mozilla/5.0'
        })
        ctype = resp.headers.get('Content-Type', 'image/jpeg')
        ext = 'jpg'
        if 'png' in ctype:
            ext = 'png'
        elif 'webp' in ctype:
            ext = 'webp'

        def generate():
            for chunk in resp.iter_content(chunk_size=32768):
                if chunk:
                    yield chunk

        return Response(
            stream_with_context(generate()),
            headers={
                'Content-Disposition': f'attachment; filename="{fname}_thumbnail.{ext}"',
                'Content-Type': ctype,
            }
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'service': 'Social Crazy Dr. Dev'})


# ─────────────────────────────────────────────
# HTML TEMPLATE
# ─────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Social Crazy Dr. Dev - Download unlimited Instagram & YouTube videos for free in highest quality.">
<title>Social Crazy Dr. Dev | Ultimate Video Downloader</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Exo+2:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
/* ── VARIABLES ── */
:root{
  --bg:#07071a;--bg2:#0d1130;--card:rgba(13,17,48,0.88);--cb:rgba(0,245,255,0.13);
  --cyan:#00f5ff;--pink:#ff006e;--purple:#8b5cf6;--green:#00ff9d;
  --text:#e2e8f0;--sub:#94a3b8;--inp:rgba(255,255,255,0.04);
  --sh:0 0 60px rgba(0,245,255,0.08);
  --gc:0 0 25px rgba(0,245,255,0.55);--gp:0 0 25px rgba(255,0,110,0.55);
  --r:14px;--trans:all .3s cubic-bezier(.4,0,.2,1);
}
[data-theme="light"]{
  --bg:#eef2ff;--bg2:#e0e7ff;--card:rgba(255,255,255,0.92);--cb:rgba(139,92,246,0.18);
  --text:#1e293b;--sub:#475569;--inp:rgba(0,0,0,0.04);--sh:0 4px 40px rgba(139,92,246,0.1);
}

/* ── RESET ── */
*{margin:0;padding:0;box-sizing:border-box;}
html{scroll-behavior:smooth;}
body{font-family:'Exo 2',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden;transition:var(--trans);}

/* ── SCROLLBAR ── */
::-webkit-scrollbar{width:6px;}
::-webkit-scrollbar-track{background:var(--bg);}
::-webkit-scrollbar-thumb{background:linear-gradient(var(--cyan),var(--pink));border-radius:3px;}

/* ── BACKGROUND ── */
.bg-wrap{position:fixed;inset:0;z-index:-2;overflow:hidden;}
.bg-blob{position:absolute;border-radius:50%;filter:blur(100px);opacity:.35;}
.blob1{width:700px;height:700px;background:radial-gradient(circle,rgba(0,245,255,.18),transparent 60%);top:-250px;left:-250px;animation:bm1 22s ease-in-out infinite;}
.blob2{width:600px;height:600px;background:radial-gradient(circle,rgba(255,0,110,.15),transparent 60%);bottom:-200px;right:-200px;animation:bm2 28s ease-in-out infinite;}
.blob3{width:400px;height:400px;background:radial-gradient(circle,rgba(139,92,246,.12),transparent 60%);top:50%;left:50%;transform:translate(-50%,-50%);animation:bm3 18s ease-in-out infinite;}
@keyframes bm1{0%,100%{transform:translate(0,0);}50%{transform:translate(120px,80px);}}
@keyframes bm2{0%,100%{transform:translate(0,0);}50%{transform:translate(-100px,-60px);}}
@keyframes bm3{0%,100%{transform:translate(-50%,-50%) scale(1);}50%{transform:translate(-50%,-50%) scale(1.3);}}

.grid-bg{position:fixed;inset:0;z-index:-1;
  background-image:linear-gradient(rgba(0,245,255,.025) 1px,transparent 1px),
                   linear-gradient(90deg,rgba(0,245,255,.025) 1px,transparent 1px);
  background-size:60px 60px;}
[data-theme="light"] .grid-bg{background-image:linear-gradient(rgba(139,92,246,.04) 1px,transparent 1px),linear-gradient(90deg,rgba(139,92,246,.04) 1px,transparent 1px);}

/* ── HEADER ── */
header{
  padding:16px 32px;display:flex;align-items:center;justify-content:space-between;
  border-bottom:1px solid var(--cb);background:rgba(7,7,26,.82);
  backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);
  position:sticky;top:0;z-index:200;
}
[data-theme="light"] header{background:rgba(238,242,255,.88);}

.logo{display:flex;align-items:center;gap:14px;text-decoration:none;}
.logo-icon{
  width:46px;height:46px;background:linear-gradient(135deg,var(--cyan),var(--purple),var(--pink));
  border-radius:12px;display:flex;align-items:center;justify-content:center;
  font-size:22px;animation:logo-pulse 3s ease-in-out infinite;flex-shrink:0;
}
@keyframes logo-pulse{
  0%,100%{box-shadow:0 0 20px rgba(0,245,255,.4);}
  50%{box-shadow:0 0 50px rgba(0,245,255,.8),0 0 80px rgba(255,0,110,.25);}
}
.logo-text .name{
  font-family:'Orbitron',sans-serif;font-weight:900;font-size:17px;
  background:linear-gradient(90deg,var(--cyan),var(--purple),var(--pink));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
  background-size:200% auto;animation:gshift 5s linear infinite;letter-spacing:.5px;
}
@keyframes gshift{0%{background-position:0%;}100%{background-position:200%;}}
.logo-text .tag{font-size:9px;color:var(--sub);letter-spacing:2.5px;text-transform:uppercase;margin-top:1px;}

.h-right{display:flex;align-items:center;gap:16px;}
.credit{text-align:right;}
.credit .cl{font-size:9px;color:var(--sub);text-transform:uppercase;letter-spacing:2px;}
.credit .cn{
  font-family:'Orbitron',sans-serif;font-size:13px;font-weight:700;
  background:linear-gradient(90deg,var(--pink),var(--purple));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
}

.theme-btn{
  width:52px;height:28px;background:var(--inp);border:1px solid var(--cb);
  border-radius:14px;cursor:pointer;position:relative;transition:var(--trans);
  display:flex;align-items:center;padding:4px;
}
.theme-btn::after{
  content:'🌙';font-size:14px;width:20px;height:20px;
  display:flex;align-items:center;justify-content:center;
  border-radius:50%;transition:transform .3s ease;
}
[data-theme="light"] .theme-btn::after{content:'☀️';transform:translateX(24px);}

/* ── MAIN ── */
main{max-width:960px;margin:0 auto;padding:48px 20px 60px;}

/* ── HERO ── */
.hero{text-align:center;margin-bottom:52px;}
.h-badge{
  display:inline-flex;align-items:center;gap:8px;
  background:linear-gradient(90deg,rgba(0,245,255,.1),rgba(255,0,110,.1));
  border:1px solid rgba(0,245,255,.25);border-radius:24px;
  padding:7px 20px;font-size:11px;color:var(--cyan);
  letter-spacing:2px;text-transform:uppercase;margin-bottom:22px;
  animation:badge-glow 2.5s ease-in-out infinite;
}
@keyframes badge-glow{0%,100%{border-color:rgba(0,245,255,.25);}50%{border-color:rgba(0,245,255,.7);box-shadow:0 0 20px rgba(0,245,255,.2);}}
.h-badge .dot{width:6px;height:6px;background:var(--cyan);border-radius:50%;animation:blink 1.2s infinite;}
@keyframes blink{0%,100%{opacity:1;}50%{opacity:.3;}}

.hero h1{
  font-family:'Orbitron',sans-serif;
  font-size:clamp(26px,5.5vw,56px);font-weight:900;
  line-height:1.15;margin-bottom:18px;letter-spacing:-1px;
}
.hero h1 .g{
  background:linear-gradient(90deg,var(--cyan),var(--purple),var(--pink));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
  background-size:200% auto;animation:gshift 4s linear infinite;
}
.hero p{color:var(--sub);font-size:15px;max-width:520px;margin:0 auto 32px;line-height:1.75;}

.stats{display:flex;justify-content:center;gap:36px;flex-wrap:wrap;}
.stat{text-align:center;}
.sn{font-family:'Orbitron',sans-serif;font-size:24px;font-weight:900;color:var(--cyan);}
.sl{font-size:10px;color:var(--sub);text-transform:uppercase;letter-spacing:1.5px;margin-top:2px;}

/* ── TABS ── */
.tabs{
  display:flex;gap:6px;background:var(--card);border:1px solid var(--cb);
  border-radius:16px;padding:6px;margin-bottom:20px;backdrop-filter:blur(20px);
}
.tab{
  flex:1;padding:13px 20px;border:none;border-radius:10px;cursor:pointer;
  font-family:'Exo 2',sans-serif;font-size:14px;font-weight:600;letter-spacing:.5px;
  transition:var(--trans);background:transparent;color:var(--sub);
  display:flex;align-items:center;justify-content:center;gap:8px;
}
.tab.on{background:linear-gradient(135deg,var(--cyan),var(--purple));color:#fff;box-shadow:0 4px 24px rgba(0,245,255,.3);}
.tab.on.yt{background:linear-gradient(135deg,#ff4444,#cc0000);box-shadow:0 4px 24px rgba(255,0,0,.35);}
.tab:hover:not(.on){color:var(--text);}

/* ── CARD ── */
.card{
  background:var(--card);border:1px solid var(--cb);border-radius:20px;
  padding:30px;backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);
  box-shadow:var(--sh);margin-bottom:22px;position:relative;overflow:hidden;
}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--cyan),var(--purple),var(--pink));}

/* ── ALERT ── */
.alert{
  padding:13px 16px;border-radius:10px;margin-bottom:18px;font-size:13.5px;
  display:none;align-items:center;gap:10px;animation:slideup .3s ease;
}
.alert.show{display:flex;}
.alert.err{background:rgba(255,68,68,.12);border:1px solid rgba(255,100,100,.3);color:#ff7878;}
.alert.ok{background:rgba(0,245,255,.08);border:1px solid rgba(0,245,255,.3);color:var(--cyan);}
.alert.inf{background:rgba(139,92,246,.1);border:1px solid rgba(139,92,246,.3);color:#c4b5fd;}
@keyframes slideup{from{opacity:0;transform:translateY(10px);}to{opacity:1;transform:translateY(0);}}

/* ── LOADING BAR ── */
.lbar{height:3px;background:var(--inp);border-radius:2px;overflow:hidden;display:none;margin-bottom:20px;}
.lbar.on{display:block;}
.lbar-fill{height:100%;width:35%;background:linear-gradient(90deg,var(--cyan),var(--pink));border-radius:2px;animation:lbar-anim 1.4s ease-in-out infinite;}
@keyframes lbar-anim{0%{margin-left:-35%;}100%{margin-left:130%;}}

/* ── INPUT GROUP ── */
.inp-grp{display:flex;gap:10px;margin-bottom:18px;}
.url-inp{
  flex:1;background:var(--inp);border:1px solid var(--cb);border-radius:12px;
  padding:14px 18px;color:var(--text);font-family:'Exo 2',sans-serif;font-size:14px;
  outline:none;transition:var(--trans);
}
.url-inp::placeholder{color:var(--sub);}
.url-inp:focus{border-color:var(--cyan);box-shadow:0 0 0 3px rgba(0,245,255,.1);}

.btn{
  padding:13px 22px;border:none;border-radius:12px;cursor:pointer;
  font-family:'Exo 2',sans-serif;font-size:14px;font-weight:600;letter-spacing:.3px;
  transition:var(--trans);display:inline-flex;align-items:center;gap:8px;white-space:nowrap;
}
.btn-c{background:linear-gradient(135deg,var(--cyan),var(--purple));color:#fff;box-shadow:0 4px 20px rgba(0,245,255,.22);}
.btn-c:hover{transform:translateY(-2px);box-shadow:0 8px 32px rgba(0,245,255,.4);}
.btn-p{background:linear-gradient(135deg,var(--pink),var(--purple));color:#fff;box-shadow:0 4px 20px rgba(255,0,110,.22);}
.btn-p:hover{transform:translateY(-2px);box-shadow:0 8px 32px rgba(255,0,110,.4);}
.btn-g{background:linear-gradient(135deg,var(--green),#00c97a);color:#07071a;}
.btn-g:hover{transform:translateY(-2px);}
.btn-out{background:transparent;border:1px solid var(--cb);color:var(--text);}
.btn-out:hover{border-color:var(--cyan);color:var(--cyan);}
.btn:disabled{opacity:.45;cursor:not-allowed;transform:none!important;box-shadow:none!important;}

/* ── VIDEO INFO ── */
.vinfo{display:none;gap:20px;background:var(--inp);border:1px solid var(--cb);border-radius:14px;padding:20px;margin-bottom:18px;}
.vinfo.show{display:flex;animation:slideup .4s ease;}

.thumb-wrap{flex-shrink:0;position:relative;}
.thumb-wrap img{width:170px;height:108px;object-fit:cover;border-radius:10px;border:1px solid var(--cb);display:block;}
.plat-badge{
  position:absolute;top:6px;left:6px;border-radius:6px;padding:3px 8px;
  font-size:9px;font-weight:800;color:#fff;text-transform:uppercase;letter-spacing:1.5px;
}
.plat-badge.instagram{background:linear-gradient(45deg,#f09433,#e6683c,#dc2743,#cc2366,#bc1888);}
.plat-badge.youtube{background:#ff0000;}

.vmeta{flex:1;min-width:0;}
.vtitle{font-weight:700;font-size:15px;margin-bottom:10px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
.mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px;margin-bottom:10px;}
.mi{display:flex;flex-direction:column;gap:2px;}
.ml{font-size:9px;color:var(--sub);text-transform:uppercase;letter-spacing:1.5px;}
.mv{font-size:13px;font-weight:700;color:var(--cyan);}
.vdesc{font-size:12px;color:var(--sub);line-height:1.6;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}

/* ── FORMAT SECTION ── */
.fsec{margin-bottom:18px;display:none;}
.fsec.show{display:block;}
.slabel{font-size:11px;color:var(--sub);text-transform:uppercase;letter-spacing:2px;margin-bottom:9px;display:flex;align-items:center;gap:7px;}
.fsel{
  width:100%;background:var(--inp);border:1px solid var(--cb);border-radius:10px;
  padding:12px 38px 12px 16px;color:var(--text);font-family:'Exo 2',sans-serif;font-size:14px;
  outline:none;cursor:pointer;transition:var(--trans);appearance:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='%2394a3b8'%3E%3Cpath d='M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center;background-size:16px;
}
.fsel:focus{border-color:var(--cyan);}
.fsel option{background:var(--bg2);}

/* ── ACTION BUTTONS ── */
.abts{display:none;gap:10px;flex-wrap:wrap;margin-top:4px;}
.abts.show{display:flex;}

/* ── PROGRESS ── */
.dl-prog{display:none;background:var(--inp);border:1px solid var(--cb);border-radius:10px;padding:16px;margin-top:14px;}
.dl-prog.show{display:block;animation:slideup .3s ease;}
.prog-lbl{font-size:13px;margin-bottom:9px;color:var(--sub);}
.pbar{height:7px;background:rgba(255,255,255,.05);border-radius:4px;overflow:hidden;}
.pfill{height:100%;background:linear-gradient(90deg,var(--cyan),var(--purple),var(--pink));border-radius:4px;transition:width .3s ease;animation:pglow 1.2s ease-in-out infinite;}
@keyframes pglow{0%,100%{box-shadow:0 0 10px rgba(0,245,255,.3);}50%{box-shadow:0 0 25px rgba(0,245,255,.7);}}

/* ── SECTION HEADER ── */
.sec-h{margin-bottom:26px;}
.sec-title{font-family:'Orbitron',sans-serif;font-size:20px;font-weight:700;margin-bottom:6px;background:linear-gradient(90deg,var(--cyan),var(--pink));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.sec-sub{color:var(--sub);font-size:13px;}

/* ── FEATURES ── */
.feat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(195px,1fr));gap:14px;margin-bottom:52px;}
.feat{
  background:var(--card);border:1px solid var(--cb);border-radius:16px;
  padding:22px;backdrop-filter:blur(20px);transition:var(--trans);cursor:default;
}
.feat:hover{transform:translateY(-5px);border-color:rgba(0,245,255,.35);box-shadow:0 12px 40px rgba(0,245,255,.12);}
.fi{font-size:30px;margin-bottom:12px;display:block;}
.ft{font-weight:700;font-size:13.5px;margin-bottom:6px;color:var(--cyan);}
.fd{font-size:12px;color:var(--sub);line-height:1.6;}

/* ── STEPS ── */
.steps-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:14px;margin-bottom:52px;}
.step{
  background:var(--card);border:1px solid var(--cb);border-radius:16px;
  padding:22px;backdrop-filter:blur(20px);text-align:center;transition:var(--trans);
}
.step:hover{transform:translateY(-4px);border-color:rgba(255,0,110,.35);}
.snum{
  width:42px;height:42px;background:linear-gradient(135deg,var(--cyan),var(--purple));
  border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-family:'Orbitron',sans-serif;font-weight:900;font-size:17px;color:#fff;
  margin:0 auto 14px;box-shadow:var(--gc);
}
.stitle{font-weight:700;font-size:14px;margin-bottom:7px;}
.sdesc{font-size:12px;color:var(--sub);line-height:1.6;}

/* ── TIPS BOX ── */
.tips{
  background:linear-gradient(135deg,rgba(0,245,255,.05),rgba(255,0,110,.05));
  border:1px solid rgba(0,245,255,.2);border-radius:16px;padding:22px 26px;
  margin-bottom:52px;
}
.tips-title{font-weight:700;font-size:14px;color:var(--cyan);margin-bottom:12px;display:flex;align-items:center;gap:8px;}
.tips ul{list-style:none;display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;}
.tips ul li{font-size:13px;color:var(--sub);display:flex;align-items:flex-start;gap:8px;}
.tips ul li::before{content:'→';color:var(--cyan);flex-shrink:0;}

/* ── FAQ ── */
.faqs{margin-bottom:52px;}
.faq{
  background:var(--card);border:1px solid var(--cb);border-radius:12px;
  margin-bottom:10px;backdrop-filter:blur(20px);overflow:hidden;transition:var(--trans);
}
.faq:hover{border-color:rgba(0,245,255,.25);}
.fq{
  padding:18px 20px;cursor:pointer;display:flex;align-items:center;justify-content:space-between;
  font-weight:600;font-size:14px;user-select:none;gap:12px;
}
.fq:hover{color:var(--cyan);}
.fq-icon{width:26px;height:26px;background:var(--inp);border-radius:50%;display:flex;align-items:center;justify-content:center;transition:transform .35s ease;flex-shrink:0;color:var(--cyan);font-size:13px;}
.faq.open .fq-icon{transform:rotate(180deg);background:rgba(0,245,255,.18);}
.fa{max-height:0;overflow:hidden;transition:max-height .45s ease,padding .3s ease;font-size:13.5px;color:var(--sub);line-height:1.75;padding:0 20px;}
.faq.open .fa{max-height:200px;padding-bottom:18px;}

/* ── FOOTER ── */
footer{text-align:center;padding:42px 20px;border-top:1px solid var(--cb);color:var(--sub);font-size:13px;}
.flogo{font-family:'Orbitron',sans-serif;font-size:17px;font-weight:700;background:linear-gradient(90deg,var(--cyan),var(--pink));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:10px;}
footer a{color:var(--cyan);text-decoration:none;}

/* ── UTILITIES ── */
.spin{display:inline-block;width:15px;height:15px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:sp .75s linear infinite;}
@keyframes sp{to{transform:rotate(360deg);}}
.mb-52{margin-bottom:52px;}

/* ── RESPONSIVE ── */
@media(max-width:768px){
  header{padding:14px 18px;}
  .logo-text .name{font-size:13px;}
  main{padding:32px 15px 50px;}
  .vinfo{flex-direction:column;}
  .thumb-wrap img{width:100%;height:190px;}
  .inp-grp{flex-direction:column;}
  .abts{flex-direction:column;}
  .abts .btn{justify-content:center;}
}
@media(max-width:480px){
  .credit{display:none;}
  .stats{gap:18px;}
  .sn{font-size:20px;}
}
</style>
</head>
<body>
<div class="bg-wrap">
  <div class="bg-blob blob1"></div>
  <div class="bg-blob blob2"></div>
  <div class="bg-blob blob3"></div>
</div>
<div class="grid-bg"></div>

<!-- ── HEADER ── -->
<header>
  <a class="logo" href="/">
    <div class="logo-icon">⚡</div>
    <div class="logo-text">
      <div class="name">Social Crazy Dr. Dev</div>
      <div class="tag">Ultimate Video Downloader</div>
    </div>
  </a>
  <div class="h-right">
    <div class="credit">
      <div class="cl">Created by</div>
      <div class="cn">Dr. Hamza</div>
    </div>
    <div class="theme-btn" id="themeBtn" title="Toggle Day / Night"></div>
  </div>
</header>

<!-- ── MAIN ── -->
<main>

<!-- HERO -->
<div class="hero">
  <div class="h-badge"><span class="dot"></span>Unlimited Downloads · Free Forever · No Sign‑Up</div>
  <h1>Download Any Video,<br><span class="g">Instantly &amp; Free.</span></h1>
  <p>The most powerful Instagram &amp; YouTube downloader. Grab videos, reels, thumbnails, and audio in the highest quality — zero limits, zero cost.</p>
  <div class="stats">
    <div class="stat"><div class="sn">∞</div><div class="sl">Downloads</div></div>
    <div class="stat"><div class="sn">4K</div><div class="sl">Max Quality</div></div>
    <div class="stat"><div class="sn">2</div><div class="sl">Platforms</div></div>
    <div class="stat"><div class="sn">0</div><div class="sl">Sign‑Up</div></div>
  </div>
</div>

<!-- TABS -->
<div class="tabs">
  <button class="tab on" id="tabIG" onclick="switchTab('instagram')">
    <i class="fab fa-instagram"></i> Instagram
  </button>
  <button class="tab" id="tabYT" onclick="switchTab('youtube')">
    <i class="fab fa-youtube"></i> YouTube
  </button>
</div>

<!-- DOWNLOADER CARD -->
<div class="card">
  <div class="alert" id="alertBox"><i class="fas fa-circle-info"></i><span id="alertMsg"></span></div>
  <div class="lbar" id="lbar"><div class="lbar-fill"></div></div>

  <!-- URL Input -->
  <div class="inp-grp">
    <input type="text" class="url-inp" id="urlInp"
      placeholder="🔗  Paste Instagram or YouTube URL here…"
      oninput="autoDetect()" onkeydown="if(event.key==='Enter')fetchInfo()">
    <button class="btn btn-c" id="fetchBtn" onclick="fetchInfo()">
      <i class="fas fa-search"></i> Fetch
    </button>
  </div>

  <!-- Video Info -->
  <div class="vinfo" id="vinfo">
    <div class="thumb-wrap">
      <img id="vthumb" src="" alt="thumbnail">
      <div class="plat-badge" id="vplat">–</div>
    </div>
    <div class="vmeta">
      <div class="vtitle" id="vtitle">—</div>
      <div class="mgrid" id="mgrid"></div>
      <div class="vdesc" id="vdesc"></div>
    </div>
  </div>

  <!-- Format -->
  <div class="fsec" id="fsec">
    <div class="slabel"><i class="fas fa-sliders"></i> Select Quality / Format</div>
    <select class="fsel" id="fsel"></select>
  </div>

  <!-- Buttons -->
  <div class="abts" id="abts">
    <button class="btn btn-c" id="dlBtn" onclick="dlVideo()">
      <i class="fas fa-download"></i> Download Video
    </button>
    <button class="btn btn-p" id="thumbBtn" onclick="dlThumb()">
      <i class="fas fa-image"></i> Thumbnail
    </button>
    <button class="btn btn-out" onclick="reset()">
      <i class="fas fa-rotate-right"></i> Reset
    </button>
  </div>

  <!-- Progress -->
  <div class="dl-prog" id="dlProg">
    <div class="prog-lbl" id="progLbl">Preparing…</div>
    <div class="pbar"><div class="pfill" id="pfill" style="width:0%"></div></div>
  </div>
</div>

<!-- FEATURES -->
<div class="mb-52">
  <div class="sec-h">
    <div class="sec-title">⚡ Powerful Features</div>
    <div class="sec-sub">Everything you need in one place</div>
  </div>
  <div class="feat-grid">
    <div class="feat"><span class="fi">📸</span><div class="ft">Instagram Posts</div><div class="fd">Download photos, videos, carousels, and Reels from any public Instagram post.</div></div>
    <div class="feat"><span class="fi">🎬</span><div class="ft">YouTube Videos</div><div class="fd">Download YouTube videos up to 4K UHD with multiple format & quality options.</div></div>
    <div class="feat"><span class="fi">🖼️</span><div class="ft">Thumbnail Saver</div><div class="fd">Grab full-resolution thumbnails from both Instagram posts and YouTube videos.</div></div>
    <div class="feat"><span class="fi">🎵</span><div class="ft">Audio Extraction</div><div class="fd">Extract high-quality MP3 audio from any YouTube video with one click.</div></div>
    <div class="feat"><span class="fi">⚙️</span><div class="ft">Quality Selector</div><div class="fd">Choose from 144p to 4K. Default always picks the highest available quality.</div></div>
    <div class="feat"><span class="fi">📊</span><div class="ft">Video Details</div><div class="fd">View full metadata: title, uploader, date, views, likes, and description.</div></div>
    <div class="feat"><span class="fi">∞</span><div class="ft">No Limits</div><div class="fd">Unlimited downloads per day — no account, no subscription, no restrictions.</div></div>
    <div class="feat"><span class="fi">🌙</span><div class="ft">Day / Night Mode</div><div class="fd">Switch between a sleek dark mode and a crisp light mode instantly.</div></div>
  </div>
</div>

<!-- HOW TO USE -->
<div class="mb-52">
  <div class="sec-h">
    <div class="sec-title">📖 How to Use</div>
    <div class="sec-sub">Download any video in 4 simple steps</div>
  </div>
  <div class="steps-grid">
    <div class="step">
      <div class="snum">1</div>
      <div class="stitle">Select Platform</div>
      <div class="sdesc">Choose the <strong>Instagram</strong> or <strong>YouTube</strong> tab. The platform is also auto‑detected from the URL you paste.</div>
    </div>
    <div class="step">
      <div class="snum">2</div>
      <div class="stitle">Paste URL</div>
      <div class="sdesc">Copy the video or post link from Instagram / YouTube and paste it into the input field above.</div>
    </div>
    <div class="step">
      <div class="snum">3</div>
      <div class="stitle">Fetch Details</div>
      <div class="sdesc">Click <strong>Fetch</strong> to load video info — title, uploader, date, views, and available quality options.</div>
    </div>
    <div class="step">
      <div class="snum">4</div>
      <div class="stitle">Download</div>
      <div class="sdesc">Pick your preferred quality, then hit <strong>Download Video</strong>, <strong>Thumbnail</strong>, or the audio option.</div>
    </div>
  </div>

  <div class="tips">
    <div class="tips-title"><i class="fas fa-lightbulb"></i> Pro Tips</div>
    <ul>
      <li>Only <strong>public</strong> Instagram accounts can be downloaded.</li>
      <li>For YouTube, <em>Best Quality</em> auto‑merges the best video + audio streams.</li>
      <li>Select <em>Audio Only (MP3)</em> to download music from YouTube videos.</li>
      <li>Instagram Reels and IGTV links are fully supported.</li>
      <li>Thumbnails are saved in the highest resolution available.</li>
      <li>Auto platform detection — just paste any URL, no need to switch tabs.</li>
    </ul>
  </div>
</div>

<!-- FAQ -->
<div class="faqs mb-52">
  <div class="sec-h">
    <div class="sec-title">❓ Frequently Asked Questions</div>
    <div class="sec-sub">Quick answers to common questions</div>
  </div>

  <div class="faq" onclick="toggleFaq(this)">
    <div class="fq">Is Social Crazy Dr. Dev completely free to use?<div class="fq-icon"><i class="fas fa-chevron-down"></i></div></div>
    <div class="fa">Yes — 100% free with no limitations whatsoever. There is no registration, no subscription, and no daily download cap. You can download as many videos as you like from Instagram and YouTube at any time, completely free of charge.</div>
  </div>

  <div class="faq" onclick="toggleFaq(this)">
    <div class="fq">What video quality can I download?<div class="fq-icon"><i class="fas fa-chevron-down"></i></div></div>
    <div class="fa">For YouTube, quality ranges from 144p all the way up to 4K UHD (2160p), depending on what the video owner uploaded. The default <em>Best Quality</em> option automatically selects the highest available resolution. You can also choose lower resolutions for smaller file sizes, or grab MP3 audio only. For Instagram, videos are downloaded in the original uploaded quality.</div>
  </div>

  <div class="faq" onclick="toggleFaq(this)">
    <div class="fq">Can I download private Instagram posts?<div class="fq-icon"><i class="fas fa-chevron-down"></i></div></div>
    <div class="fa">No — this tool only works with <strong>public</strong> Instagram accounts and posts. Private accounts require authentication that we do not collect or store. This is intentional to respect user privacy and comply with Instagram's terms of service. If a post is from a public account but fails, make sure the URL is correct and the account hasn't been deactivated.</div>
  </div>

  <div class="faq" onclick="toggleFaq(this)">
    <div class="fq">What types of Instagram content can I download?<div class="fq-icon"><i class="fas fa-chevron-down"></i></div></div>
    <div class="fa">You can download Instagram <strong>Photos</strong>, <strong>Videos</strong>, <strong>Reels</strong>, and <strong>IGTV</strong> videos from any public profile. Carousel (multiple images/videos) posts are also supported — the first media item is downloaded. Thumbnails for all post types can be saved separately using the Thumbnail button.</div>
  </div>

  <div class="faq" onclick="toggleFaq(this)">
    <div class="fq">Is it legal to download Instagram and YouTube videos?<div class="fq-icon"><i class="fas fa-chevron-down"></i></div></div>
    <div class="fa">Downloading videos for <strong>personal, offline viewing</strong> is generally accepted in most countries. However, redistributing, re‑uploading, selling, or using downloaded content commercially without the creator's explicit permission may violate copyright law and the platforms' terms of service. Always respect the original creator's intellectual property rights. This tool is intended for personal use only.</div>
  </div>
</div>

</main>

<!-- FOOTER -->
<footer>
  <div class="flogo">Social Crazy Dr. Dev</div>
  <p>Designed &amp; Developed with ❤️ by <strong style="color:var(--pink)">Dr. Hamza</strong></p>
  <p style="margin-top:8px">Download unlimited videos from Instagram &amp; YouTube · Free forever</p>
  <p style="margin-top:12px;font-size:11px;opacity:.45">For personal use only · Respect copyright and content creator rights · Not affiliated with Instagram or YouTube</p>
</footer>

<script>
/* ─── STATE ─── */
let curPlatform = 'instagram';
let curInfo = null;

/* ─── THEME ─── */
(function(){
  const t = localStorage.getItem('theme') || 'dark';
  document.documentElement.dataset.theme = t;
})();
document.getElementById('themeBtn').onclick = () => {
  const h = document.documentElement;
  h.dataset.theme = h.dataset.theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('theme', h.dataset.theme);
};

/* ─── TAB SWITCH ─── */
function switchTab(p) {
  curPlatform = p;
  const ig = document.getElementById('tabIG');
  const yt = document.getElementById('tabYT');
  ig.className = 'tab' + (p==='instagram' ? ' on' : '');
  yt.className = 'tab' + (p==='youtube' ? ' on yt' : '');
  document.getElementById('urlInp').placeholder =
    p==='instagram'
      ? '🔗  Paste Instagram URL (post / reel / IGTV)…'
      : '🔗  Paste YouTube URL (video / shorts / playlist)…';
  reset();
}

/* ─── AUTO DETECT ─── */
function autoDetect() {
  const v = document.getElementById('urlInp').value;
  if ((v.includes('youtube.com')||v.includes('youtu.be')) && curPlatform!=='youtube') switchTab('youtube');
  else if (v.includes('instagram.com') && curPlatform!=='instagram') switchTab('instagram');
}

/* ─── ALERT ─── */
function showAlert(msg, type='err') {
  const b = document.getElementById('alertBox');
  b.className = `alert show ${type}`;
  document.getElementById('alertMsg').textContent = msg;
  if(type!=='err') setTimeout(()=>b.className='alert', 5000);
}
function hideAlert(){ document.getElementById('alertBox').className='alert'; }

/* ─── LOADING ─── */
function setLoad(on) {
  document.getElementById('lbar').className = 'lbar' + (on?' on':'');
  const btn = document.getElementById('fetchBtn');
  btn.innerHTML = on ? '<div class="spin"></div> Fetching…' : '<i class="fas fa-search"></i> Fetch';
  btn.disabled = on;
}

/* ─── FETCH INFO ─── */
async function fetchInfo() {
  const url = document.getElementById('urlInp').value.trim();
  if (!url) { showAlert('Please paste a URL first!'); return; }
  setLoad(true); hideAlert(); clearInfo();
  try {
    const r = await fetch('/fetch-info', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url, platform:curPlatform})
    });
    const d = await r.json();
    if (d.error) { showAlert(d.error, 'err'); }
    else { renderInfo(d.info); showAlert('Video info loaded successfully!','ok'); }
  } catch(e) {
    showAlert('Network error — check your connection and try again.','err');
  } finally { setLoad(false); }
}

/* ─── RENDER INFO ─── */
function renderInfo(info) {
  curInfo = info;

  /* thumbnail */
  const img = document.getElementById('vthumb');
  img.src = info.thumbnail || '';
  img.onerror = () => img.src = 'https://placehold.co/170x108/07071a/00f5ff?text=No+Thumb';

  /* platform badge */
  const pb = document.getElementById('vplat');
  pb.textContent = info.platform;
  pb.className = 'plat-badge ' + info.platform;

  /* title */
  document.getElementById('vtitle').textContent = info.title || '—';

  /* meta grid */
  const items = [];
  if (info.uploader)        items.push({l:'Uploaded By', v:'@'+info.uploader});
  if (info.uploader_full && info.uploader_full !== info.uploader)
                            items.push({l:'Full Name',   v:info.uploader_full});
  if (info.date)            items.push({l:'Date',        v:info.date});
  if (info.view_count)      items.push({l:'Views',       v:fmtN(info.view_count)});
  if (info.like_count||info.likes) items.push({l:'Likes', v:fmtN(info.like_count||info.likes)});
  if (info.duration)        items.push({l:'Duration',    v:fmtDur(info.duration)});
  if (info.comments||info.comment_count) items.push({l:'Comments', v:fmtN(info.comments||info.comment_count)});
  if (info.channel_follower_count) items.push({l:'Subscribers', v:fmtN(info.channel_follower_count)});

  document.getElementById('mgrid').innerHTML = items.map(m=>
    `<div class="mi"><span class="ml">${m.l}</span><span class="mv">${m.v}</span></div>`
  ).join('');

  /* description */
  document.getElementById('vdesc').textContent = info.description || '';

  /* formats */
  const sel = document.getElementById('fsel');
  sel.innerHTML = '';
  (info.formats||[]).forEach((f,i) => {
    const o = document.createElement('option');
    o.value = f.format_id;
    const sz = f.filesize ? ` · ${fmtBytes(f.filesize)}` : '';
    const ext = f.ext ? ` [${f.ext}]` : '';
    o.textContent = `${f.quality}${ext}${sz}`;
    if(i===0) o.selected = true;
    sel.appendChild(o);
  });

  /* show sections */
  document.getElementById('vinfo').className = 'vinfo show';
  document.getElementById('fsec').className  = 'fsec show';
  document.getElementById('abts').className  = 'abts show';
}

/* ─── DOWNLOAD VIDEO ─── */
async function dlVideo() {
  if (!curInfo) return;
  const fmt = document.getElementById('fsel').value;
  const fname = sfn(curInfo.title || 'video');
  const btn = document.getElementById('dlBtn');
  btn.innerHTML = '<div class="spin"></div> Preparing…';
  btn.disabled = true;

  const prog = document.getElementById('dlProg');
  const pfill = document.getElementById('pfill');
  const plbl  = document.getElementById('progLbl');
  prog.className = 'dl-prog show';

  let pct = 0;
  const ticker = setInterval(() => {
    pct = Math.min(pct + Math.random()*2.5, 88);
    pfill.style.width = pct+'%';
    plbl.textContent = `Downloading… ${Math.round(pct)}%`;
  }, 250);

  try {
    const r = await fetch('/download', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url:curInfo.url, format_id:fmt, platform:curInfo.platform, title:curInfo.title})
    });
    if (!r.ok) {
      const e = await r.json().catch(()=>({error:'Download failed'}));
      throw new Error(e.error||'Download failed');
    }
    clearInterval(ticker);
    pfill.style.width='100%';
    plbl.textContent='Finalising… ✅';

    const blob = await r.blob();
    const isAudio = fmt.includes('bestaudio') && !fmt.includes('bestvideo');
    const ext = isAudio ? 'mp3' : 'mp4';
    triggerDownload(blob, `${fname}.${ext}`);
    showAlert('Download started! Check your downloads folder.','ok');
  } catch(e) {
    clearInterval(ticker);
    showAlert('Download failed: '+e.message,'err');
    prog.className='dl-prog';
  } finally {
    setTimeout(()=>{
      btn.innerHTML='<i class="fas fa-download"></i> Download Video';
      btn.disabled=false;
      prog.className='dl-prog';
    },2500);
  }
}

/* ─── DOWNLOAD THUMBNAIL ─── */
async function dlThumb() {
  if (!curInfo||!curInfo.thumbnail) { showAlert('No thumbnail available.','err'); return; }
  const btn = document.getElementById('thumbBtn');
  btn.innerHTML='<div class="spin"></div> Saving…';
  btn.disabled=true;
  try {
    const r = await fetch('/download-thumbnail',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({thumbnail_url:curInfo.thumbnail, filename:sfn(curInfo.title||'thumbnail')})
    });
    if(!r.ok) throw new Error('Failed');
    const blob = await r.blob();
    triggerDownload(blob, sfn(curInfo.title||'thumbnail')+'_thumbnail.jpg');
    showAlert('Thumbnail saved!','ok');
  } catch(e) {
    showAlert('Failed to download thumbnail: '+e.message,'err');
  } finally {
    btn.innerHTML='<i class="fas fa-image"></i> Thumbnail';
    btn.disabled=false;
  }
}

/* ─── HELPERS ─── */
function triggerDownload(blob, name) {
  const u = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href=u; a.download=name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(()=>URL.revokeObjectURL(u), 5000);
}

function clearInfo() {
  document.getElementById('vinfo').className='vinfo';
  document.getElementById('fsec').className='fsec';
  document.getElementById('abts').className='abts';
  document.getElementById('dlProg').className='dl-prog';
  curInfo=null;
}

function reset() {
  document.getElementById('urlInp').value='';
  clearInfo(); hideAlert();
}

function toggleFaq(el){ el.classList.toggle('open'); }

function fmtN(n){
  if(!n) return '0';
  n=Number(n);
  if(n>=1e9) return (n/1e9).toFixed(1)+'B';
  if(n>=1e6) return (n/1e6).toFixed(1)+'M';
  if(n>=1e3) return (n/1e3).toFixed(1)+'K';
  return n.toLocaleString();
}
function fmtDur(s){
  s=Math.round(Number(s)||0);
  const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=s%60;
  return h>0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}
function pad(n){ return String(n).padStart(2,'0'); }
function fmtBytes(b){
  if(!b) return '';
  b=Number(b);
  if(b>=1e9) return (b/1e9).toFixed(1)+' GB';
  if(b>=1e6) return (b/1e6).toFixed(1)+' MB';
  return (b/1e3).toFixed(0)+' KB';
}
function sfn(s){ return String(s).replace(/[^\w\s-]/g,'').trim().replace(/\s+/g,'_').substring(0,55)||'download'; }
</script>
</body>
</html>"""


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    print(f"🚀 Social Crazy Dr. Dev — starting on port {port}")
    app.run(host='0.0.0.0', port=port, debug=debug)
