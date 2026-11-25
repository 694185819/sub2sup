# Sub2Sup — 图形字幕转 SUP 工具

把 VobSub 图形字幕（`.idx + .sub` 配套，或单个 `.sub`）**无损**转换为蓝光 PGS 字幕（`.sup`），
并支持在转换前**逐段预览、删除**不需要的字幕，例如字幕组信息、片头/片尾水印等。

Windows 图形界面（中文 / English 可切换），单文件 exe，不需要安装 Python。当前版本 **v1.1.1**。
支持 Blu-ray 1080p SDR 与 UHD HDR 输出配置（1080p PGS + HDR 亮度预设，可选试验性 4K PGS）。
支持把 `.idx` / `.sub` 文件直接拖进程序窗口，或拖到 exe 图标上打开。

## 界面截图

中文界面：

![中文界面](docs/screenshot-zh.png)

English UI:

![English UI](docs/screenshot-en.png)

界面语言可在「视图 → 语言」或工具栏右上角的「语言 / Language」下拉框中随时切换；
首次启动跟随系统语言，之后会记住选择。

## 下载

到 [Releases](https://github.com/694185819/sub2sup/releases) 下载 `Sub2Sup.exe`，双击即可运行。也可直接把 `.idx` 文件拖到 exe 上打开。

> 从源码构建：见下方「从源码构建」。

## 功能

- 读取 VobSub `.idx / .sub`（自动查找同名 `.sub`），也支持单独一个 `.sub`
- 列表 + 预览：缩略图、开始/结束时间、位置（顶部/中部/底部/整屏）、尺寸
- 大图预览：显示字幕在 1920×1080 画面中的实际位置；「细节 1:1」可放大
- 多选删除 / 恢复 / 撤销 / 全部恢复；按「仅顶部、仅整屏、已删除」等筛选
- 导出标准 Blu-ray PGS `.sup`，保留原始位图、时间轴、分辨率、alpha
- 导出后自动结构自检；可直接交给 tsMuxeR / mkvmerge 封装
- 支持多语言轨 `.idx`；中文 / English 界面可实时切换
- **UHD HDR 输出**：1080p PGS + HDR 字幕亮度预设（144 灰 / 自定义），
  可选试验性 4K PGS（2× 整数放大）
- **拖拽打开**：把 `.idx` / `.sub` 拖进窗口，或拖到 exe 图标上

## 使用

1. 双击 `Sub2Sup.exe`（或把 `.idx` / `.sub` 拖到 exe 上，或拖进已打开的窗口）。
2. 「打开字幕文件…」选择 `.idx`，程序会自动找同名 `.sub`。
3. 在左侧列表浏览段落；找字幕组信息时，可把「筛选」设为「仅顶部」或「仅整屏」。
4. 选中要删除的段（可多选），点「删除所选」（或按空格/Delete/双击）。
   删除只是标记，随时可「恢复」或「撤销」，不会改动原文件。
5. 「导出为 SUP…」，确认输出路径、画布尺寸、帧率后导出。
6. 用 tsMuxeR 添加字幕轨（语言 `chi`）封装回原盘 ISO；mkvmerge 也可直接封装。

快捷键：`Ctrl+O` 打开、`Ctrl+E` 导出、`Ctrl+Z` 撤销、`Ctrl+G` 跳转时间、
`Space` 删除/恢复当前段、`PgUp/PgDn` 上下翻 10 段。

## 演示字幕

`demo/demo.idx` + `demo/demo.sub` 是程序生成的纯演示字幕（4 段中英双语文字，无版权内容），
打开即可试用本工具；上面的界面截图也来自这份演示字幕。

## 为什么是“无损”

- **位图**：按原 2-bit 调色板索引 1:1 重编码为 PGS RLE，不重新渲染、不缩放；
  每个像素都与源 SPU 一致。
- **时间轴**：起止时间沿用原 90 kHz 时间戳（与 `.idx` 一致），不做取整或重采样。
- **alpha**：源 SPU 的 4-bit alpha（0–15）精确映射为 PGS 的 0–255。
- **颜色**：调色板 RGB 转成 PGS 规定的 YCbCr（BT.709 / BT.601 limited range），
  经解码后与源 RGB 的误差 ≤1/255（YCbCr 量化本身不可避免的 1 级误差）。

## UHD / HDR（4K 原盘）

UHD 原盘的实际做法通常是 **1920×1080 PGS 由播放器放大到 4K**；推荐：

- 目标规格选 `UHD HDR`：保持 1920×1080，不缩放位图/坐标
- 帧率与视频一致：23.976 / 24 / 25 / 50 / 59.94 / 60（UHD 模式会限制这些值）
- HDR 字幕亮度：建议 **144 灰（Y≈140）**。HDR 下 SDR 的白会偏亮；程序提供
  235 / 140 / 153 / 171 / 自定义 Y 预设，只压缩亮度、不改色相
- `4K PGS`（3840×2160）为试验性输出：位图与坐标按 2× 整数 nearest 放大；
  部分播放器/封装工具可能不接受，需实测
- PGS 本身不带 HDR 元数据，调色板始终是 SDR BT.709 limited；HDR10 的
  MaxCLL / MaxFALL / Mastering display 信息在视频里，封装时保持视频流直通即可

自测：UHD 输出 20 段 / 4K 输出 8 段逐像素比对通过（PCS 1920×1080 / 3840×2160、
峰值调色板 Y≤140、无对象越界、时间戳不变）。

## 输出规格

- 容器：`.sup`（`PG` 头 + PTS/DTS，90 kHz，big-endian + PGS 段）
- 显示集：PCS / WDS / PDS / ODS / END；每段结束写入清屏显示集
- 每个字幕一个 epoch（composition_state = epoch start），对象 0、窗口 0
- 调色板：每段 5 个条目（索引 0 透明 + 像素值 0..3 对应索引 1..4）
- 帧率代码：UHD 模式 23.976 / 24 / 25 / 50 / 59.94 / 60；BD 模式另含 29.97 / 30
- 大对象（RLE > 60 KB）自动分片为 ODS 序列，通过解码校验

## 从源码构建

需要 Windows + Python 3.11+。

```bat
py -3 -m pip install pyinstaller tkinterdnd2
build.bat
```

生成 `dist\Sub2Sup.exe`。程序运行时只依赖 Python 标准库 + Tk（打包后无外部依赖）。

## 命令行（可选）

```bat
set PYTHONPATH=src
py -3 -m sub2sup --cli "影片.idx" --list
py -3 -m sub2sup --cli "影片.idx" --out "影片.sup" --drop 0,1088 --verify
py -3 -m sub2sup --cli "影片.idx" --out "影片.sup" --keep 10-20
py -3 -m sub2sup --cli "字幕.sub" --out "字幕.sup" --canvas 1920x1080
py -3 -m sub2sup --cli "影片.idx" --out "影片-uhd.sup" --profile uhd --hdr-target 140
py -3 -m sub2sup --cli "影片.idx" --out "影片-4k.sup" --profile 4k --limit 20
```

参数：`--drop 0,5,10-12` 删除指定段；`--keep` 只保留指定段；`--fps`；`--offset-ms`；
`--profile bluray|uhd|4k`；`--hdr-target 16..235`（UHD 默认 140）；
`--dump 目录` 输出每段预览 PNG；`--verify` 自检。

自检脚本（转换后逐段比对位图/位置/时间/颜色）：

```bat
py -3 tests\selftest.py "影片.idx"
py -3 tests\selftest.py demo\demo.idx
```

## 已知限制

- 单独 `.sub` 不含调色板：使用经典 VobSub 默认 16 色，画布尺寸需在导出时确认；
  有位对应的 `.idx` 时请直接打开 `.idx`。
- 极少数 DVD SPU 在一个包内含多帧动画，转换时只保留第一帧（载入时会提示）。
- `.idx` 中的 `custom colors`、`scale`、`org` 等非常规字段不支持，载入时会提示。
- 输出为 PGS 图形字幕，不可在播放器中修改字体/字号。

## 免责声明

本项目仅用于个人备份、已有合法副本的格式转换与学习交流。
请确保你对所处理的影音内容拥有合法使用权，不要将转换结果用于传播受版权保护的内容。
本仓库**不包含**任何影片、字幕样本或截图。

## 许可证

[MIT](LICENSE)
