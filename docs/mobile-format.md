# 移动版备份与资源格式

适配对象是曾验证过的 jubeat plus / jukebeat iMazing `.adbk` 备份，解密实现依据 Jubeat.app 3.9.11 的 BFCodec 行为整理。不能假定所有后续版本都使用相同格式。

## 处理链

```text
.adbk ZIP
  └─ 应用目录/Documents/<ID>.jbt ZIP
       ├─ info / infov2 → 解密 → Apple binary plist
       ├─ seq_bas / seq_adv / seq_ext → 解密 → 事件 → .mc
       ├─ artwork 等 → 解密 → CgBI → 标准 PNG
       └─ bgm / index → 解密 → AAC/M4A
```

只读取已知歌曲条目。`Documents/scImg/*.img` 可选导出，应用设置、账号、成绩数据库和未知条目不会导出。

## BFCodec 变种

- 16 轮 Blowfish，P/S 初始表使用标准常量。
- F 函数为 `(S0[a] + S1[b]) XOR (S2[c] + S3[d])`，加法按 32 位回绕。
- 密钥为格式固定口令 `Konami Bemani Mobile iPad` 的 MD5 摘要：`f9a142c70b07d9a8093b56b8c2eeb698`。
- CBC IV：`e36631da2c85a064`。
- 条目布局：`密文 || 明文长度 BE32 || 密文长度 BE32`。密文长度为 8 的倍数，解密后按明文长度截断。

这些常量属于文件格式，不是用户账号、设备凭证或 API 密钥。标准 Blowfish 库无法直接代替本变种。源码中旧的 IV 注释与实际常量曾不一致，本仓库已统一为上述值。

轮密钥必须先串行展开，再由工作线程只读复用。曾有旧实现每次解密都改写全局轮密钥，导致并发音源错位；当前 Python 封装用锁确保只初始化一次。

## 谱面

`seq_*` 解密后是小端记录流：

| 偏移 | 内容 |
|---|---|
| `0x00` | 4 字节标识，如 IJBQ / IJSQ / IJBS |
| `0x04` | 记录条数 `u32` |
| `0x08` | note 记录计数 `u32`，不要直接当作含尾判的物量 |
| `0x0c` | 总 tick `u32` |
| `0x12` | marker 位掩码 `u16` |
| `0x60` | 记录数组，每条为两个 `u32` |

`type = r0 & 255`，`tick = r0 >> 8`，`value = r1`。

| type | 含义 |
|---|---|
| 1 | 单点，value 为 0..15 面板位置 |
| 2 / 3 / 4 | END / MEASURE / HAKU |
| 5 | TEMPO，value 约为 `floor(60000000 / BPM)` |
| 6 | 长押，与 IFS 的 LONG 使用相同位段 |

长押：`index = value & 15`，`direction = (value >> 4) & 3`，`length = (value >> 6) & 3`，`duration = value >> 8`。实现使用的 `(dx, dy)` 为 `0:(0,-1)`、`1:(0,1)`、`2:(-1,0)`、`3:(1,0)`，终点坐标为起点加方向乘长度。这里明确坐标增量，避免把箭头指向与终点方向混淆。

tick 为 300 Hz。TEMPO 变化形成分段时间轴，使用有理数积分，再按 1/48 拍量化。极短长押可能量化成同拍头尾，仍保留长押；统计物量采用 `tap + 2 × hold`。

## CgBI PNG

CgBI 图片使用裸 deflate、预乘 BGRA。转换时解 PNG 行过滤、恢复 RGBA、解除 alpha 预乘并重新压缩为标准 PNG。实现限制输入体积与像素总数，校验分块和解压长度；普通 PNG 可直接透传。

## 输出与限制

- 每首歌曲分别保存三难度 `.mc`、图片、音频和元数据。
- `.mcz` 可保留 AAC 或转 Vorbis；谱面中的音频引用与实际成员名一致。
- 未提供音频、封面时不写悬空引用。
- 输出目录必须为空；包与 ID 隔离避免把移动版和街机同名谱面混在一起。
- 未知备份版本、损坏条目或超预算输入会失败，不视为成功完成。
