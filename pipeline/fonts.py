"""Download the open-licence (OFL) fonts the pipeline uses from Google Fonts' GitHub.

  python run.py fonts

Each family's OFL.txt licence is saved next to it as fonts/LICENSE-<Family>.txt.
"""
from pathlib import Path
from urllib.parse import quote

import requests

GF = "https://github.com/google/fonts/raw/main/ofl/"
# google/fonts only ships the Chinese fonts as variable fonts whose default weight is Thin,
# which libass renders hair-thin - so take the static weights from Google's noto-cjk repo.
CJK = "https://github.com/notofonts/noto-cjk/raw/main/"

# (download url, local file name, licence url, family name for the licence file)
FONTS = [
    (GF + "anton/Anton-Regular.ttf", "Anton-Regular.ttf", GF + "anton/OFL.txt", "Anton"),
    (GF + "cinzel/Cinzel[wght].ttf", "Cinzel-Variable.ttf", GF + "cinzel/OFL.txt", "Cinzel"),
    (GF + "notosans/NotoSans[wdth,wght].ttf", "NotoSans-Variable.ttf",
     GF + "notosans/OFL.txt", "NotoSans"),
    (GF + "chonburi/Chonburi-Regular.ttf", "Chonburi-Regular.ttf", GF + "chonburi/OFL.txt", "Chonburi"),
    (GF + "notosansthai/NotoSansThai[wdth,wght].ttf", "NotoSansThai-Variable.ttf",
     GF + "notosansthai/OFL.txt", "NotoSansThai"),
    (CJK + "Sans/SubsetOTF/SC/NotoSansSC-Regular.otf", "NotoSansSC-Regular.otf",
     CJK + "Sans/LICENSE", "NotoSansSC"),
    (CJK + "Sans/SubsetOTF/SC/NotoSansSC-Bold.otf", "NotoSansSC-Bold.otf",
     CJK + "Sans/LICENSE", "NotoSansSC"),
    (CJK + "Serif/SubsetOTF/SC/NotoSerifSC-Bold.otf", "NotoSerifSC-Bold.otf",
     CJK + "Serif/LICENSE", "NotoSerifSC"),
]


def _get(url, dest):
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        tmp.replace(dest)


def download_fonts(fonts_dir, force=False):
    fonts_dir = Path(fonts_dir)
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for url, local, lic_url, family in FONTS:
        dest = fonts_dir / local
        if force or not dest.exists():
            print(f"downloading {local} ...")
            _get(quote(url, safe=":/"), dest)
        lic = fonts_dir / f"LICENSE-{family}.txt"
        if force or not lic.exists():
            _get(lic_url, lic)
    print(f"Fonts ready in {fonts_dir}")


def missing_fonts(fonts_dir, local_names):
    return [n for n in local_names if not (Path(fonts_dir) / n).exists()]
