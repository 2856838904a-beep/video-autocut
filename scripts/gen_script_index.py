# -*- coding: utf-8 -*-
"""
gen_script_index.py —— 自动生成 references/scripts.md(脚本全索引)

**为什么需要它**

SKILL.md 是模型加载技能后唯一必读的文件,但它只有十几 KB,而技能里有
39 个脚本。别的模型看得到"固定流程 ⑪ 步",却看不到每个脚本的完整 CLI ——
于是要么猜参数,要么退化成"只会用 SKILL.md 里出现过的那几个"。

这份索引把**每个脚本的一句话说明 + 主要参数**从源码里静态抽出来,
保证与代码同步(改完脚本重跑一次即可)。

**为什么用 ast 静态解析而不是跑 --help**

有些脚本没接 argparse,直接跑会真的开始干活(比如 apply_verified_tracks.py)。
静态解析不执行任何代码,安全。

用法:
    python gen_script_index.py            # 生成 references/scripts.md
    python gen_script_index.py --check    # 只报告哪些脚本没被文档提到
"""
from __future__ import annotations

import ast
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(SCRIPTS)
REFERENCES = os.path.join(SKILL, "references")
OUT = os.path.join(REFERENCES, "scripts.md")

# 流水线阶段分组(决定索引里的排序与分节)
STAGES = [
    ("环境 / 自检", ["paths.py", "selftest.py", "gen_script_index.py",
                   "patch_paths.py"]),
    ("② 抽音轨", ["extract_audio.py"]),
    ("③ 音轨结构分析 / 质量判定",
     ["analyze_tracks.py", "classify_voice.py", "pick_track_by_asr.py",
      "verify_tracks_all.py"]),
    ("④ GPU 转写", ["transcribe_long.py"]),
    ("⑤ 选段前置:成片分档 + 文稿时间轴规整",
     ["tiers.py", "transcript.py"]),
    ("⑥ 字幕 OCR(剧情对白的可靠信号)", ["subtitle_ocr.py"]),
    ("⑥ 叙事/镜头边界(TextTiling 话题分段 + 镜头切点吸附)",
     ["topics.py", "shots.py"]),
    ("⑥ 画面分析 / 选段 / 组装",
     ["analyze.py", "detect_cutscenes.py", "pick.py", "pick_visual.py",
      "beats.py", "continuous.py", "rdr_compose.py"]),
    ("⑦ 压紧节奏", ["tighten.py"]),
    ("⑧ 连续剧分集(长素材必用)", ["episodes.py"]),
    ("⑨ 导出片段 / 混音",
     ["cut_clips.py", "mix_audio.py"]),
    ("⑩ 生成草稿",
     ["make_draft.py", "merge_timelines.py"]),
    ("⑪ 校验 / 修复",
     ["validate_draft.py", "verify_master.py", "fix_audio_track.py"]),
    ("⑫ 装入草稿库", ["install_when_closed.py", "install_testdraft.py",
                      "remove_draft.py"]),
    ("⑬ 清理", ["cleanup_work.py"]),
    ("辅助工具(查看中间结果)",
     ["show_transcript.py", "per_track.py"]),
    ("品类词典(加品类改这里,不要新建 Skill)", ["genres.py"]),
    ("批次专用(不是通用工具,配置段写死了本批次路径;只用它们当模板)",
     ["batch_all.py", "batch_visual.py", "batch_rdr.py", "batch_rdr_export.py",
      "rdr_stage2.py", "build_masters.py", "rebuild.py",
      "rebuild_separate.py"]),
]


def dedupe_stages() -> list[str]:
    """同一脚本出现在多个阶段时只保留首次出现,并返回重复清单。

    手写分组很容易把一个脚本塞进两处(本文件第一版就有 6 个重复),
    索引里就会重复列出、显得脚本比实际多。这里强制去重。
    """
    seen: set[str] = set()
    dups: list[str] = []
    for _, names in STAGES:
        keep = []
        for n in names:
            if n in seen:
                dups.append(n)
                continue
            seen.add(n)
            keep.append(n)
        names[:] = keep
    return dups



