"""Kindly Kept auto-publisher: posts APPROVED items from posts.json to Instagram and Pinterest when they are due.

python publisher.py --dry-run      show what is due and what would be posted (no network calls)
python publisher.py                post everything due (run by GitHub Actions every 15 minutes)

Only posts with "status": "approved" are ever published (Ranjan approves each batch).
Due = the post's date + time (US Eastern) is in the past. Results are recorded in posted.json,
so nothing is posted twice.

Environment (GitHub Actions secrets):
  MEDIA_BASE   public URL of this folder, e.g. https://<user>.github.io/kindlykept-social
  IG_USER_ID   Instagram professional account id (Instagram API with Instagram Login)
  IG_TOKEN     long-lived Instagram user token (refreshed automatically, see refresh_ig_token)
  PIN_TOKEN    Pinterest API v5 access token          (optional: pins are skipped without it)
  PIN_BOARD    Pinterest board id
"""
import json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ET = ZoneInfo("America/New_York")
IG = "https://graph.instagram.com/v21.0"
PIN = "https://api.pinterest.com/v5"
DRY = "--dry-run" in sys.argv

data = json.load(open(os.path.join(HERE, "posts.json"), encoding="utf-8"))
log_path = os.path.join(HERE, "posted.json")
log = json.load(open(log_path, encoding="utf-8")) if os.path.exists(log_path) else {}
env = os.environ.get
BASE = (env("MEDIA_BASE") or "").rstrip("/")


def call(url, params=None, method="POST", headers=None, body=None):
    if params:
        data_ = urllib.parse.urlencode(params).encode()
        if method == "GET":
            url, data_ = url + "?" + data_.decode(), None
    else:
        data_ = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data_, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code} {url.split('?')[0]}: {e.read().decode()[:400]}")


def caption(p):
    return f"{p['caption']}\n\n{data['hashtags']} {p.get('tags', '')}".strip()


def ig_container(params):
    params = dict(params, access_token=env("IG_TOKEN"))
    return call(f"{IG}/{env('IG_USER_ID')}/media", params)["id"]


def ig_wait(cid):
    """Videos are processed by Instagram before they can be published."""
    for _ in range(60):
        st = call(f"{IG}/{cid}", {"fields": "status_code", "access_token": env("IG_TOKEN")}, "GET")["status_code"]
        if st == "FINISHED":
            return
        if st == "ERROR":
            raise RuntimeError(f"Instagram could not process container {cid}")
        time.sleep(10)
    raise RuntimeError(f"Instagram still processing container {cid}")


def ig_publish(p):
    pid = p["id"]
    if p["format"] == "reel":
        cid = ig_container({"media_type": "REELS", "video_url": f"{BASE}/reels/{pid}.mp4",
                            "caption": caption(p), "share_to_feed": "true"})
        ig_wait(cid)
    elif p["format"] == "carousel":
        kids = [ig_container({"image_url": f"{BASE}/png/{pid}-{i}.png", "is_carousel_item": "true"})
                for i in range(1, len(p["slides"]) + 1)]
        cid = ig_container({"media_type": "CAROUSEL", "children": ",".join(kids), "caption": caption(p)})
    else:
        cid = ig_container({"image_url": f"{BASE}/png/{pid}.png", "caption": caption(p),
                            **({"alt_text": p["alt"]} if p.get("alt") else {})})
    return call(f"{IG}/{env('IG_USER_ID')}/media_publish", {"creation_id": cid, "access_token": env("IG_TOKEN")})["id"]


def pin_publish(p):
    return call(f"{PIN}/pins", method="POST",
                headers={"Authorization": f"Bearer {env('PIN_TOKEN')}", "Content-Type": "application/json"},
                body={"board_id": env("PIN_BOARD"), "title": p["pin_title"][:100], "description": p["pin_desc"][:500],
                      "link": data["link"], "alt_text": (p.get("alt") or p["pin_title"])[:500],
                      "media_source": {"source_type": "image_url", "url": f"{BASE}/pins/{p['id']}.png"}})["id"]


def refresh_ig_token():
    """Long-lived Instagram tokens last 60 days; refreshing extends them. The workflow saves the new token."""
    return call(f"{IG.rsplit('/', 1)[0]}/refresh_access_token",
                {"grant_type": "ig_refresh_token", "access_token": env("IG_TOKEN")}, "GET")["access_token"]


now = datetime.now(timezone.utc)
if "--now" in sys.argv:   # testing: python publisher.py --dry-run --now 2026-11-02T10:00  (US Eastern)
    now = datetime.fromisoformat(sys.argv[sys.argv.index("--now") + 1]).replace(tzinfo=ET)
errors = 0
for p in sorted(data["posts"], key=lambda x: (x["date"], x["time"])):
    due = datetime.fromisoformat(f"{p['date']}T{p['time']}").replace(tzinfo=ET)
    done = log.get(p["id"], {})
    if due > now:
        continue
    if p["status"] != "approved":
        if not done:
            print(f"SKIP {p['id']} due {due:%Y-%m-%d %H:%M} ET but status is '{p['status']}' (not approved)")
        continue
    todo = [t for t in (["instagram"] + (["pinterest"] if p.get("pin") and env("PIN_TOKEN") else [])) if t not in done]
    for target in todo:
        if DRY:
            print(f"WOULD POST {p['id']} ({p['format']}) to {target}")
            continue
        try:
            done[target] = {"id": ig_publish(p) if target == "instagram" else pin_publish(p),
                            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            print(f"POSTED {p['id']} to {target}: {done[target]['id']}")
        except Exception as e:
            errors += 1
            print(f"FAILED {p['id']} to {target}: {e}")
    if done:
        log[p["id"]] = done
        json.dump(log, open(log_path, "w", encoding="utf-8"), indent=2)

if "--refresh-token" in sys.argv and not DRY:
    print("NEW_IG_TOKEN=" + refresh_ig_token())
sys.exit(1 if errors else 0)
