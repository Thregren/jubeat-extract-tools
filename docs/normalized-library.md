# 从分版本导出生成统一歌曲库

入口：`python -m workflows.normalize_library`。它读取已经完成的分版本导出与正式比较报告，在新的目录内生成统一库。它不运行游戏、不处理帐号服务、不修改原归档，也不重新解包 IFS/JBT。仓库只提供代码、文档与人工合成测试，不包含游戏素材、密码、个人路径或本地审计报告。

## 先明确去重的口径

默认只按**命名空间和原 ID**确定身份：各街机包的同一 ID 属于 `arcade:<原ID>`；手机包采用 `<原目录名>:<原ID>`。没有提供身份关系文件时，手机与街机不会合并，jubeat plus 与 jukebeat 也不会仅因数字 ID 相同而合并。这是可恢复的保守结果，不表示已经完成跨平台歌曲匹配。

在一个已确认身份内，BSC/ADV/EXT 的 gameplay 哈希组合相同，只保留优先来源的一套。gameplay 哈希仅排除 HAKU、MEASURE 引导事件，保留其他所有已解析命令及顺序。因此引导标记变化不会制造新的谱面包，真正的时间、面板、LONG、TEMPO 等变化会保留为变体。原始 SHA、完整事件 SHA、gameplay SHA 分别保留，不能互相替代。

优先级是原版街机 → mod/Omnimix → 手机；同一优先级再按输入目录的编号选择最早观察到的包。这里的“首次收录”指**本地输入中首次观察到**，不是商业发行史。T44、增量包、合并视图仍记录来源类型，不能当作新的正式世代。

可玩资源与不完整资源分开计数：`identities` 等于 `playable_identities`，只包含至少一张可玩谱的身份；`all_resource_identities` 另包含音频/图片等不完整身份。音频专用资源按其原始音频哈希组合保留差异，不混入可玩谱面改动统计。没有可用歌曲谱、音频、封面的观察只保留在 `excluded_index_only`，记录原 manifest 与原 ID；例如非歌曲视频也会落在这里，不能因此称作 dummy。历史字段 `excluded_placeholders` / `placeholder_only_observations` 只是同一集合的兼容别名，正式计数使用 `index_only_observations`，并查看各项 reason/observed_placeholder。

仅有封面的资源也可以进入“特殊或不完整资源”，不会计为可玩歌曲。不同音频来源组合各自保留，因此不完整包数量可能比“同 ID 无条件压成一个包、排除仅封面资源”的旧一次性脚本更多；应比较同一统计口径，不为对齐旧数字丢弃源差异。

## 依赖与输入

Python 3.10+，从仓库根目录运行。完整导出需要 Pillow，以及 PATH 中可用的 ffmpeg/ffprobe：

```sh
python -m pip install 'Pillow>=10,<13'
# 需要其他资源工具时，也可安装仓库 requirements-assets.txt。
ffmpeg -version
ffprobe -version
```

`--plan` 读取 JSON、清单及文件存在性；提供显式封面+读音关系时，还会按需读取该关系涉及的 PNG 并核对实际 SHA，不扫描全库图片。它不打开音频载荷，不依赖 ffmpeg 或 Pillow。`--run` 才读取实际音频并调用转换器。可以用 `--ffmpeg PATH --ffprobe PATH` 指定安装位置；源码没有 Homebrew、用户目录或磁盘挂载点的默认值。

先生成完整比较报告：

```sh
python -m analysis.compare_versions --root "/path/to/分版本解包"
```

统一库要求该报告 `completion=complete`、`sample=false`、`issues=[]`，并再次核对每版 manifest 的实际 SHA。报告中的谱面应有原始 SHA 与完整事件/gameplay SHA。遇到缺失、损坏或已被改动的 manifest，先修复导出或重建比较报告，不通过修改哈希来绕过检查。

## 已适配的 17 种目录位置

以下数字是比较报告中的目录编号，目录余下部分可不同，构建未知也可明确写作 `unknown`。适配的是这些导出 schema，不保证任意第三方同名文件夹都可直接使用。

