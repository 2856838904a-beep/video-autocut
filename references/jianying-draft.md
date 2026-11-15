# 剪映专业版草稿格式(Windows,实测于 11.2.0.14339)

## 一句话结论

**加密只挡"读已有草稿",不挡"写新草稿"。** 在空目录里写 2 个明文 JSON,剪映能直接打开。

## 目录位置

```
%LOCALAPPDATA%\JianyingPro\User Data\Projects\com.lveditor.draft\
```

## 必需文件

| 文件 | 作用 | 手写? |
|---|---|---|
| **`draft_info.json`** | 主草稿(剪映 6.0+)。旧名 `draft_content.json` 会报"草稿内容已损坏" | ✅ 必写 |
| **`draft_meta_info.json`** | 元数据 + **素材登记表** | ✅ 必写 |
| `_media/` | 自包含素材目录(自定义,非剪映规范) | ✅ 建议 |

其余文件(`draft_settings`、`key_value.json`、`.backup/`、封面、`Timelines/`、`draft_agency_config.json` 等)剪映打开后**自动生成**,不要手写、不要依赖。

## 剪映打开后的行为(实测)

读走 `draft_info.json` 后,剪映会:

1. 生成 **`draft_content.json`(密文)** —— 它自己的权威副本,`root_meta_info.json` 里 `draft_json_file` 指向这个
2. 生成 **`Timelines/project.json`** + **`Timelines/<timeline_id>/`** 镜像(内含密文 `draft_content.json`、明文 `template.tmp`)
3. 生成 `.backup/`、`draft_settings`、`draft_cover.jpg`
4. **把自己分配的 timeline id 写回 `draft_info.json` 的 `id` 字段**
5. 把 `draft_meta_info.json` **加密**
6. 在 `root_meta_info.json` 里**自动登记**(不用手动写)

**除 `id` 外,手写字段逐字节保留。** 所以 `draft_info.json` 是可靠的手写靶点,但一旦剪映处理过,`draft_meta_info.json` 就读不回来了(单向)。

### ⚠️ 但 UI 真正加载的是 `draft_content.json`(2026-09 补测,极重要)

`root_meta_info.json` 的 `all_draft_store[]` 里每个草稿都有个 **`draft_json_file`**
字段,**实测全库 42 个草稿(含剪映自己建的)无一例外都指向 `draft_content.json`**:

```json
{"draft_name": "MC全库精华",
 "draft_json_file": ".../com.lveditor.draft/MC全库精华\\draft_content.json",
 "draft_fold_path": ".../com.lveditor.draft/MC全库精华",
 "tm_duration": 563016672}
```

含义:**首次打开时剪映从 `draft_info.json` 迁移,此后一律加载 `draft_content.json`。**
所以改完 `draft_info.json` 后如果**只**改它,剪映打开的还是老的 `draft_content.json`,
你的修改等于没做 —— 而且不报任何错。踩过的实例见 `gotchas.md` 第 0c 条。

**正确做法**:两个文件都写成同一份正确内容(`fix_audio_track.py --also-content`
就是这么做的)。剪映能读明文 `draft_content.json` —— 库里 `2月11日 (1)~(6)`
这些草稿本身就是明文 `draft_content.json`,`draft_json_file` 也指向它。
若剪映看到明文选择重新迁移,它会回到读 `draft_info.json` —— 两条路都通。

**注意**:`draft_content.json` 平时是**密文**(base64 样子,`{}` 后接二进制)。
看到草稿目录里有这种文件不要惊讶,那是剪映写的,不是损坏。

## 三条 ID 链(最容易错)

```
[segment]
  material_id  ──────────►  materials.videos[i].id
                                    │
                                    ▼
                    materials.videos[i].local_material_id  ──►  draft_meta_info.json
                                                                 draft_materials[type=0].value[j].id
```

- **链 1** `segment.material_id` 必须 = `materials.<类别>[i].id`
- **链 2** 素材对象上的 `local_material_id` 必须 = 登记条目的 `id`。**这是剪映解析本地素材的外键**
- 漏了链 2 → 报"媒体丢失",而且剪映的**重新链接对话框修不好**,必须重新生成

注意:登记条目的 `id` 与 `materials.videos[].id` 是**两个不同的 UUID**,不要写成同一个。

## 加音轨(视频原声 + 解说分开两条)

用户要求"游戏原声和解说都要保留、音量各自可调"时,做法是**视频轨带游戏原声 +
另一条 `type=audio` 轨放解说**。这条音轨极易被剪映**静默丢弃**(不报错、
时间线上直接看不见),必须严格照下面的结构写。详细踩坑记录见
`gotchas.md` 第 0c 条。

```jsonc
// ① 音频素材:materials.audios[]。type 必须是 music —— 写 "audio" 会让剪映
//    解析失败并丢弃整条音轨。
{"id": "<UUID>", "type": "music", "path": "<绝对路径>", "name": "解说.m4a",
 "duration": 33930000, "local_material_id": "<登记条目 id>",
 "check_flag": 1, "music_id": "<UUID>", "source_platform": 0,
 "copyright_limit_type": "none", "wave_points": [] /* 其余 40+ 字段给零值即可 */}

// ② 轨道对象:doc.tracks[] 追加一条(type 这里才是 "audio")
{"id": "<UUID>", "type": "audio", "attribute": 0, "flag": 0,
 "is_default_name": false, "name": "游玩解说", "segments": [...]}

// ③ 音频片段:完整 44 字段,且 extra_material_refs 必须非空
{"id": "<UUID>", "material_id": "<①的 id>",
 "target_timerange": {"start": 0, "duration": 33930000},
 "source_timerange": {"start": 0, "duration": 33930000},
 "render_index": 2, "track_render_index": 0, "track_attribute": 0,
 "volume": 1.0, "speed": 1, "visible": true, "state": 0,
 "extra_material_refs": ["<speeds id>", "<beats id>",
                         "<sound_channel_mappings id>", "<vocal_separations id>"],
 /* clip / uniform_scale / hdr_settings / caption_info / lyric_keyframes 给 null,
    keyframe_refs / common_keyframes 给 [] */}
```

