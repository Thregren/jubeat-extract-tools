# 按网页谱面核对元数据、整理版本目录并加入独立版本

这是浏览器目录导出之后的可选离线整理流程。只有明确希望参考网页曲库时运行。它只用网页 JSON 索引和 JSON 谱面确认曲名、作曲、对应难度等级和版本分类；不采用网页封面/缩略图、音频、试听或谱面替换本地文件。网页地址由使用者显式指定，运行中的本地查看器不联网补查。

## 依据与限制

物量相同仅产生候选，不作为最终认曲依据。工具将拍点按变 BPM 时间轴还原，核对全部可玩音符的键位、相对时刻、长押时长和尾部方向。BAS 与 BSC 只统一难度代号，不改变谱面。多难度条目至少有两个足够长的难度匹配；单难度输入要求该难度完整匹配，整条总头数至少 96。稀疏测试谱和空谱不会单独作为认曲证据。

兼容 JSON 比较容许整体起点平移及最多 20ms 的拍点换算误差。这个容差只用于元数据，不会删除或替换任何本地谱面，也不据此合并不同原生 tick 的条目。完整匹配才采纳全部等级；部分匹配只采纳实际匹配难度的等级。候选标题或等级冲突无法确定时不套用。最终证据包含网页条目 ID、双方 JSON 的 SHA-256、每个难度的误差和时差，不能把“同名”当作已经匹配。

优先选择原归档对应的网页版本；否则按街机优先的版本顺序选择匹配来源。版本名采用参考曲库已有分类，例如 `jubeat`、`jubeat-ripples-append`、`jubeat-copious`、`jubeat-festo`、`jubeat-beyond-ave`、`jubeat-plus`、`jukebeat`、`音乐魔方`。多份更新、合并包和 Omnimix 可以归入同一个游戏版本，但原始归档版本、来源层和改谱身份仍保留；来源索引合并为带 `originalVersion` 的列表，不丢弃原始对应关系。

## 可复现命令

先将参考网页的 `data/library.json` 下载为 `input/web-library.json`。以下命令从工具仓库根执行，所有路径均为示例。

```bash
# 只下载 data/charts/ 下的 JSON，默认并发4，有缓存可续跑；无媒体请求。
python3 -m workflows.web_metadata --index input/web-library.json \
  --base https://your-reference.example --cache output/web-chart-cache

# 基于已导出的目录与缓存生成证据计划，不修改源统一库。
python3 -m workflows.curate_library --directory output/browser \
  --web-index input/web-library.json --cache output/web-chart-cache \
  --plan output/curation-plan.json --extra input/music-cube

# 根据完整计划生成两个全新的目录，原输入保留。
python3 -m workflows.curate_library --apply \
  --unified input/unified --directory output/browser \
  --web-index input/web-library.json --cache output/web-chart-cache \
  --plan output/curation-plan.json --extra input/music-cube \
  --unified-out output/unified-curated --directory-out output/browser-curated
```

`--extra` 可省略。`--apply` 的输出必须互相独立且为空，或带有同一计划 SHA-256 的续跑标记。工具以硬链接保留媒体与原谱，兼容谱面 JSON 只更新元数据，使用原子替换，避免修改源硬链接。应用时重新验证本地匹配谱面 SHA-256；计划生成后源谱变化会拒绝套用。本地封面核对原统一库 SHA-256；没有任何网页封面复制路径。

Python 在 macOS 如提示 CA 证书缺失，应安装 Python 官方证书配置或将 `SSL_CERT_FILE` 指向系统 CA 文件；不要关闭 HTTPS 证书验证。工具没有跳过验证选项。

输出统一库的 `_reports/目录与元数据校正.json` 保存完整计划；原生成报告存入 `_reports/归档生成报告/`。新的计划、歌曲索引、生成结果与各版本 `来源索引.json` 对应新目录；原始游戏归档路径仍作为来源证据，不应被当作新的目标目录。

随后在查看器仓库对 `output/browser-curated` 运行 `tools/build_site.py --library ...`、`tools/package_directory.py`；按版本打包可在本工具仓库执行：

```bash
python3 -c 'from pathlib import Path; from workflows.browser_library import pack_versions; pack_versions(Path("output/browser-curated"), Path("output/version-zips"), verify=True)'
```

## 独立版本目录

`--extra` 接受具有 `version-index.json` 的独立目录，索引格式为 `jubeat-directory-v1`。歌曲资源路径形如 `songs/音乐魔方/C035 PUPA/bgm.wav`，实际输入文件位于 `input/music-cube/C035 PUPA/bgm.wav`。目录须包含 `song.json`、兼容 JSON、精确事件、来源证明及现有媒体。新增版本不参与街机/移动库的跨版本去重。

音乐魔方本批共 21 首 EXT、17,700 个音符，来自用户提供的视频复核抄谱目录。保留本地 WAV、PNG、原始抄谱数据和复核来源；没有 BSC/ADV 或独立试听时明确记录缺失，不从网页补谱，也不裁切正曲制作试听。其精确事件 `tick_hz` 与原游戏 300Hz 不同，必须保留各文件声明；容器能表示纳秒不等于视频拥有纳秒级准确度。实机封面裁图仍按原来源标注，不假称高清封面。

当前例库整理后为 17 个版本分类、2,457 个条目、7,329 份谱面、2,456 份完整 WAV、2,435 份原试听、2,454 份本地 PNG。2,384 条元数据有谱面匹配证据，其中 2,381 条完整、3 条部分；26 个可玩条目曲名、62 份等级仍未知。这些数字仅描述本批输入，不保证其他备份也相同。

合成测试：`python3 -m unittest discover -s tests -p test_web_metadata.py -v`，涵盖键位、时刻、长押方向、部分等级、源文件不可变、源谱变化拒绝套用及网页媒体路径拒绝。

## 移除特殊资源分类

最终例库已按分类移除 209 组 `特殊或不完整资源`，不再进入统一库、浏览器索引或最终版本包。正常条目的缺失素材仍如实记录。所有旧生成包均需重建，避免 ZIP 继续带有已删除条目。

先用 `python3 -m workflows.prune_library --unified output/unified-curated --directory output/browser-curated` 查看数量；相同命令追加 `--apply` 会实际删除该分类目录并更新索引、统计与删除记录。参数可以重复指定，用于一并处理自己生成的历史备份。该操作不按 `missing` 字段删除其他分类，也不会移除音乐魔方的正常 EXT。
