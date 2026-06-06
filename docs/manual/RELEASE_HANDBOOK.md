# TogoSpace 版本发布手册

本文档描述发布新版本的最小流程。

## 1. 更新版本号

只需要更新后端版本号。构建产物、桌面端展示和发布包名称均以该版本号为准。

编辑 `src/version.py`：

```python
__version__ = "0.1.12"  # 替换为新版本号
```

提交版本号更新：

```bash
git add src/version.py
git commit -m "chore: bump version to 0.1.12"
git push origin master
```

## 2. 创建 Git Tag

```bash
# 创建 tag
git tag v0.1.12

# 推送 tag（触发 CI 构建）
git push origin v0.1.12
```

推送 tag 后，GitHub Actions 会自动触发：
- 自动构建 arm64 和 x86_64 两个平台的版本
- 执行应用签名与苹果公证
- 自动创建 Draft Release 并上传安装包

## 3. 验证 Release

```bash
# 查看 Release 信息
gh release view v0.1.12
```

确认包含两个安装包：
- `TogoSpace-0.1.12-macos-arm64.zip` (CI 构建)
- `TogoSpace-0.1.12-macos-x86_64.zip` (CI 构建)

## 4. 补充 Release Note

Release 创建并确认产物齐全后，应补充 GitHub Release Note。

示例：

```bash
# 查看当前 Release 正文
gh release view v0.1.12

# 使用本地 markdown 文件更新 Release Note
gh release edit v0.1.12 --notes-file /tmp/v0.1.12-release-notes.md
```

建议格式：

- 中文在前，英文在后。
- 中英文内容分别成段，不要混写在同一条 bullet 里。
- 中英文之间使用 Markdown 默认横线分隔：`---`
- 每种语言控制在 5 条以内，只写本次发布最重要的变更。
- 最后一行保留 `Full Changelog` 链接，指向前一个版本到当前版本的 compare 页面。

建议写法示例：

```md
- 修复 A 问题。
- 优化 B 行为。

---

- Fixed issue A.
- Improved behavior B.

**Full Changelog**: https://github.com/alexazhou/TogoSpace/compare/v0.1.11...v0.1.12
```

Release Note 注意事项：

- 不要照抄 commit 列表。应提炼用户真正感知到的变化，而不是按提交时间流水账罗列。
- 不要写未发布到该 tag 的内容。必须以目标 tag 实际包含的代码和产物为准。
- 涉及内置二进制、前端子模块、桌面包、Docker 镜像等内容时，先核对实际版本和是否发布成功，再写进正文。
- 如果某项工作流失败但后来重跑成功，应以最终发布结果为准，不要把中间失败过程写进 Release Note。
- 如需补写旧版本 Release Note，也应按对应版本区间对比，例如 `v0.1.11...v0.1.12`。

## 5. 完整流程示例

```bash
# 1. 更新版本号
vim src/version.py                    # 改为 0.1.12

git add src/version.py
git commit -m "chore: bump version to 0.1.12"
git push origin master

# 2. 创建并推送 tag
git tag v0.1.12
git push origin v0.1.12

# 3. 等待 CI 完成（约 5-10 分钟）
# 在 GitHub Actions 页面查看进度

# 4. 验证
gh release view v0.1.12

# 5. 补充 Release Note
gh release edit v0.1.12 --notes-file /tmp/v0.1.12-release-notes.md
```

## 附录

### 追加改动到现有版本

当需要在已发布的版本号上追加改动（如热修复），保持版本号不变：

```bash
# 1. 提交前后端改动并推送
# 2. 移动已有 tag 到新提交
git tag -f v0.1.12
git push origin master --tags --force

# 3. CI 会自动重新执行构建并覆盖原有 Release 下的文件
```

**注意：** tag 版本号（如 v0.1.12）必须与 `src/version.py` 中的版本号一致，CI 才能正确打包并关联对应文件。

### build_release.py 参数说明 (供本地测试构建参考)

虽然正式发布已交由 CI 完成，但若需在本地调试构建流程，可使用 `scripts/build_release.py`：

| 参数 | 说明 |
|------|------|
| `--arch arm64/x86_64` | 目标架构，默认自动检测 |
| `--action build,sign,notarize,zip` | 要执行的步骤（逗号分隔），默认全部流水线 |
| `--action build,sign,zip` | 跳过公证，仅签名打包 |
| `--clean` | 构建前清理 dist 和 build 目录 |

如果执行本地构建与签名，需准备 `scripts/build_config.json`：
```json
{
  "apple_id": "your-apple-id@example.com",
  "app_specific_password": "xxxx-xxxx-xxxx-xxxx",
  "team_id": "YOUR_TEAM_ID",
  "signing_identity_hash": "YOUR_SIGNING_IDENTITY_HASH"
}
```
*注意：在 Codex 等沙盒环境中测试签名需提权运行，否则无法访问钥匙串证书。*

### 常用命令

```bash
# 查看 CI 构建状态
gh run list --branch master

# 查看 Release 列表
gh release list

# 删除本地 tag
git tag -d v0.1.12

# 删除远程 tag
git push origin --delete v0.1.12
```
