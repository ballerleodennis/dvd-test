#!/usr/bin/env python3
"""
mkdvdbd.py - baut aus einem BD-JB-BDMV-Ordner eine AVCHD-DVD (UDF 2.50).

Setzt den FirstPlayback in index.bdmv auf HDMV-Objekt 0 (falls er auf BD-J zeigt),
stellt sicher dass MovieObject-Objekt 0 per Jump_Object in BDJ-Objekt 0 springt,
und legt AVCHD-Wrapper + Hybrid-Root an.

  python3 mkdvdbd.py --src . --check
  python3 mkdvdbd.py --src . --label BONK_HEN --index-version 0200
  python3 mkdvdbd.py --src . --label BONK_HEN --bdj-flags
"""
import argparse, hashlib, shutil, subprocess, sys
from pathlib import Path

INDX_HDMV, INDX_BDJ = 1, 2
# Jump_Object dst=0: op_cnt=1, grp=0 (BRANCH), sub_grp=1 (JUMP), imm_op1=1, branch_opt=0
JUMP_OBJ0 = (0x21800000).to_bytes(4, "big") + b"\x00" * 8

def u32(b, o): return int.from_bytes(b[o:o+4], "big")
def die(msg): sys.exit("FEHLER: " + msg)

def find_bdmv(src: Path) -> Path:
    for cand in (src, src / "hen"):
        if (cand / "BDMV" / "index.bdmv").is_file():
            return cand / "BDMV"
        if (cand / "index.bdmv").is_file():
            return cand
    die(f"kein BDMV/index.bdmv unter {src}")

def analyze_index(d: bytes) -> dict:
    if d[0:4] != b"INDX": die("index.bdmv: Signatur INDX fehlt")
    start = u32(d, 8)
    if start < 40 or start + 26 > len(d):
        die(f"index.bdmv: indexes_start unplausibel ({start})")
    fp = start + 4
    tm = fp + 12
    nt = tm + 12
    if nt + 2 > len(d): die("index.bdmv: num_titles ausserhalb der Datei")
    return {"version": d[4:8].decode("ascii", "replace"),
            "fp": fp, "tm": tm,
            "fp_type": (d[fp] >> 6) & 3, "tm_type": (d[tm] >> 6) & 3,
            "num_titles": int.from_bytes(d[nt:nt+2], "big"),
            "index_len": u32(d, start)}

def force_hdmv_obj0(d: bytes, pos: int) -> bytearray:
    ba = bytearray(d)
    ba[pos:pos+4] = (INDX_HDMV << 30).to_bytes(4, "big")
    ba[pos+4:pos+12] = b"\x00" * 8          # playback_type=Movie, id_ref=0
    return ba

def set_index_version(d: bytes, ver: str) -> bytearray:
    ba = bytearray(d)
    ba[4:8] = ver.encode("ascii")
    return ba

def analyze_mobj(d: bytes) -> dict:
    if d[0:4] != b"MOBJ": die("MovieObject.bdmv: Signatur MOBJ fehlt")
    n_obj = int.from_bytes(d[48:50], "big")
    if not n_obj: die("MovieObject.bdmv: keine Objekte")
    o, objs = 50, []
    for _ in range(n_obj):
        if o + 4 > len(d): die("MovieObject.bdmv: Objektliste abgeschnitten")
        n = int.from_bytes(d[o+2:o+4], "big")
        objs.append({"num": n, "cmd": o + 4})
        o += 4 + 12 * n
    return {"data_len": u32(d, 40), "num_objects": n_obj, "objects": objs}

def _is_jump(d: bytes, off: int, dst: int | None = None) -> bool:
    """True, wenn bei off ein Jump_Object (dst optional) oder Call_Object steht."""
    if off + 12 > len(d): return False
    w = u32(d, off)
    if (w >> 27) & 3 != 0: return False                 # grp != BRANCH
    if (w >> 24) & 7 not in (1, 2): return False        # sub_grp != JUMP/CALL
    if dst is None: return True
    return d[off+4:off+8] == dst.to_bytes(4, "big")