| 编号 | 来源范围 | 资源定位方式 |
|---|---|---|
| 01 | 初代 jubeat | `manifest.songs`，`selected_resources`，保留 base/append 来源 |
| 02 | ripples，原构建可能未知 | 同上；不会补猜构建日期 |
| 03 | knit | 同上 |
| 04 | copious APPEND | 同上，选择后的官方谱与原层分别保留在比较报告 |
| 05 | saucer | 顶层歌曲索引指向逐歌 `manifest.json` |
| 06 | saucer fulfill | 同上 |
| 07 | prop | 同上，含 LONG 和独立徽章资源的分版本导出 |
| 08 | Qubell | 同上 |
| 09 | clan | 同上 |
| 10 | T44 JAEPO 原型 | list manifest、命名原谱，已导出的 FLAC 音频，试听键可为 `idx` |
| 11 | festo | list manifest，official 与 `mods/mod_omnimix` 独立来源 |
| 12 | Ave | list manifest，命名原谱与音频路径 |
| 13 | beyond the Ave 底包 | 同上 |
| 14 | beyond the Ave 增量包 | 只反映包内资源；缺席不解释为删曲 |
| 15 | 底包与更新合并视图 | 保留最终选用源；不是第 15 个正式街机世代 |
| 16 | jubeat plus 手机包 | mobile namespace，明文 SEQ、原始 AAC/M4A 与 artwork PNG |
| 17 | jukebeat 手机包 | 独立 mobile namespace，同上 |

其他 schema 要先扩展适配器并用合成样本测试。游戏版本名、配置构建、实际文件完整性是三个不同问题：目录名或配置字段不证明它是未改动的官方 HDD。

## 命令与目录

先查看计划；`--output` 必须是新目录，不能是原导出根、它的祖先或某个原版本目录内部：

```sh
python -m workflows.normalize_library \
  --root "/path/to/分版本解包" \
  --output "/path/to/统一歌曲库" \
  --comparison "/path/to/分版本解包/_reports/跨版本歌曲与谱面差异.json" \
  --plan
```

检查计划后先导出少量，再用同样输入完整运行：

```sh
python -m workflows.normalize_library \
  --root "/path/to/分版本解包" --output "/path/to/统一歌曲库" \
  --comparison "/path/to/分版本解包/_reports/跨版本歌曲与谱面差异.json" \
  --run --limit 3 --workers 1

# 去掉 --limit 执行全量；保留相同 root/output/comparison/关系输入。
```

`--limit` 的结果一定标为 `sample_complete`，不能当作全库完成。默认一个音频 worker；机械硬盘宜保持这个值。

```text
统一歌曲库/
  library.json                  # 工具/计划指纹，绑定本次输出
  _reports/
    生成计划.json
    生成结果.json               # complete / sample_complete / errors
    来源与去重映射.json
    身份关系验证.json           # 每条接受或拒绝的关系与理由
    歌曲索引.json
  _cache/audio/                 # PCM24 对象及校验 sidecar
  <来源版本目录>[_Omnimix]/
    首次收录/<命名空间_原ID>/
    谱面变化/<命名空间_原ID>__<稳定bundle摘要>/
    特殊或不完整资源/<身份>[__摘要]/
      bgm.wav
      preview.wav               # 有实际来源才生成
      cover.png                 # 有实际 PNG 才放入
      charts/BSC.mc             # ADV、EXT 同理；只有可玩谱导出 MC
      charts/BSC.events.json
      original/BSC.eve          # 手机来源为 BSC.seq
      song.json
      provenance.json
```

不存在的媒体不会生成空文件。`missing_charts` 明确列 BSC/ADV/EXT 中不存在的原谱；`missing` 同时列缺音频、封面与 `chart:BSC` 等项。原谱存在但零 note 会列入 `nonplayable_charts`，保留原文件和事件 JSON，不伪造可玩的 MC。

## 音频、图片和谱面的精度

