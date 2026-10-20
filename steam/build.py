#!/usr/bin/env python3
"""Builds the Steam edition of Межа.

    python3 steam/build.py [--appid 480] [--platform win|linux|all] [--with-ukraine]

From ../index.html it makes a copy for Steam (index.html with MEZHA_EDITION = 'steam'): the fonts put inside the file,
the Windows shell of the web version taken out, and the fronts traced from DeepState's and uacontrolmap.com's maps
removed (not licensed for a game that is sold; --with-ukraine keeps them, for when you have their permission).
Then it puts that page into Electron with steamworks.js: steam/dist/Mezha-Steam-<platform>/ and a zip of it.

Downloads (kept in steam/.cache): Electron, steamworks.js, the fonts from Google Fonts (SIL Open Font License).
"""
import argparse, base64, io, json, os, re, shutil, stat, subprocess, sys, tarfile, urllib.request, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(HERE, '.cache')
DIST = os.path.join(HERE, 'dist')
ELECTRON = '33.2.1'
STEAMWORKS = '0.4.0'
FONTS = 'https://fonts.googleapis.com/css2?family=Golos+Text:wght@400;500;600&family=JetBrains+Mono:wght@400;500&family=Unbounded:wght@600&display=swap'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'


def fetch(url, name=None):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name or re.sub(r'[^A-Za-z0-9._-]', '_', url)[-120:])
    if not os.path.exists(path):
        print('  download', url)
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=300) as r, open(path + '.part', 'wb') as f:
            shutil.copyfileobj(r, f)
        os.replace(path + '.part', path)
    return path


def embed_fonts(html):
    """The Google Fonts link becomes @font-face rules with the fonts inside (Cyrillic and Latin)."""
    css = open(fetch(FONTS, 'fonts.css'), encoding='utf-8').read()
    out = []
    for block in re.findall(r'/\*\s*([\w-]+)\s*\*/\s*(@font-face\s*{[^}]*})', css):
        subset, rule = block
        if subset not in ('cyrillic', 'cyrillic-ext', 'latin', 'latin-ext'):
            continue
        url = re.search(r'url\((https://[^)]+)\)', rule).group(1)
        data = base64.b64encode(open(fetch(url), 'rb').read()).decode()
        out.append(rule.replace(url, 'data:font/woff2;base64,' + data))
    if not out:
        sys.exit('no fonts found in the Google Fonts CSS')
    html = re.sub(r'<link rel="preconnect"[^>]*>\n?', '', html)
    html = re.sub(r'<link rel="stylesheet" href="https://fonts\.googleapis\.com[^>]*>', '<style>' + '\n'.join(out) + '</style>', html)
    return html


def steam_page(with_ukraine):
    html = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
    # the edition flag, before the game's script
    html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n<script>window.MEZHA_EDITION = \'steam\';</script>', 1)
    # the web version's Windows shell (a copy of itself to download) is not needed in Steam
    html, n = re.subn(r'<script type="application/octet-stream" id="winshell">[^<]*</script>\n?', '', html)
    assert n == 1, 'winshell not found'
    if not with_ukraine:
        html, n = re.subn(r'(<script type="application/json" id="ukrdata">)[^<]*(</script>)', r'\1{"pool":[],"layers":{}}\2', html)
        assert n == 1, 'ukrdata not found'
        for name in ('DS_2025_01', 'DS_2026_10', 'SUDZHA_2025', 'DONBAS_2015', 'DONBAS_JAN2015'):
            html, n = re.subn(r'^const %s = .*;$' % name, 'const %s = [];' % name, html, flags=re.M)
            assert n == 1, name + ' not found'
    html = embed_fonts(html)
    assert 'fonts.googleapis' not in html
    return html


def steamworks_pkg():
    tgz = fetch('https://registry.npmjs.org/steamworks.js/-/steamworks.js-%s.tgz' % STEAMWORKS)
    return tarfile.open(tgz)


