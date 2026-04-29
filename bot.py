import os
import json
import time
import random
import datetime
import subprocess
import requests

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from shorts_filter import get_video_details, is_short, compute_score

# ---------------- CONFIG ----------------
MAX_VIDEOS = 5
SHORTS_TIMES = ["12:00", "15:00", "18:00", "21:00", "23:00"]

# ---------------- UTIL ----------------
def load_json(file, default):
    if not os.path.exists(file):
        return default
    with open(file, "r") as f:
        return json.load(f)

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

def random_delay():
    delay = random.randint(20, 90)
    print(f"⏳ Sleeping {delay}s...")
    time.sleep(delay)

# ---------------- API ----------------
def get_youtube_service():
    token_info = json.loads(os.environ["TOKEN_JSON"])
    creds = Credentials.from_authorized_user_info(token_info)
    return build("youtube", "v3", credentials=creds)

def fetch_latest_videos(api_key, channel_id):
    url = f"https://www.googleapis.com/youtube/v3/search?key={api_key}&channelId={channel_id}&part=snippet&order=date&maxResults=5"
    res = requests.get(url).json()

    videos = []
    for item in res.get("items", []):
        if item["id"]["kind"] != "youtube#video":
            continue

        videos.append({
            "id": item["id"]["videoId"],
            "title": item["snippet"]["title"],
            "publishedAt": item["snippet"]["publishedAt"]
        })

    return videos

# ---------------- DOWNLOAD ----------------
def download_video(video_id):
    print(f"⬇️ Downloading {video_id}")
    subprocess.run([
        "yt-dlp",
        "-f", "mp4",
        "-o", "input.mp4",
        f"https://www.youtube.com/watch?v={video_id}"
    ], check=True)

# ---------------- PROCESS ----------------
def process_video(index):
    output = f"final_{index}.mp4"
    print(f"🎬 Processing → {output}")

    subprocess.run([
        "ffmpeg",
        "-i", "input.mp4",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-preset", "fast",
        "-y",
        output
    ], check=True)

    return output

# ---------------- UPLOAD ----------------
def upload_video(youtube, file, title, publish_time):
    print(f"📤 Uploading {file}")

    body = {
        "snippet": {
            "title": title[:90],
            "description": "Auto Shorts Upload",
            "categoryId": "22"
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_time.isoformat() + "Z"
        }
    }

    media = MediaFileUpload(file, chunksize=-1, resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload {int(status.progress() * 100)}%")

    print("✅ Upload done")

# ---------------- SCHEDULER ----------------
def get_schedule_times():
    now = datetime.datetime.utcnow()
    times = []

    for t in SHORTS_TIMES:
        hour, minute = map(int, t.split(":"))
        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if dt < now:
            dt += datetime.timedelta(days=1)

        times.append(dt)

    return times[:MAX_VIDEOS]

# ---------------- IFTTT ----------------
def trigger_call():
    key = os.environ["IFTTT_KEY"]
    url = f"https://maker.ifttt.com/trigger/yt_alert/with/key/{key}"
    requests.post(url)
    print("📞 Phone alert sent!")

# ---------------- MAIN ----------------
def main():
    api_key = os.environ["YOUTUBE_API_KEY"]

    channels = load_json("channels.json", [])
    processed = load_json("processed.json", [])

    all_videos = []

    print("📡 Scanning channels...")

    for ch in channels:
        videos = fetch_latest_videos(api_key, ch)

        for v in videos:
            if v["id"] in processed:
                continue

            try:
                details = get_video_details(api_key, v["id"])

                if not details:
                    continue

                # 🔥 SHORTS FILTER
                if not is_short(details["duration_sec"]):
                    continue

                v["views"] = details["views"]
                v["likes"] = details["likes"]

                publish_time = datetime.datetime.fromisoformat(
                    v["publishedAt"].replace("Z", "+00:00")
                )

                hours = (datetime.datetime.utcnow() - publish_time).total_seconds() / 3600

                v["score"] = compute_score(
                    v["views"],
                    v["likes"],
                    hours
                )

                all_videos.append(v)

            except Exception as e:
                print(f"⚠️ Skipping video: {e}")

    # ---------------- SELECT TOP ----------------
    top_videos = sorted(all_videos, key=lambda x: x["score"], reverse=True)[:MAX_VIDEOS]

    print(f"🔥 Selected {len(top_videos)} Shorts")

    youtube = get_youtube_service()
    schedule_times = get_schedule_times()

    # ---------------- PROCESS ----------------
    for i, video in enumerate(top_videos):
        try:
            download_video(video["id"])
            random_delay()

            output = process_video(i)
            random_delay()

            upload_video(
                youtube,
                output,
                video["title"],
                schedule_times[i]
            )

            processed.append(video["id"])
            save_json("processed.json", processed)

        except Exception as e:
            print(f"❌ Failed: {e}")

    # ---------------- ALERT ----------------
    trigger_call()


if __name__ == "__main__":
    main()