- 音频输出 PCM24 WAV，不指定 `-ar`、`-ac`、增益、响度或滤镜；不从 BGM 裁切试听。不把 16 bit 或有损 AAC 说成凭空获得了 24 bit 的原始细节。
- WAV 输入按实际 RIFF 数据长度严格检查采样率、声道和帧数。T44 的 FLAC、手机的 AAC/M4A 用实际 ffprobe 检查采样率/声道。AAC 有合法 `format.tags.iTunSMPB` 时，优先用第四个十六进制字段的有效样本数精确校验（容差为 0），第二/第三字段记录 encoder priming 与尾部 padding；容器 duration 可能包括这两部分，不能直接当作有效 PCM 长度。没有可用 SMPB 时，有 `nb_samples` 则精确检查，否则使用 duration/time_base 及声明的 initial_padding/首包 skip_samples，加一个 AAC 帧边界（LC 1024、HE 2048 样本；有明确 discard_padding 时也计入）作为有依据的容差。检查依据、容器帧数、延迟与实际输出帧数都写入 sidecar；不以固定 2048 样本阈值误判 iTunes 的 2112 样本编码延迟，也不裁切或补齐音频来满足头部时长。其他压缩 codec 未验证，拒绝猜测。
- PNG 不缩放、不重采样。最早选中源没有封面时，可以从同一已确认身份的其他来源补图，`asset_provenance.cover` 记录版本、ID、路径和 `borrowed=true`。音频补缺仅使用同一个 gameplay bundle 的其他观察，避免从另一种编曲/变体猜取音乐。
- MC 是编辑器便利格式，使用现有转换器的拍点积分与 1/48 beat 取整。未知正式曲名显示为 `ID <原ID>`；`song.json.title` 仍为 null，不把 CP932 读音当正式曲名。
- 同一整数 tick 有多条 TEMPO 时，以原 EVE/SEQ 文件顺序的最后一条生效，不能按 TEMPO 数值排序。该规则同时适用于街机 EVE 和移动 SEQ；是否受影响取决于事件内容，与游戏版本目录名无关。旧转换器曾错误重排同时刻 TEMPO，可能导致后半曲持续漂移；原始事件 JSON 和音频不受影响。
- 300 Hz JSON 保留整数 tick、事件顺序、命令及参数；手机另保留完整 0x60 字节头、原始两个 uint32 记录和尾随字节，可逐字节重组 SEQ。EVE 的原始编码、换行与空白由 `original/*.eve` 无损保留。MC note/LONG 数须与原事件一致。
- HAKU/MEASURE 的差别不额外复制歌曲包，但各来源的原 SHA、完整事件 SHA、原谱路径仍在 `provenance.json` 的观察列表中，可回原导出追溯。

已有导出升级时，应从保留的原 EVE/SEQ 重建受影响的 MC，再更新播放器的派生 JSON。`song.json.chart_converter_version` 记录转换器修订；本版为 `tempo-source-order-v2`。统一库构建器不会复用缺少该修订的旧完成标记，音频缓存政策和源文件保持不变。仅改 TEMPO JSON 可能遗漏当时按错误时轴换算的音符拍值，不能代替重新转换。重建后应以原整数 tick 独立核对全部音符和长押头尾的秒数；物量一致不能证明时序正确。

默认在同一文件系统内用硬链接保留原谱、封面与音频缓存，跨文件系统则复制。工具自己不会原地写原素材；硬链接文件的后续人工编辑会影响共享 inode，因此需要编辑时先另存副本。

## 可选的已审计身份关系

提供关系文件是显式采用其身份假设；工具仍会根据当前 manifest/比较结果复核，不只信输入中的“已通过”文字：

```sh
python -m workflows.normalize_library \
  --root "/path/to/分版本解包" --output "/path/to/带审计关系的统一库" \
  --comparison "/path/to/跨版本歌曲与谱面差异.json" \
  --relations "/path/to/identity-relations.json" \
  --alias-audio-audit "/path/to/alias-audio-audit.json" --plan
```

`analysis.identity_relations.resolve_identities` 接受可选、可以为空的以下列表字段。没有独立“下载审计文件”的步骤；这些文件是用户自己对其导出数据制作的分析输入，不随代码发布。

