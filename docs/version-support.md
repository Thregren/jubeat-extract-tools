# 使用版本范围与验证口径

支持按**文件结构与签名**判定，不能仅靠文件后缀或目录名。下表记录本次实际验证的包；不代表已取得某版本全部地区、所有修订或完整配信曲库。构建代码只用于说明验证范围，仓库不分发输入文件、真实曲目清单或解包结果。

| 编号 / 内容 | 已验证构建或证据 | 推荐工具与范围 |
|---|---|---|
| 01 jubeat 初代 | H44 / 2008120500 | `legacy`：PAK、命名恢复、分层原资源；`assets`：OKI4S、TEXP/TCER |
| 02 ripples | I44，具体 soft ID 未确认 | `legacy`：已验证 PAK 样式；不把 PE 时间当发行版本号 |
| 03 knit / APPEND | J44 / 2011011400 | `legacy`：基础层及 APPEND，历史尾部修复仅在校验匹配时使用 |
| 04 copious APPEND | K44 / 2012072301 | `legacy` 的原始 IFS/层资源；命名歌曲 IFS、OKI4S、TEXP |
| 05 saucer | L44 / 2014012802 | `workflows.song_ifs` + 图集/VA3；无 LONG 的已验证输入 |
| 06 saucer fulfill | L44 / 2014111800 | 同上，课程/界面等额外资源；无 LONG 的已验证输入 |
| 07 prop | L44 / 2016031700 | 命名 IFS、LONG、Step Up 素材、GFGL 单 TEX、VA3 |
| 08 Qubell | L44 / 2017062001 | 命名 IFS、LONG、方块/digdig 素材、GFGL、VA3 |
| 09 clan | L44 / 2018070901 | 命名 IFS、LONG、城市素材、GFGL、VA3 |
| 10 T44 JAEPO 测试 | T44 / 2020020180；输入另有后期重打包证据 | `workflows.t44`：EVE、直接 ASF/S3V、S3P0、图形 IFS、视频；不能认证为未修改的 2020 原包 |
| 11 festo | L44 / 2022052400；输入含 Omnimix | 命名 IFS、图集、VA3、GFGL；官方与 mod 各层单独导出 |
| 12 Ave. | L44 / 2023080800 | 命名 IFS、图集、VA3、GFGL；沿用旧前缀不算本代独有证明 |
| 13 beyond the Ave. 基础 | L44 / 2025020500 | 同上；用于同系列更新合并的基础层 |
| 14 beyond 增量 | release_code 2026072200，针对 2025020500 | 增量单独导出；只有变更资源，不能当完整独立曲库 |
| 15 合并视图 | 13 + 14 的派生结果 | `workflows.merge_update`；不算新增正式发行世代 |
| 16 jubeat plus | `.adbk` / JBT 结构；App 精确版本未知 | `mobile.full_extract`：原双音频、全部 seq/图片/metadata、marker/theme/scImg |
| 17 jukebeat | `.adbk` / JBT 结构；App 精确版本未知 | 同上；英语标题变体、marker，和 plus 分包保留 |

## 格式边界

- 固定五块 `arcade/import_ifs.py` 是原 MCZ 路线；新增命名 IFS 工作流不依赖固定五块顺序。不要把通用 UI IFS 喂给固定五块转换器。
- 原始 EVE/seq 与 300Hz 整数 tick 事件是保真层；MC/MCZ 采用拍值转换，可能量化。
- OKI4S 支持的单/双声道、VA3W 的 GDXH/版本字节、GFGL 的 GL enum、S3P0 包装均有明确检查；未知变体报错，不能自动扩展为整个 Konami 格式族。
- 单字节占位、BGM-only、零音符原谱、只有封面和 metadata-only 独立记录，不能拼成“完整歌曲”。缺少原试听时不补造，不裁切 BGM。
- 原包中未列入当前 music_info 的实际资源仍保留。缺席不证明删曲，源空谱不证明提取失败。
- iOS 备份资源结构得到验证，不代表已验证所有历史 App 版本、Android 版本或商店下载机制。内部配置计数不是 App 版本号。
- 不承诺加密磁盘镜像、未解密文件系统、未见过的 DRM、所有芯片/纹理枚举、后续更新或未提供的附件。

更详细的早期和移动版命令分别见 [早期 PAK](legacy-assets.md)、[移动全资源](mobile-full-assets.md)。测试只随库提供人工合成夹具；真实素材验证记录描述能力范围，不把素材本身打包进测试。
