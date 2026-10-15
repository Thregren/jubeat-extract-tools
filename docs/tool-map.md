# 本次额外工具的功能对照

一次性本机路径、任务日志、缓存和资源清单没有直接复制进仓库；对应导出能力整理到以下通用入口。原工具使用的完整算法、严格边界与必要 C 源码保留，编译产物在使用者本机生成。

| 本次新增能力 | 发布位置 | 输入/输出要点 |
|---|---|---|
| 早期 PAK 枚举、MD5、CRC 双哈希、特殊尾部、候选命名 | `legacy/pkfs.py`、`pkfs.c`、`python -m legacy` | PAK 和用户候选名表；按校验结果恢复，不附真实名称清单 |
| 早期基础/APPEND 选择、附加 EVE、系统音频 | `legacy/export.py` | 显式层顺序；全部层原字节保留，后层按角色选择 |
| 原始 IFS 命名成员与安全输出 | `assets/ifs.py` | 原成员在 original，生成索引与其分开 |
| 全量歌曲容器、metadata、官方/mod | `workflows/song_ifs.py` | `--root` / `--source` / 自定义版本名 |
| 原 BGM 与原试听 PCM、C 加速/Python 回退 | `assets/oki4s.py`、`oki4s.c` | BMP/OKI4S→PCM16 WAV；无裁切、归一或重采样 |
| 精确 EVE 字段、命名 EVE→MC | `assets/eve.py`、`python -m assets eve` | 原 EVE+300Hz全事件；MC 是量化附本 |
| 旧 MC 难度顺序核对修复 | `workflows/repair_mc.py` | 原谱SHA核对，仅替换不一致的派生 MC |
| TEXP/PMAN/TCER、图集裁剪、LZSS4078 | `assets/texbin.py`、`lzss.c` | PNG、TDXT、逐成员 manifest |
| GFGL/TDXT 独立徽章纹理 | `assets/individual_tex.py` | 严格 GL 枚举和长度，源行序 |
| VA3W/GDXH 音库 | `assets/va3.py`、`workflows/soundbanks.py` | 每条 WAV 与原 vol/pan 等元数据，占位单列 |
| S3P0/S3V0/ASF 音库 | `assets/s3p.py` | 包装与原ASF均保留，可另解FLAC |
| T44 原数据、谱面、音频、UI、视频、嵌入hash | `workflows/t44.py` | 明确 input/out，完整 raw 快照，失败逐阶段记录 |
| 封面候选来源与已选图校验 | `workflows/textures.py`、`covers.py` | 候选/已选SHA分开，完整图集保留 |
| 资源分类与导出审计 | `workflows/classify.py`、`audit.py` | 完整/仅BGM/dummy/partial/真实错误分别处理 |
| 基础+同系列增量、占位替换撤销、重新关联封面 | `workflows/merge_update.py` | base/patch/out 参数；源层不改动 |
| plus/jukebeat 全JBT、原双AAC、所有图片/seq/metadata | `mobile/full_extract.py` | 明文index独立保留、精确记录和空谱分类 |
| marker备用key、主题/scImg/标题变体、独立编译 | `mobile/tools/marker.c` 与 full_extract | 图片与文本成员区别处理，未知成员也保留 |
| 跨版本事件比较、特色例证、历史布局汇总 | `analysis/` | 仅读本地结果；01–17历史布局兼容范围详完整批处理文档 |

通用外层归档解压交给使用者安装的 7-Zip/系统工具；外层文件清单扫描、某次任务等待/排队脚本、真实曲库统计快照和调查中间件不是公开导出器，不包含在发布内容中。每个入口的命令、依赖、错误码和版本限制见相应中文文档及[逐版范围表](version-support.md)。

## 统一库转浏览器目录

`python3 -m workflows.browser_library`：只接受完整统一库，导出无 MCZ 目录并按版本打 ZIP64。参数、安装方法和适用版本见 [使用说明](browser-library.md)。
