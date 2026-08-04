$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$ComposeFile = Join-Path $ProjectDir "compose.test.yml"

Push-Location $ProjectDir
try {
    docker compose -f $ComposeFile up -d --build
    if ($LASTEXITCODE -ne 0) {
        throw "Docker 测试服务启动失败"
    }

    docker compose -f $ComposeFile exec -T app-backend `
        python manage.py create-admin `
        --username admin `
        --display-name "Docker 测试管理员" `
        --email admin@example.test `
        --password TestAdmin123 `
        --if-not-exists
    if ($LASTEXITCODE -ne 0) {
        throw "Docker 测试管理员创建失败"
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "测试服务已启动："
Write-Host "  应用：http://localhost:8080"
Write-Host "  邮件：http://localhost:8025"
Write-Host "  Jitsi：http://localhost:8000"
Write-Host "  管理员：admin@example.test / TestAdmin123"
