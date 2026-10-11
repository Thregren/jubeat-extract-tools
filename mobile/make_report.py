#!/usr/bin/env python3
"""读 library.json / report.json，生成人读的 report.md。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def levels_text(stats: dict) -> str:
    parts = []
    for code, name in (("bas", "BSC"), ("adv", "ADV"), ("ext", "EXT")):
        if code in stats:
            parts.append("%s Lv%s (%d)" % (name, stats[code]["level"], stats[code]["volume"]))
    return " / ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".", help="提取输出目录")
    args = ap.parse_args()
    root = Path(args.dir).expanduser().resolve()
    manifests = json.loads((root / "library.json").read_text(encoding="utf-8"))
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))

    lines: list[str] = []
    add = lines.append
    add("# jubeat 移动版提取报告")
    add("")
    add("来源：`jubeat plus` 与 `jukebeat` 的 iOS 备份。")
    add("")
    add("## 总量")
    add("")
    add("| 包 | 歌曲数 | 唯一曲名 |")
    add("|---|---:|---:|")
    for pack, items in manifests.items():
        add("| %s | %d | %d |" % (pack, len(items), len({s["title"] for s in items})))
    add("")
    add("合计 **%d 首**（含重复曲名）。" % sum(len(v) for v in manifests.values()))
    add("")
    add("## 与现有曲库对比（按曲名）")
    add("")
    add("- 曲库曲名数：%d" % report.get("library_titles", 0))
    add("- 移动包唯一曲名：%d" % report.get("mobile_titles", 0))
    add("- 曲库没有、移动包有：**%d 首**" % report.get("new_count", 0))
    add("- 移动包没有、曲库有：%d 首" % report.get("only_in_library_count", 0))
    add("")

    new = report.get("new_in_mobile", [])
    if new:
        add("## 新增曲目（曲库没有）")
        add("")
        for pack in manifests:
            items = [s for s in new if s["pack"] == pack]
            if not items:
                continue
            add("### %s（%d 首）" % (pack, len(items)))
            add("")
            add("| ID | 曲名 | 艺人 | 难度 |")
            add("|---|---|---|---|")
            for s in sorted(items, key=lambda x: x["title"]):
                add("| %s | %s | %s | %s |" % (s["id"], s["title"], s["artist"],
                                               levels_text(s["stats"])))
            add("")

    matched = report.get("matched", [])
    dup = [m for m in matched if m["diff"]]
    if dup:
        add("## 曲库已有（同名）：谱面对照")
        add("")
        add("| 曲名 | 包 | 难度 | 移动包等级/物量 | 曲库等级 |")
        add("|---|---|---|---|---|")
        for m in sorted(dup, key=lambda x: x["title"])[:400]:
            for version, d in sorted(m["diff"].items()):
                add("| %s | %s | %s | Lv%s / %d | Lv%s |" %
                    (m["title"], m["pack"], version, d["mobile_level"],
                     d["mobile_notes"], d["lib_level"]))
        add("")
        if len(dup) > 400:
            add("（仅列出前 400 行，完整数据见 report.json）")
            add("")

    only = report.get("only_in_library", [])
    if only:
        add("## 仅曲库有（移动包没有）：%d 首" % len(only))
        add("")
        add("、".join(only[:100]) + (" …" if len(only) > 100 else ""))
        add("")

    out = root / "report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("已写出 %s（%d 行）" % (out, len(lines)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
