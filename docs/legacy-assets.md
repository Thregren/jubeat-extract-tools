# 早期 PAK / 命名 IFS 原始资源导出

`legacy` 用于保留旧版资源、恢复能够被校验的文件名、导出命名 IFS 的全部成员，再按显式资源层选择歌曲成员。它不运行游戏，也不判断启动环境或服务器兼容性。

## 已验证范围与不能据此声称的内容

| 范围 | 本次实际观察并验证的输入 | 工具处理方式 |
|---|---|---|
| 初代 jubeat（识别构建 `2008120500`） | 一份旧版 `packinfo.bin` / PAK 集合，575 条目全部匹配存储 MD5 | PKFS 表、verbatim / rolling XOR、候选路径双哈希、无文件名成员保留 |
| jubeat ripples | 一份 PAK 集合，800 条目全部匹配存储 MD5 | 同上；该输入的完整内部构建码及 APPEND 状态没有确认，不据压缩包名补全 |
| jubeat knit APPEND 资源层（基础标识 `2011011400`） | 一份独立 APPEND PAK，120 条目全部匹配存储 MD5；其中 7 个音频条目需要历史尾部行为 | 同上；保留 base / append，按成员优先级选择；不能据此认定所有 knit 发行包的 PAK 都相同 |
| knit 基础资源、copious APPEND（`2012072301`）早期 IFS / 散文件 | 实际导出过命名 IFS 成员、EVE、BGM 与独立试听 | 通过可选 `ifstools` 读取命名树，保存未转换的成员字节；不假设恰好五块，也不依 metadata 曲数过滤 |

knit 输入的基础标识日期与正式 APPEND 发布时期存在差别；这里确认的是另存的 APPEND 资源层，不据它断言特定游戏程序已合并并能启动。全程没有运行游戏。

表中数量是开发时既有输入的校验范围，不是附送文件清单或官方曲库规模。代码和测试不包含游戏文件、真实歌曲 ID 字典、标题库、教程 ID 证据、包密码或固定构建列表。未知发行版、其他 PKFS 表变体、加密 IFS / 程序、其他游戏以及所有未来更新均未据此验证。

PAK 解析、XOR 与路径 CRC 代码为本项目自行编写。没有复制第三方 BMS 脚本，也不要求安装 BMS。格式调查曾将公开 BMS 的字段描述作为线索；最终输出必须逐项通过原表 MD5，路径恢复还须通过两个原表哈希。IFS 的通用容器解析由外部 `ifstools` 提供，它的许可与依赖以该项目自身发布为准。

## 环境与依赖

在仓库根目录运行，使用 Python 3.10 或以上：

```sh
python3 -m legacy --help
python3 -m legacy pak --help
python3 -m legacy export --help
```

PAK 导出、路径哈希与散文件整理只需要 Python 标准库。默认 `--native auto` 会尝试使用系统 C 编译器，把 `legacy/pkfs.c` 编译到仓库根的 `.build/`；这个目录已被 Git 忽略。没有编译器时自动使用完整的 Python 实现，速度较慢；可用 `--native off` 禁止编译，或用 `--native required` 要求编译成功。C 源与 Python 路径都经过合成测试，C 不附带预编译库。实际原生自测在 macOS 完成；其他平台的原生构建尚未实测，可以先用 Python 路径。

IFS 解包可另外安装：

```sh
python3 -m pip install ifstools
```

可选的 `--decode-audio` 调用同仓库 `assets.oki4s.bmp_to_wav`，`--decode-textures` 调用 `assets.texbin.extract_texbin`。纹理转换需要 Pillow 及公共 assets 模块注明的可选依赖。这里不复制它们的实现，也不自行安装依赖。只保留原始资源时可以完全不用转换选项；`--skip-ifs` 可明确只保留 IFS 原文件。

外层 ZIP / RAR / 7z / 分卷包请先使用你自己的归档工具解到独立的输入目录；本 CLI 不处理归档密码、不把密码写进命令或日志。输入应是所需的资源目录，不需要包含游戏可执行文件。

## 1. PAK 解包与文件名恢复

假设 `input/pack/` 内有 `packinfo.bin` 和 `d000/pack0000.pak` 等分包：

```sh
python3 -m legacy pak \
  --input input/pack \
  --output output/pak \
  --native auto
```

