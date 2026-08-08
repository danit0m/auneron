$ErrorActionPreference = "Stop"

$composeFile = Join-Path $PSScriptRoot "..\docker-compose.yml"
$frontendPort = if ($env:AUNERON_HTTP_PORT) {
    $env:AUNERON_HTTP_PORT
} else {
    "8080"
}
$backendPort = if ($env:BACKEND_HTTP_PORT) {
    $env:BACKEND_HTTP_PORT
} else {
    "8000"
}

function Wait-Http200 {
    param(
        [Parameter(Mandatory)]
        [string]$Uri,

        [int]$Attempts = 40
    )

    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            $response = Invoke-WebRequest `
                -Uri $Uri `
                -UseBasicParsing `
                -TimeoutSec 2 `
                -ErrorAction Stop

            if ($response.StatusCode -eq 200) {
                return
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }

    throw "Servico nao ficou pronto: $Uri"
}

function Get-HttpStatus {
    param(
        [Parameter(Mandatory)]
        [string]$Uri
    )

    try {
        $response = Invoke-WebRequest `
            -Uri $Uri `
            -UseBasicParsing `
            -TimeoutSec 5 `
            -ErrorAction Stop

        return [int]$response.StatusCode
    }
    catch {
        if ($_.Exception.Response) {
            return [int](
                $_.Exception.Response.StatusCode
            )
        }

        throw
    }
}

try {
    Write-Host "== Validando Docker Compose =="
    docker compose `
        -f $composeFile `
        config `
        --quiet

    Write-Host "== Subindo stack completa =="
    docker compose `
        -f $composeFile `
        up `
        -d `
        --build

    Write-Host "== Aguardando backend =="
    Wait-Http200 `
        "http://127.0.0.1:$backendPort/health"

    Write-Host "== Aguardando frontend =="
    Wait-Http200 `
        "http://127.0.0.1:$frontendPort/"

    Write-Host "== Validando rota direta sem API key =="
    $directStatus = Get-HttpStatus `
        "http://127.0.0.1:$backendPort/dashboard/"

    if ($directStatus -ne 401) {
        throw (
            "Esperado HTTP 401 sem API key; " +
            "recebido $directStatus."
        )
    }

    Write-Host "Backend sem API key: HTTP 401"

    Write-Host "== Validando proxy /api =="
    $proxyHealth = Get-HttpStatus `
        "http://127.0.0.1:$frontendPort/api/health"

    if ($proxyHealth -ne 200) {
        throw (
            "Esperado HTTP 200 em /api/health; " +
            "recebido $proxyHealth."
        )
    }

    Write-Host "Frontend /api/health: HTTP 200"

    Write-Host "== Validando API key sem sessao =="
    $dashboardStatus = Get-HttpStatus `
        "http://127.0.0.1:$frontendPort/api/dashboard/"

    if ($dashboardStatus -ne 401) {
        throw (
            "Esperado HTTP 401 com API key e sem sessao; " +
            "recebido $dashboardStatus."
        )
    }

    Write-Host (
        "Frontend /api/dashboard/ sem sessao: HTTP 401"
    )

    $meStatus = Get-HttpStatus `
        "http://127.0.0.1:$frontendPort/api/auth/me"

    if ($meStatus -ne 401) {
        throw (
            "Esperado HTTP 401 em /api/auth/me " +
            "sem cookie; recebido $meStatus."
        )
    }

    Write-Host "Frontend /api/auth/me sem sessao: HTTP 401"

    Write-Host ""
    Write-Host "COMPOSE SMOKE TEST: OK"
}
finally {
    Write-Host ""
    Write-Host "== Estado final =="
    docker compose `
        -f $composeFile `
        ps `
        -a
}
