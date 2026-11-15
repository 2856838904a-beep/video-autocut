# -*- coding: utf-8 -*-
"""把音轨修成剪映真正认的结构。

根因（比对剪映自己产出的明文草稿得出）：
  1. materials.audios[].type 写成了 "audio" —— 非法值，剪映只认
     music / text_to_audio / record / extract_music 等。解析失败的素材
     会导致整条音轨被丢弃。
  2. 段缺 extra_material_refs —— 剪映每个片段都必须引用一个 speeds 素材
     （参考草稿 speeds 数量 == 段总数），音频段还要 beats。
  3. 音频段只有 9 个字段，真实草稿是 44 个。

用法:
    python fix_audio_track.py <草稿目录> [--dry] [--also-content]
"""
import json
import os
import shutil
import sys
import time
import uuid

sys.stdout.reconfigure(encoding='utf-8')

NEWLINE = '\n'


def uid():
    return str(uuid.uuid4()).upper()


# ---------- 素材模板（取自剪映真实草稿 2月11日 (3)） ----------

def mk_speed():
    return {"id": uid(), "type": "speed", "mode": 0, "speed": 1.0,
            "curve_speed": None}


def mk_scm():
    return {"id": uid(), "type": "sound_channel_mapping",
            "audio_channel_mapping": 0, "is_config_open": False}


def mk_vs():
    return {"id": uid(), "type": "vocal_separation", "choice": 0,
            "production_path": "", "time_range": None, "removed_sounds": []}


def mk_beats():
    return {"id": uid(), "type": "beats", "enable_ai_beats": False,
            "gear": 404, "gear_count": 0, "mode": 404,
            "user_delete_ai_beats": None,
            "ai_beats": {"melody_url": "", "melody_path": "", "beats_url": "",
                         "beats_path": "", "melody_percents": [0.6],
                         "beat_speed_infos": []},
            "user_beats": []}


AUDIO_MATERIAL_TMPL = {
    "type": "music", "name": "", "path": "", "category_name": "",
    "music_id": "", "tone_type": "", "source_platform": 0, "video_id": "",
    "effect_id": "", "resource_id": "", "third_resource_id": "",
    "category_id": "", "intensifies_path": "", "formula_id": "",
    "check_flag": 1, "team_id": "", "local_material_id": "", "tone_speaker": "",
    "tone_effect_id": "", "tone_effect_name": "", "tone_platform": "",
    "tone_category_id": "", "tone_category_name": "",
    "tone_second_category_id": "", "tone_second_category_name": "",
    "tone_emotion_name_key": "", "tone_emotion_style": "",
    "tone_emotion_role": "", "tone_emotion_selection": "",
    "tone_emotion_scale": 0, "request_id": "", "query": "", "search_id": "",
    "sound_separate_type": "", "is_text_edit_overdub": False, "is_ugc": False,
    "is_ai_clone_tone": False, "is_ai_clone_tone_post": False,
    "source_from": "", "copyright_limit_type": "none",
    "aigc_history_id": "", "aigc_item_id": "", "music_source": "",
    "pgc_id": "", "pgc_name": "", "ai_music_type": 0, "lyric_type": 0,
    "tts_task_id": "", "tts_generate_scene": "", "ai_music_generate_scene": 0,
    "duration": 0, "app_id": 0,
    "similiar_music_info": {"original_song_id": "", "original_song_name": ""},
    "wave_points": [],
}

# 音频段模板：剪映真实草稿里的 44 字段结构
AUDIO_SEG_TMPL = {
    "desc": "", "state": 0, "speed": 1, "is_loop": False,
    "is_tone_modify": False, "reverse": False, "intensifies_audio": False,
    "cartoon": False, "volume": 1, "last_nonzero_volume": 1,
    "enable_lut": True, "enable_adjust": True, "enable_hsl": True,
    "visible": True, "group_id": "", "enable_color_curves": True,
    "track_render_index": 0, "enable_color_wheels": True,
    "track_attribute": 0, "is_placeholder": False, "template_id": "",
    "enable_smart_color_adjust": False, "template_scene": "default",
    "enable_color_match_adjust": False, "enable_color_correct_adjust": False,
    "enable_adjust_mask": True, "raw_segment_id": "", "enable_video_mask": True,
    "offset": 0, "render_timerange": {"start": 0, "duration": 0},
    "clip": None, "uniform_scale": None, "hdr_settings": None,
    "caption_info": None,
    "responsive_layout": {"enable": False, "target_follow": "",
                          "size_layout": 0, "horizontal_pos_layout": 0,
                          "vertical_pos_layout": 0},
    "lyric_keyframes": None, "keyframe_refs": [], "common_keyframes": [],
}


