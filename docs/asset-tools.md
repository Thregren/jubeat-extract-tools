# 单文件资产导出：中文使用说明

这组工具处理已经在本地取得的资源文件。不会获取游戏、运行游戏程序或上传素材。原有 `mobile/extract.py` 和 `arcade/import_ifs.py` 仍可单独使用；本页是补充的原始资源导出接口。

## 安装

在仓库根目录使用 Python 3.10+：

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-assets.txt
python3 -m assets --help
```

macOS 是实际批处理验证平台。Linux 提供共享库编译分支，但未承诺所有发行版和硬件组合均已实测；Windows 请使用 WSL。OKI4S、TEXBIN 的 C 加速仅在本机编译源码，缓存到忽略的 `.build/`；没有编译器时自动使用 Python 回退。可通过 `CC` 指定编译器、`JUBEAT_BUILD_DIR` 指定可写缓存目录。S3P→FLAC 和 T44 工作流额外需要 PATH 中的 `ffmpeg`、`ffprobe`。

下面所有输入名称和目录都是人工示例。单文件命令要求输出路径尚不存在，避免覆盖结果。返回 0 表示该命令成功；解析失败或部分成员失败为非零，局部结果及错误索引保留。

## 音频：Konami BMP/OKI4S

```bash
python3 -m assets bmp input/bgm.bin output/bgm.wav
python3 -m assets bmp input/idx.bin output/preview.wav
```

这里的 BMP 是以 `BMP\0` 开头的游戏音源，不是 Windows 位图。支持已验证的单声道/双声道 OKI4S：单声道依次高、低 nibble；双声道每字节高 nibble 为左、低 nibble 为右。输出 PCM16 WAV，维持源采样率和声道，不做音量归一、重采样或有损再编码。保留声明帧数、循环点及按载荷修正帧数的标记；空载荷和未知声道直接报错。

试听必须来自原 `idx`/`index`/`preview` 成员，工具不会用 BGM 裁切冒充。街机 MCZ 如需原试听，请用已有 `arcade/import_ifs.py --with-preview`。WAV 是压缩游戏音源的 PCM 重建，不等于恢复录音母带。

## TEXP/PXET：整图、TCER 裁剪、原始 TDXT

```bash
python3 -m assets texbin input/tex_example.bin output/tex_example
python3 -m assets texbin input/tex_example.bin output/png-only --no-tdxt
```

支持 `TEXP/PXET`、`PMAN/NAMP` 名表、`TCER/RECT` 裁剪表，以及字典起点为 4078 的 LZSS。默认输出整张 PNG、每个具名裁剪 PNG、解压后的 TDXT 和 `texture_manifest.json`。索引记录源 SHA256、内部名、矩形、尺寸、压缩区偏移、导出 SHA256、逐成员错误。未知像素格式不会自动当作 RGBA。

TDXT 支持范围由实际格式头决定：已用 jubeat 数据验证 RGBA、DXT5 等，另有参考实现支持的像素格式；不能据此承诺其他游戏全部纹理变体。小端 DXT1/3/5 使用 Pillow BCn 路径，其他已知格式走参考解码器。损坏偏移、越界矩形、超预算解压会报错。

**TEXP 中的 `fmt=0x10` 使用原始 RGBA 字节顺序。** 此规则仅在 `extract_texbin` 的包装层生效：直接以 RGBA 读取 TDXT 像素载荷，再由同一张整图生成 TCER 裁剪；不交换 alpha、不翻转行，也不改写保留的 TDXT。它不修改独立 TDXT 的通用 BGRA 默认解释，不使用全局 `invert_channels=True`，因此不会连带翻转 `0x0e` RGB、565、DXT 或 GFGL 图片。

依据是 [ifs_layeredfs 的 `ImageEntryParsed::tex_to_argb8888`](https://github.com/mon/ifs_layeredfs/blob/d679d0fc3bb6279e5d599f4c027917db3b6efbf7/src/texbin.cpp#L765-L805)：虽然历史枚举将 `0x10` 命名为 BGRA，该分支实际直接返回像素载荷；PNG 路径将该结果作为 RGBA 使用，其他像素格式也明确输出 RGBA。不能仅凭枚举名称推断这里应交换红蓝。合成测试使用 R/B 不相等且 alpha 非 255 的像素，同时检查整图、大小端容器的 TCER 裁剪、未改写的原始 TDXT，以及其他格式/独立 TDXT 未受影响。此解释针对该 TEXP 导出路径，不承诺所有游戏的独立 TDXT 都具有同样通道顺序。

新导出的 `texture_manifest.json` 和 API 返回值带有 `pixel_decode_policy: "texp-0x10-rgba-v1"`，表示采用上述仅 `0x10` 的 RGBA 规则。旧版此路径曾把 `0x10` 交给通用 BGRA 解码器，可能产生红蓝颠倒的 PNG；旧导出没有该策略标记时，应从原始 TEXP **重新输出到新目录**，再更新派生图片及哈希。更新代码不会自动修改已有 PNG、图集裁剪或缓存；不要对 DXT、移动版 PNG、GFGL 或所有图片统一交换通道。

## 单张 GFGL/TDXT 图片

```bash
python3 -m assets texture input/emblem.tex output/emblem.png
```

识别 `GFGLTI2D20140326` 和 `TDXT/TXDT`。GFGL 仅支持已核验的二维/矩形纹理 target、level=0、border=0、UNSIGNED_BYTE，以及 RGB/RGBA/单通道格式；实际大批量证据主要为 RGBA。完整检查载荷长度，不猜测未知 GL enum。维持源行顺序，不擅自上下翻转。

## VA3W 系统音效库

```bash
python3 -m assets va3 input/system.va3 output/system
```

只支持已验证的版本字节 `(1,0,0,2)`、`GDXH` 分组和 16 位单/双声道条目。每条输出 WAV；`va3_manifest.json` 保留条目 ID、名称、offset、压缩长度、采样率、音量、声像、flags 和未解释字段。音量、声像只记入索引，不应用到 PCM，零长度条目单列为 empty。单字节 `00` 不是 VA3W；批处理 `workflows.soundbanks` 会将其单列为占位，单文件命令则拒绝格式不符。

## S3P0/S3V0 音效库

```bash
python3 -m assets s3p input/system.s3p output/system
python3 -m assets s3p input/system.s3p output/system-flac --decode
```

保留每条完整 S3V0 包装及其原 ASF 载荷；`--decode` 另经 ffmpeg 输出 FLAC。检查表边界、成员 signature 与 header/payload 长度。已验证范围是 T44 测试数据中的 S3P0→S3V0→ASF；不声称覆盖所有后缀为 s3p/s3v 的文件。歌曲中直接承载 ASF 的 `.s3v` 由 T44 工作流处理。FLAC 解码警告会记入索引。

## 原始 IFS 命名成员与独立 EVE

```bash
python3 -m assets ifs input/example.ifs output/example-ifs
python3 -m assets eve input/ext.eve output/chart --code EXT
```

通用 IFS 接口依赖 ifstools，保留完整成员字节及 manifest，检查绝对路径、父目录跳转、盘符和输出范围；不把任意 IFS 假定为固定五块歌曲格式，也不会自动递归执行嵌套文件。EVE 命令同时保留原谱、300Hz 事件 JSON 和额外 MC；MC 采用 1/48 拍量化，不能替代原 EVE/tick 数据。

批量歌曲、元数据、封面关联及分层请继续阅读 [完整批处理](full-exports.md)；逐版适配证据及限制见 [版本范围表](version-support.md)。