def parse_script(path: str) -> dict:
    """静态抽取:一句话说明 + argparse 参数。"""
    src = open(path, encoding="utf-8", errors="replace").read()
    info = {"desc": "", "opts": [], "has_args": False, "args_required": []}
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        info["desc"] = f"!! 语法错误: {e}"
        return info

    doc = ast.get_docstring(tree) or ""
    for line in doc.splitlines():
        line = line.strip()
        if not line or line.startswith(("⚠", "**用量", "用法", "---")):
            continue
        info["desc"] = line
        break

    # 有些脚本不用 argparse,直接读 sys.argv —— 也要把用法抠出来
    info["positional"] = "sys.argv[1]" in src or "sys.argv[" in src
    # 用法可能写在 docstring 里,也可能写在 print("用法: ...") 里,两处都找
    info["usage"] = ""
    for hay in (doc, src):
        m = re.search(r"用法[:：]\s*\\?n?\s*(python\s+\S+\.py[^\n\"']*)", hay)
        if m:
            info["usage"] = m.group(1).strip()
            break
    if not info["usage"]:
        m2 = re.search(r"python\s+(\w+\.py)\s+([<\[][^\n\"']{0,80})", src)
        info["usage"] = m2.group(0).strip() if m2 else ""

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "attr", None) or getattr(fn, "id", None)
        if name == "ArgumentParser":
            info["has_args"] = True
            for kw in node.keywords:
                if kw.arg == "description" and isinstance(kw.value, ast.Constant):
                    d = str(kw.value.value).strip()
                    if d:
                        info["desc"] = d
        elif name == "add_argument":
            if not node.args:
                continue
            a0 = node.args[0]
            if not isinstance(a0, ast.Constant):
                continue
            flag = str(a0.value)
            if not flag.startswith("-"):
                continue
            helptext, default, action = "", None, None
            for kw in node.keywords:
                if kw.arg == "help" and isinstance(kw.value, ast.Constant):
                    helptext = str(kw.value.value)
                elif kw.arg == "default" and isinstance(kw.value, ast.Constant):
                    default = kw.value.value
                elif kw.arg == "action" and isinstance(kw.value, ast.Constant):
                    action = str(kw.value.value)
            kinds = [a for a in node.args[1:] if isinstance(a, ast.Name)]
            req = "required=True" in ast.unparse(node)
            info["opts"].append({
                "flag": flag, "help": helptext, "default": default,
                "action": action, "required": req,
                "type": kinds[0].id if kinds else "",
            })
    return info


def collect() -> tuple[dict, list[str], list[str]]:
    files = sorted(f for f in os.listdir(SCRIPTS) if f.endswith(".py"))
    data = {}
    for f in files:
        data[f] = parse_script(os.path.join(SCRIPTS, f))
    known = {n for _, names in STAGES for n in names}
    unknown = [f for f in files if f not in known]          # 有文件没归类
    missing = sorted(known - set(files))                    # 归了类但没文件
    return data, unknown, missing


def referenced_in_docs(name: str) -> bool:
    """这个脚本有没有在任何文档里被提到。"""
    docs = ["SKILL.md"] + [
        os.path.join("references", f) for f in os.listdir(REFERENCES)
        if f.endswith(".md")]
    for d in docs:
        p = os.path.join(SKILL, d)
        if not os.path.exists(p):
            continue
        try:
            if name in open(p, encoding="utf-8", errors="replace").read():
                return True
        except OSError:
            pass
    return False


def fmt_opts(opts: list[dict], limit: int = 4) -> str:
    if not opts:
        return "—"
    parts = []
    for o in opts[:limit]:
        f = o["flag"]
        parts.append(f"`{f}`")
    s = " ".join(parts)
    if len(opts) > limit:
        s += f" …(+{len(opts) - limit})"
    return s