**四类辅助素材每个片段一份**(`speeds`/`sound_channel_mappings`/
`vocal_separations`/`beats`),模板见 `gotchas.md` 第 0c 条。
`materials.speeds[].speed` 必须 `1.0`,否则会真的变速。

**对齐**:音频段与视频段的 `target_timerange.start` 要一致(实测 30fps 下
时长差 ≤30ms 无影响,起点差 0 即可)。

`make_draft.py --clip-voice` 已按此结构产出;存量草稿用
`fix_audio_track.py <草稿目录> --also-content` 补救。

## 必填字段值

从**本机已有明文草稿**抄,不要猜:

```jsonc
{
  "version": 400000,
  "new_version": "127.0.0",
  "fps": 30,
  "duration": 4000000,              // 微秒,必须与轨道内容一致
  "canvas_config": {"background": null, "height": 1080, "ratio": "original", "width": 1920},
  "platform": {
    "app_id": 0, "app_source": "lv",  // lv = 剪映;cc = CapCut
    "app_version": "11.2.0",          // ★ 必须与本机剪映版本一致
    "os": "windows",
    "device_id": "", "hard_disk_id": "", "mac_address": "", "os_version": ""
  },
  "last_modified_platform": { /* 同上 */ },
  "color_space": -1,
  "render_index_track_mode_on": false,
  "tracks": [ ... ],
  "materials": { ... }
}
```

- **35 个顶层键 + 54 个 materials 桶要齐全**。缺字段会被拒或行为异常
- 时间单位统一是**微秒**
- 骨架最方便的获取方式:任意草稿的 `Timelines/<id>/template.tmp`(**明文**,含完整空骨架)

## 素材登记条目

`draft_meta_info.json` → `draft_materials` 是 7 个分组(0/1/2/3/6/7/8),`type: 0` 是用户导入的媒体:

```json
{
  "ai_group_type": "", "create_time": -1,
  "duration": 3000000, "enter_from": 0,
  "extra_info": "clip.mp4",
  "file_Path": "C:/.../draft/_media/clip.mp4",
  "height": 1080,
  "id": "<UUID>",                       // ★ 要与 material.local_material_id 相等
  "import_time": -1, "import_time_ms": -1,
  "item_source": 1, "material_color_tag": "", "md5": "",
  "metetype": "video",                  // video | photo | music
  "roughcut_time_range": {"duration": -1, "start": -1},
  "sub_time_range": {"duration": -1, "start": -1},
  "type": 0, "width": 1920
}
```

- 照片的 `duration` 用标称值 `5000000`
- `-1` 时间戳与空 `md5` 在手写场景是可接受的
- 同一素材被切成多段时,**登记只做一次**,多个片段共用同一个登记 id

## 常见报错与成因

| 现象 | 成因 | 修法 |
|---|---|---|
| **"草稿内容已损坏"** | 主文件名错(6.0+ 需 `draft_info.json`);或文件以 `{` 开头却解析失败(真损坏) | 改名;真损坏从 `.bak` 恢复 |
| **"媒体丢失/请重新链接"** | `draft_materials[type=0]` 空,或素材 `local_material_id` 为空/不匹配 | 补登记 + 回填外键 |
| **"项目来自异常路径"** | 版本标记过期(缺 `version`/`new_version`/`last_modified_platform`/`color_space`/`render_index_track_mode_on`) | 用本机草稿当 seed 抄字段 |
| **时间轴为空** | 复制了 seed 的 `Timelines/`(会用模板的空时间线);或 `duration` 没重算 | 不要复制 `Timelines/`;每次改完重算 `duration` |
| **列表里看不到新草稿** | 剪映不实时刷新 | 重启剪映,或进出一次任意旧草稿 |
| **写入被覆盖** | 剪映在运行,自动保存回滚了外部写入 | 完全退出主程序(含托盘),再写 |

## 素材自包含

把素材复制进 `<草稿>/_media/`,路径写**绝对路径**(用 `/` 不用 `\`)。
实测剪映能正常读取,草稿可整体迁移。虽然 Windows 无沙盒限制,仍然建议这样做 —— 避免用户移动或删除原始素材后草稿失效。

## 关于"导入工程"

剪映专业版(国内版)支持导入 `.prproj` / FCPXML(需在 **设置 → 全局设置** 里勾选【导入工程】)。**CapCut 国际版不支持**。
但这条路线与"生成草稿"是两回事:它由剪映自己解析外部工程,不会有素材登记问题。仅当你要把别的 NLE 里已剪好的时间线搬进来时才用。**EDL / FCP7 XML 无支持证据,不要设计这条路线。**

## 其它版本的信息源

- `pyJianYingDraft` —— Python 草稿生成主流库,但**默认仍写 `draft_content.json`**(新版会报损坏),需手动改名;PR #198 尚未合并
- `jy-draftc` —— 调本机 `videoeditor.dll` 的 `EncryptUtils` 做**解密/回加密**,覆盖剪映 10.3.0–11.1.0,有预编译包。**只在需要读回加密草稿时用**
- `capcut-cli` —— Node CLI,含 `lint`/`diagnose`/`sync-timelines`,只检测加密不解密
