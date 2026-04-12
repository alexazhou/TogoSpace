# Wolf App Icon Archive

归档时间：`2026-04-13`

本目录用于留存本次 App 图标制作相关的原始素材、最终产物和处理代码，方便后续回看、复现或继续微调。

## 文件说明

- `Gemini_Generated_Image_ctm3lsctm3lsctm3.png`
  原始素材图，横向长图。
- `icon.png`
  最终导出的 `1024x1024` PNG 图标母版。
- `icon.icns`
  最终导出的 macOS App 图标文件。
- `build_icon.py`
  当前版本图标的生成脚本。

## 当前图标风格

- 透明底
- 深蓝到青蓝的主体渐变
- 保留原始狼头的白色面部留白
- 带轻微阴影和边缘高光
- 通过高分辨率合成后缩回 `1024x1024`，降低边缘锯齿感

## 处理方法

原图是长方形，不能直接作为 App 图标使用，因此处理流程如下：

1. 从原图中识别深色狼头主体区域。
2. 按主体包围盒裁切，并增加适量留白，避免耳朵和下巴太贴边。
3. 在目标尺寸上重新计算：
   - 深色主体蒙版
   - 白色内部留白区域
4. 使用深蓝渐变填充主体，使用浅蓝白渐变填充面部留白。
5. 叠加轻微阴影、边缘高光和内部轮廓压线。
6. 先在 3 倍分辨率下合成，再缩回 `1024x1024`，减少锯齿。
7. 输出 `icon.png` 和 `icon.icns`。

## 复现方法

在仓库根目录执行：

```bash
.venv/bin/python assets/icon_archive/2026-04-13-wolf-icon/build_icon.py
```

脚本会直接更新本目录下的：

- `icon.png`
- `icon.icns`

## 同步到打包入口

当前仓库打包实际使用的是：

- `assets/icon.icns`

如果要把归档版本同步成当前打包图标，可执行：

```bash
cp assets/icon_archive/2026-04-13-wolf-icon/icon.png assets/icon.png
cp assets/icon_archive/2026-04-13-wolf-icon/icon.icns assets/icon.icns
```

## 备注

- 根目录的 `assets/icon.icns` 在 `.gitignore` 中被忽略。
- 本归档目录中的 `icon.icns` 不受该规则影响，可正常纳入版本管理。
