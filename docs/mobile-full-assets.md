# 移动版完整素材导出

`mobile/full_extract.py` 面向 ZIP 形式的 jubeat plus / jukebeat 应用备份。它在原有 `mobile/extract.py` 的歌曲、谱面与 CgBI 解码函数上增加完整素材保留、独立试听、安装素材、错误清单和可复核统计。

本工具只读取备份，不运行游戏，不访问网络，不导出账户、成绩、偏好设置或设备资料。所有结果保存在本机指定目录。

## 已验证的版本范围

| 输入或格式 | 范围与证据限制 |
| --- | --- |
| jubeat plus 的 ZIP 型 `.adbk` 备份 | 已用含 `Documents/<数字ID>.jbt`、`Library/Private Documents/marker/`、`appendData/`、`Documents/scImg/` 的实际备份验证；应用具体版本缺少可靠元信息，未知。 |
| jukebeat 的 ZIP 型 `.adbk` 备份 | 已用同类歌曲包与 marker 结构的实际备份验证；应用具体版本未知。不能据资源文件的年月或歌曲列表推断 App 版本。 |
| `info` / `infov2`、四种已知 seq magic、CgBI / 标准 PNG、原始 AAC/M4A | 相应字段和实际备份中的图片、音频已有验证；seq 命令使用原始 300 Hz tick。不同命名或变体遇到时仍需检查错误清单。 |
| `infov3` 独立 key 与四字节前缀 | 依据公开格式研究增加，已用自行构造的加密 plist 合成测试验证；尚未用本次实际备份中的 `infov3` 资源验证。 |
| `.ipa`、非 ZIP 备份、其它厂商备份布局、其它 iOS/App 版本 | 不在本入口承诺范围内。工具不能解锁系统备份加密，也不能保证所有 iOS 版本均兼容。 |

原有 `jbf.c` 的算法研究背景见 [mobile-format.md](mobile-format.md)。算法来源对应的 App 版本与当前输入备份的 App 版本是两件事，不能互相代替。

## 依赖与运行

- Python 3.10 或更新版本。
- Pillow，用于实际载入和验证 PNG/JPEG/GIF/WebP；CgBI 转换复用项目已有实现。
- `ffprobe`（FFmpeg 提供），用于分别检查完整音源与试听的音频流；工具不调用 FFmpeg 转码。
- macOS 的 `cc`/Clang，或 Linux 的 `cc`/GCC。Windows 可通过 WSL 使用。

示例安装与调用，路径均为示意：

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install Pillow
# 另行用系统包管理器安装 FFmpeg 和 C 编译器，使 ffprobe/cc 位于 PATH。

python3 mobile/full_extract.py \
  --input input/plus-backup.adbk \
  --out output/plus-full \
  --app jubeatplus --jobs 1

python3 -m mobile.full_extract \
  --input input/jukebeat-backup.adbk \
  --out output/jukebeat-full \
  --app jukebeat --jobs 2 --mc
```

参数说明：

| 参数 | 作用 |
| --- | --- |
| `--input PATH` | 一份 ZIP 型备份。plus 与 jukebeat 分开调用。 |
| `--out PATH` | 新目录或空目录。已有结果不覆盖；修复后另选输出目录重跑，避免残留文件、不同 App 或不完整运行混在一起。 |
| `--app jubeatplus\|jukebeat` | 显式指定应用命名空间。保留原始数字 ID 和前导零，不自动映射为街机 ID。 |
| `--jobs 1\|2` | 默认 1。机械硬盘建议 1；最多两个歌曲 worker。 |
| `--ffprobe PATH` | 默认从 PATH 查找。每个原 `bgm` 和 `index` 独立验证，无论二者哈希是否相同。 |
| `--mc` | 另写 MC 副本。原始 seq 和完整事件 JSON 始终保留；不启用时不生成 MC。 |
| `--limit N` | 仅处理前 N 首歌曲作检查；安装素材仍处理。结果明确标记 `limited_sample=true`，状态为 partial，不能当作整包完成。 |

退出码：0 表示完整运行且无记录错误；2 表示已导出但存在失败、缺失或主动限量；1 表示输入、依赖或配置等致命错误。中断时没有最终 `stats.json` 或 `progress.finished` 仍为 false；不能把已经产生的文件数视为完成。

首次运行按需编译原有 `jbf.c` 和新的 `mobile/tools/marker.c`，产物仅位于仓库 `.build/`。纹理和 `infov3` 使用两个独立共享库，各自只初始化一次 key schedule，避免多线程换 key 污染歌曲解密。只需发布源码，不应发布这些编译产物。

## 输出结构与原始字节

```text
out/
  raw/<ID>.jbt
  songs/<ID>/
    info.json
    decrypted/<原成员名>
    metadata/<成员名>.plist|json|xml
    charts/BSC.seq|BSC.events.json|BSC.mc
    charts/ADV.seq|ADV.events.json|ADV.mc
    charts/EXT.seq|EXT.events.json|EXT.mc
    bgm.m4a
    preview.m4a
    images/<原成员名>.<实际图片格式>
  extras/
    originals/<安装素材原路径>
    unpacked/<原ZIP路径>/decrypted/<成员名>
    unpacked/<原ZIP路径>/images/<成员名>.<格式>
    decrypted/<非ZIP素材路径>
    images/<非ZIP素材路径>.<格式>
    manifest.json
    errors.json
  manifest.json
  stats.json
  errors.json
  source_inventory.json
  app_version.json
  progress.json
  zero-note-charts.json
  说明.txt
