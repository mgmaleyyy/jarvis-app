"""Download NASA Earth textures on the build machine and ship them inside the app."""
import io, os, sys, urllib.request
from PIL import Image
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "home"))
src = open(os.path.join(os.path.dirname(__file__), "..", "home", "jarvis.py"), encoding="utf-8").read()
a = src.index('CW = "'); b = src.index("TEX_STATE =")
ns = {}; exec(src[a:b], ns)
OUT = os.path.join(os.path.dirname(__file__), "..", "home", "tex"); os.makedirs(OUT, exist_ok=True)
SIZE = {"day": (4096, 2048), "night": (4096, 2048), "clouds": (2048, 1024)}
for k, urls in ns["TEX_SRC"].items():
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 JarvisBuild/1.0"})
            data = urllib.request.urlopen(req, timeout=90).read()
            im = Image.open(io.BytesIO(data)).convert("L" if k == "clouds" else "RGB")
            im = im.resize(SIZE[k], Image.LANCZOS)
            im.save(os.path.join(OUT, k + ".jpg"), "JPEG", quality=88, optimize=True, progressive=False)
            print(k, "OK from", u, os.path.getsize(os.path.join(OUT, k + ".jpg")))
            break
        except Exception as e:
            print(k, "fail", u, type(e).__name__, e)
    else:
        sys.exit(f"could not fetch {k}")