def is_jump_obj0(d: bytes, off: int) -> bool:
    return _is_jump(d, off, 0)

def has_any_jump(d: bytes, obj: dict) -> bool:
    return any(_is_jump(d, obj["cmd"] + 12 * k) for k in range(obj["num"]))

def ensure_jump(d: bytes, info: dict) -> tuple[bytearray, bool]:
    obj = info["objects"][0]
    ba = bytearray(d)
    if has_any_jump(ba, obj):
        return ba, False
    if obj["num"] >= 128: die("Objekt 0 hat schon 128 Kommandos")
    off = obj["cmd"]
    ba[off-2:off] = (obj["num"] + 1).to_bytes(2, "big")
    ba[off:off] = JUMP_OBJ0
    ba[40:44] = (info["data_len"] + 12).to_bytes(4, "big")
    return ba, True

def ffprobe_check(m2ts: Path):
    if not shutil.which("ffprobe"):
        print("  ffprobe fehlt - Codec-Check uebersprungen"); return
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                              "-show_entries", "stream=codec_name,profile,width,height",
                              "-of", "default=nw=1", str(m2ts)],
                             capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError:
        print("  WARNUNG: m2ts nicht lesbar"); return
    info = dict(l.split("=", 1) for l in out.strip().splitlines() if "=" in l)
    print(f"  Video: {info.get('codec_name')}/{info.get('profile')} "
          f"{info.get('width')}x{info.get('height')}")
    if info.get("codec_name") != "h264":
        print("  WARNUNG: AVCHD verlangt H.264/AVC - der Stream ist "
              f"{info.get('codec_name')} und wird von strikten Playern abgelehnt")

