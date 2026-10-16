# jubeat Extract Tools

新增：[按网页谱面核对曲名/等级及版本归类](docs/web-metadata.md)，可追加音乐魔方独立本地目录；只参考网页 JSON，绝不采用网页封面或媒体，不替换本地谱面。

新增：[开放目录曲库与分版本 ZIP64](docs/browser-library.md)。直接从已完成的本地统一库生成 WAV 正曲、原试听、PNG、开放 JSON、精确 tick 事件和原谱，配合 jubeat-chart-viewer v0.6.28 使用；不读取旧 MCZ 曲库或旧元数据。


整理自两条已经实际使用过的资源解析流程：**jubeat plus / jukebeat 的 iOS 备份**，以及 **jubeat 街机 / PC 游戏数据中的歌曲 IFS**。提供方法说明、源码和可复现测试。

仓库只包含代码、文档和人工构造的示例。输入备份、游戏程序、歌曲、谱面、封面、解包结果和个人工作记录均不随仓库分发。工具在本地运行，没有网络上传功能。

## 先读懂原理

**[从文件字节到可读谱面](docs/walkthrough.md)**：用同一份人工谱面，逐步演示双层 ZIP、资源解密、IFS 偏移表、长押位段、变 BPM 积分和最终 JSON。每一步都能对应到源码，已知事实、还原约定和未解释字段分别说明。

两条路线的区别是：**iOS 要解开资源加密；本工具支持的歌曲 IFS 按分块表取数据，再解析谱面和解码音频。** 两者最终都把 300 Hz 时间刻度转换成拍值。

无需游戏文件即可运行带中间结果的演示：

```bash
python3 examples/walkthrough.py
# 没有 C 编译器时：python3 examples/walkthrough.py --skip-decrypt
```

更深入的细节：[BFCodec、密钥展开与 CgBI](docs/mobile-format.md) · [IFS 地址计算与 ADPCM 手算](docs/arcade-format.md) · [证据与验证范围](docs/validation.md)。

## 内容

| 路径 | 用途 |
|---|---|
| `mobile/extract.py` | `.adbk → .jbt → 解密 → 谱面 / 图片 / 音频`，可选 `.mcz` |
| `mobile/tools/jbf.c` | BFCodec 的 Blowfish 变种实现，首次运行在本机编译 |
| `mobile/tools/cgbi.py` | Apple CgBI PNG 转标准 PNG |
| `mobile/make_report.py` | 根据本机提取结果生成 Markdown 报告 |
| `arcade/import_ifs.py` | 歌曲 IFS 解析、OKI4S 音源解码与 `.mcz` 打包 |
| `docs/` | 两种格式、时间轴、验证范围和整理原则 |
| `examples/arcade-songs.example.json` | 人工编写的元数据结构示例，不含真实曲目 |
| `examples/walkthrough.py` | 打印人工样例的字节、时间轴、像素、采样与 JSON，并核对结果 |
| `tests/` | 在临时目录生成测试资源，无需提供游戏文件 |

## 环境

- Python **3.10 或更高版本**，Python 部分只使用标准库。
- macOS 或 Linux；移动版工具需要 `cc`、Clang 或 GCC 编译 C 源码。Windows 可通过 WSL 运行；原生 Windows 未验证。
- 仅在转换音频时需要 `ffmpeg`。移动版 `--transcode` 优先使用 `libvorbis`，缺少该编码器时回退到原生 `vorbis`；街机工具优先使用可选的 `oggenc`，否则使用 ffmpeg 原生 `vorbis` 编码器。
- 街机 `--charts-only` 不需要编译器或音频工具。

所有命令都从仓库根目录执行。`input/` 与 `output/` 是示例相对路径，可以替换成自己的目录；程序不会默认扫描个人下载目录。

## iOS 备份

