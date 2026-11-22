#!/usr/bin/env python3
"""로드플러스 CCTV 사진 왼쪽 위의 '대기 / 노면' 글자를 읽어 data/cctv.json 으로 저장."""
import json, os, re, subprocess, sys, tempfile, urllib.request, datetime
from collections import Counter
from PIL import Image, ImageOps

# 이름은 웹앱(worker.js)의 지점 이름과 똑같이 써야 합니다.
CAMS = [
    {"name": "논공휴게소", "cctv": "cctv00003534"},
    {"name": "거창휴게소", "cctv": "cctv00005776"},
]
API = "https://www.roadplus.co.kr/api/main/selectCctvInfoAjax?cctvId={id}&driveDrctCd={d}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Referer": "https://www.roadplus.co.kr/"}
# 글자가 있는 영역(사진 크기 대비 비율): 왼쪽 위
BOX = (0.0, 0.20, 0.30, 0.50)
THRESHOLDS = (190, 210, 230)


def get(url, binary=False):
    last = None
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                b = r.read()
            return b if binary else b.decode("utf-8", "replace")
        except Exception as e:  # noqa
            last = e
    raise last


def find_image(cctv):
    for d in ("S", "E"):
        try:
            j = json.loads(get(API.format(id=cctv, d=d)))
            info = j.get("cctvInfo") or []
            if info and info[0].get("NOWCCTVIMG") and info[0].get("CCTVID") == cctv:
                return info[0]["NOWCCTVIMG"]
        except Exception as e:  # noqa
            print("api", cctv, d, e, file=sys.stderr)
    return None


def parse(t):
    t = t.replace("−", "-").replace("–", "-").replace("—", "-").replace("~", "-")
    num = r"(-?\s*\d{1,2}(?:\.\d)?)"
    a = re.search(r"대\s*기\s*" + num, t)
    r = re.search(r"노\s*면\s*" + num, t)
    st = re.findall(r"노\s*면\s+([가-힣]{1,4})", t)
    f = lambda m: float(m.group(1).replace(" ", "")) if m else None
    return f(a), f(r), (st[-1] if st else None)


def ocr(path):
    im = Image.open(path).convert("L")
    w, h = im.size
    c = im.crop((int(w * BOX[0]), int(h * BOX[1]), int(w * BOX[2]), int(h * BOX[3])))
    scale = max(2, round(400 / c.height))
    c = c.resize((c.width * scale, c.height * scale), Image.LANCZOS)
    votes, raws = [], []
    for th in THRESHOLDS:
        b = ImageOps.invert(c.point(lambda p, th=th: 255 if p > th else 0))
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            b.save(f.name)
            out = subprocess.run(["tesseract", f.name, "-", "-l", "kor", "--psm", "6"],
                                 capture_output=True, text=True).stdout
        raws.append(out.strip().replace("\n", " | "))
        air, road, st = parse(out)
        if air is not None and road is not None and -40 <= air <= 50 and -40 <= road <= 60:
            votes.append((air, road, st))
    if not votes:
        return None, raws
    (air, road), _ = Counter((a, r) for a, r, _ in votes).most_common(1)[0]
    st = next((s for a, r, s in votes if (a, r) == (air, road) and s), None)
    return {"air": air, "road": road, "state": st}, raws


def main():
    out = {}
    for cam in CAMS:
        try:
            url = find_image(cam["cctv"])
            if not url:
                print("no image", cam["name"]); continue
            m = re.search(r"_(\d{8})\.(\d{4})\d{2}\.", url)
            tm = (m.group(1) + m.group(2)) if m else ""
            with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
                f.write(get(url, binary=True)); f.flush()
                res, raws = ocr(f.name)
            print(cam["name"], tm, res, raws)
            if res:
                res.update({"img_tm": tm, "raw": raws[1] if len(raws) > 1 else raws[0]})
                out[cam["name"]] = res
        except Exception as e:  # noqa
            print("fail", cam["name"], e, file=sys.stderr)
    os.makedirs("data", exist_ok=True)
    with open("data/cctv.json", "w", encoding="utf-8") as fp:
        json.dump({"updated": datetime.datetime.utcnow().isoformat() + "Z", "cams": out}, fp, ensure_ascii=False)


if __name__ == "__main__":
    main()
