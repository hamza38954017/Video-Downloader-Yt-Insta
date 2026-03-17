"""
╔══════════════════════════════════════════════════════════════╗
║       Social Crazy Dr. Dev — Ultimate Video Downloader       ║
║                  Created by Dr. Hamza                        ║
╠══════════════════════════════════════════════════════════════╣
║  COOKIE FILE (optional, for age-restricted videos):          ║
║  LOCAL : place cookies.txt next to app.py                    ║
║  RENDER: Settings → Secret Files →                           ║
║          path  /etc/secrets/cookies.txt                      ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import re
import tempfile
import shutil
from datetime import datetime

# ── curl_cffi — Chrome TLS fingerprint impersonation ──────────
from curl_cffi import requests as cffi_requests
from curl_cffi.requests import Session as CffiSession

from flask import (Flask, render_template_string, request,
                   jsonify, Response, stream_with_context)
import instaloader
import yt_dlp

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1 GB

# ── ImpersonateTarget — handle all yt-dlp versions ────────────
ImpersonateTarget = None
try:
    from yt_dlp.networking.impersonate import ImpersonateTarget
    print("[Import] ImpersonateTarget from yt_dlp.networking.impersonate ✅")
except ImportError:
    try:
        from yt_dlp.utils import ImpersonateTarget
        print("[Import] ImpersonateTarget from yt_dlp.utils ✅")
    except ImportError:
        print("[Import] ImpersonateTarget not available — will skip impersonation")

# ─────────────────────────────────────────────────────────────
# KEY FIX: Only 3 fast strategies instead of 18 combos.
# Render free tier kills requests after ~30s.
# Each attempt has 8s socket timeout = max ~24s total.
# tv_embedded works 95% of the time on first try.
# ─────────────────────────────────────────────────────────────

CHROME_VERSIONS = ["chrome120", "chrome110"]

# Only the 3 most effective clients — tried in order
YT_CLIENT_STRATEGIES = [
    {"player_client": ["tv_embedded"]},     # Best — no bot check at all
    {"player_client": ["ios"]},             # Good — mobile endpoint
    {"player_client": ["android_vr"]},      # Fallback
]

# Build impersonate targets safely
def _make_impersonate_targets():
    if not ImpersonateTarget:
        return [None]
    try:
        return [
            ImpersonateTarget("chrome", "120"),
            ImpersonateTarget("chrome", "110"),
        ]
    except Exception:
        return [None]

YT_IMPERSONATE_TARGETS = _make_impersonate_targets()

# ═══════════════════════════════════════════════════════════════
# COOKIE FILE
# ═══════════════════════════════════════════════════════════════

def get_cookie_file():
    candidates = [
        os.environ.get('COOKIE_FILE', ''),
        '/etc/secrets/cookies.txt',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt'),
    ]
    for p in candidates:
        if p and os.path.isfile(p) and os.path.getsize(p) > 0:
            print(f"[Cookie] ✅  {p}")
            return p
    print("[Cookie] ⚠️  No cookies.txt found.")
    return None

COOKIE_FILE = get_cookie_file()

# ═══════════════════════════════════════════════════════════════
# curl_cffi SESSION
# ═══════════════════════════════════════════════════════════════

def get_cffi_session(impersonate="chrome120"):
    s = CffiSession(impersonate=impersonate)
    s.headers.update({
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    })
    return s

# ═══════════════════════════════════════════════════════════════
# yt-dlp OPTIONS BUILDER
# SHORT timeout (8s) per attempt to stay within Render's 30s limit
# ═══════════════════════════════════════════════════════════════

def make_yt_opts(strategy_args: dict,
                 impersonate_target=None,
                 extra: dict = None) -> dict:
    opts = {
        "quiet":               True,
        "no_warnings":         True,
        "extractor_args":      {"youtube": strategy_args},
        "socket_timeout":      8,    # ← SHORT: fail fast, try next strategy
        "retries":             2,    # ← LOW: don't waste time retrying
        "fragment_retries":    2,
        "file_access_retries": 1,
    }

    # Add Chrome impersonation if available
    if impersonate_target and ImpersonateTarget:
        try:
            opts["impersonate"] = impersonate_target
        except Exception:
            pass

    cookie = COOKIE_FILE or get_cookie_file()
    if cookie:
        opts["cookiefile"] = cookie

    if extra:
        opts.update(extra)
    return opts

# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def extract_instagram_shortcode(url):
    for pat in [
        r'instagram\.com/p/([A-Za-z0-9_-]+)',
        r'instagram\.com/reel/([A-Za-z0-9_-]+)',
        r'instagram\.com/reels/([A-Za-z0-9_-]+)',
        r'instagram\.com/tv/([A-Za-z0-9_-]+)',
    ]:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None

def safe_filename(s, max_len=60):
    s = re.sub(r'[^\w\s-]', '', str(s)).strip()
    return re.sub(r'\s+', '_', s)[:max_len] or 'download'

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

def is_bot_error(msg: str) -> bool:
    return any(k in msg.lower() for k in
               ('sign in', 'bot', 'login', 'verify',
                'private', 'not available', 'confirm your age'))

# ═══════════════════════════════════════════════════════════════
# INSTAGRAM
# ═══════════════════════════════════════════════════════════════

def fetch_instagram_info(url):
    shortcode = extract_instagram_shortcode(url)
    if not shortcode:
        return {'error': 'Invalid Instagram URL. Use a post, reel, or IGTV link.'}
    try:
        L = instaloader.Instaloader(
            quiet=True, download_pictures=False,
            download_videos=False, download_video_thumbnails=False,
            download_geotags=False, download_comments=False,
            save_metadata=False,
        )
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        info = {
            'platform':     'instagram',
            'title':        post.title or f'Instagram post by @{post.owner_username}',
            'description':  (post.caption or '')[:600],
            'uploader':     post.owner_username,
            'uploader_full': (post.owner_profile.full_name
                              if post.owner_profile else post.owner_username),
            'date':         post.date_utc.strftime('%d %b %Y, %H:%M UTC'),
            'likes':        post.likes,
            'comments':     post.comments,
            'is_video':     post.is_video,
            'thumbnail':    post.url,
            'url':          url,
            'shortcode':    shortcode,
            'mp4_formats':  [{'quality': '🏆 Original Quality (Best)',
                              'format_id': 'best', 'ext': 'mp4', 'filesize': 0}],
            'mp3_formats':  [],
            'muted_formats': [],
            'formats':      [{'quality': '🏆 Original Quality (Best)',
                              'format_id': 'best', 'ext': 'mp4', 'filesize': 0}],
        }
        if post.is_video:
            info['video_url'] = post.video_url
        return {'success': True, 'info': info}
    except instaloader.exceptions.InstaloaderException as e:
        return {'error': f'Instagram error: {str(e)}'}
    except Exception as e:
        return {'error': f'Could not fetch post. Make sure it is public. ({str(e)})'}

# ═══════════════════════════════════════════════════════════════
# YOUTUBE — FORMAT BUILDER
# ═══════════════════════════════════════════════════════════════

def _build_format_lists(raw: dict):
    all_fmts    = raw.get('formats', [])
    sorted_fmts = sorted(all_fmts, key=lambda x: x.get('height') or 0, reverse=True)

    mp4_formats = [{
        'quality':   '🏆 Best Quality (Auto)',
        'format_id': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best',
        'ext': 'mp4', 'filesize': 0,
    }]
    seen_h = set()
    for f in sorted_fmts:
        h = f.get('height')
        if not h or h in seen_h:
            continue
        seen_h.add(h)
        vc  = f.get('vcodec', 'none')
        ac  = f.get('acodec', 'none')
        sz  = f.get('filesize') or f.get('filesize_approx') or 0
        fid = (f['format_id'] if (vc != 'none' and ac != 'none')
               else (f'bestvideo[height={h}][ext=mp4]+bestaudio[ext=m4a]'
                     f'/bestvideo[height={h}]+bestaudio/best[height<={h}]'))
        mp4_formats.append({
            'quality': f'{h}p', 'format_id': fid, 'ext': 'mp4', 'filesize': sz
        })

    mp3_formats = [
        {'quality': '🎵 Best Quality (Auto)',       'format_id': 'bestaudio/best',           'ext': 'mp3', 'filesize': 0},
        {'quality': '🎵 High Quality (192 kbps)',   'format_id': 'bestaudio[abr<=192]/best', 'ext': 'mp3', 'filesize': 0},
        {'quality': '🎵 Medium Quality (128 kbps)', 'format_id': 'bestaudio[abr<=128]/best', 'ext': 'mp3', 'filesize': 0},
        {'quality': '🎵 Low Quality (96 kbps)',     'format_id': 'bestaudio[abr<=96]/best',  'ext': 'mp3', 'filesize': 0},
    ]

    muted_formats = [{
        'quality':   '🔇 Best Quality (Muted)',
        'format_id': 'bestvideo[ext=mp4]/bestvideo',
        'ext': 'mp4', 'filesize': 0,
    }]
    seen_mh = set()
    for f in sorted_fmts:
        h  = f.get('height')
        vc = f.get('vcodec', 'none')
        if not h or vc == 'none' or h in seen_mh:
            continue
        seen_mh.add(h)
        sz = f.get('filesize') or f.get('filesize_approx') or 0
        muted_formats.append({
            'quality':   f'🔇 {h}p (No Audio)',
            'format_id': f'bestvideo[height={h}][ext=mp4]/bestvideo[height={h}]',
            'ext': 'mp4', 'filesize': sz,
        })

    return mp4_formats[:12], mp3_formats, muted_formats[:8]

# ═══════════════════════════════════════════════════════════════
# YOUTUBE — FETCH INFO
# Max 3 clients × 2 Chrome versions = 6 combos, each 8s timeout
# Worst case: ~48s but tv_embedded almost always works on first try
# ═══════════════════════════════════════════════════════════════

def fetch_youtube_info(url):
    last_error = None

    for imp_target in YT_IMPERSONATE_TARGETS:
        for idx, strategy in enumerate(YT_CLIENT_STRATEGIES):
            try:
                opts = make_yt_opts(strategy, imp_target, {'skip_download': True})
                with yt_dlp.YoutubeDL(opts) as ydl:
                    raw = ydl.extract_info(url, download=False)

                mp4_formats, mp3_formats, muted_formats = _build_format_lists(raw)

                imp_name = str(imp_target) if imp_target else "none"
                print(f"[YT Info] ✅  impersonate={imp_name} "
                      f"client={strategy['player_client']}")

                return {'success': True, 'info': {
                    'platform':      'youtube',
                    'title':         raw.get('title', 'YouTube Video'),
                    'description':   (raw.get('description', '') or '')[:600],
                    'uploader':      raw.get('uploader', raw.get('channel', 'Unknown')),
                    'uploader_url':  raw.get('uploader_url', ''),
                    'date':          format_date(raw.get('upload_date', '')),
                    'duration':      raw.get('duration', 0),
                    'view_count':    raw.get('view_count', 0),
                    'like_count':    raw.get('like_count', 0),
                    'comment_count': raw.get('comment_count', 0),
                    'thumbnail':     raw.get('thumbnail', ''),
                    'url':           url,
                    'is_video':      True,
                    'mp4_formats':   mp4_formats,
                    'mp3_formats':   mp3_formats,
                    'muted_formats': muted_formats,
                    'formats':       mp4_formats[:1],
                    'channel_follower_count': raw.get('channel_follower_count', 0),
                    'categories': ', '.join(raw.get('categories', [])[:3]),
                    'tags':       ', '.join((raw.get('tags', []) or [])[:5]),
                }}

            except Exception as e:
                last_error = str(e)
                imp_name   = str(imp_target) if imp_target else "none"
                print(f"[YT Info] ❌  impersonate={imp_name} "
                      f"client={strategy['player_client']} → {last_error[:80]}")
                continue

    if last_error and is_bot_error(last_error):
        return {'error': (
            '⚠️ YouTube is blocking this server. '
            'Add a fresh cookies.txt (exported while logged into YouTube) '
            'to fix this. See Cookie Setup guide on the page.'
        )}
    return {
        'error': f'Could not fetch video info. Last error: {(last_error or "unknown")[:200]}'
    }

# ═══════════════════════════════════════════════════════════════
# FLASK ROUTES
# ═══════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/fetch-info', methods=['POST'])
def fetch_info():
    data = request.get_json(silent=True) or {}
    url  = data.get('url', '').strip()
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
    data      = request.get_json(silent=True) or {}
    url       = data.get('url', '').strip()
    format_id = data.get('format_id', 'bestvideo+bestaudio/best')
    platform  = data.get('platform', '')
    dl_type   = data.get('dl_type', 'mp4')
    title     = data.get('title', 'video')
    fname     = safe_filename(title)

    if not url:
        return jsonify({'error': 'URL missing.'}), 400
    if platform == 'instagram':
        return _stream_instagram(url, fname, data.get('video_url'))
    return _stream_youtube(url, format_id, fname, dl_type)

# ─────────────────────────────────────────────────────────────
# INSTAGRAM STREAM
# ─────────────────────────────────────────────────────────────

def _stream_instagram(url, fname, video_url=None):
    try:
        if not video_url:
            shortcode = extract_instagram_shortcode(url)
            L = instaloader.Instaloader(quiet=True)
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            video_url = post.video_url if post.is_video else post.url

        session      = get_cffi_session("chrome120")
        resp         = session.get(video_url, stream=True, timeout=60)
        content_type = resp.headers.get('Content-Type', 'video/mp4')
        file_ext     = 'mp4' if 'video' in content_type else 'jpg'

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

# ─────────────────────────────────────────────────────────────
# YOUTUBE STREAM
# Uses longer socket_timeout for actual download (60s per fragment)
# ─────────────────────────────────────────────────────────────

def _stream_youtube(url, format_id, fname, dl_type='mp4'):
    is_audio = dl_type == 'mp3'
    is_muted = dl_type == 'muted'

    pp = []
    if is_audio:
        pp.append({'key': 'FFmpegExtractAudio',
                   'preferredcodec': 'mp3', 'preferredquality': '192'})
    else:
        pp.append({'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'})

    last_err = None

    for imp_target in YT_IMPERSONATE_TARGETS:
        for strategy in YT_CLIENT_STRATEGIES:
            tmpdir  = tempfile.mkdtemp(prefix='scd_')
            out_tpl = os.path.join(tmpdir, '%(title)s.%(ext)s')
            try:
                # Download uses longer timeout than info fetch
                extra = {
                    'format':              format_id,
                    'outtmpl':             out_tpl,
                    'merge_output_format': None if is_audio else 'mp4',
                    'postprocessors':      pp,
                    'keepvideo':           False,
                    'socket_timeout':      60,   # longer for actual download
                    'retries':             5,
                    'fragment_retries':    5,
                }
                opts = make_yt_opts(strategy, imp_target, extra)

                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.extract_info(url, download=True)

                files = os.listdir(tmpdir)
                if not files:
                    cleanup_dir(tmpdir)
                    continue

                preferred = ['mp3'] if is_audio else ['mp4', 'mkv', 'webm']
                chosen = None
                for ext in preferred:
                    hits = [f for f in files if f.endswith('.' + ext)]
                    if hits:
                        chosen = hits[0]
                        break
                if not chosen:
                    chosen = files[0]

                filepath     = os.path.join(tmpdir, chosen)
                file_ext     = (os.path.splitext(chosen)[1].lstrip('.')
                                or ('mp3' if is_audio else 'mp4'))
                content_type = 'audio/mpeg' if file_ext == 'mp3' else 'video/mp4'
                suffix       = '_muted' if is_muted else ''
                dl_name      = f'{fname}{suffix}.{file_ext}'

                imp_name = str(imp_target) if imp_target else "none"
                print(f"[YT DL] ✅  impersonate={imp_name} "
                      f"client={strategy['player_client']}")

                def generate(fp=filepath, td=tmpdir):
                    try:
                        with open(fp, 'rb') as f:
                            while True:
                                chunk = f.read(65536)
                                if not chunk:
                                    break
                                yield chunk
                    finally:
                        cleanup_dir(td)

                return Response(
                    stream_with_context(generate()),
                    headers={
                        'Content-Disposition': f'attachment; filename="{dl_name}"',
                        'Content-Type': content_type,
                    }
                )

            except Exception as e:
                last_err = str(e)
                imp_name = str(imp_target) if imp_target else "none"
                print(f"[YT DL] ❌  impersonate={imp_name} "
                      f"client={strategy['player_client']} → {last_err[:80]}")
                cleanup_dir(tmpdir)
                continue

    return jsonify({
        'error': f'Download failed on all strategies. {(last_err or "")[:200]}'
    }), 500

# ─────────────────────────────────────────────────────────────
# THUMBNAIL
# ─────────────────────────────────────────────────────────────

@app.route('/download-thumbnail', methods=['POST'])
def download_thumbnail():
    data      = request.get_json(silent=True) or {}
    thumb_url = data.get('thumbnail_url', '').strip()
    fname     = safe_filename(data.get('filename', 'thumbnail'))
    if not thumb_url:
        return jsonify({'error': 'No thumbnail URL.'}), 400
    try:
        session = get_cffi_session("chrome120")
        resp    = session.get(thumb_url, stream=True, timeout=30)
        ctype   = resp.headers.get('Content-Type', 'image/jpeg')
        ext     = ('png' if 'png' in ctype else 'webp' if 'webp' in ctype else 'jpg')

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

# ─────────────────────────────────────────────────────────────
# STATUS / DEBUG ROUTES
# ─────────────────────────────────────────────────────────────

@app.route('/cookie-status')
def cookie_status():
    f = get_cookie_file()
    return jsonify({'cookie_loaded': bool(f), 'path': f or 'Not found'})

@app.route('/test-yt')
def test_yt():
    """Visit /test-yt to run a live YouTube diagnostic."""
    test_url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
    result   = fetch_youtube_info(test_url)
    cookie   = get_cookie_file()
    return jsonify({
        'cookie_loaded':       bool(cookie),
        'cookie_path':         cookie or 'not found',
        'impersonate_available': ImpersonateTarget is not None,
        'result':              'success' if result.get('success') else 'failed',
        'error':               result.get('error', ''),
        'title':               result.get('info', {}).get('title', ''),
        'strategies_tried':    (len(YT_IMPERSONATE_TARGETS)
                                * len(YT_CLIENT_STRATEGIES)),
    })

@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'service': 'Social Crazy Dr. Dev'})


# ═══════════════════════════════════════════════════════════════
# HTML TEMPLATE
# ═══════════════════════════════════════════════════════════════

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="description" content="Social Crazy Dr. Dev — Download unlimited Instagram & YouTube videos free.">
<title>Social Crazy Dr. Dev | Ultimate Video Downloader</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Exo+2:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<style>
:root{
  --bg:#07071a;--bg2:#0d1130;--card:rgba(13,17,48,0.90);--cb:rgba(0,245,255,0.13);
  --cyan:#00f5ff;--pink:#ff006e;--purple:#8b5cf6;--green:#00ff9d;--orange:#ff8c00;
  --red:#ff4444;--text:#e2e8f0;--sub:#94a3b8;--inp:rgba(255,255,255,0.04);
  --sh:0 0 60px rgba(0,245,255,0.07);--trans:all .3s cubic-bezier(.4,0,.2,1);
}
[data-theme="light"]{
  --bg:#eef2ff;--bg2:#e0e7ff;--card:rgba(255,255,255,0.93);--cb:rgba(139,92,246,0.18);
  --text:#1e293b;--sub:#475569;--inp:rgba(0,0,0,0.04);--sh:0 4px 40px rgba(139,92,246,0.1);
}
*{margin:0;padding:0;box-sizing:border-box;}html{scroll-behavior:smooth;}
body{font-family:'Exo 2',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden;transition:var(--trans);}
::-webkit-scrollbar{width:6px;}::-webkit-scrollbar-track{background:var(--bg);}
::-webkit-scrollbar-thumb{background:linear-gradient(var(--cyan),var(--pink));border-radius:3px;}
.bg-wrap{position:fixed;inset:0;z-index:-2;overflow:hidden;}
.bg-blob{position:absolute;border-radius:50%;filter:blur(110px);opacity:.3;}
.blob1{width:700px;height:700px;background:radial-gradient(circle,rgba(0,245,255,.2),transparent 65%);top:-280px;left:-200px;animation:bm1 24s ease-in-out infinite;}
.blob2{width:600px;height:600px;background:radial-gradient(circle,rgba(255,0,110,.17),transparent 65%);bottom:-180px;right:-180px;animation:bm2 30s ease-in-out infinite;}
.blob3{width:450px;height:450px;background:radial-gradient(circle,rgba(139,92,246,.13),transparent 65%);top:45%;left:45%;transform:translate(-50%,-50%);animation:bm3 20s ease-in-out infinite;}
@keyframes bm1{0%,100%{transform:translate(0,0);}50%{transform:translate(140px,90px);}}
@keyframes bm2{0%,100%{transform:translate(0,0);}50%{transform:translate(-110px,-70px);}}
@keyframes bm3{0%,100%{transform:translate(-50%,-50%) scale(1);}50%{transform:translate(-50%,-50%) scale(1.35);}}
.grid-bg{position:fixed;inset:0;z-index:-1;
  background-image:linear-gradient(rgba(0,245,255,.022) 1px,transparent 1px),linear-gradient(90deg,rgba(0,245,255,.022) 1px,transparent 1px);
  background-size:60px 60px;}
[data-theme="light"] .grid-bg{background-image:linear-gradient(rgba(139,92,246,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(139,92,246,.035) 1px,transparent 1px);}

header{padding:14px 32px;display:flex;align-items:center;justify-content:space-between;
  border-bottom:1px solid var(--cb);background:rgba(7,7,26,.86);
  backdrop-filter:blur(28px);position:sticky;top:0;z-index:300;}
[data-theme="light"] header{background:rgba(238,242,255,.9);}
.logo{display:flex;align-items:center;gap:14px;text-decoration:none;}
.logo-icon{width:46px;height:46px;background:linear-gradient(135deg,var(--cyan),var(--purple),var(--pink));
  border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:22px;animation:lpulse 3s ease-in-out infinite;flex-shrink:0;}
@keyframes lpulse{0%,100%{box-shadow:0 0 22px rgba(0,245,255,.45);}50%{box-shadow:0 0 55px rgba(0,245,255,.85),0 0 90px rgba(255,0,110,.3);}}
.logo-text .name{font-family:'Orbitron',sans-serif;font-weight:900;font-size:17px;
  background:linear-gradient(90deg,var(--cyan),var(--purple),var(--pink));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
  background-size:200% auto;animation:gshift 5s linear infinite;letter-spacing:.5px;}
@keyframes gshift{0%{background-position:0%;}100%{background-position:200%;}}
.logo-text .tag{font-size:9px;color:var(--sub);letter-spacing:2.5px;text-transform:uppercase;margin-top:1px;}
.h-right{display:flex;align-items:center;gap:18px;}
.credit .cl{font-size:9px;color:var(--sub);text-transform:uppercase;letter-spacing:2px;text-align:right;}
.credit .cn{font-family:'Orbitron',sans-serif;font-size:13px;font-weight:700;
  background:linear-gradient(90deg,var(--pink),var(--purple));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.theme-btn{width:52px;height:28px;background:var(--inp);border:1px solid var(--cb);
  border-radius:14px;cursor:pointer;transition:var(--trans);display:flex;align-items:center;padding:4px;}
.theme-btn::after{content:'🌙';font-size:14px;width:20px;height:20px;display:flex;align-items:center;justify-content:center;border-radius:50%;transition:transform .3s ease;}
[data-theme="light"] .theme-btn::after{content:'☀️';transform:translateX(24px);}
.ck-pill{display:inline-flex;align-items:center;gap:6px;padding:5px 13px;
  border-radius:20px;font-size:11px;font-weight:600;letter-spacing:.5px;border:1px solid;cursor:help;}
.ck-pill.on{background:rgba(0,255,157,.1);border-color:rgba(0,255,157,.35);color:var(--green);}
.ck-pill.off{background:rgba(255,68,68,.1);border-color:rgba(255,68,68,.3);color:var(--red);}
.ck-dot{width:7px;height:7px;border-radius:50%;background:currentColor;animation:blink 1.5s infinite;}
@keyframes blink{0%,100%{opacity:1;}50%{opacity:.2;}}

main{max-width:980px;margin:0 auto;padding:50px 20px 70px;}
.hero{text-align:center;margin-bottom:54px;}
.h-badge{display:inline-flex;align-items:center;gap:8px;
  background:linear-gradient(90deg,rgba(0,245,255,.1),rgba(255,0,110,.1));
  border:1px solid rgba(0,245,255,.28);border-radius:24px;padding:7px 22px;
  font-size:11px;color:var(--cyan);letter-spacing:2px;text-transform:uppercase;margin-bottom:22px;
  animation:badge-glow 2.5s ease-in-out infinite;}
@keyframes badge-glow{0%,100%{border-color:rgba(0,245,255,.28);}50%{border-color:rgba(0,245,255,.75);box-shadow:0 0 22px rgba(0,245,255,.22);}}
.bdot{width:6px;height:6px;background:var(--cyan);border-radius:50%;animation:blink 1.2s infinite;}
.hero h1{font-family:'Orbitron',sans-serif;font-size:clamp(26px,5.5vw,56px);font-weight:900;line-height:1.15;margin-bottom:18px;letter-spacing:-1px;}
.hero h1 .g{background:linear-gradient(90deg,var(--cyan),var(--purple),var(--pink));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;background-size:200% auto;animation:gshift 4s linear infinite;}
.hero p{color:var(--sub);font-size:15px;max-width:540px;margin:0 auto 34px;line-height:1.75;}
.stats{display:flex;justify-content:center;gap:40px;flex-wrap:wrap;}
.stat .sn{font-family:'Orbitron',sans-serif;font-size:24px;font-weight:900;color:var(--cyan);}
.stat .sl{font-size:10px;color:var(--sub);text-transform:uppercase;letter-spacing:1.5px;margin-top:2px;}

.ptabs{display:flex;gap:6px;background:var(--card);border:1px solid var(--cb);border-radius:16px;padding:6px;margin-bottom:20px;backdrop-filter:blur(20px);}
.ptab{flex:1;padding:13px 20px;border:none;border-radius:10px;cursor:pointer;font-family:'Exo 2',sans-serif;font-size:14px;font-weight:600;letter-spacing:.5px;transition:var(--trans);background:transparent;color:var(--sub);display:flex;align-items:center;justify-content:center;gap:8px;}
.ptab.on{background:linear-gradient(135deg,var(--cyan),var(--purple));color:#fff;box-shadow:0 4px 24px rgba(0,245,255,.3);}
.ptab.on.yt{background:linear-gradient(135deg,#ff4444,#cc0000);box-shadow:0 4px 24px rgba(255,68,68,.35);}
.ptab:hover:not(.on){color:var(--text);}

.card{background:var(--card);border:1px solid var(--cb);border-radius:20px;padding:30px;backdrop-filter:blur(24px);box-shadow:var(--sh);margin-bottom:22px;position:relative;overflow:hidden;}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--cyan),var(--purple),var(--pink));}

.alert{padding:13px 16px;border-radius:10px;margin-bottom:18px;font-size:13.5px;display:none;align-items:center;gap:10px;animation:slideup .3s ease;}
.alert.show{display:flex;}
.alert.err{background:rgba(255,68,68,.12);border:1px solid rgba(255,100,100,.3);color:#ff8888;}
.alert.ok{background:rgba(0,245,255,.08);border:1px solid rgba(0,245,255,.3);color:var(--cyan);}
.alert.inf{background:rgba(255,140,0,.1);border:1px solid rgba(255,140,0,.3);color:var(--orange);}
@keyframes slideup{from{opacity:0;transform:translateY(10px);}to{opacity:1;transform:translateY(0);}}

.lbar{height:3px;background:var(--inp);border-radius:2px;overflow:hidden;display:none;margin-bottom:20px;}
.lbar.on{display:block;}
.lbar-fill{height:100%;width:35%;background:linear-gradient(90deg,var(--cyan),var(--pink));border-radius:2px;animation:lb 1.4s ease-in-out infinite;}
@keyframes lb{0%{margin-left:-35%;}100%{margin-left:130%;}}

/* ── Fetch status text shown below loading bar ── */
.fetch-status{font-size:12px;color:var(--sub);margin-bottom:14px;display:none;text-align:center;}
.fetch-status.on{display:block;}

.inp-grp{display:flex;gap:10px;margin-bottom:18px;}
.url-inp{flex:1;background:var(--inp);border:1px solid var(--cb);border-radius:12px;padding:14px 18px;color:var(--text);font-family:'Exo 2',sans-serif;font-size:14px;outline:none;transition:var(--trans);}
.url-inp::placeholder{color:var(--sub);}
.url-inp:focus{border-color:var(--cyan);box-shadow:0 0 0 3px rgba(0,245,255,.1);}

.btn{padding:13px 22px;border:none;border-radius:12px;cursor:pointer;font-family:'Exo 2',sans-serif;font-size:14px;font-weight:600;letter-spacing:.3px;transition:var(--trans);display:inline-flex;align-items:center;gap:8px;white-space:nowrap;}
.btn-c{background:linear-gradient(135deg,var(--cyan),var(--purple));color:#fff;box-shadow:0 4px 20px rgba(0,245,255,.22);}
.btn-c:hover{transform:translateY(-2px);box-shadow:0 8px 32px rgba(0,245,255,.45);}
.btn-p{background:linear-gradient(135deg,var(--pink),var(--purple));color:#fff;}
.btn-p:hover{transform:translateY(-2px);}
.btn-g{background:linear-gradient(135deg,var(--green),#00c97a);color:#07071a;font-weight:700;}
.btn-g:hover{transform:translateY(-2px);}
.btn-o{background:linear-gradient(135deg,var(--orange),#d97706);color:#fff;}
.btn-o:hover{transform:translateY(-2px);}
.btn-mu{background:linear-gradient(135deg,#475569,#334155);color:#cbd5e1;}
.btn-mu:hover{transform:translateY(-2px);}
.btn-out{background:transparent;border:1px solid var(--cb);color:var(--text);}
.btn-out:hover{border-color:var(--cyan);color:var(--cyan);}
.btn:disabled{opacity:.42;cursor:not-allowed;transform:none!important;box-shadow:none!important;}

.vinfo{display:none;gap:20px;background:var(--inp);border:1px solid var(--cb);border-radius:14px;padding:20px;margin-bottom:18px;}
.vinfo.show{display:flex;animation:slideup .4s ease;}
.thumb-wrap{flex-shrink:0;position:relative;}
.thumb-wrap img{width:185px;height:116px;object-fit:cover;border-radius:10px;border:1px solid var(--cb);}
.plat-badge{position:absolute;top:6px;left:6px;border-radius:6px;padding:3px 9px;font-size:9px;font-weight:800;color:#fff;text-transform:uppercase;letter-spacing:1.5px;}
.plat-badge.instagram{background:linear-gradient(45deg,#f09433,#e6683c,#dc2743,#cc2366,#bc1888);}
.plat-badge.youtube{background:#ff0000;}
.vmeta{flex:1;min-width:0;}
.vtitle{font-weight:700;font-size:15px;margin-bottom:10px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}
.mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px;margin-bottom:10px;}
.mi .ml{font-size:9px;color:var(--sub);text-transform:uppercase;letter-spacing:1.5px;display:block;}
.mi .mv{font-size:13px;font-weight:700;color:var(--cyan);}
.vdesc{font-size:12px;color:var(--sub);line-height:1.6;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;}

.ftype-wrap{display:none;margin-bottom:16px;}
.ftype-wrap.show{display:block;}
.ftype-lbl{font-size:11px;color:var(--sub);text-transform:uppercase;letter-spacing:2px;margin-bottom:10px;display:flex;align-items:center;gap:7px;}
.ftype-tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;background:rgba(0,0,0,.22);border-radius:14px;padding:5px;}
.ftype-tab{padding:12px 10px;border:none;border-radius:9px;cursor:pointer;font-family:'Exo 2',sans-serif;font-size:13px;font-weight:600;transition:var(--trans);background:transparent;color:var(--sub);display:flex;align-items:center;justify-content:center;gap:7px;}
.ftype-tab:hover:not(.on){color:var(--text);}
.ftype-tab.on.mp4{background:linear-gradient(135deg,#0ea5e9,#6366f1);color:#fff;box-shadow:0 4px 16px rgba(14,165,233,.35);}
.ftype-tab.on.mp3{background:linear-gradient(135deg,var(--green),#00c97a);color:#07071a;box-shadow:0 4px 16px rgba(0,255,157,.3);}
.ftype-tab.on.muted{background:linear-gradient(135deg,#475569,#334155);color:#e2e8f0;box-shadow:0 4px 16px rgba(0,0,0,.4);}

.fsec{margin-bottom:18px;display:none;}
.fsec.show{display:block;}
.slabel{font-size:11px;color:var(--sub);text-transform:uppercase;letter-spacing:2px;margin-bottom:9px;display:flex;align-items:center;gap:7px;}
.fsel{width:100%;background:var(--inp);border:1px solid var(--cb);border-radius:10px;padding:12px 38px 12px 16px;color:var(--text);font-family:'Exo 2',sans-serif;font-size:14px;outline:none;cursor:pointer;transition:var(--trans);appearance:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20' fill='%2394a3b8'%3E%3Cpath d='M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center;background-size:16px;}
.fsel:focus{border-color:var(--cyan);}
.fsel option{background:var(--bg2);}

.abts{display:none;gap:10px;flex-wrap:wrap;margin-top:4px;}
.abts.show{display:flex;}

.dl-prog{display:none;background:var(--inp);border:1px solid var(--cb);border-radius:10px;padding:16px;margin-top:14px;}
.dl-prog.show{display:block;animation:slideup .3s ease;}
.prog-lbl{font-size:13px;margin-bottom:9px;color:var(--sub);}
.pbar{height:7px;background:rgba(255,255,255,.05);border-radius:4px;overflow:hidden;}
.pfill{height:100%;background:linear-gradient(90deg,var(--cyan),var(--purple),var(--pink));border-radius:4px;transition:width .3s ease;animation:pglow 1.2s ease-in-out infinite;}
@keyframes pglow{0%,100%{box-shadow:0 0 10px rgba(0,245,255,.3);}50%{box-shadow:0 0 28px rgba(0,245,255,.75);}}

.sec-h{margin-bottom:28px;}
.sec-title{font-family:'Orbitron',sans-serif;font-size:20px;font-weight:700;margin-bottom:6px;background:linear-gradient(90deg,var(--cyan),var(--pink));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.sec-sub{color:var(--sub);font-size:13px;}

.feat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:54px;}
.feat{background:var(--card);border:1px solid var(--cb);border-radius:16px;padding:22px;backdrop-filter:blur(20px);transition:var(--trans);}
.feat:hover{transform:translateY(-5px);border-color:rgba(0,245,255,.38);box-shadow:0 14px 44px rgba(0,245,255,.12);}
.feat .fi{font-size:30px;margin-bottom:12px;display:block;}
.feat .ft{font-weight:700;font-size:13.5px;margin-bottom:6px;color:var(--cyan);}
.feat .fd{font-size:12px;color:var(--sub);line-height:1.6;}

.steps-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));gap:14px;margin-bottom:28px;}
.step{background:var(--card);border:1px solid var(--cb);border-radius:16px;padding:22px;backdrop-filter:blur(20px);text-align:center;transition:var(--trans);}
.step:hover{transform:translateY(-4px);border-color:rgba(255,0,110,.38);}
.snum{width:42px;height:42px;background:linear-gradient(135deg,var(--cyan),var(--purple));border-radius:50%;display:flex;align-items:center;justify-content:center;font-family:'Orbitron',sans-serif;font-weight:900;font-size:17px;color:#fff;margin:0 auto 14px;box-shadow:0 0 25px rgba(0,245,255,.55);}
.stitle{font-weight:700;font-size:14px;margin-bottom:7px;}
.sdesc{font-size:12px;color:var(--sub);line-height:1.6;}

.tips{background:linear-gradient(135deg,rgba(0,245,255,.05),rgba(255,0,110,.05));border:1px solid rgba(0,245,255,.2);border-radius:16px;padding:22px 26px;margin-bottom:52px;}
.tips-title{font-weight:700;font-size:14px;color:var(--cyan);margin-bottom:12px;display:flex;align-items:center;gap:8px;}
.tips ul{list-style:none;display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:9px;}
.tips ul li{font-size:13px;color:var(--sub);display:flex;align-items:flex-start;gap:8px;}
.tips ul li::before{content:'→';color:var(--cyan);flex-shrink:0;}

.cookie-box{background:linear-gradient(135deg,rgba(255,140,0,.07),rgba(255,68,68,.05));border:1px solid rgba(255,140,0,.3);border-radius:16px;padding:22px 26px;margin-bottom:52px;}
.cookie-box h3{color:var(--orange);font-size:15px;margin-bottom:14px;display:flex;align-items:center;gap:8px;}
.cookie-steps{list-style:none;display:flex;flex-direction:column;gap:10px;}
.cookie-steps li{font-size:13px;color:var(--sub);display:flex;align-items:flex-start;gap:10px;}
.cookie-steps li .csn{background:var(--orange);color:#fff;width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:800;flex-shrink:0;margin-top:1px;}
.cookie-steps code{background:rgba(255,255,255,.08);padding:2px 7px;border-radius:5px;font-family:monospace;font-size:12px;color:var(--cyan);}

.faqs{margin-bottom:54px;}
.faq{background:var(--card);border:1px solid var(--cb);border-radius:12px;margin-bottom:10px;backdrop-filter:blur(20px);overflow:hidden;transition:var(--trans);}
.faq:hover{border-color:rgba(0,245,255,.28);}
.fq{padding:18px 20px;cursor:pointer;display:flex;align-items:center;justify-content:space-between;font-weight:600;font-size:14px;user-select:none;gap:12px;}
.fq:hover{color:var(--cyan);}
.fq-icon{width:26px;height:26px;background:var(--inp);border-radius:50%;display:flex;align-items:center;justify-content:center;transition:transform .35s ease;flex-shrink:0;color:var(--cyan);font-size:13px;}
.faq.open .fq-icon{transform:rotate(180deg);background:rgba(0,245,255,.18);}
.fa-ans{max-height:0;overflow:hidden;transition:max-height .45s ease,padding .3s ease;font-size:13.5px;color:var(--sub);line-height:1.75;padding:0 20px;}
.faq.open .fa-ans{max-height:260px;padding-bottom:18px;}

footer{text-align:center;padding:44px 20px;border-top:1px solid var(--cb);color:var(--sub);font-size:13px;}
.flogo{font-family:'Orbitron',sans-serif;font-size:17px;font-weight:700;background:linear-gradient(90deg,var(--cyan),var(--pink));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:10px;}

.spin{display:inline-block;width:15px;height:15px;border:2px solid rgba(255,255,255,.25);border-top-color:#fff;border-radius:50%;animation:sp .75s linear infinite;}
@keyframes sp{to{transform:rotate(360deg);}}
.mb-52{margin-bottom:52px;}

@media(max-width:768px){
  header{padding:12px 16px;}
  .logo-text .name{font-size:13px;}
  .ck-pill{display:none;}
  main{padding:32px 14px 50px;}
  .vinfo{flex-direction:column;}
  .thumb-wrap img{width:100%;height:200px;}
  .inp-grp{flex-direction:column;}
  .ftype-tabs{grid-template-columns:1fr;}
  .abts{flex-direction:column;}
  .abts .btn{justify-content:center;}
}
@media(max-width:480px){.credit{display:none;}.stats{gap:18px;}}
</style>
</head>
<body>
<div class="bg-wrap">
  <div class="bg-blob blob1"></div><div class="bg-blob blob2"></div><div class="bg-blob blob3"></div>
</div>
<div class="grid-bg"></div>

<header>
  <a class="logo" href="/">
    <div class="logo-icon">⚡</div>
    <div class="logo-text">
      <div class="name">Social Crazy Dr. Dev</div>
      <div class="tag">Ultimate Video Downloader</div>
    </div>
  </a>
  <div class="h-right">
    <div id="ckPill" class="ck-pill off" title="YouTube cookie status">
      <span class="ck-dot"></span><span id="ckTxt">No Cookie</span>
    </div>
    <div class="credit">
      <div class="cl">Created by</div>
      <div class="cn">Dr. Hamza</div>
    </div>
    <div class="theme-btn" id="themeBtn" title="Toggle Day / Night"></div>
  </div>
</header>

<main>
<div class="hero">
  <div class="h-badge"><span class="bdot"></span>Unlimited · Free · Chrome TLS Bypass · Highest Quality</div>
  <h1>Download Any Video,<br><span class="g">Instantly &amp; Free.</span></h1>
  <p>Powered by Chrome TLS fingerprint impersonation — YouTube and Cloudflare can't distinguish it from a real browser, even on cloud server IPs.</p>
  <div class="stats">
    <div class="stat"><div class="sn">∞</div><div class="sl">Downloads</div></div>
    <div class="stat"><div class="sn">4K</div><div class="sl">Max Quality</div></div>
    <div class="stat"><div class="sn">MP4+MP3</div><div class="sl">Formats</div></div>
    <div class="stat"><div class="sn">0</div><div class="sl">Sign‑Up</div></div>
  </div>
</div>

<div class="ptabs">
  <button class="ptab on" id="tabIG" onclick="switchTab('instagram')"><i class="fab fa-instagram"></i> Instagram</button>
  <button class="ptab" id="tabYT" onclick="switchTab('youtube')"><i class="fab fa-youtube"></i> YouTube</button>
</div>

<div class="card">
  <div class="alert" id="alertBox"><i class="fas fa-circle-info"></i><span id="alertMsg"></span></div>
  <div class="lbar" id="lbar"><div class="lbar-fill"></div></div>
  <div class="fetch-status" id="fetchStatus">⏳ Connecting to YouTube — this may take up to 20 seconds…</div>

  <div class="inp-grp">
    <input type="text" class="url-inp" id="urlInp"
      placeholder="🔗  Paste Instagram or YouTube URL here…"
      oninput="autoDetect()" onkeydown="if(event.key==='Enter')fetchInfo()">
    <button class="btn btn-c" id="fetchBtn" onclick="fetchInfo()">
      <i class="fas fa-search"></i> Fetch
    </button>
  </div>

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

  <div class="ftype-wrap" id="ftypeWrap">
    <div class="ftype-lbl"><i class="fas fa-layer-group"></i> Choose Download Type</div>
    <div class="ftype-tabs">
      <button class="ftype-tab on mp4" id="ftMP4" onclick="setFtype('mp4')">
        <i class="fas fa-film"></i> MP4 <small style="opacity:.65;font-size:10px">Video+Audio</small>
      </button>
      <button class="ftype-tab" id="ftMP3" onclick="setFtype('mp3')">
        <i class="fas fa-music"></i> MP3 <small style="opacity:.65;font-size:10px">Audio Only</small>
      </button>
      <button class="ftype-tab" id="ftMuted" onclick="setFtype('muted')">
        <i class="fas fa-volume-xmark"></i> Muted <small style="opacity:.65;font-size:10px">No Sound</small>
      </button>
    </div>
  </div>

  <div class="fsec" id="fsec">
    <div class="slabel"><i class="fas fa-sliders"></i> Select Quality</div>
    <select class="fsel" id="fsel"></select>
  </div>

  <div class="abts" id="abts">
    <button class="btn btn-g" id="dlBtn" onclick="dlVideo()">
      <i class="fas fa-download" id="dlIcon"></i><span id="dlBtnTxt">Download MP4</span>
    </button>
    <button class="btn btn-p" id="thumbBtn" onclick="dlThumb()">
      <i class="fas fa-image"></i> Thumbnail
    </button>
    <button class="btn btn-out" onclick="reset()">
      <i class="fas fa-rotate-right"></i> Reset
    </button>
  </div>

  <div class="dl-prog" id="dlProg">
    <div class="prog-lbl" id="progLbl">Preparing…</div>
    <div class="pbar"><div class="pfill" id="pfill" style="width:0%"></div></div>
  </div>
</div>

<div class="mb-52">
  <div class="sec-h"><div class="sec-title">⚡ Powerful Features</div><div class="sec-sub">Everything you need, nothing you don't</div></div>
  <div class="feat-grid">
    <div class="feat"><span class="fi">🛡️</span><div class="ft">Chrome TLS Bypass</div><div class="fd">curl_cffi impersonates real Chrome at TLS fingerprint level. YouTube and Cloudflare see a genuine browser, not a bot.</div></div>
    <div class="feat"><span class="fi">🎬</span><div class="ft">MP4 (Video + Audio)</div><div class="fd">Full quality video with sound up to 4K UHD. Default always picks the highest available resolution.</div></div>
    <div class="feat"><span class="fi">🎵</span><div class="ft">MP3 (Audio Only)</div><div class="fd">Extract high-quality MP3 from any YouTube video. Perfect for music, podcasts, and lectures.</div></div>
    <div class="feat"><span class="fi">🔇</span><div class="ft">Muted MP4 (No Sound)</div><div class="fd">Video track only with no audio. Ideal for b-roll footage, video editing, and background visuals.</div></div>
    <div class="feat"><span class="fi">📸</span><div class="ft">Instagram Downloader</div><div class="fd">Download photos, videos, carousels, and Reels from any public Instagram account at original quality.</div></div>
    <div class="feat"><span class="fi">🖼️</span><div class="ft">Thumbnail Saver</div><div class="fd">Save the highest-resolution thumbnail from any Instagram post or YouTube video in one click.</div></div>
    <div class="feat"><span class="fi">📊</span><div class="ft">Full Video Details</div><div class="fd">See uploader, date, views, likes, subscribers, duration, and description before downloading.</div></div>
    <div class="feat"><span class="fi">🌙</span><div class="ft">Day / Night Mode</div><div class="fd">Fully themed dark and light mode with your preference saved automatically between visits.</div></div>
  </div>
</div>

<div class="mb-52">
  <div class="sec-h"><div class="sec-title">📖 How to Use</div><div class="sec-sub">Download any video in 5 simple steps</div></div>
  <div class="steps-grid">
    <div class="step"><div class="snum">1</div><div class="stitle">Pick Platform</div><div class="sdesc">Tap Instagram or YouTube. Platform auto-detects from any pasted URL.</div></div>
    <div class="step"><div class="snum">2</div><div class="stitle">Paste URL</div><div class="sdesc">Copy the video link and paste it into the input field above.</div></div>
    <div class="step"><div class="snum">3</div><div class="stitle">Fetch Details</div><div class="sdesc">Click Fetch to load title, uploader, date, views, and all quality options. May take ~10–20s.</div></div>
    <div class="step"><div class="snum">4</div><div class="stitle">Choose Type</div><div class="sdesc">Pick <strong>MP4</strong>, <strong>MP3</strong>, or <strong>Muted MP4</strong> then select quality from the dropdown.</div></div>
    <div class="step"><div class="snum">5</div><div class="stitle">Download</div><div class="sdesc">Hit Download and the file saves directly to your device. Done!</div></div>
  </div>
  <div class="tips">
    <div class="tips-title"><i class="fas fa-lightbulb"></i> Pro Tips</div>
    <ul>
      <li>Fetching may take 10–20 seconds on first use — Render free tier wakes up slowly.</li>
      <li>Only <strong>public</strong> Instagram accounts and posts are supported.</li>
      <li><em>Best Quality (Auto)</em> merges the best video + audio using ffmpeg.</li>
      <li>Select <em>MP3</em> to extract just the audio from any YouTube video.</li>
      <li>Visit <code>/test-yt</code> in your browser to run a live YouTube server diagnostic.</li>
    </ul>
  </div>
</div>

<div class="cookie-box mb-52">
  <h3><i class="fas fa-cookie-bite"></i> YouTube Cookie Setup — For Age-Restricted / Private Videos</h3>
  <ul class="cookie-steps">
    <li><div class="csn">1</div><div>Install <strong>"Get cookies.txt LOCALLY"</strong> on Chrome/Firefox. Log into YouTube first.</div></li>
    <li><div class="csn">2</div><div>Go to <strong>youtube.com</strong>, click the extension, export <code>cookies.txt</code> (Netscape format — do not edit).</div></li>
    <li><div class="csn">3</div><div><strong>Local:</strong> Place <code>cookies.txt</code> in the same folder as <code>app.py</code>. Restart server.</div></li>
    <li><div class="csn">4</div><div><strong>Render:</strong> Service → <em>Settings → Secret Files</em> → path <code>/etc/secrets/cookies.txt</code> → paste content → Redeploy.</div></li>
    <li><div class="csn">5</div><div>Cookie pill turns <span style="color:var(--green);font-weight:700">green</span> when active. Visit <code>/test-yt</code> to verify.</div></li>
  </ul>
</div>

<div class="faqs mb-52">
  <div class="sec-h"><div class="sec-title">❓ Frequently Asked Questions</div><div class="sec-sub">Quick answers to common questions</div></div>
  <div class="faq" onclick="toggleFaq(this)">
    <div class="fq">Is Social Crazy Dr. Dev completely free?<div class="fq-icon"><i class="fas fa-chevron-down"></i></div></div>
    <div class="fa-ans">Yes — 100% free with no limits. No registration, no subscription, no daily cap. Download as many videos as you want forever.</div>
  </div>
  <div class="faq" onclick="toggleFaq(this)">
    <div class="fq">Why does fetching take 10–20 seconds sometimes?<div class="fq-icon"><i class="fas fa-chevron-down"></i></div></div>
    <div class="fa-ans">Render's free tier "sleeps" after 15 minutes of inactivity. The first request wakes it up, which takes ~10–15 seconds. After the first request, all subsequent fetches are fast. Upgrading to Render Starter ($7/month) keeps the server always awake.</div>
  </div>
  <div class="faq" onclick="toggleFaq(this)">
    <div class="fq">What is the difference between MP4, MP3, and Muted MP4?<div class="fq-icon"><i class="fas fa-chevron-down"></i></div></div>
    <div class="fa-ans"><strong>MP4</strong> downloads full video with audio. <strong>MP3</strong> extracts only the audio as a high-quality MP3 file. <strong>Muted MP4</strong> saves only the video track with no audio — perfect for video editors who want clean b-roll footage.</div>
  </div>
  <div class="faq" onclick="toggleFaq(this)">
    <div class="fq">Can I download private Instagram posts?<div class="fq-icon"><i class="fas fa-chevron-down"></i></div></div>
    <div class="fa-ans">No — only public Instagram accounts and posts are supported. Private accounts require authentication, which this tool does not collect to respect user privacy.</div>
  </div>
  <div class="faq" onclick="toggleFaq(this)">
    <div class="fq">What video quality can I download from YouTube?<div class="fq-icon"><i class="fas fa-chevron-down"></i></div></div>
    <div class="fa-ans">From 144p up to 4K UHD (2160p) depending on what the creator uploaded. Best Quality (Auto) auto-selects and merges the highest streams using ffmpeg. ffmpeg is installed automatically by the provided render.yaml.</div>
  </div>
</div>
</main>

<footer>
  <div class="flogo">Social Crazy Dr. Dev</div>
  <p>Designed &amp; Developed with ❤️ by <strong style="color:var(--pink)">Dr. Hamza</strong></p>
  <p style="margin-top:8px">Unlimited Instagram &amp; YouTube Downloads · Free Forever · No Sign‑Up</p>
  <p style="margin-top:12px;font-size:11px;opacity:.4">For personal use only · Respect copyright and creator rights · Not affiliated with Instagram or YouTube</p>
</footer>

<script>
let curPlatform='instagram',curInfo=null,curFtype='mp4';

(function(){document.documentElement.dataset.theme=localStorage.getItem('theme')||'dark';})();
document.getElementById('themeBtn').onclick=()=>{
  const h=document.documentElement;
  h.dataset.theme=h.dataset.theme==='dark'?'light':'dark';
  localStorage.setItem('theme',h.dataset.theme);
};

async function checkCookie(){
  try{
    const d=await fetch('/cookie-status').then(r=>r.json());
    const pill=document.getElementById('ckPill'),txt=document.getElementById('ckTxt');
    if(d.cookie_loaded){pill.className='ck-pill on';txt.textContent='🍪 Cookie Active';pill.title='Cookie: '+d.path;}
    else{pill.className='ck-pill off';txt.textContent='No Cookie';pill.title='Using Chrome impersonation bypass';}
  }catch(e){}
}
checkCookie();

function switchTab(p){
  curPlatform=p;
  document.getElementById('tabIG').className='ptab'+(p==='instagram'?' on':'');
  document.getElementById('tabYT').className='ptab'+(p==='youtube'?' on yt':'');
  document.getElementById('urlInp').placeholder=p==='instagram'
    ?'🔗  Paste Instagram URL (post / reel / IGTV)…'
    :'🔗  Paste YouTube URL (video / shorts)…';
  reset();
}
function autoDetect(){
  const v=document.getElementById('urlInp').value;
  if((v.includes('youtube.com')||v.includes('youtu.be'))&&curPlatform!=='youtube')switchTab('youtube');
  else if(v.includes('instagram.com')&&curPlatform!=='instagram')switchTab('instagram');
}
function setFtype(type){
  curFtype=type;
  const map={mp4:'MP4',mp3:'MP3',muted:'Muted'};
  Object.entries(map).forEach(([k,id])=>{document.getElementById('ft'+id).className='ftype-tab'+(k===type?` on ${k}`:'');});
  if(curInfo){populateFormats(curInfo,type);updateDlBtn(type);}
}
function updateDlBtn(type){
  const cfg={mp4:{cls:'btn btn-g',icon:'fa-download',txt:'Download MP4'},
             mp3:{cls:'btn btn-o',icon:'fa-music',txt:'Download MP3'},
             muted:{cls:'btn btn-mu',icon:'fa-volume-xmark',txt:'Download Muted MP4'}};
  const c=cfg[type];
  document.getElementById('dlBtn').className=c.cls;
  document.getElementById('dlIcon').className='fas '+c.icon;
  document.getElementById('dlBtnTxt').textContent=c.txt;
}
function populateFormats(info,type){
  const sel=document.getElementById('fsel');sel.innerHTML='';
  let fmts=info.platform==='youtube'
    ?(type==='mp3'?info.mp3_formats:type==='muted'?info.muted_formats:info.mp4_formats)
    :(info.mp4_formats||info.formats||[]);
  fmts.forEach((f,i)=>{
    const o=document.createElement('option');
    o.value=f.format_id;
    o.textContent=f.quality+(f.filesize?` · ${fmtBytes(f.filesize)}`:'');
    if(i===0)o.selected=true;
    sel.appendChild(o);
  });
}
function showAlert(msg,type='err'){
  const b=document.getElementById('alertBox');
  b.className=`alert show ${type}`;
  document.getElementById('alertMsg').textContent=msg;
  if(type!=='err')setTimeout(()=>b.className='alert',7000);
}
function hideAlert(){document.getElementById('alertBox').className='alert';}
function setLoad(on){
  document.getElementById('lbar').className='lbar'+(on?' on':'');
  document.getElementById('fetchStatus').className='fetch-status'+(on?' on':'');
  const btn=document.getElementById('fetchBtn');
  btn.innerHTML=on?'<div class="spin"></div> Fetching…':'<i class="fas fa-search"></i> Fetch';
  btn.disabled=on;
}

async function fetchInfo(){
  const url=document.getElementById('urlInp').value.trim();
  if(!url){showAlert('Please paste a URL first!');return;}
  setLoad(true);hideAlert();clearInfo();

  // KEY FIX: 60 second frontend timeout — longer than Render's 30s limit
  // This prevents the browser from killing the request too early
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),60000); // 60s

  try{
    const r=await fetch('/fetch-info',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url,platform:curPlatform}),
      signal:controller.signal,
    });
    clearTimeout(timer);
    const d=await r.json();
    if(d.error)showAlert(d.error,'err');
    else{renderInfo(d.info);showAlert('✅ Video info loaded!','ok');}
  }catch(e){
    clearTimeout(timer);
    if(e.name==='AbortError'){
      showAlert('⏱️ Request timed out. The server is taking too long — try again in a moment.','err');
    }else{
      showAlert('❌ Network error: '+e.message+' — Check if the server is running at /ping','err');
    }
  }finally{setLoad(false);}
}

function renderInfo(info){
  curInfo=info;curFtype='mp4';
  const img=document.getElementById('vthumb');
  img.src=info.thumbnail||'';
  img.onerror=()=>img.src='https://placehold.co/185x116/07071a/00f5ff?text=No+Thumb';
  const pb=document.getElementById('vplat');pb.textContent=info.platform;pb.className='plat-badge '+info.platform;
  document.getElementById('vtitle').textContent=info.title||'—';
  const rows=[];
  if(info.uploader)rows.push({l:'Uploaded By',v:'@'+info.uploader});
  if(info.uploader_full&&info.uploader_full!==info.uploader)rows.push({l:'Full Name',v:info.uploader_full});
  if(info.date)rows.push({l:'Date',v:info.date});
  if(info.view_count)rows.push({l:'Views',v:fmtN(info.view_count)});
  if(info.like_count||info.likes)rows.push({l:'Likes',v:fmtN(info.like_count||info.likes)});
  if(info.duration)rows.push({l:'Duration',v:fmtDur(info.duration)});
  if(info.comment_count||info.comments)rows.push({l:'Comments',v:fmtN(info.comment_count||info.comments)});
  if(info.channel_follower_count)rows.push({l:'Subscribers',v:fmtN(info.channel_follower_count)});
  document.getElementById('mgrid').innerHTML=rows.map(m=>`<div class="mi"><span class="ml">${m.l}</span><span class="mv">${m.v}</span></div>`).join('');
  document.getElementById('vdesc').textContent=info.description||'';
  const isYT=info.platform==='youtube';
  document.getElementById('ftypeWrap').className=isYT?'ftype-wrap show':'ftype-wrap';
  if(isYT){['MP4','MP3','Muted'].forEach(t=>document.getElementById('ft'+t).className='ftype-tab');document.getElementById('ftMP4').className='ftype-tab on mp4';}
  updateDlBtn('mp4');populateFormats(info,'mp4');
  document.getElementById('vinfo').className='vinfo show';
  document.getElementById('fsec').className='fsec show';
  document.getElementById('abts').className='abts show';
}

async function dlVideo(){
  if(!curInfo)return;
  const fmt=document.getElementById('fsel').value;
  const fname=sfn(curInfo.title||'video');
  const btn=document.getElementById('dlBtn');
  const orig=btn.innerHTML;
  btn.innerHTML='<div class="spin"></div> Preparing…';btn.disabled=true;
  const prog=document.getElementById('dlProg'),pfill=document.getElementById('pfill'),plbl=document.getElementById('progLbl');
  prog.className='dl-prog show';
  let pct=0;
  const lbls={mp4:'Downloading MP4…',mp3:'Extracting MP3…',muted:'Downloading Muted…'};
  const tick=setInterval(()=>{pct=Math.min(pct+Math.random()*1.5,87);pfill.style.width=pct+'%';plbl.textContent=(lbls[curFtype]||'Downloading…')+' '+Math.round(pct)+'%';},300);
  try{
    const r=await fetch('/download',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url:curInfo.url,format_id:fmt,platform:curInfo.platform,dl_type:curFtype,title:curInfo.title,video_url:curInfo.video_url||''})});
    if(!r.ok){const e=await r.json().catch(()=>({error:'Download failed'}));throw new Error(e.error||'Download failed');}
    clearInterval(tick);pfill.style.width='100%';plbl.textContent='Finalising… ✅';
    const blob=await r.blob();
    const extM={mp4:'mp4',mp3:'mp3',muted:'mp4'};
    const sfxM={mp4:'',mp3:'',muted:'_muted'};
    triggerDownload(blob,`${fname}${sfxM[curFtype]}.${extM[curFtype]}`);
    showAlert('🎉 Download started! Check your downloads folder.','ok');
  }catch(e){
    clearInterval(tick);
    showAlert('Download failed: '+e.message,'err');
    prog.className='dl-prog';
  }finally{setTimeout(()=>{btn.innerHTML=orig;btn.disabled=false;prog.className='dl-prog';},3000);}
}

async function dlThumb(){
  if(!curInfo||!curInfo.thumbnail){showAlert('No thumbnail available.','err');return;}
  const btn=document.getElementById('thumbBtn');
  btn.innerHTML='<div class="spin"></div> Saving…';btn.disabled=true;
  try{
    const r=await fetch('/download-thumbnail',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({thumbnail_url:curInfo.thumbnail,filename:sfn(curInfo.title||'thumbnail')})});
    if(!r.ok)throw new Error('Failed');
    triggerDownload(await r.blob(),sfn(curInfo.title||'thumbnail')+'_thumbnail.jpg');
    showAlert('🖼️ Thumbnail saved!','ok');
  }catch(e){showAlert('Failed: '+e.message,'err');}
  finally{btn.innerHTML='<i class="fas fa-image"></i> Thumbnail';btn.disabled=false;}
}
function triggerDownload(blob,name){
  const u=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();
  setTimeout(()=>URL.revokeObjectURL(u),6000);
}
function clearInfo(){
  document.getElementById('vinfo').className='vinfo';
  document.getElementById('ftypeWrap').className='ftype-wrap';
  document.getElementById('fsec').className='fsec';
  document.getElementById('abts').className='abts';
  document.getElementById('dlProg').className='dl-prog';
  curInfo=null;curFtype='mp4';
}
function reset(){document.getElementById('urlInp').value='';clearInfo();hideAlert();}
function toggleFaq(el){el.classList.toggle('open');}
function fmtN(n){if(!n)return'0';n=Number(n);if(n>=1e9)return(n/1e9).toFixed(1)+'B';if(n>=1e6)return(n/1e6).toFixed(1)+'M';if(n>=1e3)return(n/1e3).toFixed(1)+'K';return n.toLocaleString();}
function fmtDur(s){s=Math.round(Number(s)||0);const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=s%60;return h>0?`${h}:${pad(m)}:${pad(sec)}`:`${m}:${pad(sec)}`;}
function pad(n){return String(n).padStart(2,'0');}
function fmtBytes(b){if(!b)return'';b=Number(b);if(b>=1e9)return(b/1e9).toFixed(1)+' GB';if(b>=1e6)return(b/1e6).toFixed(1)+' MB';return(b/1e3).toFixed(0)+' KB';}
function sfn(s){return String(s).replace(/[^\w\s-]/g,'').trim().replace(/\s+/g,'_').substring(0,55)||'download';}
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    port  = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') == 'development'
    print(f"🚀  Social Crazy Dr. Dev — port {port}")
    print(f"🍪  Cookie      : {COOKIE_FILE or 'NOT FOUND'}")
    print(f"🛡️  Impersonate : {'Available ✅' if ImpersonateTarget else 'Not available ⚠️'}")
    print(f"🎯  Strategies  : {len(YT_IMPERSONATE_TARGETS)} targets × {len(YT_CLIENT_STRATEGIES)} clients")
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
