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
        }
        catch {
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

function Assert-Header {
    param(
        [Parameter(Mandatory)]
        $Response,

        [Parameter(Mandatory)]
        [string]$Name,

        [Parameter(Mandatory)]
        [string]$Contains
    )

    $value = $Response.Headers[$Name]

    if (-not $value) {
        throw "Header ausente: $Name"
    }

    if ($value -notlike "*$Contains*") {
        throw (
            "Header $Name nao contem valor esperado. " +
            "Recebido: $value"
        )
    }
}

try {
    Write-Host "== Validando Docker Compose =="
    docker compose `
        -f $composeFile `
        config `
        --quiet

    if ($LASTEXITCODE -ne 0) {
        throw "docker compose config falhou."
    }

    Write-Host "== Subindo stack completa =="
    docker compose `
        -f $composeFile `
        up `
        -d `
        --build

    if ($LASTEXITCODE -ne 0) {
        throw "docker compose up falhou."
    }

    Write-Host "== Aguardando liveness do backend =="
    Wait-Http200 `
        "http://127.0.0.1:$backendPort/health"

    Write-Host "== Aguardando readiness do backend =="
    Wait-Http200 `
        "http://127.0.0.1:$backendPort/ready"

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

    Write-Host "== Validando health/readiness pelo proxy =="
    $proxyHealth = Get-HttpStatus `
        "http://127.0.0.1:$frontendPort/api/health"

    if ($proxyHealth -ne 200) {
        throw (
            "Esperado HTTP 200 em /api/health; " +
            "recebido $proxyHealth."
        )
    }

    $proxyReady = Get-HttpStatus `
        "http://127.0.0.1:$frontendPort/api/ready"

    if ($proxyReady -ne 200) {
        throw (
            "Esperado HTTP 200 em /api/ready; " +
            "recebido $proxyReady."
        )
    }

    Write-Host "Frontend /api/health: HTTP 200"
    Write-Host "Frontend /api/ready: HTTP 200"

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

    Write-Host "== Validando security headers do Nginx =="
    $frontendResponse = Invoke-WebRequest `
        -Uri "http://127.0.0.1:$frontendPort/" `
        -UseBasicParsing `
        -TimeoutSec 5 `
        -ErrorAction Stop

    Assert-Header `
        -Response $frontendResponse `
        -Name "X-Content-Type-Options" `
        -Contains "nosniff"

    Assert-Header `
        -Response $frontendResponse `
        -Name "X-Frame-Options" `
        -Contains "DENY"

    Assert-Header `
        -Response $frontendResponse `
        -Name "Referrer-Policy" `
        -Contains "strict-origin-when-cross-origin"

    Assert-Header `
        -Response $frontendResponse `
        -Name "Permissions-Policy" `
        -Contains "camera=()"

    Assert-Header `
        -Response $frontendResponse `
        -Name "Content-Security-Policy" `
        -Contains "frame-ancestors 'none'"

    Write-Host "Security headers do Nginx: OK"

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
