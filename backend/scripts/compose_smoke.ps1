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

    Write-Host "== Validando rota protegida direta =="
    $directStatus = 0

    try {
        Invoke-WebRequest `
            -Uri "http://127.0.0.1:$backendPort/dashboard/" `
            -UseBasicParsing `
            -ErrorAction Stop |
            Out-Null

        $directStatus = 200
    } catch {
        if ($_.Exception.Response) {
            $directStatus = [int](
                $_.Exception.Response.StatusCode
            )
        } else {
            throw
        }
    }

    if ($directStatus -ne 401) {
        throw (
            "Esperado HTTP 401 sem API key; " +
            "recebido $directStatus."
        )
    }

    Write-Host "Backend protegido sem chave: HTTP 401"

    Write-Host "== Validando /api pelo frontend =="
    $dashboard = Invoke-RestMethod `
        -Uri "http://127.0.0.1:$frontendPort/api/dashboard/" `
        -ErrorAction Stop

    if ($null -eq $dashboard.resumo) {
        throw (
            "Dashboard via /api nao retornou " +
            "o payload esperado."
        )
    }

    Write-Host "Frontend: HTTP 200"
    Write-Host "Frontend /api/dashboard/: HTTP 200"
    Write-Host (
        "Clientes: {0}" -f
        $dashboard.resumo.clientes_total
    )

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