def build(platform, appid, page):
    name = 'Mezha-Steam-' + ('Windows' if platform == 'win' else 'Linux')
    out = os.path.join(DIST, name)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    # Electron
    ez = fetch('https://github.com/electron/electron/releases/download/v%s/electron-v%s-%s-x64.zip' % (ELECTRON, ELECTRON, 'win32' if platform == 'win' else 'linux'))
    with zipfile.ZipFile(ez) as z:
        for info in z.infolist():
            z.extract(info, out)
            mode = info.external_attr >> 16
            if mode:
                os.chmod(os.path.join(out, info.filename), mode)
    exe = 'Mezha.exe' if platform == 'win' else 'Mezha'
    os.rename(os.path.join(out, 'electron.exe' if platform == 'win' else 'electron'), os.path.join(out, exe))
    os.remove(os.path.join(out, 'resources', 'default_app.asar'))
    # the Chromium languages: Russian and English are enough
    loc = os.path.join(out, 'locales')
    for f in os.listdir(loc):
        if f not in ('ru.pak', 'en-US.pak'):
            os.remove(os.path.join(loc, f))
    # the game
    app = os.path.join(out, 'resources', 'app')
    os.makedirs(app)
    for f in ('main.js', 'preload.js'):
        shutil.copy(os.path.join(HERE, f), app)
    pkg = json.load(open(os.path.join(HERE, 'package.json'), encoding='utf-8'))
    pkg['steamAppId'] = appid
    json.dump(pkg, open(os.path.join(app, 'package.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    open(os.path.join(app, 'index.html'), 'w', encoding='utf-8').write(page)
    shutil.copy(os.path.join(ROOT, 'app', 'icon.png'), os.path.join(app, 'icon.png'))
    # steamworks.js, with the native part for this platform only
    sw = os.path.join(app, 'node_modules', 'steamworks.js')
    keep = ('dist/win64/' if platform == 'win' else 'dist/linux64/')
    with steamworks_pkg() as t:
        for m in t.getmembers():
            rel = m.name[len('package/'):]
            if not m.isfile() or (rel.startswith('dist/') and not rel.startswith(keep)) or rel.endswith('.lib'):
                continue
            dst = os.path.join(sw, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with t.extractfile(m) as src, open(dst, 'wb') as f:
                shutil.copyfileobj(src, f)
    # Steam's own library must sit next to the program as well, where the system looks for it first
    lib = 'steam_api64.dll' if platform == 'win' else 'libsteam_api.so'
    shutil.copy(os.path.join(sw, keep, lib), out)
    if appid == 480:
        # the test App ID (Spacewar): the game starts without Steam relaunching it; not for a release
        open(os.path.join(out, 'steam_appid.txt'), 'w').write('480')
    for f in ('CREDITS.txt',):
        shutil.copy(os.path.join(HERE, f), out)
    if platform == 'linux':
        st = os.stat(os.path.join(out, exe)); os.chmod(os.path.join(out, exe), st.st_mode | stat.S_IEXEC)
    # the zip
    zp = out + '.zip'
    if os.path.exists(zp):
        os.remove(zp)
    with zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d, _, files in os.walk(out):
            for f in files:
                p = os.path.join(d, f)
                info = zipfile.ZipInfo.from_file(p, os.path.join(name, os.path.relpath(p, out)))
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(p, 'rb') as src:
                    z.writestr(info, src.read())
    print('  %s  %.1f MB' % (zp, os.path.getsize(zp) / 1e6))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--appid', type=int, default=480, help='the App ID from Steamworks (480 = Valve\'s test app)')
    ap.add_argument('--platform', choices=['win', 'linux', 'all'], default='all')
    ap.add_argument('--with-ukraine', action='store_true', help='keep the fronts from DeepState and uacontrolmap.com')
    a = ap.parse_args()
    print('page…')
    page = steam_page(a.with_ukraine)
    os.makedirs(DIST, exist_ok=True)
    open(os.path.join(DIST, 'index.html'), 'w', encoding='utf-8').write(page)
    for p in (['win', 'linux'] if a.platform == 'all' else [a.platform]):
        print(p + '…')
        build(p, a.appid, page)


if __name__ == '__main__':
    main()
