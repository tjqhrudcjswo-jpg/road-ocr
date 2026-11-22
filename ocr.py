#!/usr/bin/env python3
"""로드플러스 CCTV 사진 왼쪽 위의 '대기 / 노면' 글자를 읽어 data/cctv.json 으로 저장."""
import json, os, re, subprocess, sys, tempfile, time, urllib.error, urllib.request, datetime
from collections import Counter
from PIL import Image, ImageOps

# 이름은 웹앱(worker.js)의 지점 이름과 똑같이 써야 합니다.
CAMS = [
    # img_id: 사진 주소 가운데의 카메라별 고정 값 (API가 안 열릴 때 직접 주소를 만드는 데 사용)
    {"name": "논공휴게소", "cctv": "cctv00003534", "img_id": "n6pQWvRdaX5tJPupDGM1m3AqrHy2_iGo8RYil2IfesM"},
    {"name": "거창휴게소", "cctv": "cctv00005776", "img_id": "aFoZkD6yCUDcOqjZrvVzBL0vFko_5i_inMhrxlgYD3Y"},
]
API = "https://www.roadplus.co.kr/api/main/selectCctvInfoAjax?cctvId={id}&driveDrctCd={d}"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Referer": "https://www.roadplus.co.kr/"}
# 글자가 있는 영역(사진 크기 대비 비율): 왼쪽 위
BOX = (0.0, 0.20, 0.30, 0.50)
THRESHOLDS = (190, 210, 230)


DEADLINE = time.time() + 90  # 전체 접속 시간 상한(초). 넘으면 더 기다리지 않고 포기


def get(url, binary=False, timeout=15, tries=2):
    last = None
    for i in range(tries):
        if time.time() > DEADLINE:
            raise TimeoutError("전체 시간 초과")
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
            return b if binary else b.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
        except Exception as e:  # noqa
            last = e
        time.sleep(1)
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


def _val(m):
    """숫자 글자를 값으로. 표시는 항상 소수 한 자리(예: 2.8). 점이 사라지거나 다른 숫자로 읽힌 경우도 복원."""
    if not m:
        return None, False
    s = m.group(1).replace(" ", "")
    neg = s.startswith("-")
    d = s.lstrip("-")
    dot = "." in d
    try:
        if dot:
            v = float(d)
        elif len(d) == 2:
            v = int(d) / 10.0
        elif len(d) == 3:           # 점이 숫자로 잘못 읽힘: 첫 자리 . 끝 자리
            v = float(d[0] + "." + d[2])
        else:
            return None, False
    except ValueError:
        return None, False
    return (-v if neg else v), dot


def guess_image(cam):
    """API가 안 될 때: 카메라 고정 ID + 최근 분(KST)으로 사진 주소를 직접 만들어 시도."""
    num = int(re.sub(r"\D", "", cam["cctv"]))
    now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9)
    for back in range(1, 6):
        t = (now - datetime.timedelta(minutes=back)).strftime("%Y%m%d.%H%M")
        for host in ("exmobile4", "exmobile3"):
            url = f"https://{host}.hscdn.com/cctv{num}image/{cam['img_id']}_{t}00.000.jpg"
            try:
                get(url, binary=True, timeout=8, tries=1)
                return url
            except Exception:
                continue
    return None


def _cands(raw):
    """읽힌 글자에서 (값, 가중치) 후보를 만든다. 표시는 항상 소수 한 자리(X.Y).
    - 점이 있으면 소수 첫째 자리까지만(뒤에 붙은 잡글자 제거)
    - 점이 없고 3자리면: 점이 숫자로 읽힌 경우(X?Y)와 뒤에 잡글자가 붙은 경우(XY?) 둘 다 반반"""
    m = re.search(r"(-?)\s?(\d+)(?:\.(\d+))?", raw)
    if not m:
        return []
    neg, ip, fp = m.group(1) == "-", m.group(2), m.group(3)
    sg = -1 if neg else 1
    out = []
    if fp is not None:
        if len(ip) <= 2:
            out.append((sg * float(ip + "." + fp[0]), 1.0))
    else:
        if len(ip) == 2:
            out.append((sg * int(ip) / 10.0, 1.0))
        elif len(ip) == 3:
            out.append((sg * float(ip[0] + "." + ip[2]), 0.5))
            out.append((sg * float(ip[0] + "." + ip[1]), 0.5))
    return [(v, w) for v, w in out if -40 <= v <= 60]


def parse(t):
    t = t.replace("−", "-").replace("–", "-").replace("—", "-").replace("~", "-")
    num = r"(-?\s?\d+(?:\.\d+)?)"
    a = re.search(r"대\s*기\s*" + num, t)
    r = re.search(r"노\s*면\s*" + num, t)
    st = re.findall(r"노\s*면\s+([가-힣]{1,4})", t)
    av, ad = _val(a)
    rv, rd = _val(r)
    return av, rv, (st[-1] if st else None), ad, rd


def _run(img, lang, psm):
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        img.save(f.name)
        return subprocess.run(["tesseract", f.name, "-", "-l", lang, "--psm", str(psm)],
                              capture_output=True, text=True).stdout


def _prep(im, box, th, target=400):
    w, h = im.size
    c = im.crop((int(w * box[0]), int(h * box[1]), int(w * box[2]), int(h * box[3])))
    sc = max(3, round(target / max(1, c.height)))
    c = c.resize((c.width * sc, c.height * sc), Image.LANCZOS)
    return ImageOps.invert(c.point(lambda p: 255 if p > th else 0))