def build(data: dict, unknown: list[str]) -> str:
    L = []
    L.append("# 脚本全索引")
    L.append("")
    L.append("> **本文件由 `gen_script_index.py` 从源码静态生成,不要手改。**")
    L.append("> 改了脚本的参数或说明,重跑 `python gen_script_index.py` 即可。")
    L.append("")
    L.append("模型加载技能后,`SKILL.md` 给出流程骨架,本文件给出**每个脚本"
             "的确切入口**。想看某个脚本的完整参数,直接 "
             "`python <脚本> --help`。")
    L.append("")
    L.append(f"共 **{len(data)}** 个脚本。")
    L.append("")

    # 目录
    L.append("## 速查表")
    L.append("")
    L.append("| 脚本 | 一句话 | 主要参数 |")
    L.append("|---|---|---|")
    for _, names in STAGES:
        for n in names:
            if n not in data:
                continue
            d = data[n]
            desc = (d["desc"] or "").replace("|", "/")[:70]
            L.append(f"| [`{n}`](#{n.replace('.', '').replace('_', '-')}) "
                     f"| {desc} | {fmt_opts(d['opts'], 3)} |")
    for n in unknown:
        d = data[n]
        desc = (d["desc"] or "").replace("|", "/")[:70]
        L.append(f"| `{n}` | {desc} | {fmt_opts(d['opts'], 3)} |")
    L.append("")

    # 分节详解
    for title, names in STAGES:
        present = [n for n in names if n in data]
        if not present:
            continue
        L.append(f"## {title}")
        L.append("")
        for n in present:
            d = data[n]
            L.append(f"### {n}")
            L.append("")
            L.append(d["desc"] or "(无说明)")
            L.append("")
            if d["opts"]:
                L.append("| 参数 | 说明 | 默认 |")
                L.append("|---|---|---|")
                for o in d["opts"]:
                    h = (o["help"] or "").replace("|", "/").replace("\n", " ")
                    dv = "" if o["default"] is None else f"`{o['default']}`"
                    if o["action"] in ("store_true", "store_false"):
                        dv = dv or "false"
                    L.append(f"| `{o['flag']}` | {h[:90]} | {dv} |")
                L.append("")
            elif d.get("positional"):
                L.append("**无 argparse,参数走位置参数。** 用法:")
                L.append("")
                L.append(f"```\n{d.get('usage') or 'python ' + n + ' <参数>'}\n```")
                L.append("")
            elif n in ("paths.py",):
                L.append("直接运行即自检: `python paths.py`")
                L.append("")
            else:
                L.append("(无命令行参数 —— 作为模块被其它脚本 import,")
                L.append("或直接运行执行固定动作)")
                L.append("")

    if unknown:
        L.append("## 未归入任何阶段的脚本")
        L.append("")
        for n in unknown:
            L.append(f"- **{n}** — {data[n]['desc'] or '(无说明)'}")
        L.append("")

    return "\n".join(L) + "\n"


def main() -> int:
    dups = dedupe_stages()
    data, unknown, missing = collect()
    if dups:
        print(f"注意: 分组里有 {len(dups)} 个重复项,已去重: {dups}")
    if missing:
        print(f"注意: 分组里列了 {len(missing)} 个技能里不存在的脚本"
              f"(它们在别的工作目录,不在 skill 内): {missing}")

    if "--check" in sys.argv:
        print(f"共 {len(data)} 个脚本\n")
        gap = [f for f in data if not referenced_in_docs(f)]
        print(f"未被任何文档提及的脚本: {len(gap)} 个")
        for f in gap:
            print(f"   - {f}  ({data[f]['desc'][:50]})")
        no_desc = [f for f in data if not data[f]["desc"]]
        print(f"\n没有 docstring / description 的: {len(no_desc)} 个")
        for f in no_desc:
            print(f"   - {f}")
        noargs = [f for f in data if not data[f]["opts"]
                  and not data[f].get("positional")
                  and not data[f].get("usage")
                  and not f.startswith("_")]
        print(f"\n没有命令行参数(作为模块被 import): {len(noargs)} 个")
        for f in noargs:
            print(f"   - {f}")
        pos = [f for f in data if data[f].get("positional")]
        print(f"\n走位置参数(sys.argv)而非 argparse: {len(pos)} 个")
        for f in sorted(pos):
            u = data[f].get("usage") or "(源码里没写用法)"
            print(f"   - {f:<26}{u[:70]}")
        return 0

    md = build(data, unknown)
    os.makedirs(REFERENCES, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    print(f"已生成 {OUT}")
    print(f"   {len(data)} 个脚本, {len(md.splitlines())} 行, "
          f"{len(md)} 字符")
    if unknown:
        print(f"   注意: {len(unknown)} 个脚本未归入阶段: {unknown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
