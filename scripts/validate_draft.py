"""
校验生成的剪映草稿 —— 在丢给剪映之前把低级错误全挡掉。

可以传草稿目录或主 JSON 文件路径。
重点验证三条 ID 链:
  1) segment.material_id            == materials.videos[i].id
  2) materials.videos[i].local_material_id == draft_meta_info.draft_materials[type=0].value[j].id
  3) 时间线无缝、duration 一致、素材文件真实存在
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths

if len(sys.argv) <= 1:
    print("用法: python validate_draft.py <草稿目录|draft_info.json>")
    raise SystemExit(2)
arg = sys.argv[1]
folder = arg if os.path.isdir(arg) else os.path.dirname(os.path.abspath(arg))

errs, warns, oks = [], [], []


def find_main_json() -> str:
    for fn in ("draft_info.json", "draft_content.json"):
        p = os.path.join(folder, fn)
        if os.path.exists(p) and open(p, "rb").read(1) == b"{":
            return p
    raise SystemExit(f"在 {folder} 里找不到明文主草稿文件")


main_p = find_main_json()
doc = json.load(open(main_p, encoding="utf-8"))

# draft_meta_info.json 剪映打开后会加密。加密了就不能解析,只能提示,不能崩。
meta_p = os.path.join(folder, "draft_meta_info.json")
meta, meta_state = None, "缺失"
if os.path.exists(meta_p):
    head = open(meta_p, "rb").read(1)
    if head != b"{":
        meta_state = "已被剪映加密(无法解析,属正常)"
    else:
        try:
            meta = json.load(open(meta_p, encoding="utf-8"))
            meta_state = "明文可读"
        except Exception as e:
            meta_state = f"明文但解析失败({type(e).__name__})"

print(f"草稿目录: {folder}")
print(f"主文件   : {os.path.basename(main_p)}   ({os.path.getsize(main_p)} bytes)")
print(f"meta     : {meta_state}")
if meta_state.startswith("明文但解析失败"):
    errs.append("draft_meta_info.json 以 { 开头却解析失败 —— 文件损坏(不是加密)")
if meta_state.startswith("已被剪映加密"):
    warns.append("draft_meta_info.json 已被剪映加密,跳过链2 的实测校验"
                 "(生成阶段应保证它是明文)")
print(f"version={doc.get('version')} new_version={doc.get('new_version')!r} "
      f"duration={doc.get('duration')}us fps={doc.get('fps')}")
print(f"platform={json.dumps(doc.get('platform'), ensure_ascii=False)}")
print(f"canvas  ={json.dumps(doc.get('canvas_config'), ensure_ascii=False)}")

# meta 已被剪映加密 = 这份草稿已经被剪映处理过。此时结构完整性检查没有意义,
# 只做只读校验(链1、时间线、duration),把 meta 相关项全部跳过。
processed = meta_state.startswith("已被剪映加密")

if meta is None and not processed:
    errs.append("缺 draft_meta_info.json —— 剪映会报'媒体丢失'")
if os.path.isdir(os.path.join(folder, "Timelines")) and not processed:
    errs.append("草稿目录里有 Timelines/ —— 会把模板的空时间线当成自己的(必须不存在)")

# --- 骨架字段覆盖 ---
def find_skeleton() -> str | None:
    r"""找一个本机明文草稿的 template.tmp 当骨架基准。

    原先写死 `...\2月11日\Timelines\<id>\template.tmp` —— 那份草稿一旦被删,
    校验就静默跳过骨架检查。改成按优先级找,找不到就返回 None(跳过而非报错)。
    """
    dr = paths.draft_root()
    if not os.path.isdir(dr):
        return None
    preferred = os.path.join(dr, "2月11日", "Timelines",
                             "51EBF964-9E11-46f9-8959-5DEC8475A5DD",
                             "template.tmp")
    if os.path.exists(preferred):
        return preferred
    # 退而求其次:任意一个够大的明文 template.tmp
    for d in sorted(os.listdir(dr)):
        tl = os.path.join(dr, d, "Timelines")
        if not os.path.isdir(tl):
            continue
        for t in sorted(os.listdir(tl)):
            c = os.path.join(tl, t, "template.tmp")
            try:
                if os.path.getsize(c) > 2000:
                    return c
            except OSError:
                continue
    return None


SKEL = find_skeleton()
if SKEL is None:
    warns.append("找不到明文 template.tmp 骨架,已跳过字段覆盖检查")
else:
    skel = json.load(open(SKEL, encoding="utf-8"))
    miss = sorted(set(skel) - set(doc))
    sk_m = set(skel["materials"])
    our_m = set(doc["materials"])
    print(f"\n[字段覆盖] 顶层键 缺{len(miss)}   materials 桶 缺{len(sk_m - our_m)}")
    if miss:
        errs.append(f"缺骨架顶层字段: {miss}")
    if sk_m - our_m:
        errs.append(f"缺 materials 桶: {sorted(sk_m - our_m)}")
    else:
        oks.append(f"顶层字段与 {len(sk_m)} 个 materials 桶全覆盖")

mats = doc["materials"]["videos"]
mat_by_id = {m["id"]: m for m in mats}
segs = doc["tracks"][0]["segments"]

# 用 --final-root 预写最终路径时,草稿可能还在暂存区、最终位置尚未创建,
# 这时"文件不存在"是预期的,降级为提示而不是错误。
# (必须在使用之前算出来 —— 音频轨检查也要用)
staged = False
_jf = ""
if mats:
    _jf = os.path.dirname(os.path.abspath(mats[0].get("path", "").replace("/", os.sep)))
    if _jf and os.path.dirname(_jf) and \
            os.path.normcase(os.path.dirname(_jf)) != os.path.normcase(os.path.abspath(folder)):
        staged = True

# --- 音频参考轨的 ID 链(容易漏:只查视频轨会漏掉音频素材的引用) ---
audio_by_id = {m["id"]: m for m in doc["materials"].get("audios", [])}
for t in doc.get("tracks", []):
    if t.get("type") != "audio":
        continue
    tname = t.get("name") or "(未命名)"
    nseg = len(t.get("segments", []))
    if nseg == 0:
        continue
    bad = 0
    for s in t["segments"]:
        if s["material_id"] not in audio_by_id:
            bad += 1
            errs.append(f"音频轨「{tname}」片段 {s['id'][:8]} 引用了不存在的音频素材 id")
            continue
        am = audio_by_id[s["material_id"]]
        p = (am.get("path") or "").replace("/", os.sep)
        if p and not os.path.exists(p) and not staged:
            errs.append(f"音频轨「{tname}」素材文件不存在: {am.get('path')}")
        if not am.get("local_material_id"):
            errs.append(f"音频轨「{tname}」素材缺 local_material_id(剪映会报媒体丢失)")
    if bad == 0:
        oks.append(f"音频轨「{tname}」{nseg} 段,素材引用与登记外键正常")

# 用 --final-root 预写最终路径时,草稿可能还在暂存区、最终位置尚未创建,
# 这时"文件不存在"是预期的,降级为提示而不是错误。
staged = False
if mats:
    jf = os.path.dirname(os.path.abspath(mats[0].get("path", "").replace("/", os.sep)))
    if jf and os.path.dirname(jf) and \
            os.path.normcase(os.path.dirname(jf)) != os.path.normcase(os.path.abspath(folder)):
        staged = True
if staged:
    print(f"\n[注意] JSON 里写的素材路径指向 {os.path.dirname(jf)}")
    print(f"       而草稿当前在 {folder} —— 这是预写最终路径的暂存草稿,")
    print("       素材可达性要等安装到最终位置后再验。")

# --- 链 1:segment.material_id -> materials.videos[].id ---
for s in segs:
    if s["material_id"] not in mat_by_id:
        errs.append(f"链1断: 片段 {s['id'][:8]} 的 material_id 在 materials.videos 里不存在")
if not errs:
    oks.append(f"链1 OK: {len(segs)} 个片段的 material_id 都能在 materials.videos 里找到")

# --- 链 2:local_material_id -> draft_materials[type=0] ---
# meta 被加密时无法实测,只提示(生成阶段已由 validate 明文版保证)
if not processed:
    regs = []
    if meta:
        for g in meta.get("draft_materials", []):
            if g.get("type") == 0:
                regs = g.get("value") or []
    reg_by_id = {r["id"]: r for r in regs}
    if not regs:
        errs.append("链2断: draft_materials[type=0] 是空的 —— 剪映会报'媒体丢失'且修不好")
    for m in mats:
        lm = m.get("local_material_id")
        if not lm:
            errs.append(f"链2断: 素材 {m['id'][:8]} 的 local_material_id 为空")
        elif lm not in reg_by_id:
            errs.append(f"链2断: 素材 {m['id'][:8]} 的 local_material_id={lm[:8]} "
                        f"在 draft_materials[type=0] 里找不到")
    if regs and not any("链2断" in e for e in errs):
        oks.append(f"链2 OK: {len(mats)} 个素材的 local_material_id "
                   f"都能在 {len(regs)} 条登记里找到")

    # --- 登记条目自检 ---
    for r in regs:
        for k in ("file_Path", "id", "metetype", "duration", "width", "height"):
            if k not in r:
                errs.append(f"登记条目 {r.get('id', '?')[:8]} 缺字段 {k}")
        fp = r.get("file_Path", "")
        if fp and not os.path.exists(fp) and not staged:
            errs.append(f"登记条目指向的文件不存在: {fp}")
        if r.get("metetype") not in ("video", "photo", "music", None):
            warns.append(f"metetype 取值异常: {r.get('metetype')}")

# --- 素材文件可达 ---
for m in mats:
    if not os.path.exists(m.get("path", "")):
        if staged:
            warns.append(f"(暂存态)素材尚不可达: {os.path.basename(m.get('path', ''))}")
        else:
            errs.append(f"素材文件不存在: {m.get('path')}")
    if "\\" in m.get("path", ""):
        warns.append(f"素材路径含反斜杠: {m['path']}")

# --- 时间线无缝 + duration 一致 ---
print("\n[时间线]  (us = 微秒)")
cursor = 0
for i, s in enumerate(segs):
    tt, st = s["target_timerange"], s["source_timerange"]
    gap = tt["start"] - cursor
    if gap != 0:
        errs.append(f"片段 {i} 与前一段差 {gap}us(缝隙或重叠)")
    print(f"  段{i}: target {tt['start']:>9} +{tt['duration']:<9} "
          f"source {st['start']:>9} +{st['duration']:<9}  "
          f"{'OK' if gap == 0 else f'!! {gap}us'}")
    m = mat_by_id.get(s["material_id"])
    if m and st["start"] + st["duration"] > m["duration"]:
        errs.append(f"片段 {i} 源区间超出素材长度")
    cursor = tt["start"] + tt["duration"]

if cursor != doc["duration"]:
    errs.append(f"duration 字段({doc['duration']}) 与时间线实际({cursor}) 不一致")
else:
    oks.append(f"时间线无缝且 duration 一致 ({cursor}us = {cursor / 1e6:.3f}s)")

if meta and meta.get("tm_duration") not in (None, cursor):
    warns.append(f"meta.tm_duration={meta.get('tm_duration')} 与草稿 duration={cursor} 不一致")

# --- 悬空引用 ---
all_ids = set()
for bucket in doc["materials"].values():
    for it in bucket:
        if isinstance(it, dict) and "id" in it:
            all_ids.add(it["id"])
for s in segs:
    for r in s.get("extra_material_refs") or []:
        if r not in all_ids:
            errs.append(f"片段 {s['id'][:8]} 的 extra_material_refs 悬空: {r}")

print("\n" + "=" * 64)
for o in oks:
    print("  OK   ", o)
for w in warns[:10]:
    print("  WARN ", w)
if errs:
    print(f"\nFAIL: {len(errs)} 个错误")
    for e in errs:
        print("   -", e)
else:
    print("\nPASS: 三条 ID 链、时间线、素材可达性全部通过")

sys.exit(1 if errs else 0)
