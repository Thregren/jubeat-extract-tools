> 如需用网页曲库确认名称、等级并统一游戏版本分类，先看 [离线谱面核对与音乐魔方](web-metadata.md)。参考网页仅提供元数据证据，歌曲、封面与音频仍取自本地。运行时与本基础导出器不查询网页。

# 把统一库导出为网页目录，并按版本打包

`workflows.browser_library` 是统一库的下游导出工具，输入仅为本地归一化结果。它不扫描原游戏目录、旧 MCZ 曲库、旧播放器缓存或外部歌曲表，不执行网络请求，不从完整音频裁切试听。

## 环境与输入要求

Python 3.10+，仅标准库；不需要 ffmpeg、Pillow 或编译器。输入必须由 `workflows.normalize_library` 完整生成，包含 `_reports/生成计划.json`、`生成结果.json`、`歌曲索引.json`、各版本 `来源索引.json` 及歌曲文件。`生成结果.json` 的状态必须是 `complete`，计划和索引须对应同一批歌曲。

音频应已是统一库的 PCM24 WAV，封面应已修正颜色为标准 PNG。导出原样保留字节，不重新解码/转码。未知曲名/等级保持未知，不借用旧曲库补齐。试听仅在源库存在 `preview.wav` 时收录；源库缺失的素材不生成空文件。

## 导出与打包

```bash
# 以下命令都在解包工具仓库根运行，示例路径需替换。
python3 -m workflows.browser_library \
  --library input/unified \
  --output output/browser \
  --archives output/version-zips \
  --verify-zips
```

`--output` 必须位于统一库以外，避免递归导出或修改源库。同盘优先硬链接 WAV、PNG、谱面和来源文件，跨盘回退到哈希校验后的复制。硬链接共享底层内容，因此输出歌曲文件应视为只读；若要编辑歌曲，先复制到独立文件。

| 参数 | 用途 |
|---|---|
| `--library` | 已完成的本地统一库，必填 |
| `--output` | 开放目录曲库根目录，必填 |
| `--archives` | 可选，按归档版本生成 ZIP64 的目录，建议放到浏览器根目录以外 |
| `--verify-zips` | 完整读回每个 ZIP，验证全部成员 CRC；建议正式打包时使用 |
| `--compress-audio` | 对 WAV/PNG/原谱也启用 ZIP 无损压缩；不改变解压后的媒体字节，但耗时更长 |
| `--workers` | 打包并发，默认 1，上限 4；机械盘建议 1 |
| `--limit` | 仅调试导出前若干条，结果状态为 sample，不要与正式结果混用 |

默认 JSON 使用 ZIP Deflate，WAV/PNG 等使用 Stored，减少大型本地库的打包耗时；不会有损压缩音频。ZIP64 可容纳超过 4GB 的版本包，解压软件必须支持 ZIP64。

重复导出时，相同硬链接或哈希一致的资源复用；同名资源内容不一致则拒绝覆盖。修改统一库后建议导出到新空目录。ZIP 成功后写入 `.zip.json`；重复打包对文件清单、大小、mtime 和 ZIP 大小/mtime 检查，满足条件时复用。失败的 `.partial` 不视为已完成包。元数据和媒体在打包期间必须保持不变。

## 补上网页和公共包

