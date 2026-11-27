# G1: inicia o no Mesh DESCARTAVEL num container do Docker Desktop. Quem roda e Daniel.
# Pede o token do no (copiado do painel da Cloudflare) sem mostrar na tela e sem gravar em arquivo.
# Nao mexe na VPS, no cloudflared do PC, no Ollama nem na rede do Windows: tudo fica dentro do container.
$ErrorActionPreference = "Stop"
$IMAGEM = "cloudflare/mesh@sha256:1c135f07ddac619562f45da5816b25d3ac7663a491c2a065bd83c005007dec5e"
$NOME = "aigw-g1-mesh"
$VOLUME = "aigw-g1-mesh-estado"

if (docker ps -a --filter "name=^$NOME$" --format "{{.Names}}") { throw "PARADO: o container $NOME ja existe. Avise o PO." }
if (docker volume ls --filter "name=^$VOLUME$" --format "{{.Name}}") { throw "PARADO: o volume $VOLUME ja existe (estado antigo teria prioridade sobre o token). Avise o PO." }

$seguro = Read-Host -AsSecureString "Cole o token do no Mesh G1 e tecle Enter"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($seguro)
try {
    $env:MESH_NODE_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    if ($env:MESH_NODE_TOKEN.Length -lt 20) { throw "PARADO: token vazio ou curto demais." }
    docker run -d --name $NOME --cap-add NET_ADMIN --cap-add NET_RAW --device /dev/net/tun `
        --security-opt no-new-privileges -e MESH_NODE_TOKEN -v "${VOLUME}:/var/lib/cloudflare-warp" `
        --restart no $IMAGEM | Out-Null
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    Remove-Item Env:MESH_NODE_TOKEN -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 15
docker exec $NOME warp-cli status
Write-Host "MESH G1 INICIADO. Pode avisar o PO."
