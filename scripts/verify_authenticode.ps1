<#
.SYNOPSIS
  Authenticode 签名校验（#250）。

.DESCRIPTION
  判据：`Get-AuthenticodeSignature` 的 Status 必须等于 -Expect；当 -ExpectedSubject 给出时，
  签名主体必须包含该片段（用于确认「是 SignPath Foundation 签的」而不是别的证书）。

  为什么单独成脚本、而不是把 if 内联进 workflow：
    因为「校验步骤本身」也需要判据自证。CI 里用同一个脚本跑四个方向：
      1) 未签名安装包 + -Expect NotSigned  ⇒ 通过（证明脚本能读出 NotSigned）
      2) 未签名安装包 + -Expect Valid      ⇒ 必须失败（证明 Valid 判据不是恒真）
      3) 系统自带已签名程序 + -Expect Valid -ExpectedSubject Microsoft ⇒ 通过（证明 Valid 判据与主体断言可用）
      4) 系统自带已签名程序 + -ExpectedSubject SignPath Foundation   ⇒ 必须失败（证明主体断言有牙）
    内联 if 做不到 2) 与 4) 这种「反向取证」。

.EXAMPLE
  pwsh -NoProfile -File scripts/verify_authenticode.ps1 -Path .\Togobot_x64-setup.exe -Expect Valid -ExpectedSubject "SignPath Foundation"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [ValidateSet('Valid', 'NotSigned')][string]$Expect = 'Valid',
    [string]$ExpectedSubject = ''
)

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    Write-Host "::error::文件不存在: $Path"
    exit 1
}

$resolved = (Resolve-Path -LiteralPath $Path).Path
$signature = Get-AuthenticodeSignature -LiteralPath $resolved
$status = [string]$signature.Status
$subject = ''
if ($signature.SignerCertificate) {
    $subject = [string]$signature.SignerCertificate.Subject
}

Write-Host "file       = $resolved"
Write-Host "size_bytes = $((Get-Item -LiteralPath $resolved).Length)"
Write-Host "status     = $status (期望 $Expect)"
Write-Host "subject    = $subject"
if ($signature.SignerCertificate) {
    Write-Host "issuer     = $($signature.SignerCertificate.Issuer)"
    Write-Host "thumbprint = $($signature.SignerCertificate.Thumbprint)"
}
if ($signature.StatusMessage) {
    Write-Host "message    = $($signature.StatusMessage)"
}

$failures = @()
if ($status -ne $Expect) {
    $failures += "Status=$status，期望 $Expect"
}
if ($Expect -eq 'Valid' -and $ExpectedSubject -and ($subject -notlike "*$ExpectedSubject*")) {
    $failures += "签名主体 '$subject' 不含期望片段 '$ExpectedSubject'（可能不是 SignPath Foundation 签的）"
}

if ($failures.Count -gt 0) {
    foreach ($failure in $failures) {
        Write-Host "::error::$failure"
    }
    exit 1
}

Write-Host "=== Authenticode 校验通过（status=$status）==="
exit 0