def _find_lines(im):
    """왼쪽 위 글자 영역에서 '대기' 줄과 '노면' 줄의 세로 위치(사진 비율)를 찾는다."""
    w, h = im.size
    box = (0.0, 0.16, 0.20, 0.50)
    found = {}
    for th in (190, 170, 210):
        img = _prep(im, box, th, 500)
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            img.save(f.name)
            tsv = subprocess.run(["tesseract", f.name, "-", "-l", "kor", "--psm", "6", "tsv"],
                                 capture_output=True, text=True).stdout
        lines = {}
        for row in tsv.splitlines()[1:]:
            c = row.split("\t")
            if len(c) < 12 or c[0] != "5" or not c[11].strip():
                continue
            key = (c[2], c[3], c[4])
            top, hh = int(c[7]), int(c[9])
            L = lines.setdefault(key, {"t": top, "b": top + hh, "txt": ""})
            L["t"], L["b"] = min(L["t"], top), max(L["b"], top + hh)
            L["txt"] += c[11] + " "
        ordered = sorted(lines.values(), key=lambda x: x["t"])
        for L in ordered:
            y0 = box[1] + L["t"] / img.height * (box[3] - box[1])
            y1 = box[1] + L["b"] / img.height * (box[3] - box[1])
            if "air" not in found and re.search(r"대|기", L["txt"]):
                found["air"] = (y0, y1)
            elif "road" not in found and re.search(r"노|면", L["txt"]) and re.search(r"\d", L["txt"]):
                found["road"] = (y0, y1)
        if "air" in found and "road" in found:
            break
    return found


NUM_X = (0.083, 0.150)
FALLBACK = {"air": (0.226, 0.282), "road": (0.288, 0.346)}


def _read_number(im, yr):
    tally = Counter()
    runs = 0
    pad = 0.004
    for dy in (0.0, -0.004, 0.004):
        for x0, x1 in ((NUM_X[0], NUM_X[1]), (NUM_X[0] - 0.010, NUM_X[1] + 0.006), (NUM_X[0] + 0.004, NUM_X[1] - 0.004)):
            for th in (170, 195, 220):
                img = ImageOps.expand(_prep(im, (x0, yr[0] - pad + dy, x1, yr[1] + pad + dy), th, 200), border=40, fill=255)
                with tempfile.NamedTemporaryFile(suffix=".png") as f:
                    img.save(f.name)
                    raw = subprocess.run(["tesseract", f.name, "-", "-l", "eng", "--psm", "7",
                                          "-c", "tessedit_char_whitelist=0123456789.-"],
                                         capture_output=True, text=True).stdout.replace(" ", "").strip()
                c = _cands(raw)
                if c:
                    runs += 1
                    for v, w in c:
                        tally[v] += w
    if not tally:
        return None, 0, 0, ""
    val, n = tally.most_common(1)[0]
    top = ", ".join(f"{k}:{round(v, 1)}" for k, v in tally.most_common(4))
    return val, round(n, 1), runs, top


def _read_state(im, road_y):
    """노면 줄 바로 아래 줄('노면 마름')에서 상태 글자를 읽는다."""
    for dy in (0.0, 0.01):
        for th in (190, 210):
            box = (0.0, road_y[1] + dy - 0.004, 0.30, road_y[1] + dy + 0.075)
            t = _run(_prep(im, box, th, 220), "kor", 6)
            m = re.search(r"노\s*면\s*([가-힣]{1,4})", t)
            if m:
                return m.group(1)
    return None


def ocr(path):
    im = Image.open(path).convert("L")
    lines = _find_lines(im)
    ya = lines.get("air") or FALLBACK["air"]
    yr = lines.get("road") or FALLBACK["road"]
    air, na, ta, topa = _read_number(im, ya)
    road, nr, tr, topr = _read_number(im, yr)
    info = [f"size={im.size}", f"lines={lines}", f"air {na}/{ta} [{topa}]", f"road {nr}/{tr} [{topr}]"]
    # 같은 값으로 읽힌 비율이 낮으면(자신 없음) 값을 쓰지 않는다
    if air is None or road is None or na < 0.3 * ta or nr < 0.3 * tr or ta < 5 or tr < 5:
        info.append("신뢰도 낮음 → 값 사용 안 함")
        return None, info
    return {"air": air, "road": road, "state": _read_state(im, yr), "votes": f"{na}/{ta},{nr}/{tr}"}, info


def work(cam):
    try:
        url = find_image(cam["cctv"])
        if not url and cam.get("img_id"):
            print("API 실패 → 주소 직접 생성", cam["name"], flush=True)
            url = guess_image(cam)
        if not url:
            print("no image", cam["name"], flush=True)
            return cam["name"], None
        m = re.search(r"_(\d{8})\.(\d{4})\d{2}\.", url)
        tm = (m.group(1) + m.group(2)) if m else ""
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            data = get(url, binary=True)
            f.write(data); f.flush()
            try:
                os.makedirs("data", exist_ok=True)
                with open("data/" + cam["cctv"] + ".jpg", "wb") as g:
                    g.write(data)
            except Exception:
                pass
            res, raws = ocr(f.name)
        print(cam["name"], tm, res, raws, flush=True)
        if res:
            res.update({"img_tm": tm, "raw": " ; ".join(raws)})
        return cam["name"], res
    except Exception as e:  # noqa
        print("fail", cam["name"], e, file=sys.stderr, flush=True)
        return cam["name"], None


def main():
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(CAMS)) as ex:
        results = list(ex.map(work, CAMS))
    out = {n: r for n, r in results if r}
    os.makedirs("data", exist_ok=True)
    with open("data/cctv.json", "w", encoding="utf-8") as fp:
        json.dump({"updated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "cams": out}, fp, ensure_ascii=False)


if __name__ == "__main__":
    main()
