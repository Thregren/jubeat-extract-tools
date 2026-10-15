# 格式参考与第三方代码

- `assets/vendor/tdxt.py`、`dxt.py` 来自 [DragonMinded/bemaniutils](https://github.com/DragonMinded/bemaniutils) 的格式模块，保留其 Unlicense 于 `assets/vendor/LICENSE`。本地调整包括包内导入、像素预算；TEXBIN 包装另使用 Pillow BCn 加速路径。
- TEXP/PMAN/TCER 布局、字典起点 4078 和像素格式解释参考 [mon/ifs_layeredfs](https://github.com/mon/ifs_layeredfs/blob/master/src/texbin.cpp)。这里的边界检查、C/Python LZSS 与导出组织为本项目实现，不随库捆绑该项目二进制。
- VA3W 表布局参考 [fisyher/gitadora-customs](https://github.com/fisyher/gitadora-customs) 的 `vas3tool.py`，保留源音量/声像元数据；音频算法复用本项目 OKI4S，实现与独立参考逐字节校验。
- 移动 marker/theme 的独立 key 与封装格式参考 [Tatsh/dade](https://github.com/Tatsh/dade)；具体出处、许可及本地实现说明见 [移动全资源文档](mobile-full-assets.md)。
- 可选 Python 依赖通过 `requirements-assets.txt` 安装，按各自项目许可证使用；不把安装目录、动态库或下载缓存提交进仓库。

第三方许可证只适用于相应代码，不授予任何游戏音频、谱面、图像或程序的分发许可。本仓库仅发布通用源码、中文说明与人工测试数据生成代码。