def build_audio_material(old):
    """把旧的非法 "audio" 素材转成剪映认的 "music" 素材，保留 id/path/时长。"""
    m = dict(AUDIO_MATERIAL_TMPL)
    m["id"] = old["id"]
    m["path"] = old.get("path", "")
    m["name"] = old.get("name") or os.path.basename(m["path"])
    m["duration"] = old.get("duration", 0)
    # local_material_id 保留原 UUID（剪映本地文件用它做去重键）
    lmid = old.get("local_material_id")
    m["local_material_id"] = lmid if lmid else ""
    m["similiar_music_info"] = {"original_song_id": "", "original_song_name": ""}
    return m


def fix_doc(j):
    """就地修好一个 draft 文档，返回 (改了什么, 统计)。"""
    stats = {"audio_segs": 0, "audio_mats": 0, "new_speed": 0, "new_scm": 0,
             "new_vs": 0, "new_beats": 0}
    mats = j.setdefault("materials", {})

    for k in ("speeds", "sound_channel_mappings", "vocal_separations", "beats"):
        mats.setdefault(k, [])

    audio_tracks = [t for t in j.get("tracks", []) if t.get("type") == "audio"]
    if not audio_tracks:
        return stats

    # 段引用 material_id → 音频素材
    amap = {a["id"]: a for a in mats.get("audios", [])}

    new_speeds, new_scm, new_vs, new_beats = [], [], [], []

    for t in audio_tracks:
        # 轨道字段对齐（真实草稿有 flag）
        t.setdefault("flag", 0)
        t.setdefault("attribute", 0)
        if t.get("name"):
            t["is_default_name"] = False

        for s in t["segments"]:
            stats["audio_segs"] += 1
            seg = dict(AUDIO_SEG_TMPL)

            # 保留原有关键字段
            seg["id"] = s.get("id") or uid()
            seg["material_id"] = s["material_id"]
            seg["target_timerange"] = dict(s["target_timerange"])
            seg["source_timerange"] = dict(s.get("source_timerange")
                                           or s["target_timerange"])
            seg["volume"] = s.get("volume", 1.0)
            seg["speed"] = 1
            # 音轨排在视频轨之后
            seg["render_index"] = 2
            seg["track_render_index"] = 0
            seg["track_attribute"] = 0

            # 补齐四类必备素材引用
            sp, sc, vs, bt = mk_speed(), mk_scm(), mk_vs(), mk_beats()
            new_speeds.append(sp)
            new_scm.append(sc)
            new_vs.append(vs)
            new_beats.append(bt)
            seg["extra_material_refs"] = [sp["id"], bt["id"], sc["id"], vs["id"]]

            # 原段内容整体覆盖（用新结构替换掉旧的 9 字段版本）
            s.clear()
            s.update(seg)

            # 素材本身也要修
            old_mat = amap.get(s["material_id"])
            if old_mat is not None and old_mat.get("type") != "music":
                amap[s["material_id"]] = build_audio_material(old_mat)
                stats["audio_mats"] += 1

    mats["speeds"].extend(new_speeds)
    mats["sound_channel_mappings"].extend(new_scm)
    mats["vocal_separations"].extend(new_vs)
    mats["beats"].extend(new_beats)
    stats["new_speed"] = len(new_speeds)
    stats["new_scm"] = len(new_scm)
    stats["new_vs"] = len(new_vs)
    stats["new_beats"] = len(new_beats)

    # 替换 audios 列表
    if amap:
        newlist, seen = [], set()
        for a in mats.get("audios", []):
            fixed = amap.get(a["id"], a)
            if fixed["id"] not in seen:
                newlist.append(fixed)
                seen.add(fixed["id"])
        mats["audios"] = newlist

    return stats


