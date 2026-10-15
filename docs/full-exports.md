# 完整资源批处理

这些命令把本次逐版解包中使用的模块整理为可复用工作流。需要先解压自己的外层压缩包：例如用系统 ZIP 工具、7-Zip 或 bsdtar。仓库不附带解压器二进制。分卷文件应保留同目录并从首卷解压；本工具不假定压缩包名就是游戏版本。

## 歌曲 IFS：命名成员、双音频、原谱、元数据

```bash
# 第一次：导入预解压目录，给本地导出起一个版本目录名
python3 -m workflows.song_ifs --root output --source input/package Ave

# 依次导出额外纹理、VA3 音效、独立 TEX 图片
python3 -m workflows.textures --root output Ave
python3 -m workflows.soundbanks --root output Ave
python3 -m workflows.individual --root output Ave

# 最后按实际资源分类、核验封面来源及导出文件
python3 -m workflows.classify --root output Ave
python3 -m workflows.covers --root output Ave
python3 -m workflows.audit --root output Ave
```

`--source` 可以是包含 `contents/data` 的包目录，也可以是包含 `ifs_pack`、`d3`、`se` 的 data 目录。要识别 `data_mods/<层名>`，传入包含这层目录的共同父目录。名称 `Ave` 只是用户选择的输出目录名，不会强制为文件贴发行版本标签。每次先从少量输入测试，核对输出中的实际构建证据。

第一次会把输入树以硬链接保存为 `output/Ave/raw`；跨文件系统时回退复制。输入、输出不能嵌套，输入不接受符号链接。硬链接文件共享字节，请勿原地编辑原始素材；需要修改时先复制。后续命令省略 `--source`。所有工具都只处理本地文件。

歌曲工具递归寻找 `*_msc.ifs`，依完整内部成员名称判断角色，兼容 `bas/adv/ext` 以及带 8 位 ID 的 `b/a/e.eve`，不假设固定 block 顺序。原 IFS、完整成员、原 EVE、精确 300 Hz 事件、WAV、额外 MC 全保留；试听从独立原 idx 成员取得。未知成员仍保存在 `original/`。音乐元数据从实际 `music_info` XML 解码，原 XML/二进制输入不丢弃。

官方层位于 `songs/<ID>`；`data_mods/omnimix` 等来源层位于 `mods/mod_omnimix/songs/<ID>`，各有元数据，互不覆盖。封面按纹理内部 `BNR_BIG_ID`/`BNR_ID` 关联；完整图集及其他候选仍保留于 extras，封面来源索引记录候选与实际采用文件的哈希，封面存在不能冒充歌曲资源齐全。

## 完成、占位与重跑

每一阶段有单独 JSON 索引。`stats.finished=true` 只代表歌曲阶段已跑完；还需要纹理、音库、独立 TEX、封面审计以及错误索引确认，不能据此宣称整版所有内容已完成。

- `complete`：本地 BGM、原试听、BSC/ADV/EXT 三份可玩谱齐备。
- `bgm_only`：源容器只有 BGM，不能据此宣称丢失了另外四块。
- `raw_only`：原始内容保留，但没有可玩谱和双音频；严格识别的单字节 dummy 与其他未知内容分别记录。
- `partial`：有部分资源，逐项列缺少的导出组成；源空谱和源缺席不自动视为包损坏。
- `conversion_error`：真实解析/转换错误，需看对应错误索引。

批处理会复用已有 `info.json`、图像 manifest 和音频缓存；复用是为恢复同一份不可变输入，**不是内容变化检测器**。更换原始数据后请使用新输出目录，不要在旧 raw 上直接覆盖重跑。MC 是辅助输出，原谱与精确事件才是无量化保留。

如果要核对旧流程生成的 MC 是否错误地按固定块顺序匹配难度，可额外执行：

```bash
python3 -m workflows.repair_mc --root output Ave
```

它按 manifest 的原 EVE SHA256 找到命名成员、重新计算时间轴和音符，只有派生 MC 不一致时才原子替换；原谱、精确事件、音频和元数据不变。`mc_named_member_audit.json` 记录旧/新哈希和改动。此命令面向命名 IFS 工作流的 manifest，不用于任意来源 MC、MCZ 或缺失原谱的目录。

## T44 测试数据

```bash
python3 -m workflows.t44 --input input/t44/data --out output/T44
```

输入需包含 `music/music_info.xml`、歌曲子目录，以及同一 data 树中的 IFS/S3P。需要 PATH 中的 ffmpeg、ffprobe，以及 Pillow、ifstools。输入和输出不能嵌套，输出必须不存在；拒绝输入符号链接和特殊文件。首先将**整个输入树**保存为 `output/T44/raw`，优先硬链接，跨文件系统时复制；原 IFS、完整 S3P、包装头、未知文件、原视频都保留。硬链接共享字节，不能原地编辑输入或 raw；需要改动时另作副本。