没有候选路径也会导出每个可以校验的条目。未知名称保留为 `_unnamed/序号_crc32_crc16.扩展名`；扩展名只是有限的内容签名分类，不是原文件名恢复。

若你有从自己输入目录或自有静态调查获得的虚拟路径，可在 UTF-8 文本里一行一个精确路径，例如下列**虚构路径**：

```text
/data/example/resource.bin
/append/data/example/extra.bin
```

```sh
python3 -m legacy pak \
  --input input/pack \
  --output output/pak \
  --candidates input/candidate-paths.txt \
  --native off
```

`--candidates` 可以重复。不要加行尾说明或注释；大小写、斜杠、空格和前导 `/` 都参与哈希，工具不自动小写或猜补。路径文本文件本身为 UTF-8；若调查证明游戏计算路径哈希时使用另一种字节编码，可显式设置 `--path-encoding`。实际已验证输入的候选路径为 ASCII。

查看路径的两个校验值：

```sh
python3 -m legacy hash /data/example/resource.bin
```

恢复名称必须同时满足 CRC32 与 CRC16/X25。只中一个值不算匹配；若两个值对应多个候选路径，标为歧义并继续保留无名输出。工具不提供曲号枚举器，不猜标题，也不把候选路径当成已证实的游戏曲目。

### PKFS 与特殊尾部的精确定义

- 表头 16 字节，偏移 `8` 的 little-endian uint32 为条目区字节数；每条为 `<16sIHHII`：MD5、路径 CRC32、CRC16 子键、分包号、偏移、长度。
- 分包定位为 `d{pack//30:03}/pack{pack:04}.pak`。该布局只覆盖这里观察过的格式。
- 初始 XOR 键为 `ROL32(crc32 + crc16, 11)`，每个 little-endian DWORD 异或当前键后更新为 `ROL32(key + crc16, 11)`，计算均截断为 32 位。
- 对不足一个 DWORD 的尾部先尝试逐字节 XOR；历史实现则把所有剩余键字节连续异或到**第一个剩余数据字节**，其余尾字节保持存储值。长度余 0 / 1 时两者等价，余 2 / 3 才可能不同。
- 依次尝试原样、普通 XOR、历史尾部；只有 MD5 与表中存储值相同才认定为有效明文。失败时输出 `.stored` 原存储片段，并留下错误，绝不把未经确认的 XOR 结果叫作已恢复音频。

### PAK 输出

```text
output/pak/
  raw/                     原样复制的全部 PAK 与 packinfo.bin
  payloads/                已校验条目，或明确的 .stored 片段
  manifest.json            每条源分包/偏移/长度/MD5/变换/名称及SHA256
  stats.json
  errors.json
```

输入根内有多个 `packinfo.bin` 时，为避免跨索引覆盖，`payloads/` 下先保留索引相对父目录，再接恢复出的虚拟路径。以 manifest 的 `path` 为准。孤立未引用 PAK 也复制到 `raw/`，不因当前索引未引用就丢弃。

默认单条分配上限为 512 MiB，可用 `--max-entry-bytes` 调整；索引读取上限为 128 MiB。偏移越界、缺包、表长不整除、MD5 失败均有明确错误。不会自动修正错误长度。

## 2. 散文件 / 命名 IFS 按层整理

单一输入层：

```sh
python3 -m legacy export \
  --input input/base-data \
  --output output/base
```

base 与 APPEND 分开，后传入的层优先级更高：

```sh
python3 -m legacy export \
  --layer base=input/base-data \
  --layer append=input/append-data \
  --output output/layered \
  --decode-audio \
  --decode-textures
```

每个角色单独选择，APPEND 只提供 BGM 时，基础层的原试听与谱面仍保留并参与选择。两个原层都保存在 `raw/<层>/`，不会把 append 字节覆盖到 base。相同层出现同 ID 同角色而内容不同时，记录冲突，保留原文件，不能用任意遍历顺序伪装成确定覆盖规则。层名只允许字母、数字、下划线、连字符；名字本身不推断来源。需要把 PAK 产物用于第二步时，请选择其 `payloads/` 内**已经明确分开的** base / append 子目录，不把两层一起标成 base。

IFS 通过命名树逐个保存 `load(convert_kbin=False)` 的成员，不自动将二进制 XML 改写成文本，也不按五块固定顺序猜成员；额外保存 IFS 自身清单文本。原 IFS 文件继续存在。默认依赖缺失、损坏或成员不安全会计入错误；`--skip-ifs` 是显式只保留原包，stats 会注明未请求成员导出。