def process(path, dry=False):
    try:
        j = json.load(open(path, encoding='utf-8'))
    except Exception as e:
        return None, f"读不了: {e}"
    before = json.dumps(j, ensure_ascii=False, sort_keys=True)
    st = fix_doc(j)
    after = json.dumps(j, ensure_ascii=False, sort_keys=True)
    changed = before != after
    if changed and not dry:
        write_json(path, j)
    return st, ("已改" if changed else "无变化")


def write_json(path, obj):
    """按剪映风格写（键值间留空格，与真实草稿一致）。"""
    with open(path, 'w', encoding='utf-8', newline=NEWLINE) as f:
        json.dump(obj, f, ensure_ascii=False, separators=(', ', ': '))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    draft = sys.argv[1].rstrip('\\/')
    dry = '--dry' in sys.argv
    also_content = '--also-content' in sys.argv

    targets = []
    root_info = os.path.join(draft, 'draft_info.json')
    if os.path.exists(root_info):
        targets.append(root_info)
    tdir = os.path.join(draft, 'Timelines')
    if os.path.isdir(tdir):
        for name in sorted(os.listdir(tdir)):
            p = os.path.join(tdir, name, 'draft_info.json')
            if os.path.exists(p):
                targets.append(p)

    if not targets:
        print(f"没找到 draft_info.json: {draft}")
        return 1

    print(f"{'[试运行] ' if dry else ''}草稿: {os.path.basename(draft)}")
    print(f"共 {len(targets)} 个文档\n")

    # 备份
    if not dry:
        bk = os.path.join(draft, f'.backup_{time.strftime("%Y%m%d_%H%M%S")}')
        os.makedirs(bk, exist_ok=True)
        n = 0
        for p in targets:
            rel = os.path.relpath(p, draft)
            dst = os.path.join(bk, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(p, dst)
            n += 1
        # 备份剪映加密版 draft_content.json
        for p in ([os.path.join(draft, 'draft_content.json')]
                  + [os.path.join(tdir, d, 'draft_content.json')
                     for d in (os.listdir(tdir) if os.path.isdir(tdir) else [])]):
            if os.path.exists(p):
                rel = os.path.relpath(p, draft)
                dst = os.path.join(bk, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(p, dst)
                n += 1
        print(f"已备份 {n} 个文件 -> {os.path.basename(bk)}\n")

    tot = {"audio_segs": 0, "audio_mats": 0, "new_speed": 0}
    for p in targets:
        st, msg = process(p, dry)
        rel = os.path.relpath(p, draft)
        if st is None:
            print(f"  {rel:<58} {msg}")
            continue
        for k in tot:
            tot[k] += st.get(k, 0)
        print(f"  {rel:<58} {msg}  音轨段{st['audio_segs']:>3} "
              f"素材{st['audio_mats']:>3} 新增speeds{st['new_speed']:>3}")

    print(f"\n合计: 音轨段 {tot['audio_segs']}  修素材 {tot['audio_mats']}  "
          f"新增 speeds {tot['new_speed']}")

    # 同步写一份明文 draft_content.json（含时间线）
    # 剪映 11.2.0 的实际主文档是 draft_content.json（它会在打开时把
    # draft_info.json 迁移过来）。两边都写成正确结构，读哪个都对。
    if also_content and not dry:
        n = 0
        for p in targets:
            d = json.load(open(p, encoding='utf-8'))
            dst = os.path.join(os.path.dirname(p), 'draft_content.json')
            write_json(dst, d)
            n += 1
        print(f"\n已同步写出 {n} 个明文 draft_content.json")

        # 清掉剪映留下的加密旧档（template-2.tmp 是写入临时文件，
        # 留着可能被当成待恢复内容）。已备份，可随时还原。
        removed = []
        for base in [draft] + [os.path.dirname(p) for p in targets]:
            for fn in ('draft_content.json.bak', 'template-2.tmp'):
                fp = os.path.join(base, fn)
                if os.path.exists(fp):
                    os.remove(fp)
                    removed.append(os.path.relpath(fp, draft))
        if removed:
            print(f"已清理 {len(removed)} 个剪映临时档: {', '.join(removed)}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
