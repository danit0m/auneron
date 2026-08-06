$ErrorActionPreference = "Stop"

$projectRoot = Split-Path `
    -Parent `
    $PSScriptRoot

Set-Location $projectRoot

$previousAppEnv = $env:APP_ENV
$previousDatabaseUrl = $env:DATABASE_URL

try {
    if (-not $env:TEST_DATABASE_URL) {
        $env:TEST_DATABASE_URL = `
            "postgresql+psycopg://auneron:auneron_dev_password@localhost:5432/auneron_test"
    }

    $env:APP_ENV = "test"
    $env:DATABASE_URL = $env:TEST_DATABASE_URL

    python -c "from app.database.database import engine; assert engine.url.database == 'auneron_test', f'Banco inseguro: {engine.url.database}'; print('Banco de testes:', engine.url.database)"

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
}