支持的歌曲角色由文件名决定：散文件的八位数字 ID 加 `b.eve/a.eve/e.eve/bgm.bin/idx.bin`，以及 `<八位ID>_msc.ifs` 内的 `bsc.eve/adv.eve/ext.eve/bgm.bin/idx.bin` 等已观察名称。仅BGM、零note谱、未知名称、额外模型、字体、音效和二进制都保留；未知内容不会被硬凑成完整歌曲。这里不用于移动版九位 ID。

### 分层输出

```text
output/layered/
  raw/<层>/                所有输入文件原样复制
  unpacked/<层>/<IFS>/     所有命名成员原字节 + _ifs_manifest.xml
  songs/<ID>/
    info.json              所有来源层与当前按角色选择结果
    charts/*.events.json   当前选择谱的300Hz整数tick JSON
    bgm.wav                可选；原BMP ADPCM解码
    preview.wav            可选；独立原试听解码
  extras/charts/           未关联或非当前选择EVE的事件JSON
  extras/audio/            可选；额外/下层系统BMP音频解码
  extras/textures/         可选；完整TEXP图集与命名裁剪PNG
  manifest.json
  stats.json
  errors.json
```

每首歌的原 EVE / 音频资源路径在 `info.json` 的 `layers` 和 `selected_resources` 中；不为制造目录外观再复制一份相同原文件。封面和其他纹理都在完整的纹理导出记录中保留；本 CLI 不凭 ID 强行补图或保证每首有本包封面。

`source_five_asset_ids` 只表示 BGM、原试听、三个原谱成员都存在；`three_playable_chart_five_asset_ids` 还要求三个谱各有 `PLAY` 或 `LONG`。二者都不是官方配信数量，也不保证能直接进入任何模拟器游玩。原成员存在不等于解码成功，请同时读 `errors.json`、各资源的转换字段以及 `decoded_wav_files`。

EVE 原文本保留；JSON 不改 tick、不重排、不量化拍值，未知指令也保留。无法解析的行保留十六进制与行号并报错。需要 MC 导出时另用街机导入器；MC 的拍值量化不是本 JSON 格式。`--max-member-bytes` 限制单个已加载成员的导出大小，默认 512 MiB；通用 IFS 库内部解压的峰值内存不由此限制承诺。

原 BGM 与原 idx/preview 分开处理，从不截取 BGM 充当缺失试听。WAV 为源 ADPCM 的 PCM 重建，保持公共解码器提供的采样率/声道/电平，不宣称恢复录音母带。额外系统 BMP 音效也会在明确请求时转换。纹理转换保存 atlas 与内部命名裁剪；未识别格式仍原样留存，不称为已解码。

## 重跑、失败与验证

输入与输出目录必须互不包含，避免重复扫描生成物。输入符号链接不跟随，路径穿越成员被拒绝。原始文件不会被编辑。

原始复制和 PAK 输出重跑时校验相同字节；若已有目标不同，报错并保留已有目标。已成功转换的 WAV 通过 sidecar 中源 SHA256 与输出 SHA256 校验后复用；纹理按转换缓存和每张 PNG SHA256 复用。中途留下、没有完整缓存的转换目录不会被悄悄视为成功，请保留原输入，在新的输出目录重试该转换。输出 JSON 可以重建，资源字节冲突不会自动覆盖。

退出码 `0` 表示本次请求范围无记录错误；`2` 表示参数/读取失败或记录了导出错误。`--skip-ifs`、不请求音频/纹理解码时的 `0` 只覆盖明确请求的范围，不代表未请求阶段已经完成。所有未知和失败都应在详细 manifest / errors 中核对。

运行独立的纯合成测试：

```sh
python3 -m unittest discover -s tests -p 'test_legacy_assets.py' -v
```

测试覆盖标准 CRC 向量、独立参考 XOR 与 0/1/2/3 余数、C/Python 一致、MD5约束、坏索引/越界、候选路径、原始保留、可重复导出、层优先级、同层冲突、命名 IFS、关闭句柄、路径穿越、整数tick与零note口径。测试 IFS 使用注入的合成命名树，不冒充真实 IFS 编码器；真实 IFS 兼容范围来自上表既有输入的本地静态导出。可选 C 环境不可用时仅 C 对照测试跳过，Python 用例仍运行。
