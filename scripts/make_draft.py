"""
make_draft.py —— 程序化生成剪映专业版草稿(纯明文 JSON,不需要解密)

★ 实测验证(剪映专业版 11.2.0.14339,Windows):
    在空文件夹里写入 draft_info.json + draft_meta_info.json,剪映能直接打开;
    4 段素材按预期依次播放。剪映随后自己生成 draft_content.json(密文)、
    Timelines/<id>/ 镜像、.backup/ 与 <草稿名>/_media/ 自包含素材,并把
    自己分配的时间线 id 写回 draft_info.json。除 id 外我写的字段逐字节保留。

原理:剪映的加密只挡"读已有草稿",不挡"写新草稿"。生成时直接写明文 JSON,
剪映打开后会自己升级为密文并补齐其余文件 —— 单向,之后不要再指望读回。

本脚本不硬编码猜版本号,而是从本机已有草稿里取真实值:
  - materials 全部 54 个桶 + config 结构  <- 剪映自建的 template.tmp 骨架
  - version / new_version                 <- 明文草稿 draft_info.json
  - 素材与片段字段集合                     <- 明文草稿里的真实对象

必须写对的三条 ID 链(最容易出错的地方):
  segment.material_id            == materials.videos[i].id
  materials.videos[i].local_material_id == draft_meta_info.json 里 draft_materials[type=0].value[j].id
  漏了第二条 -> 剪映报"媒体丢失",且它的重新链接对话框修不好。

用法:
  python make_draft.py --name "AI测试" --clip a.mp4 --clip "b.mp4|1.5|4.0"
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths

DRAFT_ROOT = paths.draft_root()
FFPROBE = paths.ffprobe()

# 剪映自建的完整骨架(35 顶层键 + 54 materials 桶)
SKELETON_DRAFT = "2月11日"
SKELETON_TL = "51EBF964-9E11-46f9-8959-5DEC8475A5DD"
# 明文且含真实 tracks/materials 的草稿,用来抄字段集合与版本号
FULL_DRAFT = "2月11日 (3)"

JY_APP_VERSION = "11.2.0"      # 与本机剪映一致
MEDIA_SUBDIR = "_media"        # 素材内联目录(草稿自包含,不依赖外部路径)


def new_uid() -> str:
    return str(uuid.uuid4()).upper()


def load_json(path: str) -> dict:
    return json.load(open(path, encoding="utf-8"))


def load_skeleton() -> dict:
    p = os.path.join(DRAFT_ROOT, SKELETON_DRAFT, "Timelines", SKELETON_TL, "template.tmp")
    if not os.path.exists(p):
        raise SystemExit(f"骨架文件不存在: {p}")
    return load_json(p)


def load_reference() -> dict:
    p = os.path.join(DRAFT_ROOT, FULL_DRAFT, "draft_content.json")
    if not os.path.exists(p):
        raise SystemExit(f"参考草稿不存在: {p}")
    j = load_json(p)
    if "tracks" not in j or "materials" not in j:
        raise SystemExit(f"参考草稿结构异常: {p}")
    return j


def probe(path: str) -> dict:
    r = subprocess.run(
        [FFPROBE, "-v", "error",
         "-show_entries", "stream=codec_type,width,height,duration",
         "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        j = paths.parse_probe_json(r.stdout)
    except Exception:
        return {}
    streams = j.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = float(j.get("format", {}).get("duration") or v.get("duration") or 0)
    if dur <= 0:
        return {}
    return {
        "width": int(v.get("width") or 1920),
        "height": int(v.get("height") or 1080),
        "duration_us": int(round(dur * 1_000_000)),
        "has_audio": a is not None,
    }


def inline_media(clip_path: str, folder: str, used: dict,
                 media_src: str | None = None) -> str:
    """
    把素材复制进 <folder>/_media/,使草稿自包含。

    media_src: 素材的真正来源。留空则用 clip_path 自己。
      为什么需要它:调试时草稿先生成到暂存目录、再整体复制进草稿库,
      如果素材是从暂存目录拷的,复制之后路径就全断了。指定原始位置即可避免。
    """
    src = os.path.abspath(media_src or clip_path)
    if src in used:
        return used[src]
    dst_dir = os.path.join(folder, MEDIA_SUBDIR)
    os.makedirs(dst_dir, exist_ok=True)
    base = os.path.basename(src)
    stem, ext = os.path.splitext(base)
    dst = os.path.join(dst_dir, base)
    k = 1
    while os.path.exists(dst):
        dst = os.path.join(dst_dir, f"{stem}_{k}{ext}")
        k += 1
    shutil.copy2(src, dst)
    rel = f"{MEDIA_SUBDIR}/{os.path.basename(dst)}"
    used[src] = rel
    return rel


def probe_audio(path: str) -> dict:
    """音频文件的时长(微秒)。"""
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        d = float(paths.parse_probe_json(r.stdout)["format"]["duration"])
    except Exception:
        return {}
    if d <= 0:
        return {}
    return {"duration_us": int(round(d * 1_000_000))}


# ---------------------------------------------------------------------------
# 音频素材 / 音频片段构造
#
# 字段集与取值全部取自剪映自己产出的明文草稿(本机「2月11日」系列,含真实
# audio 轨),不是猜的。踩过的坑见 references/gotchas.md「音轨被整条丢弃」。
#
# 三条硬规则 —— 违反任意一条,剪映会把**整条音轨丢掉**,时间线上根本不显示,
# 而且不报错:
#   1. materials.audios[].type 只能是 music / text_to_audio / record /
#      extract_music 等已知值。"audio" 不是合法值。
#   2. 每个片段必须通过 extra_material_refs 引用一个 speeds 素材
#      (实测真实草稿:speeds 数量 == 片段总数)。音频段还要 beats。
#   3. 音频段必须是完整结构(44 字段),不能只给 9 字段的最小版。
# ---------------------------------------------------------------------------

_AUX_TEMPLATES = {
    "speeds": {"type": "speed", "mode": 0, "speed": 1.0, "curve_speed": None},
    "sound_channel_mappings": {"type": "sound_channel_mapping",
                               "audio_channel_mapping": 0,
                               "is_config_open": False},
    "vocal_separations": {"type": "vocal_separation", "choice": 0,
                          "production_path": "", "time_range": None,
                          "removed_sounds": []},
    "beats": {"type": "beats", "enable_ai_beats": False, "gear": 404,
              "gear_count": 0, "mode": 404, "user_delete_ai_beats": None,
              "ai_beats": {"melody_url": "", "melody_path": "",
                           "beats_url": "", "beats_path": "",
                           "melody_percents": [0.6], "beat_speed_infos": []},
              "user_beats": []},
}


def build_audio_material() -> dict:
    """本地导入音频的素材骨架。type 必须是 music —— 写 "audio" 会让剪映
    解析失败并丢弃整条音轨。"""
    return {
        "id": "", "type": "music", "name": "", "path": "",
        "category_name": "", "music_id": "", "tone_type": "",
        "source_platform": 0, "video_id": "", "effect_id": "",
        "resource_id": "", "third_resource_id": "", "category_id": "",
        "intensifies_path": "", "formula_id": "", "check_flag": 1,
        "team_id": "", "local_material_id": "", "tone_speaker": "",
        "tone_effect_id": "", "tone_effect_name": "", "tone_platform": "",
        "tone_category_id": "", "tone_category_name": "",
        "tone_second_category_id": "", "tone_second_category_name": "",
        "tone_emotion_name_key": "", "tone_emotion_style": "",
        "tone_emotion_role": "", "tone_emotion_selection": "",
        "tone_emotion_scale": 0, "request_id": "", "query": "", "search_id": "",
        "sound_separate_type": "", "is_text_edit_overdub": False,
        "is_ugc": False, "is_ai_clone_tone": False,
        "is_ai_clone_tone_post": False, "source_from": "",
        "copyright_limit_type": "none", "aigc_history_id": "",
        "aigc_item_id": "", "music_source": "", "pgc_id": "", "pgc_name": "",
        "ai_music_type": 0, "lyric_type": 0, "tts_task_id": "",
        "tts_generate_scene": "", "ai_music_generate_scene": 0,
        "duration": 0, "app_id": 0,
        "similiar_music_info": {"original_song_id": "", "original_song_name": ""},
        "wave_points": [],
    }


def make_audio_segment(doc: dict, material_id: str, start_us: int, dur_us: int,
                       render_index: int = 2, volume: float = 1.0) -> dict:
    """构造剪映认的完整音频片段,顺手把必需的辅助素材写进 doc。

    缺 extra_material_refs 或字段不足 → 剪映丢弃整条音轨。
    """
    mats = doc["materials"]
    refs = {}
    for key, tmpl in _AUX_TEMPLATES.items():
        mats.setdefault(key, [])
        m = copy.deepcopy(tmpl)
        m["id"] = new_uid()
        mats[key].append(m)
        refs[key] = m["id"]

    return {
        "id": new_uid(), "desc": "", "state": 0, "speed": 1,
        "is_loop": False, "is_tone_modify": False, "reverse": False,
        "intensifies_audio": False, "cartoon": False,
        "volume": volume, "last_nonzero_volume": 1,
        "material_id": material_id, "render_index": render_index,
        "enable_lut": True, "enable_adjust": True, "enable_hsl": True,
        "visible": True, "group_id": "", "enable_color_curves": True,
        "track_render_index": 0, "enable_color_wheels": True,
        "track_attribute": 0, "is_placeholder": False, "template_id": "",
        "enable_smart_color_adjust": False, "template_scene": "default",
        "enable_color_match_adjust": False,
        "enable_color_correct_adjust": False, "enable_adjust_mask": True,
        "raw_segment_id": "", "enable_video_mask": True, "offset": 0,
        "source_timerange": {"start": 0, "duration": dur_us},
        "target_timerange": {"start": start_us, "duration": dur_us},
        "render_timerange": {"start": 0, "duration": 0},
        "clip": None, "uniform_scale": None, "hdr_settings": None,
        "caption_info": None,
        "responsive_layout": {"enable": False, "target_follow": "",
                              "size_layout": 0, "horizontal_pos_layout": 0,
                              "vertical_pos_layout": 0},
        "lyric_keyframes": None,
        "extra_material_refs": [refs["speeds"], refs["beats"],
                                refs["sound_channel_mappings"],
                                refs["vocal_separations"]],
        "keyframe_refs": [], "common_keyframes": [],
    }


def build_meta_info(name: str, folder: str, registrations: list[dict],
                    duration_us: int, draft_id: str) -> dict:
    """draft_meta_info.json —— 项目列表/元数据 sidecar + 素材登记表。

    字段集合取自 pyJianYingDraft 的 DRAFT_META_TEMPLATE(事实上的最小可用集),
    并用本机真实草稿的路径写法。
    """
    win_folder = folder.replace("\\", "/")
    win_root = DRAFT_ROOT.replace("\\", "/")
    now = int(time.time() * 1e6)

    return {
        "cloud_package_completed_time": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_materials": [],
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {
            "draft_enterprise_extra": "",
            "draft_enterprise_id": "",
            "draft_enterprise_name": "",
            "enterprise_material": [],
        },
        "draft_fold_path": win_folder,
        "draft_id": draft_id,
        # 7 个分组,type=0 是用户导入的媒体 —— 剪映靠它判断素材是否可用
        "draft_materials": [
            {"type": 0, "value": registrations},
            {"type": 1, "value": []},
            {"type": 2, "value": []},
            {"type": 3, "value": []},
            {"type": 6, "value": []},
            {"type": 7, "value": []},
            {"type": 8, "value": []},
        ],
        "draft_materials_copied_info": [],
        "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False,
        "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False,
        "draft_is_from_deeplink": "false",
        "draft_is_invisible": False,
        "draft_name": name,
        "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": win_root,
        "draft_segment_extra_info": [],
        "draft_type": "",
        "draft_json_file": f"{win_folder}/draft_info.json",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_modified": 0,
        "tm_draft_create": now,
        "tm_draft_modified": now,
        "tm_draft_removed": 0,
        "tm_duration": duration_us,
    }


def build(name: str, clips: list[dict], fps: float, out_root: str,
          media_src: str | None = None, final_root: str | None = None,
          extra_audio: list[tuple[str, float, str]] | None = None,
          clip_audios: list[str | None] | None = None) -> dict:
    """
    final_root: 草稿最终要放的位置。如果与 out_root 不同(先在暂存区生成、
    之后整体搬进草稿库的情况),所有素材路径与注册路径都按 final_root 写,
    这样复制过去之后路径依然有效 —— 避免事后做字符串替换。

    clip_audios: 与 clips 一一对应的解说音频(或 None)。给了就为每个片段
    建一条**独立的音频轨片段** —— 视频轨只含游戏原声,解说单独一条轨,
    剪映里可分别调音量/静音。这是用户明确要求的形态。
    """
    # ★ 防护:--final-root 必须是草稿库**根**,不能是草稿文件夹本身。
    #   传成「根/名字」会让 path_folder 变成 根/名字/名字,素材路径全部指向
    #   不存在的目录 —— 剪映里表现为"媒体丢失",且很难看出原因。
    #   (2026-11 影之刃批次实际踩过:6 个草稿 136~226 条素材路径全部不可达)
    if final_root:
        fr = os.path.normpath(final_root)
        if os.path.basename(fr) == name:
            raise SystemExit(
                f"--final-root 应该传草稿库根目录,而不是草稿文件夹本身。\n"
                f"  收到: {final_root}\n"
                f"  应传: {os.path.dirname(fr)}\n"
                f"(脚本会自动在根目录下拼 /{name})")
    path_root = final_root or out_root
    skel = load_skeleton()
    ref = load_reference()

    doc = copy.deepcopy(skel)
    doc["id"] = new_uid()
    doc["name"] = name
    doc["create_time"] = int(time.time() * 1e6)
    doc["update_time"] = doc["create_time"]
    doc["fps"] = fps
    doc["draft_type"] = "video"

    # --- 版本标记:抄本机真实草稿,不猜 ---
    doc["version"] = ref.get("version", doc.get("version"))
    doc["new_version"] = ref.get("new_version", doc.get("new_version"))
    doc["color_space"] = ref.get("color_space", doc.get("color_space", -1))
    doc["render_index_track_mode_on"] = ref.get(
        "render_index_track_mode_on", doc.get("render_index_track_mode_on", False))
    plat = {
        "app_id": 0,
        "app_source": "lv",              # lv = 剪映
        "app_version": JY_APP_VERSION,
        "device_id": "",
        "hard_disk_id": "",
        "mac_address": "",
        "os": "windows",
        "os_version": "",
    }
    doc["platform"] = plat
    doc["last_modified_platform"] = copy.deepcopy(plat)

    # --- 画布:跟随第一段素材的宽高比 ---
    if clips:
        c0 = clips[0]
        ratio = c0["width"] / c0["height"] if c0["height"] else 16 / 9
        if ratio > 1.5:
            cw, ch = 1920, 1080
        elif ratio < 0.9:
            cw, ch = 1080, 1920
        else:
            cw, ch = 1080, 1080
        doc["canvas_config"] = {"background": None, "height": ch,
                                "ratio": "original", "width": cw}

    ref_seg = None
    for t in ref.get("tracks", []):
        if t.get("type") == "video" and t.get("segments"):
            ref_seg = t["segments"][0]
            break
    if ref_seg is None:
        raise SystemExit("参考草稿里没有可抄的 video segment")

    folder = os.path.join(out_root, name)
    if os.path.exists(folder):
        if os.environ.get("AUTOCUT_ALLOW_REPLACE") != "1":
            raise SystemExit(f"草稿目录已存在,拒绝覆盖: {folder}\n"
                             f"(设 AUTOCUT_ALLOW_REPLACE=1 可覆盖,或换个 --name)")
        shutil.rmtree(folder)
    os.makedirs(folder, exist_ok=True)

    # 写进 JSON 里的路径按最终位置算;素材实体按实际生成目录放
    actual_folder = folder
    path_folder = os.path.join(path_root, name).replace("\\", "/")

    mats = doc["materials"]
    segments, registrations = [], []
    used_media: dict[str, str] = {}
    t_cursor = 0
    # 独立解说音频轨的片段(与视频片段同起点同时长)
    clip_voice_segments: list[dict] = []
    clip_voice_regs: list[dict] = []

    for i, c in enumerate(clips):
        in_us = c.get("in_us", 0)
        out_us = c.get("out_us", c["duration_us"])
        src_dur = max(out_us - in_us, 1)

        # 素材实体拷进 out_root 下的草稿目录(out_root 若是暂存区,之后整体搬走)
        rel = inline_media(c["path"], actual_folder, used_media, media_src)
        mat_id = new_uid()
        reg_id = new_uid()          # 登记条目 id —— 必须与素材的 local_material_id 相同

        # 同一条素材可能被引用多次(切成几段),登记只做一次
        if not any(r["file_Path"].endswith(os.path.basename(rel)) for r in registrations):
            registrations.append({
                "ai_group_type": "",
                "create_time": -1,
                "duration": c["duration_us"],
                "enter_from": 0,
                "extra_info": os.path.basename(rel),
                "file_Path": f"{path_folder}/{rel}",
                "height": c["height"],
                "id": reg_id,
                "import_time": -1,
                "import_time_ms": -1,
                "item_source": 1,
                "material_color_tag": "",
                "md5": "",
                "metetype": "video",
                "roughcut_time_range": {"duration": -1, "start": -1},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0,
                "width": c["width"],
            })
        else:
            reg_id = next(r["id"] for r in registrations
                          if r["file_Path"].endswith(os.path.basename(rel)))

        # --- 素材条目 ---
        mat = copy.deepcopy(ref["materials"]["videos"][0])
        mat.update({
            "id": mat_id,
            "type": "video",
            "path": f"{path_folder}/{rel}",
            "media_path": "",
            "local_id": "",
            "material_name": os.path.basename(rel),
            "material_url": "",
            "width": c["width"],
            "height": c["height"],
            "duration": c["duration_us"],
            "has_audio": bool(c.get("has_audio", False)),
            "crop_scale": 1.0,
            "source": 0,
            "source_platform": 0,
            "local_material_id": reg_id,      # ★ 外键:指向 draft_materials[type=0]
            "check_flag": 65535,
        })
        mats["videos"].append(mat)

        # --- 片段 ---
        seg = copy.deepcopy(ref_seg)
        seg.update({
            "id": new_uid(),
            "material_id": mat_id,            # ★ 指向 materials.videos[i].id
            "local_material_id": None,
            "target_timerange": {"start": t_cursor, "duration": src_dur},
            "source_timerange": {"start": in_us, "duration": src_dur},
            "render_index": i + 1,
            "visible": True,
            "volume": 1.0,
            "speed": 1.0,
            "reverse": False,
            "extra_material_refs": [],
        })
        for k in ("keyframe_refs", "common_keyframes", "group_id", "raw_segment_id"):
            if k in seg:
                seg[k] = [] if isinstance(seg[k], list) else None
        segments.append(seg)

        # --- 独立解说片段(同一时间窗,放单独一条音频轨)---
        if clip_audios and i < len(clip_audios) and clip_audios[i]:
            vpath = clip_audios[i]
            if os.path.exists(vpath):
                vinfo = probe_audio(vpath)
                if vinfo:
                    rel_v = inline_media(vpath, actual_folder, used_media)
                    vmat_id = new_uid()
                    vreg_id = new_uid()
                    vdur = min(vinfo["duration_us"], src_dur)
                    clip_voice_regs.append({
                        "ai_group_type": "", "create_time": -1,
                        "duration": vinfo["duration_us"], "enter_from": 0,
                        "extra_info": os.path.basename(rel_v),
                        "file_Path": f"{path_folder}/{rel_v}",
                        "height": 0, "id": vreg_id,
                        "import_time": -1, "import_time_ms": -1, "item_source": 1,
                        "material_color_tag": "", "md5": "", "metetype": "music",
                        "roughcut_time_range": {"duration": -1, "start": -1},
                        "sub_time_range": {"duration": -1, "start": -1},
                        "type": 0, "width": 0,
                    })
                    vmat = build_audio_material()
                    vmat.update({
                        "id": vmat_id,
                        "path": f"{path_folder}/{rel_v}",
                        "name": os.path.basename(rel_v),
                        "duration": vinfo["duration_us"],
                        "local_material_id": vreg_id,
                        "music_id": vmat_id,
                        "source_platform": 0,
                    })
                    doc["materials"]["audios"].append(vmat)
                    clip_voice_segments.append(make_audio_segment(
                        doc, vmat_id, t_cursor, vdur, render_index=2))
        t_cursor += src_dur

    registrations.extend(clip_voice_regs)

    doc["tracks"] = [{
        "id": new_uid(),
        "type": "video",
        "attribute": 0,
        "is_default_name": True,
        "name": "",
        "segments": segments,
    }]
    doc["duration"] = t_cursor

    # --- 独立「游玩解说」音频轨(逐片段的解说)---
    # 与视频轨同起点同时长,单独一条轨 —— 剪映里可分别调音量/静音。
    if clip_voice_segments:
        doc["tracks"].append({
            "id": new_uid(), "type": "audio", "attribute": 0, "flag": 0,
            "is_default_name": True, "name": "游玩解说",
            "segments": clip_voice_segments,
        })
        print(f"  [独立解说轨] 加入 {len(clip_voice_segments)} 个解说片段")

    # --- 可选:额外的音频参考轨 ---
    # 用途:大镖客那批的解说被录得极轻(-70~-85dB),不适合做主音轨,
    # 但作为"参考轨"放在时间线上有价值 —— 你能对照着听自己当时说了什么。
    # extra_audio: [(文件路径, 目标起始秒, 显示名), ...]
    if extra_audio:
        audio_segments = []
        audio_regs = []
        for path, at_sec, label in extra_audio:
            if not os.path.exists(path):
                print(f"  [警告] 参考音轨不存在: {path}")
                continue
            info = probe_audio(path)
            if not info:
                print(f"  [警告] 参考音轨解析失败: {path}")
                continue
            rel_a = inline_media(path, actual_folder, used_media)
            mat_a_id = new_uid()
            reg_a_id = new_uid()
            audio_regs.append({
                "ai_group_type": "", "create_time": -1,
                "duration": info["duration_us"], "enter_from": 0,
                "extra_info": label or os.path.basename(path),
                "file_Path": f"{path_folder}/{rel_a}",
                "height": 0, "id": reg_a_id,
                "import_time": -1, "import_time_ms": -1, "item_source": 1,
                "material_color_tag": "", "md5": "",
                "metetype": "music",           # 音频类登记用 music
                "roughcut_time_range": {"duration": -1, "start": -1},
                "sub_time_range": {"duration": -1, "start": -1},
                "type": 0, "width": 0,
            })
            amat = build_audio_material()
            amat.update({
                "id": mat_a_id,
                "path": f"{path_folder}/{rel_a}",
                "name": label or os.path.basename(path),
                "duration": info["duration_us"],
                "local_material_id": reg_a_id,
                "music_id": mat_a_id,
                "source_platform": 0,
            })
            doc["materials"]["audios"].append(amat)
            t0a = int(round(at_sec * 1e6))
            dur_a = max(1, min(info["duration_us"], t_cursor - t0a))
            audio_segments.append(make_audio_segment(
                doc, mat_a_id, t0a, dur_a, render_index=3))
        registrations.extend(audio_regs)
        if audio_segments:
            doc["tracks"].append({
                "id": new_uid(), "type": "audio", "attribute": 0, "flag": 0,
                "is_default_name": True, "name": "解说参考",
                "segments": audio_segments,
            })
            print(f"  [参考轨] 加入 {len(audio_segments)} 条解说参考片段")

    # --- 落盘 ---
    # 主文件只写 draft_info.json。实测(本机 11.2.0.14339,2026-09 验证通过):
    #   剪映读走 draft_info.json 后,自己生成 draft_content.json(密文,权威)
    #   + Timelines/<id>/ 镜像 + .backup/,并把自己分配的时间线 id 写回 draft_info.json 的 id 字段。
    #   我写的其他字段逐字节保留 —— 所以 draft_info.json 就是可靠的手写靶点。
    body = json.dumps(doc, ensure_ascii=False)
    written = []
    with open(os.path.join(folder, "draft_info.json"), "w", encoding="utf-8") as f:
        f.write(body)
    written.append("draft_info.json")

    draft_id = new_uid()
    meta = build_meta_info(name, path_folder, registrations, t_cursor, draft_id)
    with open(os.path.join(folder, "draft_meta_info.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False))
    written.append("draft_meta_info.json")

    return {"folder": folder, "written": written, "doc": doc, "meta": meta}


def split_spec(spec: str) -> tuple[str, float | None, float | None]:
    """Windows 盘符自带冒号,所以用 | 分隔:path.mp4|1.5|3.0"""
    if "|" in spec:
        parts = spec.split("|")
        if len(parts) != 3:
            raise SystemExit(f"--clip 格式应为 路径|起始秒|结束秒 : {spec}")
        return parts[0], float(parts[1]), float(parts[2])
    return spec, None, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--clip", action="append", default=[])
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--out-root", default=DRAFT_ROOT)
    ap.add_argument("--no-inline", action="store_true",
                    help="不把素材复制进草稿目录(默认会复制,使草稿自包含)")
    ap.add_argument("--media-src", default=None,
                    help="素材的原始位置(单个文件路径)。生成到暂存目录时会用到")
    ap.add_argument("--final-root", default=None,
                    help="草稿库**根目录**(不是草稿文件夹本身)。脚本会自动在其下拼 "
                         "<--name>。先在暂存区生成、之后整体搬进草稿库时用它一次写对路径。"
                         "例:--out-root D:\\tmp --final-root <剪映草稿库根>")
    ap.add_argument("--extra-audio", action="append", default=[],
                    help="额外的音频参考轨,格式 起始秒|文件|显示名(起始秒可省,默认 0)。"
                         "用于把音量很小的解说轨单独铺一条轨作参考,不混入主音轨")
    ap.add_argument("--clip-voice", action="append", default=[],
                    help="逐片段的解说音频,与 --clip 一一对应(顺序必须一致)。"
                         "给了就建一条「游玩解说」独立音频轨,视频轨只含游戏原声")
    a = ap.parse_args()

    if not a.clip:
        print("至少要一个 --clip", file=sys.stderr)
        return 2

    clips = []
    for spec in a.clip:
        path, in_s, out_s = split_spec(spec)
        if not os.path.exists(path):
            print(f"素材不存在: {path}", file=sys.stderr)
            return 2
        info = probe(path)
        if not info:
            print(f"无法解析素材: {path}", file=sys.stderr)
            return 2
        c = {"path": os.path.abspath(path), **info}
        if in_s is not None:
            c["in_us"] = int(round(in_s * 1e6))
            c["out_us"] = int(round(out_s * 1e6))
        clips.append(c)
        span = f"  裁剪 {in_s:.2f}s~{out_s:.2f}s" if in_s is not None else ""
        print(f"  素材 {os.path.basename(path)}  {info['width']}x{info['height']}  "
              f"{info['duration_us']/1e6:.2f}s  音轨={info['has_audio']}{span}")

    # 解析 --extra-audio(格式: 起始秒|文件|显示名;起始秒可省,默认 0)
    extra: list[tuple[str, float, str]] = []
    for spec in (a.extra_audio or []):
        parts = spec.split("|")
        if len(parts) == 3:
            try:
                at = float(parts[0])
            except ValueError:
                print(f"--extra-audio 起始秒无法解析: {spec}", file=sys.stderr)
                return 2
            extra.append((parts[1], at, parts[2]))
        elif len(parts) == 2:
            extra.append((parts[1], 0.0, parts[0]))
        else:
            print(f"--extra-audio 格式应为 起始秒|文件|名称 : {spec}", file=sys.stderr)
            return 2

    res = build(a.name, clips, a.fps, a.out_root, a.media_src, a.final_root,
                extra_audio=extra or None,
                clip_audios=(a.clip_voice or None))
    doc, meta = res["doc"], res["meta"]
    regs = meta["draft_materials"][0]["value"]
    print(f"\n草稿已生成: {res['folder']}")
    print(f"写出文件: {', '.join(res['written'])}")
    print(f"时间线 {doc['duration']/1e6:.2f}s / 片段 {len(doc['tracks'][0]['segments'])} "
          f"/ 素材 {len(doc['materials']['videos'])} / 登记条目 {len(regs)}")
    print(f"version={doc['version']} new_version={doc['new_version']!r} "
          f"platform.app_version={doc['platform']['app_version']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
