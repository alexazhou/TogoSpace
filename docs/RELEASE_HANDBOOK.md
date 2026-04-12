# TogoAgent 版本发布手册

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

# 推送 tag（触发 CI 构建 arm64 版本）
git push origin v0.1.12
```

推送 tag 后，GitHub Actions 会自动触发：
- 构建 arm64 版本
- 签名 + 公证
- 创建 Release 并上传安装包

## 3. 本地构建（x86_64 版本）

CI 仅构建 arm64，x86_64 需本地构建并上传。

### 3.1 准备配置文件

确保 `scripts/build_config.json` 存在并配置正确：
```json
{
  "apple_id": "your-apple-id@example.com",
  "app_specific_password": "xxxx-xxxx-xxxx-xxxx",
  "team_id": "YOUR_TEAM_ID",
  "signing_identity_hash": "YOUR_SIGNING_IDENTITY_HASH"
}
```

补充说明：

- 如果在 Codex 中执行签名相关命令，需要申请提权运行。
- 原因是沙盒环境下通常无法访问登录钥匙串中的 codesign identity，像 `security find-identity -v -p codesigning` 可能会错误显示 `0 valid identities found`。
- 因此，`python scripts/build_release.py ...`、`codesign ...`、`security find-identity ...` 这类命令在 Codex 中都应优先按提权命令执行。

### 3.2 执行构建脚本

```bash
# 构建带签名和公证的 x86_64 版本
python scripts/build_release.py --arch x86_64

# 或跳过公证（仅签名打包，用于快速测试）
python scripts/build_release.py --arch x86_64 --skip-notarize
```

输出：`dist/TogoAgent-0.1.12-macos-x86_64.zip`

## 4. 上传到 Release

```bash
# 上传 x86_64 安装包到已有 Release
gh release upload v0.1.12 dist/TogoAgent-0.1.12-macos-x86_64.zip
```

## 5. 验证 Release

```bash
# 查看 Release 信息
gh release view v0.1.12
```

确认包含两个安装包：
- `TogoAgent-0.1.12-macos-arm64.zip` (CI 构建)
- `TogoAgent-0.1.12-macos-x86_64.zip` (本地构建)

## 6. 完整流程示例

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

# 4. 本地构建 x86_64
python scripts/build_release.py --arch x86_64

# 5. 上传到 Release
gh release upload v0.1.12 dist/TogoAgent-0.1.12-macos-x86_64.zip

# 6. 验证
gh release view v0.1.12
```

## 附录

### build_release.py 参数说明

| 参数 | 说明 |
|------|------|
| `--arch arm64/x86_64` | 目标架构，默认自动检测 |
| `--skip-build` | 跳过构建，仅签名公证已有 app |
| `--skip-notarize` | 跳过公证，仅签名打包 |
| `--clean` | 构建前清理 dist 和 build 目录 |

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