把自行准备的备份放在 `input/`，保持文件名为 `jp.konami.jubeatplus.adbk` / `jp.konami.jukebeat.adbk`。也支持短文件名 `jubeatplus.adbk` / `jukebeat.adbk` 配合 `--packages`。

```bash
# 先提取少量曲目，确认适配
python3 mobile/extract.py --package-dir input --pack jubeatplus \
  --limit 3 --jobs 2 --out output/mobile-sample

# 提取两份备份；每次使用新的空输出目录
python3 mobile/extract.py --package-dir input --out output/mobile --scimg

# 单独指定备份，并额外打包 .mcz；默认保留 AAC 音频
python3 mobile/extract.py --packages input/jukebeat.adbk --pack jukebeat \
  --out output/jukebeat --mcz

# 为已生成的结果生成人读报告
python3 mobile/make_report.py --dir output/mobile
```

可选项：`--no-audio` 跳过音频输出；`--mcz --transcode` 将 `.mcz` 内音频转为 Ogg Vorbis；`--library input/existing-library` 对已有 `.mcz` 曲库做只读比较。比较按曲名进行，不能据此认定两份同名谱面一致。

输出按包与曲目 ID 分开，包含 `.mc`、标准 PNG、M4A、`info.json`、`library.json` 和统计报告。备份中的应用设置、账号与成绩数据库不在提取范围内。任何曲目失败会返回非零退出码；已生成的局部结果保留，请用新目录重跑。

详见 [移动版格式](docs/mobile-format.md)。

## 街机 / PC 歌曲 IFS

这里的 PC 指游戏数据来源。工具处理已取得的 `contents/data/ifs_pack/`，不负责获取或运行游戏，也不是通用 IFS 解包器。

1. 自备包含 `data/ifs_pack/d<前7位ID>/<8位ID>_msc.ifs` 的输入目录。
2. 参照示例编写 `input/songs.json`：填写曲目 ID、标题、艺人、版本目录及三个难度等级。示例是虚构数据，不能直接对应真实资源。
3. 若需要封面，自备以曲目 ID 命名的 PNG 目录。

```bash
# 校验元数据及三难度谱面，不产生输出
python3 arcade/import_ifs.py --songs input/songs.json \
  --extract input/contents --out output/arcade --dry-run

# 只导出谱面，无需 ffmpeg；.mcz 内不包含无效的音频或封面引用
python3 arcade/import_ifs.py --songs input/songs.json \
  --extract input/contents --out output/arcade --charts-only

# 包含音频与自备封面
python3 arcade/import_ifs.py --songs input/songs.json \
  --extract input/contents --covers input/covers --out output/arcade-audio

# 同时保留游戏自带的试听音频（独立的 IFS block 1）
python3 arcade/import_ifs.py --songs input/songs.json \
  --extract input/contents --out output/arcade-preview --with-preview
```

`--with-preview` 将非空试听块解码并写入 `.mcz` 的 `0/preview.ogg`，不从 BGM 裁切。
试听时长依实际区块而定。空块不写试听文件；非空但无法解码的块会使该曲失败，
不会发布不完整的 `.mcz`。此选项不能与 `--charts-only` 合用。
试听是附加资源，谱面的播放音源引用仍为 `bgm.ogg`；播放器是否使用试听由其自行决定。

`--only 00000001,00000002` 可筛选清单内的 ID。默认保持解码后的音频电平；`--target-rms-db -14.61` 可启用历史流程使用的归一化目标。

输出为 `<版本>/<清理后的曲名>__<ID>.mcz`。ID 后缀避免同名与大小写撞名；已有文件不覆盖，成功完成 ZIP 后才发布最终文件。详见 [IFS 格式与转换](docs/arcade-format.md)。

## 全资源与额外导出工具

新增工具保留原始文件与精确事件，不局限于 MCZ。完整中文文档和逐版范围：