| 字段 | 当前证据门槛 |
|---|---|
| `unique_three_chart_relations` | `mobile_version/mobile_id/arcade_id`；三张可玩谱必须在同一个观察中精确匹配；候选范围为独立正式街机包的 official 层，排除 mod 改 ID、原型、增量和 merged 重复视图。全部该范围内候选须落在一个已准许组件内；可用 `gameplay_hashes` 再约束三谱 |
| `mobile_relations_resolved_via_verified_arcade_alias_group` | 同上，但 `arcade_ids` 的多个候选只能指向同一个已通过组件 |
| `plus_jukebeat_same_content_relations` | `left/right` 各有 `version/id`；三谱及原始 BGM、试听哈希同时相同 |
| `strict_title_artist_and_bgm_identity_edges` | `left/right`；非空标题、作者经 NFKC/casefold/空白规范后精确相同，且原始 BGM SHA 相同；不同谱面仍保留变体 |
| `title_artist_bgm_anchored_variant_relations` | `version/id/arcade_id_candidate` 与 `identical_original_bgm_to_anchor`；anchor 已接入目标街机身份，当前标题、作者、BGM 同时相同 |
| 音频审计的 `arcade_alias_group_audio_evidence` | `arcade_ids`、`all_ids_connected_by_identical_dual_audio=true`；还须由当前三谱+BGM+试听共同相同的边连接全部 ID |
| `arcade_cover_reading_alias_groups` | 显式提供 `arcade_ids/name_reading_cp932/cover_sha256/evidence`；逐 ID 复核同一个当前观察的完整三谱、metadata 中的读音，以及实际 PNG 文件 SHA。只延迟读取这些 ID 的封面；不扫描全库图片。允许原音频不同，但不会把读音自动升级为正式曲名 |

单难度匹配、名称相似、只相同 BGM、ID 加减或截位、未批准的三谱 alias 组都不足以合并。模糊、过期、不完整的关系留在 `身份关系验证.json.rejected`，保持原身份分开；它不是解码失败，但需要人工查看是否符合自己的身份政策。

封面+读音关系是明确的人工审计入口，不会仅因全库中某两张封面相同就自动合并。调用 Python API 时必须提供 `cover_hash(observation)` 回调；CLI 的回调验证源文件位于 root 内、具有 PNG 签名，再计算实际 SHA。来源中的不同 BGM/试听哈希、路径和参数留在 `provenance.json.observations`，选用首个音频并不把其他源音频说成相同。

若两份封面本身不同，另可显式提供 `cover_sha256_by_id` 字典（每个原 ID 对应各自实际 PNG SHA），同时必须有非空 `title`、`title_visual_verified: true` 及 `observations` 证书。每条证书指定 `id/version/layer/manifest/cover_sha256`，将人工确认的相同可见标题绑定到准确的源观察，不能拿 Omnimix 图片充作 official 的证据。工具只读取被证书绑定的封面，复核实际哈希、同一 CP932 读音、完整三谱，以及当前 metadata 的 BPM 范围和三难度等级（数值规范后比较）。缺少可视证书、错误图片、错误来源层或不一致 BPM/等级均拒绝；工具不执行 OCR、不自动从标题相似推出身份。原来的共同 `cover_sha256` 模式仍兼容；也可以用 observations 限定其来源。

## 断点恢复与验证

再次执行完全相同的命令会重建确定性计划，并验证 `library.json` 绑定的指纹。更换比较报告、关系、政策或输出位置时，使用新的输出目录；不把旧歌曲包静默当作新计划成果。

音频缓存复用前验证实际输入音频 SHA、缓存文件 SHA、PCM24 WAV 头、实际源 rate/ch 与帧数政策、sidecar 和当前声明参数。已有 `song.json` 也不是“存在即成功”：包指纹、完整预期文件集、每个输出的长度和 SHA、当前原谱 SHA、封面来源、音频缓存身份都须一致。自己的 MC/PNG 等输出损坏会重建；源谱改变或缓存本身不可信会报错，不能把坏结果计入 complete。修改文件后不要在同一次运行中继续混用旧输入。

`song.json` 最后写入。进程中断留下的临时文件不代表歌曲完成。`生成结果.json` 汇总错误、成功项和 ffmpeg 成功运行时的全部 stderr 警告；即使退出码为零，也请查看 warnings 与 missing。未完成或失败以非零退出码结束。

验证命令（测试全部使用人工合成数据）：

```sh
python -m unittest discover -s tests -p 'test_normalize_library.py' -v
python -m workflows.normalize_library --help
```

测试覆盖同版同身份多变体目录、三谱/命名空间证据、显式封面+读音关系与错封面拒绝、缺谱说明、路径及 symlink 穿越、过期清单、PCM24 参数、坏缓存拒绝、补封面溯源、未知标题、重跑与损坏 MC 修复、iTunSMPB 精确样本数与无标签 priming 回退，以及实际合成 AAC/FLAC 的完整解码导出。需要 ffmpeg/ffprobe/Pillow 的测试在依赖缺失时明确 skip，不把 skip 当作媒体验证通过。
