$ErrorActionPreference = "Stop"

$projectRoot = Split-Path `
    -Parent `
    $PSScriptRoot

Set-Location $projectRoot

$previousAppEnv = $env:APP_ENV
$previousDatabaseUrl = $env:DATABASE_URL
$previousTestDatabaseUrl = $env:TEST_DATABASE_URL

function Import-TestDatabaseUrl {
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return
    }

    foreach ($rawLine in Get-Content $Path) {
        $line = $rawLine.Trim()

        if (
            -not $line `
            -or $line.StartsWith("#") `
            -or -not $line.Contains("=")
        ) {
            continue
        }

        $parts = $line -split "=", 2
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")

        if (
            $name -eq "TEST_DATABASE_URL" `
            -and $value
        ) {
            $env:TEST_DATABASE_URL = $value
            return
        }
    }
}

try {
    if (-not $env:TEST_DATABASE_URL) {
        Import-TestDatabaseUrl `
            -Path (
                Join-Path `
                    $projectRoot `
                    ".env.test"
            )
    }

    if (-not $env:TEST_DATABASE_URL) {
        throw (
            "TEST_DATABASE_URL não está definida. " +
            "Copie .env.test.example para .env.test, " +
            "informe a senha local e execute novamente."
        )
    }

    $env:APP_ENV = "test"
    $env:DATABASE_URL = $env:TEST_DATABASE_URL

    python -c "from app.core.config import settings; assert settings.environment == 'test'; assert settings.database_name == 'auneron_test', f'Banco inseguro: {settings.database_name}'; print('Banco de testes:', settings.database_name)"

    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    python -m alembic upgrade head

    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    python -m pytest

    exit $LASTEXITCODE
}
finally {
    if ($null -eq $previousAppEnv) {
        Remove-Item Env:APP_ENV `
            -ErrorAction SilentlyContinue
    }
    else {
        $env:APP_ENV = $previousAppEnv
    }

    if ($null -eq $previousDatabaseUrl) {
        Remove-Item Env:DATABASE_URL `
            -ErrorAction SilentlyContinue
    }
    else {
        $env:DATABASE_URL = $previousDatabaseUrl
    }

    if ($null -eq $previousTestDatabaseUrl) {
        Remove-Item Env:TEST_DATABASE_URL `
            -ErrorAction SilentlyContinue
    }
    else {
        $env:TEST_DATABASE_URL = `
            $previousTestDatabaseUrl
    }
}