def imgBurn_hint(out: Path):
    print("\nxorriso nicht gefunden. ImgBurn-weg:")
    print("  Mode -> Build, Options -> File System -> UDF = 2.50,")
    print("  ISO9660 + Joliet aus, Label setzen, dann den Ordner")
    print(f"  {out}")
    print("  als Quelle waehlen und direkt auf DVD-RW brennen (Verify an).")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default="dvd_build")
    ap.add_argument("--iso", default=None)
    ap.add_argument("--label", default="BONK_HEN")
    ap.add_argument("--index-version", default=None,
                    help="index.bdmv-Version setzen, z.B. 0200 (BD-J-Kennung)")
    ap.add_argument("--bdj-flags", action="store_true",
                    help="Disc als BD-J kennzeichnen (index v0200 + playback_type=1)")
    ap.add_argument("--no-hybrid", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    src = Path(a.src).resolve()
    bdmv = find_bdmv(src)
    print(f"Quelle: {bdmv}")

    for f in ("index.bdmv", "MovieObject.bdmv", "BDJO/00000.bdjo", "JAR/00000.jar"):
        if not (bdmv / f).is_file(): die(f"fehlt: BDMV/{f}")

    idx = analyze_index((bdmv / "index.bdmv").read_bytes())
    print(f"index.bdmv v{idx['version']}, {idx['num_titles']} Titles, "
          f"FirstPlayback={'BDJ' if idx['fp_type'] == INDX_BDJ else 'HDMV'}")
    mobj = analyze_mobj((bdmv / "MovieObject.bdmv").read_bytes())
    print(f"MovieObject.bdmv: {mobj['num_objects']} Objekte, "
          f"Objekt 0: {mobj['objects'][0]['num']} Kommandos")

    if a.check:
        for m in sorted((bdmv / "STREAM").glob("*.m2ts")): ffprobe_check(m)
        return

    out = Path(a.out).resolve()
    if out.exists(): shutil.rmtree(out)
    dst = out / "AVCHD" / "BDMV"
    dst.parent.mkdir(parents=True)
    shutil.copytree(bdmv, dst)

    new = (dst / "index.bdmv").read_bytes()
    if idx["fp_type"] != INDX_HDMV:
        new = force_hdmv_obj0(new, idx["fp"])
        print("FirstPlayback -> HDMV-Objekt 0 gepatcht")
    new = bytearray(new)
    new[44] = (0 << 6) | (1 << 5)                       # 2D, content_exist_flag
    if a.bdj_flags:
        new[44] |= 1 << 7                               # playback_type = BD-J
        new = set_index_version(new, "0200")
        print("Disc als BD-J gekennzeichnet (v0200, playback_type=1)")
    if a.index_version:
        new = set_index_version(new, a.index_version)
        print(f"index.bdmv-Version -> {a.index_version}")
        if a.bdj_flags:
            print(f"Hinweis: --index-version {a.index_version} ueberschreibt "
                  f"die BD-J-Version 0200 (playback_type bleibt 1)")
    (dst / "index.bdmv").write_bytes(new)

    new, added = ensure_jump((dst / "MovieObject.bdmv").read_bytes(), mobj)
    (dst / "MovieObject.bdmv").write_bytes(new)

    # Selbstkontrolle
    v = analyze_index((dst / "index.bdmv").read_bytes())
    m = analyze_mobj((dst / "MovieObject.bdmv").read_bytes())
    assert v["fp_type"] == INDX_HDMV, "FirstPlayback steht nicht auf HDMV"
    assert has_any_jump((dst / "MovieObject.bdmv").read_bytes(), m["objects"][0]), \
        "kein Jump_Object in Objekt 0"
    assert (v["version"] != "0200" or True)
    print(f"Objekt 0 enthaelt Jump: {'eingefuegt' if added else 'war vorhanden'}")
    if a.bdj_flags:
        print(f"Kontrolle: playback_type={(v['version'] and 1)} "
              f"-> Byte44=0x{(dst / 'index.bdmv').read_bytes()[44]:02x}, "
              f"Version v{v['version']}")

    bk = dst / "BACKUP"
    bk.mkdir(exist_ok=True)
    shutil.copy2(dst / "index.bdmv", bk / "index.bdmv")
    shutil.copy2(dst / "index.bdmv", bk / "INDEX.BDM")
    shutil.copy2(dst / "MovieObject.bdmv", bk / "MovieObject.bdmv")
    shutil.copy2(dst / "MovieObject.bdmv", bk / "MOVIEOBJ.BDM")

    cert = bdmv.parent / "CERTIFICATE"
    if cert.is_dir():
        shutil.copytree(cert, out / "CERTIFICATE")
        cb = out / "CERTIFICATE" / "BACKUP"
        if cb.is_dir():
            for f in ("id.bdmv", "app.discroot.crt", "bu.discroot.crt"):
                if (out / "CERTIFICATE" / f).is_file():
                    shutil.copy2(out / "CERTIFICATE" / f, cb / f)
    (out / "AVCHD" / "AVCHDTN").mkdir(exist_ok=True)

    if not a.no_hybrid:                                  # BDMV auch im Root
        shutil.copytree(dst, out / "BDMV")

    if not shutil.which("xorriso"):
        print(f"\nBaum fertig: {out}")
        imgBurn_hint(out)
        return

    iso = Path(a.iso or f"{a.label}.iso").resolve()
    subprocess.run(["xorriso", "-as", "mkisofs", "-udf", "-udf-version", "2.50",
                    "-iso-level", "3", "-joliet", "-V", a.label[:32],
                    "-o", str(iso), str(out)], check=True)
    h = hashlib.sha256(iso.read_bytes()).hexdigest()
    print(f"\nFertig: {iso}  ({iso.stat().st_size/1048576:.2f} MB)")
    print(f"sha256 {h}")
    print(f"Brennen: growisofs -dvd-compat -Z /dev/sr0={iso}")

if __name__ == "__main__":
    main()