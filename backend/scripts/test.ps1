$ErrorActionPreference = "Stop"

$projectRoot = Split-Path `
    -Parent `
    $PSScriptRoot

Set-Location $projectRoot

$previousAppEnv = $env:APP_ENV
$previousDatabaseUrl = $env:DATABASE_URL
$previousApiKey = $env:API_KEY
$previousTestDatabaseUrl = $env:TEST_DATABASE_URL
$previousTestApiKey = $env:TEST_API_KEY

function Import-TestEnvironment {
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return
    }

    $allowedNames = @(
        "TEST_DATABASE_URL",
        "TEST_API_KEY"
    )

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
            $name -in $allowedNames `
            -and $value
        ) {
            Set-Item `
                -Path "Env:$name" `
                -Value $value
        }
    }
}

try {
    if (
        -not $env:TEST_DATABASE_URL `
        -or -not $env:TEST_API_KEY
    ) {
        Import-TestEnvironment `
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

    if (-not $env:TEST_API_KEY) {
        throw (
            "TEST_API_KEY não está definida. " +
            "Adicione uma chave de teste com pelo " +
            "menos 32 caracteres ao arquivo .env.test."
        )
    }

    $env:APP_ENV = "test"
    $env:DATABASE_URL = $env:TEST_DATABASE_URL
    $env:API_KEY = $env:TEST_API_KEY

    python -c "from app.core.config import settings; assert settings.environment == 'test'; assert settings.database_name == 'auneron_test', f'Banco inseguro: {settings.database_name}'; assert settings.api_key is not None; print('Banco de testes:', settings.database_name); print('API de testes: protegida')"

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

    if ($null -eq $previousApiKey) {
        Remove-Item Env:API_KEY `
            -ErrorAction SilentlyContinue
    }
    else {
        $env:API_KEY = $previousApiKey
    }

    if ($null -eq $previousTestDatabaseUrl) {
        Remove-Item Env:TEST_DATABASE_URL `
            -ErrorAction SilentlyContinue
    }
    else {
        $env:TEST_DATABASE_URL = `
            $previousTestDatabaseUrl
    }

    if ($null -eq $previousTestApiKey) {
        Remove-Item Env:TEST_API_KEY `
            -ErrorAction SilentlyContinue
    }
    else {
        $env:TEST_API_KEY = `
            $previousTestApiKey
    }
}
