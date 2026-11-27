# PREPARADO, NAO EXECUTADO. Monta uma COPIA do projeto (contexto de build) + wheelhouse offline.
param([Parameter(Mandatory = $true)][string]$Out)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (Test-Path $Out) { throw "o destino ja existe; use uma pasta nova: $Out" }
New-Item -ItemType Directory -Path $Out | Out-Null
foreach ($d in "src", "tests", "tools", "config") {
    robocopy "$root\$d" "$Out\$d" /E /XD __pycache__ /NFL /NDL /NJH /NJS | Out-Null
}
Copy-Item "$root\requirements.txt", "$root\requirements-dev.txt", "$root\pytest.ini" $Out
# wheelhouse para Linux/cp311 com verificacao de hash (mesmos arquivos fixados do projeto)
$env:PIP_USE_FEATURE = "truststore"   # usa o repositorio de certificados do Windows; NAO desliga verificacao
& "$root\.venv\Scripts\python.exe" -m pip download --require-hashes --only-binary=:all: `
    --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 --platform any `
    --python-version 3.11 --implementation cp --abi cp311 `
    -d "$Out\wheelhouse" -r "$root\requirements.txt" -r "$root\requirements-dev.txt"
Write-Host "Contexto pronto em $Out. Preencha o digest da imagem base e siga docker\ROTEIRO.md."