该导出器只生成数据目录及版本包。使用配套 [jubeat-chart-viewer](https://github.com/Thregren/jubeat-chart-viewer) **v0.6.28 或更新的目录模式**，在查看器仓库运行：

```bash
python3 tools/build_site.py --library ../output/browser
python3 tools/serve.py ../output/browser --port 8124
python3 tools/package_directory.py --library ../output/browser \
  --output ../output/version-zips/00_浏览器公共文件.zip
```

构建器只从明确指定的导出目录读歌曲。查看器按键动画属于程序 UI 素材，不从旧歌曲库获取歌曲数据。公共包带网页、按键动画、清单和 Python 启动脚本；它不重复打包歌曲。

安装时先将公共包解压到空目录，再将所需版本包解压到同一目录，保留 `data/`、`songs/` 层级。运行 `python3 启动查看器.py` 后打开提示地址；无需 MCZ 或 ffmpeg。未安装的版本索引 404 会被跳过，其他索引错误不会静默忽略。不要直接双击 `index.html`。新导出批次应使用新的安装目录，公共包与版本包要属于同一批次。

## 文件格式

```text
output/browser/
  data/library.json                    顶层版本索引清单
  data/versions/<版本>.json             该版本新增或改谱条目
  data/sources/<版本>.json              从统一库保留的来源索引（含被早版去重的观察记录）
  data/export.json                     总数、各版本条目数及导出状态
  songs/<版本>/<分类>/<身份>/
    song.json                          格式标识、素材路径、missing、未知元数据标志
    provenance.json                    原始来源证明
    bgm.wav / preview.wav / cover.png  仅在源库存在时提供
    charts/BSC.json                    开放JSON，现有播放器兼容结构
    charts/BSC.events.json             精确事件，原样保留各自tick_hz
    original/BSC.eve 或 BSC.seq         原始谱面，ADV/EXT同理
```

索引格式标识是 `jubeat-directory-v1`。`assets` 指定相对路径：`audio`、`preview`、`cover`、`thumb`、`chart:BSC`、`exact:BSC`；没有该素材就不声明路径。`resourceOnly: true` 表示没有可玩谱面。`missing` 保留统一库字段，例如 `bgm`、`preview`、`cover`、`chart:BSC`。`titleKnown`、`charts[].levelKnown` 避免把未知曲名/等级误写为可靠值。

`charts[].notes` 使用查看器连击口径（头数＋长押数，长押头尾各判一次），`heads` 则保留统一库的音符头数。零音符谱面若存在也会保留，不因为物量为 0 直接扔掉。

`BSC.json` 是统一库 `.mc` 内容的原样开放 JSON，移除的是 MCZ 容器依赖，底层播放数据结构仍为 `meta/time/note/extra`。当前兼容拍点存在原流程的换算量化；精确时间研究应使用旁边保留的 `events.json` 和原谱（原游戏一般300Hz；音乐魔方视频抄谱保留自己的tick_hz，分辨率不等于观测准确度），不应声称网页已经直接按原 tick 播放。

## 去重和适用版本

完全延续统一库归属，不在这里重新匹配歌曲：最早街机版本优先、移动版补充，仅可玩音符或时序变化另存。纯 HAKU/MEASURE 引导变化不另存。源文件归档版本不是绝对的实际发行首发日期，跨版本复用素材可查 `provenance.json`。

适用范围是当前归一化流程已支持的街机 jubeat、ripples、knit、copious APPEND、saucer、saucer fulfill、prop、Qubell、clan、T44/JAEPO、festo（官方与 Omnimix 分开）、Ave.、beyond the Ave.，以及 jubeat plus / jukebeat。移动版按备份资源包归档，不凭此声称覆盖所有商店时期或地区发行。

原批 18 个归档来源导出 2,645 条目；按网页版本分类整理并加入音乐魔方后为 17 个版本分类、2,666 条目，其中 2,457 个可玩、209 个仅资源；7,329 份谱面、2,664 份完整 WAV、2,435 份原试听、2,455 份本地 PNG。具体以自己的 `data/export.json` 为准。T44、合并来源、jukebeat 等零新增版本仍生成来源索引包，避免复制已经归属更早版本的歌曲。因此版本包首数不代表该版本完整曲库首数。

## 核验结果

版本 ZIP 中的 `PACKAGE.json` 列出每个文件的字节数与 SHA-256。包外 `.zip.json` 记录整包 SHA-256、大小、CRC 是否完整读回验证；`打包校验.json` 汇总各版本。可用任意支持 ZIP64 的解压工具进行 CRC 检查，再按清单校验成员 SHA-256。

导出时核对 WAV 长度、封面 SHA-256、计划与索引对应关系以及谱面结构。跨盘复制核对内容 SHA-256；同盘媒体硬链接天然同一文件。完整原音频来源的取证与解码校验属于上游归一化报告，导出器不重新伪造来源证明。

人工样例测试：

```bash
python3 -m unittest discover -s tests -p test_browser_library.py -v
```

测试覆盖未知等级/标题、缺谱、独立试听、PCM24 字节保留、300Hz 事件保留、原谱保留、零新增版本、ZIP64/CRC/SHA 清单、幂等复用和路径越界拒绝，不需要真实游戏文件。
