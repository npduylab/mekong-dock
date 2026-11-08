# -*- coding: utf-8 -*-
"""Don Desktop: shortcut/script -> "Ung dung", tai lieu -> "Tai lieu". Thu muc giu nguyen.

Chay:   python tidy_desktop.py          don
        python tidy_desktop.py --undo   tra moi tep ve cho cu (doc tidy_desktop_log.json)
Duong dan trong dock_apps.json duoc cap nhat theo de Dock van mo duoc ung dung.
"""
import ctypes
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "tidy_desktop_log.json"
APPS = HERE / "dock_apps.json"

APP_EXT = {".lnk", ".url", ".bat", ".cmd", ".exe", ".bak"}
DOC_EXT = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".pdf", ".zip", ".rar", ".7z", ".json", ".txt", ".csv",
           ".png", ".jpg", ".jpeg", ".mp4"}


def desktop() -> Path:
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
    return Path(buf.value)


def update_dock(moves: dict[str, str]) -> None:
    if not APPS.exists():
        return
    apps = json.loads(APPS.read_text(encoding="utf-8-sig"))
    n = 0
    for a in apps:
        if a.get("path") in moves:
            a["path"] = moves[a["path"]]
            n += 1
    APPS.write_text(json.dumps(apps, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Cập nhật {n} đường dẫn trong dock_apps.json")


def tidy() -> None:
    d = desktop()
    plan = []
    for f in sorted(d.iterdir()):
        if f.is_dir() or f.name.lower() == "desktop.ini" or f.name.startswith("~$"):
            continue
        ext = f.suffix.lower()
        folder = "Ứng dụng" if ext in APP_EXT else "Tài liệu" if ext in DOC_EXT else None
        if folder:
            plan.append((f, d / folder / f.name))
    done, failed = [], []
    for src, dst in plan:
        dst.parent.mkdir(exist_ok=True)
        if dst.exists():
            failed.append((src.name, "đã có tệp trùng tên"))
            continue
        try:
            shutil.move(str(src), str(dst))
            done.append({"from": str(src), "to": str(dst)})
        except OSError as e:  # vd tep dang mo trong Word/Excel
            failed.append((src.name, str(e)))
    old = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
    LOG.write_text(json.dumps(old + done, ensure_ascii=False, indent=2), encoding="utf-8")
    update_dock({m["from"]: m["to"] for m in done})
    for m in done:
        print(f"  {Path(m['from']).name}  ->  {Path(m['to']).parent.name}\\")
    print(f"Đã chuyển {len(done)} tệp.")
    for name, why in failed:
        print(f"  BỎ QUA {name}: {why}")


def undo() -> None:
    moves = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else []
    back = {}
    for m in reversed(moves):
        if Path(m["to"]).exists() and not Path(m["from"]).exists():
            shutil.move(m["to"], m["from"])
            back[m["to"]] = m["from"]
    update_dock(back)
    LOG.unlink(missing_ok=True)
    print(f"Đã trả {len(back)} tệp về chỗ cũ.")


if __name__ == "__main__":
    undo() if "--undo" in sys.argv else tidy()