```

`raw/<ID>.jbt` 是原备份成员的完整字节，包含 ZIP 末尾可能存在的额外数据，不重打包。清单保存外层成员位置、ZIP CRC、JBT SHA-256/长度，以及每个内层密文与明文的 SHA-256/长度。ZIP 读取会检查 CRC；SHA 用于追溯字节一致性，不等于验证来源真实性。工具没有把 JBT 尾部摘要当作认证签名。

`decrypted/` 保留已识别和成功解密的完整明文字节。未知加密成员若具备合法 BFCodec 长度封装，也尝试导出明文；无可识别封装的未知成员保留在完整原 JBT，列入 `unknown_members`，不被当作已成功解释。无 MAC 的 BFCodec 无法仅凭解密过程证明 key 正确，已知谱面、plist、图片与音频还需通过相应解析验证。未知明文不作成功解码的保证。

`info`、`infov2` 和出现时的 `infov3` 各自完整保存，不只挑选歌曲名和等级。plist 原件为权威副本；JSON 的 bytes、datetime、UID、非有限浮点数采用带类型字段保存。若 XML 不能表示 UID 等类型，会记录 `xml_status`，保留原 plist 与完整 JSON；不会为强制 XML 转换丢字段。`infov3` 的完整四字节前缀保存在 decrypted，metadata 中另存实际 plist 载荷。

`seq_` 所有难度成员均尝试保存和解析。事件 JSON 保存 96 字节头、声明记录数、头部 note 数、marker mask、每条记录的字节偏移、r0/r1、类型、原始 tick/value、尾随字节；未知类型以 `TYPE_<数字>` 原样记录。LONG 保留原值及拆出的端点、方向、长度、持续 tick。tick 单位始终为 1/300 秒，没有为了 MC 转换而改写。MC 复用现有 1/48 拍网格转换，只是可选副本，不承担完整原谱的角色。

`bgm` 与 `index` 分别写为完整音源和 `preview`，绝不从 BGM 裁切试听。按实际签名使用 `.m4a`、`.aac` 等扩展名；识别不了时保存 `.bin` 并由验证结果解释。验证失败仍保留音频和完整明文，清单记录失败，不静默删除。工具不重新有损编码、不改采样率，也不将同哈希的两个原资源合并。

图片包括 `artwork`、`artwork_s`、`name_b`、`name_w`、英文名变体及可识别的其它图片。原 CgBI 字节仍保留，展示副本转标准 PNG。其它支持格式保留原容器，并实际载入验证。纹理封装的四字节前缀不混入 PNG，完整前缀仍可在 decrypted 中追溯。每个失败记入 errors，不用图片张数推断所有图片已成功。

## marker、主题和 scImg

只从备份选择以下安装素材位置：根 `icon.png`、`Documents/scImg/`、`Library/Private Documents/marker/`、`Library/Private Documents/appendData/`。marker/hold-marker/主题 ZIP 原件保留，逐成员尝试相应纹理 key、前缀识别及图片转换；`ma...`、`h1...` 至 `h4...` 预期帧不成图时明确记错。主题内 `filename.txt` 等可读文本原样保留；未知内容独立保留并计数。`__MACOSX`、`.DS_Store` 不另行展开，仍存在于原 ZIP。

`scImg` 仅指上述明确选择的应用素材目录。本入口不会另外导出系统截图、相机、用户编辑会话、存档、成绩数据库、账户、偏好设置或备份根 Manifest 的设备信息。原安装素材路径会出现在本机运行清单中；发布工具仓库时不能把这些运行结果一起提交。

应用版本仅从匹配所选 bundle ID 的 `<应用>.app/Info.plist` 读取 `CFBundleIdentifier`、`CFBundleShortVersionString`、`CFBundleVersion`，不导出 plist 其它字段。没有对应证据或出现相互冲突的版本时输出未知；不用设置中的 `internal_version`、资源时间或歌单猜测。`--app` 本身只是调用者的选择，不是独立的版本证据。

## 完成与空谱统计

| 字段 | 含义 |
| --- | --- |
| `finished` | 本次排队任务已处理并写最终统计，不代表无错误。 |
| `status` | complete 须所有歌曲都处理、无错误且未使用 `--limit`；否则 partial。 |
| `songs_complete` | 歌曲处理未记录错误；不保证三难度均含可玩 note。 |
| `three_raw_charts_present` | BSC/ADV/EXT 原 seq 均已保存并成功解析。 |
| `three_playable_charts` | 三难度各至少包含 PLAY 或 LONG。 |
| `raw_five_assets_preserved` | 三份已解析原谱与原 BGM/原试听都已保留；音频验证是否通过另看 `*_validated`。 |
| `complete_five_assets` | 三份谱均可玩，且完整音源与原试听均验证通过。 |
| `bgm_preserved` / `preview_preserved` | 各原始解密音频块已经写出；验证失败仍计入。 |
| `bgm_validated` / `preview_validated` | ffprobe 找到可读取的音频流。`*_missing` 与 `*_failed` 分开。 |
| `images_decoded` / `extras_images_decoded` | 歌曲内图片与安装素材图片分别统计，二者不重叠。 |
| `marker_frame_images` / `theme_images_decoded` / `scimg_exported` | extras 中的对应分项；不包含在歌曲图片数中。 |
| `zero_note_charts` | 原谱成功解析，但没有 PLAY/LONG；详见 `zero-note-charts.json`。 |

例如某首三个原 seq 和双音频都存在，其中一个难度只有 TEMPO/MEASURE/HAKU/END，它仍可能完整导出，原始五件套成立；三谱可玩与可玩五件套不成立。头部声明 note 数和实际 PLAY/LONG 记录均保留，不用头部数字覆盖实际事件。

输出路径拒绝 `..`、绝对路径、反斜线、路径别名、符号链接成员、重复/大小写碰撞；单成员默认 64 MiB，原 JBT 128 MiB，单内层包解压预算 256 MiB，并限制成员数。超过限额记录失败或中止，不伪造完整结果。相同 ID 的多个原包需要调用者先分开备份来源，工具不选择一个覆盖另一个。

## 自测

```sh
python3 -m unittest discover -s tests -p 'test_mobile_full.py' -v
python3 -m unittest discover -s tests -v
```

测试全部自行生成：合成 seq、plist、CgBI、两种独立音频块、marker/theme ZIP、未知成员和故意损坏的资源。替代 key 使用独立参考加密器验证，并测试并发构造不污染歌曲 key；覆盖完整原包、300 Hz 未知记录/头/尾、元数据特殊类型、试听丢失与验证失败、空谱分类、图片失败和路径边界。大部分音频测试用受控 probe 返回值测试导出逻辑；若 PATH 中同时有 `ffmpeg`/`ffprobe`，另运行一项自行生成静音 AAC 的实际探测集成测试；缺依赖时该项明确 skip。不会用本地真实曲目充当公开 fixture。

## 格式研究来源与许可

歌曲 key 和 BFCodec 解码复用本仓库已有 `mobile/extract.py` / `mobile/tools/jbf.c`，CgBI 与谱面转换同样复用现有实现。

新增纹理与 `infov3` 的 key 及四字节封装事实参考 [Tatsh/dade 的 cipher.py](https://github.com/Tatsh/dade/blob/master/dade/jubeatplus/cipher.py) 和 [archives.py](https://github.com/Tatsh/dade/blob/master/dade/jubeatplus/archives.py)。该项目采用 [MIT 许可](https://github.com/Tatsh/dade/blob/master/LICENSE.txt)，版权标注为 “Copyright (c) 2026 dade authors”。这里只使用可核查的格式常量与布局事实，Python 调度和 C 入口在本仓库独立编写，没有复制、内嵌或安装 dade 实现及其依赖。没有引入存档、账户或其它运行状态 key，也没有采用来源不明的第三方解密代码。
