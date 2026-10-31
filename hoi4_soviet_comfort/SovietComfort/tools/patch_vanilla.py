#!/usr/bin/env python3
"""
Soviet Comfort - patcher for vanilla files.

Takes two files from YOUR copy of Hearts of Iron IV and writes patched
copies into this mod folder (so they always match your game version):

  common/national_focus/soviet.txt
      every focus gets "SC_focus_completed = yes" in its completion_reward
      (a small, growing bonus - see common/scripted_effects).

  common/technologies/industry.txt
      the xor between Concentrated and Dispersed industry is removed,
      so both branches can be researched.

Usage:
  python patch_vanilla.py                      (Steam default path)
  python patch_vanilla.py "D:/Games/Hearts of Iron IV"

Re-run it after every game update.
"""
import os
import re
import sys

DEFAULT_PATHS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV",
    r"C:\Program Files\Steam\steamapps\common\Hearts of Iron IV",
    r"D:\SteamLibrary\steamapps\common\Hearts of Iron IV",
    os.path.expanduser("~/.steam/steam/steamapps/common/Hearts of Iron IV"),
    os.path.expanduser("~/.local/share/Steam/steamapps/common/Hearts of Iron IV"),
    os.path.expanduser("~/Library/Application Support/Steam/steamapps/common/Hearts of Iron IV"),
]

MOD_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOCUS_FILE = os.path.join("common", "national_focus", "soviet.txt")
TECH_FILE = os.path.join("common", "technologies", "industry.txt")
MARKER = "SC_focus_completed = yes"

TOKEN_RE = re.compile(r'#[^\n]*|"(?:[^"\\]|\\.)*"|[{}=]|[^\s{}=#"]+')


def tokens(text):
    """Yield (value, start, end); comments are skipped."""
    for m in TOKEN_RE.finditer(text):
        v = m.group(0)
        if v.startswith("#"):
            continue
        yield v, m.start(), m.end()


def read(path):
    # latin-1 keeps every byte as is; we only touch ASCII.
    with open(path, "rb") as f:
        return f.read().decode("latin-1")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(text.encode("latin-1"))


def patch_focus(text):
    toks = list(tokens(text))
    inserts = []  # (pos, string)
    depth = 0
    tree_depth = None  # depth inside focus_tree block
    focus = None  # dict for current focus
    n_focus = n_reward = n_added = 0

    for i, (v, s, e) in enumerate(toks):
        nxt = toks[i + 1][0] if i + 1 < len(toks) else None
        nxt2 = toks[i + 2][0] if i + 2 < len(toks) else None
        is_block = nxt == "=" and nxt2 == "{"

        if v == "{":
            depth += 1
            continue
        if v == "}":
            if focus is not None and depth == focus["depth"]:
                if not focus["has_reward"]:
                    inserts.append((s, "\tcompletion_reward = { %s }\n\t" % MARKER))
                    n_added += 1
                focus = None
            depth -= 1
            if tree_depth is not None and depth < tree_depth:
                tree_depth = None
            continue

        if is_block and v == "focus_tree" and tree_depth is None:
            tree_depth = depth + 1
        elif is_block and v == "focus" and tree_depth is not None and depth == tree_depth and focus is None:
            focus = {"depth": depth + 1, "has_reward": False}
            n_focus += 1
        elif is_block and v == "completion_reward" and focus is not None and depth == focus["depth"]:
            focus["has_reward"] = True
            n_reward += 1
            brace_end = toks[i + 2][2]
            inserts.append((brace_end, "\n\t\t\t%s" % MARKER))

    for pos, s in sorted(inserts, reverse=True):
        text = text[:pos] + s + text[pos:]
    return text, n_focus, n_reward, n_added


def patch_tech(text):
    """Remove xor = { ... } blocks that mention concentrated/dispersed industry."""
    toks = list(tokens(text))
    cuts = []
    i = 0
    while i < len(toks):
        v, s, _ = toks[i]
        if v == "xor" and i + 2 < len(toks) and toks[i + 1][0] == "=" and toks[i + 2][0] == "{":
            j = i + 3
            names = []
            while j < len(toks) and toks[j][0] != "}":
                names.append(toks[j][0])
                j += 1
            if j < len(toks) and any("concentrated_industry" in n or "dispersed_industry" in n for n in names):
                cuts.append((s, toks[j][2]))
            i = j
        i += 1
    for s, e in reversed(cuts):
        text = text[:s] + "# xor removed by Soviet Comfort" + text[e:]
    return text, len(cuts)


def main():
    if len(sys.argv) > 1:
        game = sys.argv[1]
    else:
        game = next((p for p in DEFAULT_PATHS if os.path.isdir(p)), None)
        if game is None:
            print("Game folder not found. Run:  python patch_vanilla.py \"<path to Hearts of Iron IV>\"")
            return 1

    src_focus = os.path.join(game, FOCUS_FILE)
    src_tech = os.path.join(game, TECH_FILE)
    for p in (src_focus, src_tech):
        if not os.path.isfile(p):
            print("File not found:", p)
            return 1

    text = read(src_focus)
    if MARKER in text:
        print("soviet.txt in the game folder is already patched?! Verify game files in Steam.")
        return 1
    text, n_focus, n_reward, n_added = patch_focus(text)
    write(os.path.join(MOD_DIR, FOCUS_FILE), text)
    print("soviet.txt:   %d focuses (%d completion_reward patched, %d added)" % (n_focus, n_reward, n_added))
    if n_focus == 0:
        print("WARNING: no focuses found - file format changed?")

    text, n_xor = patch_tech(read(src_tech))
    write(os.path.join(MOD_DIR, TECH_FILE), text)
    print("industry.txt: %d xor blocks removed" % n_xor)
    if n_xor == 0:
        print("WARNING: no Concentrated/Dispersed xor found - file format changed?")

    print("Done. Files written to:", MOD_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