歌曲目录另存原文件、完整 EVE、精确事件 JSON、MC、原 S3V 和解码 FLAC，以及大小封面。完整音源和试听分别从原 `bgm.s3v` / `idx.s3v` 转换，绝不从 BGM 裁切。精确事件 JSON 保留每行的全部字段、原命令值、未知命令和未解析行；`HAKU/MEASURE/END` 的原值不重写为零。原 EVE 字节和 SHA256 是编码、换行及字段拼写的权威证据；MC 使用 1/48 拍转换，是辅助副本，不能代替原事件。坏行或转换失败进入错误清单，仍保留原文件及已生成的精确事件。

图形 IFS 转换前检查内部路径和碰撞，嵌套 IFS 同样检查；原容器一直留在 raw。S3P 调用通用 S3P0 解析器，逐成员保留完整 S3V0 包装、原 ASF 载荷及可解码的 FLAC。某个成员或音频解码失败不会跳过同库其余成员；完整原库仍在 raw。视频预览保留原 WMV，另生成无音轨 H.264 预览；探测或转换失败也会落入最终 `errors.json`。

若有 `__metadata.metatxt`，只在 raw 内校验其声明的文件、大小与 SHA1；拒绝 `..` 等越界路径。缺文件、哈希不匹配或声明格式错误均计错。`stats.finished=true` 只表示本次队列处理结束；`status=complete` 表示处理未记录错误，不能据此宣称每个目录都有三份可玩谱和双音频。没有曲目元数据的目录以 `special` 单独标记，原内容照常保留，不能算作正式歌曲身份的证据。

退出码 0 表示本次处理无错误；2 表示已导出但存在明确记录的解析、校验或转换错误；1 表示输入、依赖或输出路径等致命错误。最终 `raw_manifest.json`、`manifest.json`、`errors.json`、`stats.json` 及 extras 各项索引应一起检查。当前只验证了 T44 JAEPO 数据结构；不把测试包等同于正式 festo，不由后缀推及所有 S3P/S3V 或全部 T44 variant，也不认证所给数据是未经后期重打包的原始 2020 包。

## 基础包与增量合并

```bash
python3 -m workflows.merge_update \
  --base output/Base --patch output/Update --out output/Latest
python3 -m workflows.classify --root output Latest
python3 -m workflows.covers --root output Latest
python3 -m workflows.audit --root output Latest
```

Base、Update 必须先分别完成本工作流导出。支持 `raw/contents/data`、`raw/data` 或外包一层目录的对应 data 树；存在多个候选会拒绝猜测。输出必须是新的独立目录。当前验证场景是 beyond the Ave. 基础包加同系列增量；它不是通用跨代升级器，不合并 Omnimix。

增量按原文件路径覆盖；同 ID 新 IFS（包括 dummy）替换整个旧歌曲导出，避免残留旧谱面、音频。更新的纹理/音库/IFS/单 TEX 会先撤销对应旧派生资源，即使新文件只是占位且没有新图片；封面再从实际存活候选重新关联。基础元数据与更新元数据独立保留，更新未见某 ID 不等于官方删曲。只生成本地派生视图，不改动基础包、增量包和原始压缩包。

## 跨版本分析与总表

```bash
python3 -m analysis.compare_versions --root output
python3 -m analysis.features --root output
python3 -m analysis.summary --root output
```

`analysis` 是本次 **01–17 编号批量布局的兼容分析器**，不是对任意来源报告都能自动识别的工具。目录编号映射见 [版本表](version-support.md)；自定义版本名的单版导出直接看该版 manifest/stats。比较原 SHA、完整 300Hz 事件 SHA、仅排除 HAKU/MEASURE 的 gameplay SHA；不打开音频，不量化时间，不按同名曲推定谱面相同。移动 9 位 ID 与街机 8 位 ID 保持独立，只另列精确事件相同的候选。

这些分析脚本兼容本次历史批量结果的分阶段 schema 和完成标记，并非新 CLI 的统一总调度器。可移植 `legacy`、各独立工作流的输出结构未必满足历史汇总的全部字段；直接混用可能得到 partial，单版准确统计应读取该工具生成的 stats。特色选择器的默认目录是 `11_festo_2022052400`、`12_Ave_2023080800`、`13_beyond_the_Ave_2025020500`、`14_beyond_the_Ave_2026072200_update`；它不会从任意自定义名字猜对应世代。

缺 manifest、未完成阶段、哈希不吻合等会标成 partial/非零退出；不要把部分比较当完整结论。特色选择器从完成索引挑选实际存在的 PNG/WAV，只是本包例证，不证明该资源首次出现或本代独有。汇总预设要求 17 个目录全部完成，不适合拿一个自定义目录冒充全覆盖。