| 入口 | 导出内容 | 详细中文说明 |
|---|---|---|
| `python3 -m legacy` | 初代/ripples/knit 的 PAK、双哈希恢复、历史尾部校验、早期分层资源 | [早期 PAK 与分层导出](docs/legacy-assets.md) |
| `python3 -m assets` | OKI4S WAV、TEXP/TCER、TDXT/GFGL PNG、VA3/S3P、原始 IFS、EVE | [单文件资产工具](docs/asset-tools.md) |
| `python3 -m workflows.song_ifs` 等 | 完整命名 IFS、原双音频、三谱、元数据、UI、封面、徽章、官方/mod 分层 | [完整批处理和更新合并](docs/full-exports.md) |
| `python3 -m workflows.t44` | T44 的 S3V/FLAC、S3P、谱面、IFS 与视频 | [T44 用法](docs/full-exports.md#t44-测试数据) |
| `python3 -m mobile.full_extract` | plus/jukebeat 原 JBT/seq/AAC/试听、全部图片、marker、主题、scImg | [移动全资源](docs/mobile-full-assets.md) |
| `python3 -m analysis.compare_versions` 等 | 本次 01–17 批量布局的精确事件比较、特色例证与完整性汇总 | [分析与总表](docs/full-exports.md#跨版本分析与总表) |
| `python3 -m workflows.normalize_library` | 在已解包结果上生成 WAV / PNG / MC＋精确事件统一库，按首次观察和玩法改谱归档 | [统一库、去重与适用范围](docs/normalized-library.md) |

先安装可选依赖：`python3 -m pip install -r requirements-assets.txt`。原有标准库命令不因此强制安装图像/IFS 依赖。C 加速按需在本机编译，不分发动态库；无编译器时 OKI4S/TEXBIN 有 Python 回退。

[本次额外工具功能对照](docs/tool-map.md)覆盖所有新增导出能力。

统一库工具保持原采样率、声道和电平，将完整歌曲与原独立试听转为 PCM24 WAV。默认按命名空间和 ID 保守去重；跨平台或跨 ID 合并需要显式提供并重新验证身份关系。它读取既有批量导出的 manifest 与精确比较报告，不直接解开任意游戏目录。原始 EVE/SEQ 和精确 300Hz 事件始终保留，详见[中文使用说明](docs/normalized-library.md)。

**[使用版本范围表](docs/version-support.md)** 列出初代至 beyond、T44、两种 iOS 备份的实际验证构建与限制。版本目录名不是自动鉴定结果；格式支持不等于任意地区/任意修订均适用。原试听独立保留，缺失不裁切补造；原 EVE/seq 和 300Hz 整数事件优先于量化 MC。占位、仅 BGM、零音符谱、只有封面、mod 与真实转换错误分别统计。第三方参考及许可证见 [来源说明](docs/third-party.md)。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_public_tree.py
```

测试会在临时目录合成密文、谱面、PNG 和归档。C 编译产物位于忽略的 `.build/`，不会被提交。历史真实素材验证与当前测试的区别见 [验证说明](docs/validation.md)。

时间转换采用 **300 Hz tick → 分段 BPM → 1/48 拍量化**。这适合 `.mc` / `.mcz` 转换，但不等同于保留原始 tick 的无损导出。同名曲目、不同地区或版本的谱面可能不同。

同一 tick 的 TEMPO 按原文件顺序取最后一条；旧转换器按数值排序可能造成持续时序漂移。已有 MC 升级与缓存重建说明见[统一库文档](docs/normalized-library.md)。街机 EVE、移动 SEQ 均已加入相反事件顺序的回归测试；原始谱面、精确事件和音频不受影响。

## 发布范围

整理时去除了原项目目录假设、部署脚本、私人站点地址、逐曲导入记录、旧会话和编译二进制。提交内容使用文件白名单与隐私检查控制，详见 [整理说明](docs/publication.md)。

代码沿用 [MIT License](LICENSE)。名称用于说明适配对象；游戏资源和商标不包含在该代码许可证的授权范围内。
