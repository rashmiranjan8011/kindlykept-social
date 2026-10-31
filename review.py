"""Kindly Kept email approval, using GitHub issues (GitHub emails the owner; replying to that email adds a comment).

python review.py ask      open one approval issue for the posts due in the next 31 h that were not asked yet
                          (run nightly at 23:30 IST, so it covers the next day's 19:30 and 05:30 IST posts)
python review.py reply    read the new comment (GitHub Actions issue_comment event) and approve or skip posts

Reply words (only comments by the repo owner count; anything after the first quoted line is ignored):
  approve            approve every post in that email that is still waiting
  approve r01 03     approve only those posts
  skip 03            do not post 03 (it stays unposted)
Nothing is posted without approval; a post nobody approved is never published.
"""
import json, os, re, sys, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ET, IST = ZoneInfo("America/New_York"), ZoneInfo("Asia/Kolkata")
REPO = os.environ.get("GITHUB_REPOSITORY", "rashmiranjan8011/kindlykept-social")
OWNER = REPO.split("/")[0]
BASE = (os.environ.get("MEDIA_BASE") or f"https://{OWNER}.github.io/{REPO.split('/')[1]}").rstrip("/")
DRY = "--dry-run" in sys.argv
POSTS = os.path.join(HERE, "posts.json")

data = json.load(open(POSTS, encoding="utf-8"))


def gh(path, body=None, method=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}{path}", method=method or ("POST" if body else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def due(p):
    return datetime.fromisoformat(f"{p['date']}T{p['time']}").replace(tzinfo=ET)


def when(p):
    d = due(p)
    return f"{d.astimezone(IST):%a %d %b, %H:%M} IST ({d:%H:%M} US Eastern)"


def save():
    json.dump(data, open(POSTS, "w", encoding="utf-8"), indent=2, ensure_ascii=False)


def block(p):
    kind = {"reel": "Reel", "carousel": "Carousel", "single": "Image"}.get(p["format"], p["format"])
    lines = [f"### {p['id']} · {kind} · {when(p)}", ""]
    if p["format"] == "reel":
        lines += [f"▶ **[Watch the Reel (with music)]({BASE}/reels/{p['id']}.mp4)**", ""]
    elif p["format"] == "carousel":
        n = len(p.get("slides") or [])
        lines += [f'<img src="{BASE}/png/{p["id"]}-1.png" width="360">', "",
                  "Slides: " + " · ".join(f"[{i}]({BASE}/png/{p['id']}-{i}.png)" for i in range(1, n + 1)), ""]
    else:
        lines += [f'<img src="{BASE}/png/{p["id"]}.png" width="360">', ""]
    cap = f"{p['caption']}\n\n{data['hashtags']} {p.get('tags', '')}".strip()
    lines += ["> " + l if l else ">" for l in cap.split("\n")]
    return "\n".join(lines)


def ask():
    now = datetime.now(timezone.utc)
    batch = [p for p in sorted(data["posts"], key=due)
             if p["status"] == "draft" and not p.get("issue") and now < due(p) <= now + timedelta(hours=31)]
    if not batch:
        print("nothing to ask")
        return
    first = due(batch[0]).astimezone(IST)
    title = f"Approve {len(batch)} post{'s' * (len(batch) > 1)} for {first:%a %d %b}: " + ", ".join(p["id"] for p in batch)
    body = "\n\n".join([
        f"@{OWNER} please review. **Reply to this email with `approve`** to publish all of them on time, "
        f"or `approve {batch[0]['id']}` / `skip {batch[-1]['id']}` for single posts. Nothing is posted without your reply.",
        *[block(p) for p in batch]])
    if DRY:
        print(title, "\n\n", body)
        return
    num = gh("/issues", {"title": title, "body": body})["number"]
    for p in batch:
        p["issue"] = num
    save()
    print("opened issue", num, title)


def reply():
    ev = json.load(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8"))
    num, c = ev["issue"]["number"], ev["comment"]
    if c["user"]["login"].lower() != OWNER.lower():
        print("ignored comment from", c["user"]["login"])
        return
    text = []
    for line in c["body"].splitlines():          # stop at the quoted part of an email reply
        if line.startswith(">") or re.match(r"^On .+ wrote:", line):
            break
        text.append(line)
    words = re.findall(r"[a-z0-9-]+", " ".join(text).lower())
    mine = [p for p in data["posts"] if p.get("issue") == num]
    ids = {p["id"] for p in mine}
    picked = [w for w in words if w in ids]
    if any(w in ("skip", "no", "dont", "don't", "reject") for w in words):
        action = "skipped"
    elif any(w in ("approve", "approved", "yes", "ok", "okay", "go") for w in words):
        action = "approved"
    else:
        gh(f"/issues/{num}/comments", {"body": "I did not understand that. Reply `approve`, `approve <id>` or `skip <id>`."})
        return
    targets = [p for p in mine if (p["id"] in picked if picked else p["status"] == "draft")]
    now = datetime.now(timezone.utc)
    notes = []
    for p in targets:
        p["status"] = action
        late = action == "approved" and due(p) <= now
        notes.append(f"- {p['id']}: {action}, {'its time has passed, so it posts in the next 15 minutes' if late else when(p)}")
    save()
    left = [p["id"] for p in mine if p["status"] == "draft"]
    msg = "\n".join(notes) or "Nothing changed (those posts were already decided)."
    if left:
        msg += f"\n\nStill waiting for you: {', '.join(left)}."
    gh(f"/issues/{num}/comments", {"body": msg})
    if not left:
        gh(f"/issues/{num}", {"state": "closed"}, "PATCH")
    print(msg)


{"ask": ask, "reply": reply}[sys.argv[1]]()
