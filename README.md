# jubeat Extract Tools

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
```

`--only 00000001,00000002` 可筛选清单内的 ID。默认保持解码后的音频电平；`--target-rms-db -14.61` 可启用历史流程使用的归一化目标。

输出为 `<版本>/<清理后的曲名>__<ID>.mcz`。ID 后缀避免同名与大小写撞名；已有文件不覆盖，成功完成 ZIP 后才发布最终文件。详见 [IFS 格式与转换](docs/arcade-format.md)。

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_public_tree.py
```

测试会在临时目录合成密文、谱面、PNG 和归档。C 编译产物位于忽略的 `.build/`，不会被提交。历史真实素材验证与当前测试的区别见 [验证说明](docs/validation.md)。

时间转换采用 **300 Hz tick → 分段 BPM → 1/48 拍量化**。这适合 `.mc` / `.mcz` 转换，但不等同于保留原始 tick 的无损导出。同名曲目、不同地区或版本的谱面可能不同。

## 发布范围

整理时去除了原项目目录假设、部署脚本、私人站点地址、逐曲导入记录、旧会话和编译二进制。提交内容使用文件白名单与隐私检查控制，详见 [整理说明](docs/publication.md)。

代码沿用 [MIT License](LICENSE)。名称用于说明适配对象；游戏资源和商标不包含在该代码许可证的授权范围内。
