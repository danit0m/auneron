<#
.SYNOPSIS
    Script de homologacao local do Auneron (Windows PowerShell 5.1).

.DESCRIPTION
    Opera a homologacao local com um conjunto FIXO de comandos. Os segredos
    ficam em um cofre cifrado (DPAPI do usuario do Windows) FORA do repositorio.
    Nenhum comando imprime, exporta ou copia valores do cofre.

    Comandos: configurar, status, versao-banco, pendentes, backup, ensaiar, migrar,
              registrar-acao-pagamento,
              subir-backend, parar-backend, subir-frontend, parar-frontend

    Exemplos:
      .\homologacao.ps1 configurar                 (o diretor, no terminal dele)
      .\homologacao.ps1 status
      .\homologacao.ps1 versao-banco -Pasta C:\auneron
      .\homologacao.ps1 backup
      .\homologacao.ps1 ensaiar -Pasta C:\auneron -ConfirmarBackup C:\...\hml-X.dump
      .\homologacao.ps1 migrar -Pasta C:\auneron -ConfirmarBackup C:\...\hml-X.dump
      .\homologacao.ps1 parar-backend -Confirmar

    Nao existe downgrade neste script. Veja backend/docs/DEPLOYMENT.md.
    Autoteste offline: backend/scripts/test_homologacao.ps1
#>
[CmdletBinding(DefaultParameterSetName = 'Normal')]
param(
    [Parameter(ParameterSetName = 'Normal', Position = 0, Mandatory = $true)]
    [ValidateSet(
        'configurar', 'status', 'versao-banco', 'pendentes', 'backup', 'ensaiar', 'migrar',
        'registrar-acao-pagamento',
        'subir-backend', 'parar-backend', 'subir-frontend', 'parar-frontend'
    )]
    [string]$Comando,

    # Raiz do checkout a usar (precisa ter backend\alembic.ini e frontend\package.json).
    [Parameter(ParameterSetName = 'Normal')][string]$Pasta,
    # Caminho do cofre (padrao: <Base>\cofre.xml).
    [Parameter(ParameterSetName = 'Normal')][string]$Cofre,
    # Pasta base de estado/logs/backups (padrao: %LOCALAPPDATA%\Auneron\homologacao).
    [Parameter(ParameterSetName = 'Normal')][string]$Base,
    # Destino do dump (backup).
    [Parameter(ParameterSetName = 'Normal')][string]$Destino,
    # Dump existente, criado ha menos de 24 h (ensaiar, migrar).
    [Parameter(ParameterSetName = 'Normal')][string]$ConfirmarBackup,
    [Parameter(ParameterSetName = 'Normal')][switch]$Confirmar,
    [Parameter(ParameterSetName = 'Normal')][switch]$Simular,
    # Nao consulta portas/HTTP (so com status ou -Simular; usado no autoteste).
    [Parameter(ParameterSetName = 'Normal')][switch]$SemSondagem,

    # Uso interno do autoteste: apenas define as funcoes (dot-source).
    [Parameter(ParameterSetName = 'Funcoes', Mandatory = $true)][switch]$CarregarFuncoes
)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
$script:PortaBackend = 8000
$script:PortaFrontend = 5180
$script:UrlBackend = 'http://127.0.0.1:8000'
$script:UrlFrontend = 'http://127.0.0.1:5180'

# Segredos: sempre no cofre, nunca em texto puro.
$script:NomesSegredos = @('DATABASE_URL', 'API_KEY')

# Configuracao NAO secreta, padrao de homologacao (fonte: backend/app/core/config.py
# e backend/.env.example). Nao ficam no cofre.
$script:ConfigPadrao = [ordered]@{
    'APP_ENV'            = 'development'
    'DEBUG'              = 'false'
    'LOG_LEVEL'          = 'INFO'
    'CORS_ORIGINS'       = 'http://127.0.0.1:5180,http://localhost:5180'
    'FORWARDED_ALLOW_IPS' = '127.0.0.1'
    'BUSINESS_TIMEZONE'  = 'America/Sao_Paulo'
    'INTELLIGENCE_MODE'  = 'off'
}

$script:Ctx = @{
    Base = ''; Cofre = ''; Pasta = ''
    Simular = $false; Confirmar = $false; SemSondagem = $false
}
$script:Conhecidos = New-Object System.Collections.ArrayList

# ---------------------------------------------------------------------------
# Utilidades basicas
# ---------------------------------------------------------------------------
function Falhar {
    param([string]$Mensagem)
    throw $Mensagem
}

function Info {
    param([string]$Texto)
    Write-Host $Texto
}

function Registrar-Segredo {
    # Guarda valor (e variante percent-encoded) apenas em memoria, para mascarar saidas.
    param([string]$Valor)
    if ([string]::IsNullOrEmpty($Valor)) { return }
    if ($Valor.Length -lt 4) { return }
    [void]$script:Conhecidos.Add($Valor)
    $enc = [Uri]::EscapeDataString($Valor)
    if ($enc -ne $Valor) { [void]$script:Conhecidos.Add($enc) }
}

function Mascarar-Texto {
    param([string]$Texto)
    if ($null -eq $Texto) { return '' }
    $t = $Texto
    $ordenados = @($script:Conhecidos | Sort-Object -Property Length -Descending)
    foreach ($v in $ordenados) {
        if ($v) { $t = $t.Replace([string]$v, '***') }
    }
    return $t
}

function Remover-ValoresDeLinha {
    # Para exibir linha de comando de terceiros: tira o que parece credencial.
    param([string]$Linha)
    if ([string]::IsNullOrEmpty($Linha)) { return '' }
    $t = Mascarar-Texto $Linha
    $t = [regex]::Replace($t, '://[^/\s@"'']*@', '://***@')
    $t = [regex]::Replace($t, '(?i)((?:key|password|passwd|pwd|token|secret)[A-Za-z0-9_-]*(?:=|\s+|:))\S+', '$1***')
    if ($t.Length -gt 220) { $t = $t.Substring(0, 220) + '...' }
    return $t
}

function ConvertTo-ArgWin {
    param([string]$A)
    if ($A -eq '') { return '""' }
    if ($A -notmatch '[\s"]') { return $A }
    $s = $A -replace '(\\*)"', '$1$1\"'
    $s = $s -replace '(\\+)$', '$1$1'
    return '"' + $s + '"'
}

function Get-BaseDir {
    if ($script:Ctx.Base) { return $script:Ctx.Base }
    if (-not $env:LOCALAPPDATA) { Falhar 'LOCALAPPDATA nao definida; informe -Base.' }
    return (Join-Path $env:LOCALAPPDATA 'Auneron\homologacao')
}

function Get-CofreCaminho {
    if ($script:Ctx.Cofre) { return $script:Ctx.Cofre }
    return (Join-Path (Get-BaseDir) 'cofre.xml')
}

# ---------------------------------------------------------------------------
# Cofre (DPAPI do usuario; valores cifrados, nomes em claro)
# ---------------------------------------------------------------------------
function Ler-Cofre {
    $caminho = Get-CofreCaminho
    $r = @{}
    if (-not (Test-Path -LiteralPath $caminho)) { return $r }
    try {
        $o = Import-Clixml -LiteralPath $caminho
    }
    catch {
        Falhar 'Cofre ilegivel (arquivo corrompido). Rode configurar novamente.'
    }
    if ($o -isnot [hashtable]) {
        Falhar 'Cofre em formato inesperado. Rode configurar novamente.'
    }
    foreach ($k in @($o.Keys)) {
        if ($script:NomesSegredos -contains [string]$k) {
            $v = [string]$o[$k]
            if ($v) { $r[[string]$k] = $v }
        }
    }
    return $r
}

function Salvar-Cofre {
    param([hashtable]$Cifrados)
    $caminho = Get-CofreCaminho
    $dir = Split-Path -Parent $caminho
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        [void](New-Item -ItemType Directory -Force -Path $dir)
    }
    $tmp = $caminho + '.novo'
    Export-Clixml -LiteralPath $tmp -InputObject $Cifrados
    Move-Item -LiteralPath $tmp -Destination $caminho -Force
}

function Decifrar-Valor {
    param([string]$Cifrado)
    $bstr = [IntPtr]::Zero
    try {
        $ss = ConvertTo-SecureString -String $Cifrado
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss)
        $v = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    }
    catch {
        Falhar 'Nao foi possivel decifrar o cofre (criado por outro usuario do Windows ou outra maquina?). Rode configurar novamente.'
    }
    finally {
        if ($bstr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    }
    return $v
}

function Secure-ParaTexto {
    param([System.Security.SecureString]$Ss)
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Ss)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

function Exigir-Segredos {
    param([hashtable]$Cofre, [string[]]$Nomes)
    $faltam = @($Nomes | Where-Object { -not $Cofre.ContainsKey($_) })
    if ($faltam.Count -gt 0) {
        Falhar ('Segredos nao definidos no cofre: ' + ($faltam -join ', ') + '. O diretor deve rodar: homologacao.ps1 configurar')
    }
}

function Obter-Segredo {
    param([hashtable]$Cofre, [string]$Nome)
    $v = Decifrar-Valor $Cofre[$Nome]
    Registrar-Segredo $v
    return $v
}

# ---------------------------------------------------------------------------
# DATABASE_URL
# ---------------------------------------------------------------------------
function ConvertFrom-DatabaseUrl {
    param([string]$Url)
    $m = [regex]::Match($Url, '^(?<esq>postgresql(?:\+psycopg2?)?)://(?<auth>[^/?#]*)(?:/(?<banco>[^?#]*))?(?:\?(?<q>[^#]*))?(?:#.*)?$')
    if (-not $m.Success) {
        Falhar 'DATABASE_URL: formato nao suportado (use postgresql://, postgresql+psycopg:// ou postgresql+psycopg2://).'
    }
    $auth = $m.Groups['auth'].Value
    $at = $auth.LastIndexOf('@')
    $userinfo = ''
    $hostport = $auth
    if ($at -ge 0) {
        $userinfo = $auth.Substring(0, $at)
        $hostport = $auth.Substring($at + 1)
    }
    $usuario = ''
    $senha = ''
    if ($userinfo) {
        $c = $userinfo.IndexOf(':')
        if ($c -ge 0) {
            $usuario = [Uri]::UnescapeDataString($userinfo.Substring(0, $c))
            $senha = [Uri]::UnescapeDataString($userinfo.Substring($c + 1))
        }
        else {
            $usuario = [Uri]::UnescapeDataString($userinfo)
        }
    }
    $hm = $null
    if ($hostport.StartsWith('[')) {
        $hm = [regex]::Match($hostport, '^\[(?<h>[^\]]+)\](?::(?<p>\d+))?$')
    }
    else {
        $hm = [regex]::Match($hostport, '^(?<h>[^:]*)(?::(?<p>\d+))?$')
    }
    if (-not $hm.Success) { Falhar 'DATABASE_URL: host/porta invalidos.' }
    $hostName = [Uri]::UnescapeDataString($hm.Groups['h'].Value)
    if (-not $hostName) { $hostName = 'localhost' }
    $porta = 5432
    if ($hm.Groups['p'].Success -and $hm.Groups['p'].Value) {
        $porta = [int]$hm.Groups['p'].Value
        if ($porta -lt 1 -or $porta -gt 65535) { Falhar 'DATABASE_URL: porta fora do intervalo.' }
    }
    $banco = [Uri]::UnescapeDataString($m.Groups['banco'].Value)
    if (-not $banco) { Falhar 'DATABASE_URL: o banco nao foi informado.' }
    $ssl = ''
    $q = $m.Groups['q'].Value
    if ($q) {
        $sm = [regex]::Match($q, '(?:^|&)sslmode=(?<v>[A-Za-z-]+)(?:&|$)')
        if ($sm.Success) { $ssl = $sm.Groups['v'].Value }
    }
    foreach ($campo in @($hostName, $usuario, $banco)) {
        if ($campo.StartsWith('-')) { Falhar 'DATABASE_URL: componente comeca com hifen (recusado).' }
    }
    return [pscustomobject]@{
        Host = $hostName; Porta = $porta; Usuario = $usuario
        Senha = $senha; Banco = $banco; SslMode = $ssl
    }
}

# ---------------------------------------------------------------------------
# Processos e portas
# ---------------------------------------------------------------------------
function Test-ProcessoAlvo {
    # Funcao pura: o processo (nome + linha de comando) e o uvicorn/vite esperado?
    param([string]$Nome, [string]$LinhaComando, [string]$Alvo)
    if ([string]::IsNullOrEmpty($Nome) -or [string]::IsNullOrEmpty($LinhaComando)) { return $false }
    if ($Alvo -eq 'backend') {
        if ($Nome -notmatch '(?i)^pythonw?(\d+(\.\d+)*)?(\.exe)?$') { return $false }
        if ($LinhaComando -notmatch '(?i)uvicorn') { return $false }
        if ($LinhaComando -notmatch 'app\.main:app') { return $false }
        return $true
    }
    if ($Alvo -eq 'frontend') {
        if ($Nome -notmatch '(?i)^node(\.exe)?$') { return $false }
        if ($LinhaComando -notmatch '(?i)(^|[\\/\s"])vite([\\/\s".]|$)') { return $false }
        return $true
    }
    return $false
}

function Get-Escutando {
    param([int]$Porta)
    $r = @()
    $con = @(Get-NetTCPConnection -State Listen -LocalPort $Porta -ErrorAction SilentlyContinue)
    $ids = @($con | Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($idp in $ids) {
        $nome = ''
        $linha = ''
        $exe = ''
        $cim = Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId=" + [int]$idp) -ErrorAction SilentlyContinue
        if ($cim) {
            $nome = [string]$cim.Name
            $linha = [string]$cim.CommandLine
            $exe = [string]$cim.ExecutablePath
        }
        $enderecos = @($con | Where-Object { $_.OwningProcess -eq $idp } | Select-Object -ExpandProperty LocalAddress -Unique)
        $r += [pscustomobject]@{
            Pid = [int]$idp; Nome = $nome; LinhaComando = $linha
            Executavel = $exe; Enderecos = ($enderecos -join ',')
        }
    }
    return $r
}

function Descrever-Escutando {
    param($Item)
    return ('PID {0} ({1}) em {2}; comando: {3}' -f $Item.Pid, $Item.Nome, $Item.Enderecos, (Remover-ValoresDeLinha $Item.LinhaComando))
}

function Get-Http {
    param([string]$Url)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return [int]$r.StatusCode
    }
    catch {
        $resp = $_.Exception.Response
        if ($resp) { return [int]$resp.StatusCode }
        return 0
    }
}

function Exigir-PortaLivre {
    param([int]$Porta)
    if ($script:Ctx.SemSondagem) { Info ('(porta {0} nao verificada: -SemSondagem)' -f $Porta); return }
    $esc = @(Get-Escutando $Porta)
    if ($esc.Count -gt 0) {
        Falhar ('Porta {0} ja esta ocupada por: {1}' -f $Porta, (Descrever-Escutando $esc[0]))
    }
}

# ---------------------------------------------------------------------------
# Estado (PIDs/pastas dos processos iniciados por este script)
# ---------------------------------------------------------------------------
function Get-EstadoCaminho { return (Join-Path (Get-BaseDir) 'estado.json') }

function Ler-Estado {
    $r = @{}
    $c = Get-EstadoCaminho
    if (-not (Test-Path -LiteralPath $c)) { return $r }
    try {
        $o = Get-Content -LiteralPath $c -Raw | ConvertFrom-Json
        foreach ($p in $o.PSObject.Properties) {
            $r[$p.Name] = @{ pid = [int]$p.Value.pid; pasta = [string]$p.Value.pasta; iniciado = [string]$p.Value.iniciado }
        }
    }
    catch { }
    return $r
}

function Gravar-Estado {
    param([string]$Servico, $Idp, [string]$PastaUsada)
    $e = Ler-Estado
    if ($null -eq $Idp) { $e.Remove($Servico) }
    else { $e[$Servico] = @{ pid = [int]$Idp; pasta = $PastaUsada; iniciado = (Get-Date).ToString('s') } }
    $dir = Get-BaseDir
    if (-not (Test-Path -LiteralPath $dir)) { [void](New-Item -ItemType Directory -Force -Path $dir) }
    ($e | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath (Get-EstadoCaminho) -Encoding ASCII
}

# ---------------------------------------------------------------------------
# Execucao de processos filhos (segredos so por ambiente, nunca por argumento)
# ---------------------------------------------------------------------------
function Invoke-Filho {
    param(
        [string]$Exe,
        [string[]]$Argumentos = @(),
        [string]$DiretorioTrabalho = '',
        [hashtable]$Ambiente = @{}
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $Exe
    $psi.Arguments = (($Argumentos | ForEach-Object { ConvertTo-ArgWin $_ }) -join ' ')
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
    if ($DiretorioTrabalho) { $psi.WorkingDirectory = $DiretorioTrabalho }
    foreach ($k in @($Ambiente.Keys)) {
        if ($null -eq $Ambiente[$k]) {
            if ($psi.EnvironmentVariables.ContainsKey($k)) { $psi.EnvironmentVariables.Remove($k) }
        }
        else { $psi.EnvironmentVariables[$k] = [string]$Ambiente[$k] }
    }
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    [void]$p.Start()
    $p.StandardInput.Close()
    $tarefa = $p.StandardOutput.ReadToEndAsync()
    $err = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    $out = $tarefa.Result
    $saida = Mascarar-Texto (($out + $err).TrimEnd())
    return [pscustomobject]@{ Codigo = $p.ExitCode; Saida = $saida }
}

function Aplicar-Ambiente {
    param([hashtable]$Amb)
    $salvo = @{}
    foreach ($k in @($Amb.Keys)) {
        $salvo[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
        [Environment]::SetEnvironmentVariable($k, $Amb[$k], 'Process')
    }
    return $salvo
}

function Restaurar-Ambiente {
    param([hashtable]$Salvo)
    foreach ($k in @($Salvo.Keys)) {
        [Environment]::SetEnvironmentVariable($k, $Salvo[$k], 'Process')
    }
}

function Montar-Ambiente {
    # Valores em memoria; devolve hashtable nome -> valor ($null = remover do filho).
    param([string]$Alvo)
    $cofre = Ler-Cofre
    $amb = @{}
    if ($Alvo -eq 'backend') {
        Exigir-Segredos $cofre $script:NomesSegredos
        foreach ($k in $script:ConfigPadrao.Keys) { $amb[$k] = $script:ConfigPadrao[$k] }
        foreach ($n in $script:NomesSegredos) {
            $v = Obter-Segredo $cofre $n
            $amb[$n] = $v
            if ($n -eq 'DATABASE_URL') {
                $db = ConvertFrom-DatabaseUrl $v
                Registrar-Segredo $db.Senha
            }
        }
        $amb['PYTHONUTF8'] = '1'
    }
    elseif ($Alvo -eq 'frontend') {
        Exigir-Segredos $cofre @('API_KEY')
        $amb['AUNERON_BACKEND_URL'] = $script:UrlBackend
        $amb['AUNERON_API_KEY'] = Obter-Segredo $cofre 'API_KEY'
    }
    return $amb
}

function Nomes-Do-Ambiente {
    param([string]$Alvo)
    if ($Alvo -eq 'backend') {
        return (@($script:ConfigPadrao.Keys) + $script:NomesSegredos + @('PYTHONUTF8')) -join ', '
    }
    return 'AUNERON_BACKEND_URL, AUNERON_API_KEY'
}

# ---------------------------------------------------------------------------
# Pasta / Python / ferramentas
# ---------------------------------------------------------------------------
function Resolver-Pasta {
    if (-not $script:Ctx.Pasta) { Falhar 'Este comando exige -Pasta <raiz do checkout>.' }
    if (-not (Test-Path -LiteralPath $script:Ctx.Pasta -PathType Container)) { Falhar 'A pasta informada em -Pasta nao existe.' }
    $raiz = (Resolve-Path -LiteralPath $script:Ctx.Pasta).ProviderPath.TrimEnd('\')
    if (-not (Test-Path -LiteralPath (Join-Path $raiz 'backend\alembic.ini') -PathType Leaf)) {
        Falhar 'Pasta invalida: nao existe backend\alembic.ini nela.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $raiz 'frontend\package.json') -PathType Leaf)) {
        Falhar 'Pasta invalida: nao existe frontend\package.json nela.'
    }
    return $raiz
}

function Resolver-Python {
    # Unicas fontes aceitas: .venv do checkout de -Pasta, senao python.exe do PATH.
    param([string]$Raiz)
    $venv = Join-Path $Raiz 'backend\.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv -PathType Leaf) { return $venv }
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) {
        if (Test-PythonStoreStub $cmd.Source) {
            Falhar 'O python.exe do PATH e o atalho da Microsoft Store (WindowsApps), nao um Python real. Instale o Python ou crie backend\.venv no checkout.'
        }
        return $cmd.Source
    }
    Falhar 'Python nao encontrado (nem backend\.venv\Scripts\python.exe no checkout, nem python.exe no PATH).'
}

function Test-PythonStoreStub {
    # Funcao pura: o caminho e o stub da Microsoft Store?
    param([string]$Caminho)
    if ([string]::IsNullOrEmpty($Caminho)) { return $false }
    return ($Caminho -match '(?i)[\\/]WindowsApps[\\/]')
}

function Buscar-FerramentaPgEmRaiz {
    # Enumera os subdiretorios de versao (nome numerico) da raiz e testa
    # <versao>\bin\<nome>.exe; devolve o caminho da MAIOR versao numerica, ou $null.
    param([string]$Raiz, [string]$Nome)
    if ([string]::IsNullOrEmpty($Raiz)) { return $null }
    if (-not (Test-Path -LiteralPath $Raiz -PathType Container)) { return $null }
    $cands = @()
    $dirs = @(Get-ChildItem -LiteralPath $Raiz -Directory -ErrorAction SilentlyContinue)
    foreach ($d in $dirs) {
        if ($d.Name -notmatch '^[0-9]+(\.[0-9]+)*$') { continue }
        $exe = Join-Path (Join-Path $d.FullName 'bin') ($Nome + '.exe')
        if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { continue }
        $partes = @($d.Name.Split('.'))
        if ($partes.Count -lt 2) { $partes += '0' }
        if ($partes.Count -gt 4) { continue }
        $ver = $null
        try { $ver = [version]($partes -join '.') } catch { continue }
        $cands += [pscustomobject]@{ Versao = $ver; Caminho = $exe }
    }
    if ($cands.Count -eq 0) { return $null }
    $melhor = @($cands | Sort-Object -Property Versao -Descending)[0]
    return $melhor.Caminho
}

function Resolver-FerramentaPg {
    param([string]$Nome)  # 'pg_dump' | 'pg_restore'
    $cmd = Get-Command ($Nome + '.exe') -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    return (Buscar-FerramentaPgEmRaiz 'C:\Program Files\PostgreSQL' $Nome)
}

function Resolver-FerramentaPgIrma {
    # Ferramenta (ex.: psql) do MESMO diretorio de uma ferramenta de referencia ja resolvida.
    param([string]$CaminhoReferencia, [string]$Nome)
    if ([string]::IsNullOrEmpty($CaminhoReferencia)) { return $null }
    $c = Join-Path (Split-Path -Parent $CaminhoReferencia) ($Nome + '.exe')
    if (Test-Path -LiteralPath $c -PathType Leaf) { return $c }
    return $null
}

function Exigir-FerramentaPg {
    param([string]$Nome)
    $c = Resolver-FerramentaPg $Nome
    if (-not $c) {
        Falhar ("{0} nao encontrado no PATH nem em C:\Program Files\PostgreSQL\*\bin. Instale o PostgreSQL Client Tools ou adicione a pasta bin ao PATH." -f $Nome)
    }
    return $c
}

# ---------------------------------------------------------------------------
# Alembic
# ---------------------------------------------------------------------------
function Rodar-Alembic {
    param([string]$Raiz, [string[]]$Args2, [string]$UrlBanco = '')
    $py = Resolver-Python $Raiz
    $amb = Montar-Ambiente 'backend'
    if ($UrlBanco) {
        # Ensaio: o filho enxerga SO o banco descartavel (URL montada em memoria).
        Registrar-Segredo $UrlBanco
        $amb['DATABASE_URL'] = $UrlBanco
    }
    $res = Invoke-Filho -Exe $py -Argumentos (@('-m', 'alembic') + $Args2) -DiretorioTrabalho (Join-Path $Raiz 'backend') -Ambiente $amb
    if ($res.Codigo -ne 0) {
        $linhas = @($res.Saida -split "`r?`n" | Select-Object -Last 12)
        Falhar ("alembic {0} falhou (codigo {1}):`n{2}" -f ($Args2 -join ' '), $res.Codigo, ($linhas -join "`n"))
    }
    return $res.Saida
}

function Extrair-Revisoes {
    param([string]$Saida)
    $r = @()
    foreach ($l in ($Saida -split "`r?`n")) {
        $m = [regex]::Match($l, '^\s*(?<id>[0-9A-Za-z_]+)(\s+\(.*\))?\s*$')
        if ($m.Success) { $r += $m.Groups['id'].Value }
    }
    return $r
}

function Mostrar-Pendentes {
    param([string]$Raiz)
    $atual = @(Extrair-Revisoes (Rodar-Alembic $Raiz @('current')))
    $heads = @(Extrair-Revisoes (Rodar-Alembic $Raiz @('heads')))
    Info ('Revisao atual : ' + $(if ($atual.Count -gt 0) { $atual -join ', ' } else { '(banco sem versao)' }))
    Info ('Head          : ' + ($heads -join ', '))
    if ($atual.Count -gt 1 -or $heads.Count -ne 1) {
        Falhar 'Mais de uma revisao atual ou mais de um head; resolva manualmente (nada foi aplicado).'
    }
    if ($atual.Count -eq 1 -and $atual[0] -eq $heads[0]) {
        Info 'Nenhuma migracao pendente.'
        return 0
    }
    $inicio = 'base'
    if ($atual.Count -eq 1) { $inicio = $atual[0] }
    $hist = Rodar-Alembic $Raiz @('history', '-r', ($inicio + ':head'))
    $linhas = @($hist -split "`r?`n" | Where-Object { $_ -match '->' })
    Info 'Revisoes no caminho ate o head (a primeira e a atual, se houver):'
    foreach ($l in $linhas) { Info ('  ' + $l) }
    return $linhas.Count
}

# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Ensaio da migracao: funcoes puras e auxiliares
# ---------------------------------------------------------------------------
function Test-NomeBancoEnsaio {
    # Funcao pura: o nome e do padrao auneron_ensaio_<14 digitos> e NAO e o banco real?
    param([string]$Nome, [string]$BancoReal)
    if ([string]::IsNullOrEmpty($Nome)) { return $false }
    if ($Nome -cnotmatch '^auneron_ensaio_[0-9]{14}\z') { return $false }
    if ($BancoReal -and ($Nome -ieq $BancoReal)) { return $false }
    return $true
}

function Montar-UrlBancoEnsaio {
    # Funcao pura: mesma URL (esquema, credenciais como estao, host, porta, query);
    # so o nome do banco muda. O nome precisa ser do padrao do ensaio.
    param([string]$Url, [string]$NomeBanco)
    if (-not (Test-NomeBancoEnsaio $NomeBanco '')) { Falhar 'Nome de banco de ensaio invalido.' }
    $m = [regex]::Match($Url, '^(?<esq>postgresql(?:\+psycopg2?)?)://(?<auth>[^/?#]*)(?:/(?<banco>[^?#]*))?(?:\?(?<q>[^#]*))?(?:#.*)?$')
    if (-not $m.Success) { Falhar 'DATABASE_URL: formato nao suportado.' }
    $r = $m.Groups['esq'].Value + '://' + $m.Groups['auth'].Value + '/' + $NomeBanco
    $q = $m.Groups['q'].Value
    if ($q) { $r += '?' + $q }
    return $r
}

function Get-DataUtcRecibo {
    param($Valor)
    if ($Valor -is [datetime]) { return $Valor.ToUniversalTime() }
    $s = [string]$Valor
    $dt = [datetime]::MinValue
    $ok = [datetime]::TryParseExact($s, "yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture,
        ([Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal), [ref]$dt)
    if (-not $ok) { return $null }
    return $dt
}

function Avaliar-ReciboEnsaio {
    # Funcao pura: '' se o recibo serve; senao o motivo da recusa.
    param($Recibo, [string]$Sha, [string]$Commit, [string]$Head, [datetime]$AgoraUtc)
    if ($null -eq $Recibo) { return 'recibo ilegivel' }
    if ([string]$Recibo.sha256 -ine $Sha) { return 'o recibo e de outro dump (SHA-256 diferente)' }
    if ([string]$Recibo.commit -ine $Commit) { return 'o recibo e de outro commit do checkout' }
    if ([string]$Recibo.revisao_final -ne $Head -or [string]$Recibo.head -ne $Head) {
        return 'a revisao final do ensaio nao e o head atual do checkout'
    }
    if ($Recibo.arvore_limpa -ne $true) { return 'o ensaio foi feito com a arvore do checkout suja' }
    $criado = Get-DataUtcRecibo $Recibo.criado_em_utc
    if ($null -eq $criado) { return 'data do recibo ilegivel' }
    $idade = $AgoraUtc - $criado
    if ($idade.TotalHours -ge 24 -or $idade.TotalSeconds -lt -300) { return ('o recibo tem {0:N1} h (limite 24 h)' -f $idade.TotalHours) }
    return ''
}

function Selecionar-ReciboEnsaio {
    # Funcao pura sobre uma lista de recibos. Devolve '' se algum serve; senao a mensagem de recusa.
    param($Recibos, [string]$Sha, [string]$Commit, [string]$Head, [datetime]$AgoraUtc)
    $mesmos = @($Recibos | Where-Object { $null -ne $_ -and [string]$_.sha256 -ieq $Sha })
    if ($mesmos.Count -eq 0) {
        return 'Nao ha recibo de ensaio para este dump; rode ensaiar antes de migrar.'
    }
    $ultimo = ''
    foreach ($r in $mesmos) {
        $mot = Avaliar-ReciboEnsaio $r $Sha $Commit $Head $AgoraUtc
        if ($mot -eq '') { return '' }
        $ultimo = $mot
    }
    return ('Nenhum recibo de ensaio serve (' + $ultimo + '); rode ensaiar antes de migrar.')
}

function Get-EnsaiosDir { return (Join-Path (Get-BaseDir) 'ensaios') }

function Ler-RecibosEnsaio {
    $dir = Get-EnsaiosDir
    $r = @()
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { return $r }
    foreach ($f in @(Get-ChildItem -LiteralPath $dir -Filter 'ensaio-*.json' -File | Sort-Object Name)) {
        try { $r += (Get-Content -LiteralPath $f.FullName -Raw -Encoding UTF8 | ConvertFrom-Json) }
        catch { }
    }
    return $r
}

function Obter-InfoGit {
    param([string]$Raiz)
    $git = Get-Command git.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $git) { Falhar 'git.exe nao encontrado no PATH (necessario para identificar o commit do checkout).' }
    $h = Invoke-Filho -Exe $git.Source -Argumentos @('-C', $Raiz, 'rev-parse', 'HEAD')
    if ($h.Codigo -ne 0 -or $h.Saida.Trim() -notmatch '^[0-9a-fA-F]{40}$') { Falhar 'Nao foi possivel obter o commit do checkout (git rev-parse HEAD).' }
    $s = Invoke-Filho -Exe $git.Source -Argumentos @('--no-optional-locks', '-C', $Raiz, 'status', '--porcelain')
    if ($s.Codigo -ne 0) { Falhar 'git status falhou no checkout.' }
    return [pscustomobject]@{ Commit = $h.Saida.Trim().ToLowerInvariant(); Limpa = ($s.Saida.Trim() -eq '') }
}

function Obter-HeadUnico {
    param([string]$Raiz, [string]$UrlBanco = '')
    $heads = @(Extrair-Revisoes (Rodar-Alembic $Raiz @('heads') $UrlBanco))
    if ($heads.Count -ne 1) { Falhar 'O checkout tem mais de um head (ou nenhum) de migracao; resolva antes.' }
    return $heads[0]
}

function Validar-DumpArquivo {
    # Existe, nao vazio, menos de 24 h. Devolve o FileInfo.
    param([string]$Caminho, [string]$Opcao)
    if (-not (Test-Path -LiteralPath $Caminho -PathType Leaf)) { Falhar ('O arquivo de ' + $Opcao + ' nao existe.') }
    $item = Get-Item -LiteralPath $Caminho
    if ($item.Length -le 0) { Falhar 'O arquivo de backup esta vazio.' }
    $idade = (Get-Date) - $item.LastWriteTime
    if ($idade.TotalHours -ge 24 -or $idade.TotalSeconds -lt -300) {
        Falhar ('O backup tem {0:N1} h; precisa ter sido criado ha menos de 24 h. Rode backup de novo.' -f $idade.TotalHours)
    }
    return $item
}

function Invoke-Psql {
    # Sempre no banco de manutencao 'postgres'; senha so por ambiente.
    param([string]$Psql, $Db, [string]$Sql)
    $amb = @{ 'PGPASSWORD' = $(if ($Db.Senha) { $Db.Senha } else { $null }); 'PGCLIENTENCODING' = 'UTF8'; 'PGCONNECT_TIMEOUT' = '10' }
    if ($Db.SslMode) { $amb['PGSSLMODE'] = $Db.SslMode }
    $a = @('-h', $Db.Host, '-p', [string]$Db.Porta)
    if ($Db.Usuario) { $a += @('-U', $Db.Usuario) }
    $a += @('-d', 'postgres', '-w', '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-c', $Sql)
    return (Invoke-Filho -Exe $Psql -Argumentos $a -Ambiente $amb)
}

function Limpar-BancoEnsaio {
    # UNICO ponto do script que remove um banco: so o descartavel criado nesta execucao,
    # com o nome conferido pelo padrao. Devolve '' se removeu, senao a mensagem de erro.
    param([string]$Psql, $Db, [string]$Nome)
    if (-not (Test-NomeBancoEnsaio $Nome $Db.Banco)) {
        return ('Remocao recusada: nome fora do padrao de ensaio (' + $Nome + ').')
    }
    $res = Invoke-Psql $Psql $Db ('DROP DATABASE "' + $Nome + '"')
    if ($res.Codigo -ne 0) {
        return ("Falha ao remover o banco descartavel {0}; REMOVA-O MANUALMENTE. Saida:`n{1}" -f $Nome, $res.Saida)
    }
    return ''
}

function Cmd-Ensaiar {
    if (-not $script:Ctx.ConfirmarBackup) {
        Falhar 'ensaiar exige -ConfirmarBackup <caminho do .dump> criado ha menos de 24 h (rode backup antes).'
    }
    $raiz = Resolver-Pasta
    $item = Validar-DumpArquivo $script:Ctx.ConfirmarBackup '-ConfirmarBackup'
    $hash = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $cofre = Ler-Cofre
    Exigir-Segredos $cofre @('DATABASE_URL')
    $url = Obter-Segredo $cofre 'DATABASE_URL'
    $db = ConvertFrom-DatabaseUrl $url
    Registrar-Segredo $db.Senha
    $nome = 'auneron_ensaio_' + (Get-Date).ToString('yyyyMMddHHmmss')
    if (-not (Test-NomeBancoEnsaio $nome $db.Banco)) { Falhar 'Nome do banco descartavel recusado (igual ao banco real ou fora do padrao).' }
    $urlEnsaio = Montar-UrlBancoEnsaio $url $nome
    Registrar-Segredo $urlEnsaio
    $pgrestore = Resolver-FerramentaPg 'pg_restore'
    $psql = Resolver-FerramentaPgIrma $pgrestore 'psql'
    if ($script:Ctx.Simular) {
        Info 'SIMULACAO: nada sera criado.'
        Info ('Dump     : {0} ({1} bytes, SHA-256 {2})' -f $item.FullName, $item.Length, $hash)
        Info ('Servidor : host={0} porta={1} usuario={2} (banco real {3} NAO sera tocado)' -f $db.Host, $db.Porta, $db.Usuario, $db.Banco)
        Info ('Descartavel: ' + $nome)
        Info ('pg_restore: ' + $(if ($pgrestore) { $pgrestore } else { 'NAO ENCONTRADO' }))
        Info ('psql      : ' + $(if ($psql) { $psql } else { 'NAO ENCONTRADO' }))
        Info ('Interpretador: ' + (Resolver-Python $raiz))
        Info ('Faria: pg_restore --list; checar permissao de criar banco e inexistencia do nome; criar o banco; pg_restore --no-owner; em {0}\backend, com DATABASE_URL do filho apontando ao descartavel: alembic current, upgrade head, current (exige 1 head e revisao final == head); remover SEMPRE o descartavel; gravar recibo em {1}.' -f $raiz, (Get-EnsaiosDir))
        return
    }
    if (-not $pgrestore) { [void](Exigir-FerramentaPg 'pg_restore') }
    if (-not $psql) { Falhar 'psql.exe nao encontrado na mesma pasta do pg_restore.' }
    $lst = Invoke-Filho -Exe $pgrestore -Argumentos @('--list', $item.FullName)
    if ($lst.Codigo -ne 0) { Falhar 'pg_restore --list reprovou o arquivo informado em -ConfirmarBackup.' }
    $git0 = Obter-InfoGit $raiz
    if (-not $git0.Limpa) { Info 'AVISO: a arvore do checkout esta suja; o recibo sera gravado, mas migrar o recusara.' }
    Info ('Dump aceito: {0} (SHA-256 {1})' -f $item.FullName, $hash)
    Info ('Banco descartavel: {0} em {1}:{2} (banco real {3} nao sera tocado)' -f $nome, $db.Host, $db.Porta, $db.Banco)

    $criado = ''
    $erroCorpo = $null
    $revIni = @()
    $revFim = @()
    $head = ''
    try {
        $perm = Invoke-Psql $psql $db 'SELECT (rolcreatedb OR rolsuper) FROM pg_roles WHERE rolname = current_user'
        if ($perm.Codigo -ne 0) { Falhar ("Nao foi possivel consultar o servidor com psql:`n" + $perm.Saida) }
        if ($perm.Saida.Trim() -ne 't') {
            Falhar ("O usuario '{0}' nao tem permissao para criar bancos (CREATEDB); o ensaio precisa criar um banco descartavel. Peca a um administrador do PostgreSQL: ALTER ROLE <usuario> CREATEDB." -f $db.Usuario)
        }
        $ex = Invoke-Psql $psql $db ("SELECT 1 FROM pg_database WHERE datname = '" + $nome + "'")
        if ($ex.Codigo -ne 0) { Falhar ("Falha ao verificar o banco descartavel:`n" + $ex.Saida) }
        if ($ex.Saida.Trim() -ne '') { Falhar ('O banco ' + $nome + ' ja existe; nada foi feito. Tente de novo.') }
        $cr = Invoke-Psql $psql $db ('CREATE DATABASE "' + $nome + '"')
        if ($cr.Codigo -ne 0) { Falhar ("Falha ao criar o banco descartavel:`n" + $cr.Saida) }
        $criado = $nome
        Info 'Banco descartavel criado. Restaurando o dump ...'
        $amb = @{ 'PGPASSWORD' = $(if ($db.Senha) { $db.Senha } else { $null }); 'PGCLIENTENCODING' = 'UTF8' }
        if ($db.SslMode) { $amb['PGSSLMODE'] = $db.SslMode }
        $ar = @('-h', $db.Host, '-p', [string]$db.Porta)
        if ($db.Usuario) { $ar += @('-U', $db.Usuario) }
        $ar += @('-d', $nome, '--no-owner', '--exit-on-error', '-w', $item.FullName)
        $rs = Invoke-Filho -Exe $pgrestore -Argumentos $ar -Ambiente $amb
        if ($rs.Codigo -ne 0) {
            $l = @($rs.Saida -split "`r?`n" | Select-Object -Last 12)
            Falhar ("pg_restore falhou no banco descartavel (codigo {0}):`n{1}" -f $rs.Codigo, ($l -join "`n"))
        }
        Info 'Restauracao concluida. Rodando alembic no descartavel ...'
        $revIni = @(Extrair-Revisoes (Rodar-Alembic $raiz @('current') $urlEnsaio))
        Info ('Revisao inicial: ' + $(if ($revIni.Count -gt 0) { $revIni -join ', ' } else { '(sem versao)' }))
        $head = Obter-HeadUnico $raiz $urlEnsaio
        $up = Rodar-Alembic $raiz @('upgrade', 'head') $urlEnsaio
        foreach ($l in @($up -split "`r?`n" | Where-Object { $_ -match 'Running upgrade' })) { Info ('  ' + $l) }
        $revFim = @(Extrair-Revisoes (Rodar-Alembic $raiz @('current') $urlEnsaio))
        Info ('Revisao final  : ' + ($revFim -join ', '))
        if ($revFim.Count -ne 1 -or $revFim[0] -ne $head) {
            Falhar ('Revisao final do ensaio ({0}) difere do head ({1}).' -f ($revFim -join ', '), $head)
        }
    }
    catch { $erroCorpo = Mascarar-Texto ([string]$_.Exception.Message) }

    $erroLimpeza = ''
    if ($criado -ne '') {
        $erroLimpeza = Limpar-BancoEnsaio $psql $db $criado
        if ($erroLimpeza -eq '') { Info ('Banco descartavel removido: ' + $criado) }
    }
    if ($erroCorpo -or $erroLimpeza) {
        $partes = @()
        if ($erroCorpo) { $partes += ('ENSAIO FALHOU: ' + $erroCorpo) }
        if ($erroLimpeza) { $partes += $erroLimpeza }
        Falhar ($partes -join "`n")
    }

    $git1 = Obter-InfoGit $raiz
    if ($git1.Commit -ne $git0.Commit -or $git1.Limpa -ne $git0.Limpa) {
        Falhar 'O checkout mudou durante o ensaio (commit ou arvore); nenhum recibo foi gravado.'
    }
    $dirE = Get-EnsaiosDir
    if (-not (Test-Path -LiteralPath $dirE)) { [void](New-Item -ItemType Directory -Force -Path $dirE) }
    $agora = Get-Date
    $arq = Join-Path $dirE ('ensaio-' + $agora.ToString('yyyyMMdd-HHmmss') + '.json')
    if (Test-Path -LiteralPath $arq) { Falhar 'O recibo de ensaio ja existe; o script nunca sobrescreve.' }
    $recibo = [ordered]@{
        sha256         = $hash
        dump           = $item.FullName
        revisao_inicial = ($revIni -join ',')
        revisao_final  = $revFim[0]
        head           = $head
        commit         = $git1.Commit
        arvore_limpa   = $git1.Limpa
        criado_em_utc  = $agora.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'")
    }
    [IO.File]::WriteAllText($arq, ($recibo | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    Info ''
    Info 'ENSAIO CONCLUIDO: o backup restaura e as migracoes pendentes aplicam sem erro.'
    Info ('SHA-256 do dump : ' + $hash)
    Info ('Revisao inicial : ' + $(if ($revIni.Count -gt 0) { $revIni -join ', ' } else { '(sem versao)' }))
    Info ('Revisao final   : ' + $revFim[0] + ' (head)')
    Info ('Commit          : {0} (arvore limpa: {1})' -f $git1.Commit, $(if ($git1.Limpa) { 'sim' } else { 'NAO' }))
    Info ('Recibo          : ' + $arq)
}

function Cmd-RegistrarAcaoPagamento {
    $raiz = Resolver-Pasta
    $modulo = 'scripts.register_account_mark_paid_skill'
    $arqPy = Join-Path $raiz 'backend\scripts\register_account_mark_paid_skill.py'
    if (-not (Test-Path -LiteralPath $arqPy -PathType Leaf)) {
        Falhar 'Nao existe backend\scripts\register_account_mark_paid_skill.py no checkout informado.'
    }
    $py = Resolver-Python $raiz
    if ($script:Ctx.Simular) {
        Info 'SIMULACAO: nada sera feito.'
        Info ('Faria, em {0}\backend: {1} -m {2}' -f $raiz, $py, $modulo)
        Info ('Ambiente do filho (nomes): ' + (Nomes-Do-Ambiente 'backend'))
        return
    }
    $amb = Montar-Ambiente 'backend'
    $res = Invoke-Filho -Exe $py -Argumentos @('-m', $modulo) -DiretorioTrabalho (Join-Path $raiz 'backend') -Ambiente $amb
    if ($res.Saida) { Info $res.Saida }
    if ($res.Codigo -ne 0) {
        $script:Codigo = $res.Codigo
        Falhar ('{0} terminou com codigo {1}.' -f $modulo, $res.Codigo)
    }
}

function Cmd-Configurar {
    # Recusa ANTES de ler qualquer coisa.
    $interativo = [Environment]::UserInteractive
    $redirecionado = $false
    try { $redirecionado = [Console]::IsInputRedirected } catch { $redirecionado = $true }
    if ((-not $interativo) -or $redirecionado) {
        Falhar 'configurar so roda em terminal interativo (o diretor, no terminal dele). Sessao nao interativa ou entrada redirecionada: nada foi lido.'
    }
    $cofre = Ler-Cofre
    $novo = @{}
    foreach ($k in $cofre.Keys) { $novo[$k] = $cofre[$k] }
    Info ('Cofre: ' + (Get-CofreCaminho))
    Info 'Digite o valor de cada segredo (nao aparece na tela). Enter mantem o valor ja gravado.'
    foreach ($n in $script:NomesSegredos) {
        $estado = 'nao definido'
        if ($novo.ContainsKey($n)) { $estado = 'definido' }
        $ss = Read-Host -Prompt ("{0} [{1}]" -f $n, $estado) -AsSecureString
        if ($ss.Length -eq 0) {
            if (-not $novo.ContainsKey($n)) { Info ('  ' + $n + ': continua nao definido') }
            else { Info ('  ' + $n + ': mantido') }
            continue
        }
        $ok = $true
        if ($n -eq 'API_KEY' -and $ss.Length -lt 32) {
            Info '  API_KEY: precisa ter pelo menos 32 caracteres; valor ignorado.'
            $ok = $false
        }
        if ($ok -and $n -eq 'DATABASE_URL') {
            $txt = Secure-ParaTexto $ss
            try { [void](ConvertFrom-DatabaseUrl $txt) }
            catch { Info ('  DATABASE_URL: ' + $_.Exception.Message + ' Valor ignorado.'); $ok = $false }
            $txt = $null
        }
        if ($ok) {
            $novo[$n] = ConvertFrom-SecureString -SecureString $ss
            Info ('  ' + $n + ': gravado')
        }
    }
    Salvar-Cofre $novo
    Info ''
    Info 'Situacao do cofre:'
    $cofre2 = Ler-Cofre
    foreach ($n in $script:NomesSegredos) {
        Info ('  {0}: {1}' -f $n, $(if ($cofre2.ContainsKey($n)) { 'definido' } else { 'nao definido' }))
    }
}

function Cmd-Status {
    $caminho = Get-CofreCaminho
    Info '== Cofre =='
    Info ('Caminho: ' + $caminho)
    $existe = Test-Path -LiteralPath $caminho
    Info ('Existe : ' + $(if ($existe) { 'sim' } else { 'nao' }))
    if ($existe) {
        $cofre = Ler-Cofre
        foreach ($n in $script:NomesSegredos) {
            Info ('  {0}: {1}' -f $n, $(if ($cofre.ContainsKey($n)) { 'definido' } else { 'nao definido' }))
        }
    }
    Info ''
    Info '== Configuracao nao secreta (padrao de homologacao) =='
    foreach ($k in $script:ConfigPadrao.Keys) { Info ('  {0}={1}' -f $k, $script:ConfigPadrao[$k]) }
    Info ''
    Info '== Estado gravado pelo script =='
    $e = Ler-Estado
    if ($e.Count -eq 0) { Info '  (vazio)' }
    foreach ($k in $e.Keys) { Info ('  {0}: PID {1}, pasta {2}, iniciado {3}' -f $k, $e[$k].pid, $e[$k].pasta, $e[$k].iniciado) }
    Info ''
    if ($script:Ctx.SemSondagem) {
        Info '== Portas e HTTP: nao consultados (-SemSondagem) =='
        return
    }
    Info '== Portas =='
    foreach ($par in @(@($script:PortaBackend, 'backend'), @($script:PortaFrontend, 'frontend'))) {
        $porta = [int]$par[0]
        $alvo = [string]$par[1]
        $esc = @(Get-Escutando $porta)
        if ($esc.Count -eq 0) { Info ('  {0}: livre' -f $porta); continue }
        foreach ($i in $esc) {
            $casa = Test-ProcessoAlvo -Nome $i.Nome -LinhaComando $i.LinhaComando -Alvo $alvo
            Info ('  {0}: {1}; reconhecido como {2}: {3}' -f $porta, (Descrever-Escutando $i), $alvo, $(if ($casa) { 'sim' } else { 'NAO' }))
            if ($i.Executavel) { Info ('        executavel: ' + $i.Executavel) }
        }
    }
    Info ''
    Info '== Backend HTTP =='
    Info ('  GET /health : ' + (Get-Http ($script:UrlBackend + '/health')) + ' (0 = sem resposta)')
    Info ('  GET /ready  : ' + (Get-Http ($script:UrlBackend + '/ready')) + ' (503 = banco offline)')
}

function Cmd-VersaoBanco {
    $raiz = Resolver-Pasta
    $atual = @(Extrair-Revisoes (Rodar-Alembic $raiz @('current')))
    if ($atual.Count -eq 0) { Info 'Revisao atual: (banco sem versao)' }
    else { Info ('Revisao atual: ' + ($atual -join ', ')) }
}

function Cmd-Pendentes {
    $raiz = Resolver-Pasta
    [void](Mostrar-Pendentes $raiz)
}

function Cmd-Backup {
    $cofre = Ler-Cofre
    Exigir-Segredos $cofre @('DATABASE_URL')
    $url = Obter-Segredo $cofre 'DATABASE_URL'
    $db = ConvertFrom-DatabaseUrl $url
    Registrar-Segredo $db.Senha
    if ($script:Ctx.Destino) { $dest = [IO.Path]::GetFullPath($script:Ctx.Destino) }
    else { $dest = Join-Path (Join-Path (Get-BaseDir) 'backups') ('hml-' + (Get-Date).ToString('yyyyMMdd-HHmmss') + '.dump') }
    if (Test-Path -LiteralPath $dest) { Falhar 'O destino do backup ja existe; o script nunca sobrescreve. Escolha outro -Destino.' }
    $pgdump = Resolver-FerramentaPg 'pg_dump'
    if ($script:Ctx.Simular) {
        Info 'SIMULACAO: nada sera feito.'
        Info ('Banco   : host={0} porta={1} usuario={2} banco={3}' -f $db.Host, $db.Porta, $db.Usuario, $db.Banco)
        Info ('Destino : ' + $dest)
        if ($pgdump) { Info ('pg_dump : ' + $pgdump) }
        else { Info 'AVISO: pg_dump nao encontrado; instale o PostgreSQL Client Tools ou ponha a pasta bin no PATH.' }
        Info 'Faria: pg_dump -F c (senha via PGPASSWORD no ambiente), pg_restore --list, tamanho e SHA-256.'
        return
    }
    if (-not $pgdump) { [void](Exigir-FerramentaPg 'pg_dump') }
    $pgrestore = Exigir-FerramentaPg 'pg_restore'
    $dir = Split-Path -Parent $dest
    if (-not (Test-Path -LiteralPath $dir)) { [void](New-Item -ItemType Directory -Force -Path $dir) }
    $amb = @{ 'PGPASSWORD' = $(if ($db.Senha) { $db.Senha } else { $null }); 'PGCLIENTENCODING' = 'UTF8' }
    if ($db.SslMode) { $amb['PGSSLMODE'] = $db.SslMode }
    $args1 = @('-h', $db.Host, '-p', [string]$db.Porta)
    if ($db.Usuario) { $args1 += @('-U', $db.Usuario) }
    $args1 += @('-d', $db.Banco, '-F', 'c', '-w', '-f', $dest)
    Info ('Executando pg_dump em {0}:{1}/{2} ...' -f $db.Host, $db.Porta, $db.Banco)
    $res = Invoke-Filho -Exe $pgdump -Argumentos $args1 -Ambiente $amb
    if ($res.Codigo -ne 0) {
        if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Force }
        Falhar ("pg_dump falhou (codigo {0}):`n{1}" -f $res.Codigo, $res.Saida)
    }
    $lst = Invoke-Filho -Exe $pgrestore -Argumentos @('--list', $dest)
    if ($lst.Codigo -ne 0) {
        Falhar ("pg_restore --list reprovou o arquivo; NAO USE este dump: {0}`n{1}" -f $dest, $lst.Saida)
    }
    $item = Get-Item -LiteralPath $dest
    if ($item.Length -le 0) { Falhar ('Dump vazio: ' + $dest) }
    $hash = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash
    $entradas = @($lst.Saida -split "`r?`n" | Where-Object { $_ -and -not $_.StartsWith(';') }).Count
    Info 'Backup validado.'
    Info ('Arquivo : ' + $dest)
    Info ('Tamanho : {0} bytes' -f $item.Length)
    Info ('SHA-256 : ' + $hash)
    Info ('Entradas no catalogo (pg_restore --list): ' + $entradas)
}

function Cmd-Migrar {
    if (-not $script:Ctx.ConfirmarBackup) {
        Falhar 'migrar exige -ConfirmarBackup <caminho do .dump> criado ha menos de 24 h (rode backup antes).'
    }
    if ($script:Ctx.SemSondagem -and -not $script:Ctx.Simular) {
        Falhar '-SemSondagem so vale com -Simular ou status.'
    }
    $raiz = Resolver-Pasta
    $item = Validar-DumpArquivo $script:Ctx.ConfirmarBackup '-ConfirmarBackup'
    $idade = (Get-Date) - $item.LastWriteTime
    $hash = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $recibos = @(Ler-RecibosEnsaio)
    if (@($recibos | Where-Object { [string]$_.sha256 -ieq $hash }).Count -eq 0) {
        Falhar ('Nao ha recibo de ensaio para este dump (SHA-256 {0}); rode ensaiar antes de migrar.' -f $hash)
    }
    $pgrestore = Exigir-FerramentaPg 'pg_restore'
    $lst = Invoke-Filho -Exe $pgrestore -Argumentos @('--list', $item.FullName)
    if ($lst.Codigo -ne 0) { Falhar 'pg_restore --list reprovou o arquivo informado em -ConfirmarBackup.' }
    $gitInfo = Obter-InfoGit $raiz
    if (-not $gitInfo.Limpa) { Falhar 'A arvore do checkout de -Pasta esta suja; migrar exige checkout limpo (e um novo ensaiar).' }
    $headAtual = Obter-HeadUnico $raiz
    $mot = Selecionar-ReciboEnsaio $recibos $hash $gitInfo.Commit $headAtual ((Get-Date).ToUniversalTime())
    if ($mot -ne '') { Falhar $mot }
    Info ('Recibo de ensaio aceito (commit {0}, head {1}).' -f $gitInfo.Commit, $headAtual)
    Exigir-PortaLivre $script:PortaBackend
    Info ('Backup aceito: {0} ({1:N1} h, {2} bytes)' -f $item.FullName, $idade.TotalHours, $item.Length)
    if ($script:Ctx.Simular) {
        Info 'SIMULACAO: nada sera feito.'
        Info ('Faria, em {0}\backend: alembic current/heads/history (somente leitura), depois python -m alembic upgrade head, depois alembic current.' -f $raiz)
        Info ('Interpretador: ' + (Resolver-Python $raiz))
        Info ('Ambiente do filho (nomes): ' + (Nomes-Do-Ambiente 'backend'))
        return
    }
    Info 'Pendentes antes de migrar:'
    [void](Mostrar-Pendentes $raiz)
    Info 'Aplicando: alembic upgrade head ...'
    $saida = Rodar-Alembic $raiz @('upgrade', 'head')
    $linhas = @($saida -split "`r?`n" | Where-Object { $_ -match 'Running upgrade' })
    foreach ($l in $linhas) { Info ('  ' + $l) }
    $atual = @(Extrair-Revisoes (Rodar-Alembic $raiz @('current')))
    Info ('Revisao final: ' + ($atual -join ', '))
}

function Aguardar-Porta {
    param([int]$Porta, $Processo, [int]$TimeoutSeg)
    $limite = (Get-Date).AddSeconds($TimeoutSeg)
    while ((Get-Date) -lt $limite) {
        $esc = @(Get-Escutando $Porta)
        if ($esc.Count -gt 0) { return $esc }
        if ($Processo -and $Processo.HasExited) { return @() }
        Start-Sleep -Milliseconds 1000
    }
    return @()
}

function Mostrar-FimDoLog {
    param([string]$Arquivo)
    if (Test-Path -LiteralPath $Arquivo) {
        $l = @(Get-Content -LiteralPath $Arquivo -Tail 15 -ErrorAction SilentlyContinue)
        foreach ($x in $l) { Info ('   | ' + (Mascarar-Texto $x)) }
    }
}

function Iniciar-Servico {
    param([string]$Alvo, [int]$Porta, [string]$Exe, [string[]]$Args3, [string]$Dir, [string]$Raiz)
    $logs = Join-Path (Get-BaseDir) 'logs'
    $carimbo = (Get-Date).ToString('yyyyMMdd-HHmmss')
    $logOut = Join-Path $logs ($Alvo + '-' + $carimbo + '.out.log')
    $logErr = Join-Path $logs ($Alvo + '-' + $carimbo + '.err.log')
    $linhaArgs = (($Args3 | ForEach-Object { ConvertTo-ArgWin $_ }) -join ' ')
    if ($script:Ctx.Simular) {
        Info 'SIMULACAO: nada sera feito.'
        Info ('Interpretador/executavel: ' + $Exe)
        Info ('Executaria : {0} {1}' -f $Exe, $linhaArgs)
        Info ('Em         : ' + $Dir)
        Info ('Ambiente   : ' + (Nomes-Do-Ambiente $Alvo) + ' (valores do cofre, so no ambiente do processo filho)')
        Info ('Logs       : ' + $logOut + ' e ' + $logErr)
        return
    }
    if (-not (Test-Path -LiteralPath $logs)) { [void](New-Item -ItemType Directory -Force -Path $logs) }
    $entrada = Join-Path (Get-BaseDir) 'entrada-vazia.txt'
    if (-not (Test-Path -LiteralPath $entrada)) { Set-Content -LiteralPath $entrada -Value '' -Encoding ASCII }
    $amb = Montar-Ambiente $Alvo
    $salvo = Aplicar-Ambiente $amb
    $proc = $null
    try {
        $proc = Start-Process -FilePath $Exe -ArgumentList $linhaArgs -WorkingDirectory $Dir -WindowStyle Hidden -PassThru `
            -RedirectStandardInput $entrada -RedirectStandardOutput $logOut -RedirectStandardError $logErr
    }
    finally { Restaurar-Ambiente $salvo }
    Info ('Executavel usado: ' + $Exe)
    Info ('Processo iniciado (PID inicial {0}); aguardando a porta {1} ...' -f $proc.Id, $Porta)
    $esc = @(Aguardar-Porta -Porta $Porta -Processo $proc -TimeoutSeg 60)
    if ($esc.Count -eq 0) {
        Info 'A porta nao abriu. Final do log de erro:'
        Mostrar-FimDoLog $logErr
        Mostrar-FimDoLog $logOut
        Falhar ('{0} nao subiu na porta {1} (logs em {2}).' -f $Alvo, $Porta, $logs)
    }
    $dono = $esc[0]
    Gravar-Estado $Alvo $dono.Pid $Raiz
    if ($Alvo -eq 'backend') {
        $codigo = Get-Http ($script:UrlBackend + '/health')
        if ($codigo -ne 200) { Falhar ('Backend escutando, mas GET /health retornou ' + $codigo + '.') }
        $pronto = Get-Http ($script:UrlBackend + '/ready')
        Info ('Backend no ar: PID {0}, /health 200, /ready {1}.' -f $dono.Pid, $pronto)
    }
    else {
        $codigo = Get-Http ($script:UrlFrontend + '/')
        if ($codigo -ne 200) { Falhar ('Frontend escutando, mas GET / retornou ' + $codigo + '.') }
        Info ('Frontend no ar: PID {0}, GET / 200.' -f $dono.Pid)
    }
    Info ('Logs: {0} | {1}' -f $logOut, $logErr)
}

function Cmd-SubirBackend {
    $raiz = Resolver-Pasta
    $py = Resolver-Python $raiz
    $cofre = Ler-Cofre
    Exigir-Segredos $cofre $script:NomesSegredos
    Exigir-PortaLivre $script:PortaBackend
    $args3 = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', [string]$script:PortaBackend)
    Iniciar-Servico -Alvo 'backend' -Porta $script:PortaBackend -Exe $py -Args3 $args3 -Dir (Join-Path $raiz 'backend') -Raiz $raiz
}

function Cmd-SubirFrontend {
    $raiz = Resolver-Pasta
    $vite = Join-Path $raiz 'frontend\node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $vite -PathType Leaf)) {
        Falhar ("node_modules ausente em {0}\frontend. Rode la: npm ci (o script nao instala nada)." -f $raiz)
    }
    $node = Get-Command node.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $node) { Falhar 'node.exe nao encontrado no PATH.' }
    $cofre = Ler-Cofre
    Exigir-Segredos $cofre @('API_KEY')
    Exigir-PortaLivre $script:PortaFrontend
    if (-not $script:Ctx.SemSondagem) {
        if (@(Get-Escutando $script:PortaBackend).Count -eq 0) { Info 'AVISO: nada escuta na porta 8000; o frontend subira, mas /api falhara ate o backend subir.' }
    }
    $args3 = @($vite, '--host', '127.0.0.1', '--port', [string]$script:PortaFrontend, '--strictPort')
    Iniciar-Servico -Alvo 'frontend' -Porta $script:PortaFrontend -Exe $node.Source -Args3 $args3 -Dir (Join-Path $raiz 'frontend') -Raiz $raiz
}

function Cmd-Parar {
    param([string]$Alvo, [int]$Porta)
    if ($script:Ctx.SemSondagem) { Falhar '-SemSondagem nao se aplica a parar-*.' }
    $esc = @(Get-Escutando $Porta)
    if ($esc.Count -eq 0) { Info ('Nada escutando na porta {0}; nada a parar.' -f $Porta); return }
    $alvos = @($esc | Where-Object { Test-ProcessoAlvo -Nome $_.Nome -LinhaComando $_.LinhaComando -Alvo $Alvo })
    if ($alvos.Count -ne $esc.Count) {
        foreach ($i in $esc) { Info ('  escutando: ' + (Descrever-Escutando $i)) }
        Falhar ('A porta {0} esta ocupada por processo que NAO e o {1} esperado; nada foi parado.' -f $Porta, $Alvo)
    }
    foreach ($i in $alvos) { Info ('Sera parado: ' + (Descrever-Escutando $i)) }
    if ($script:Ctx.Simular) { Info 'SIMULACAO: nada foi parado.'; return }
    if (-not $script:Ctx.Confirmar) { Info 'Nada foi parado. Repita com -Confirmar para parar.'; return }
    foreach ($i in $alvos) {
        $atual = Get-CimInstance -ClassName Win32_Process -Filter ("ProcessId=" + $i.Pid) -ErrorAction SilentlyContinue
        if (-not $atual -or -not (Test-ProcessoAlvo -Nome $atual.Name -LinhaComando $atual.CommandLine -Alvo $Alvo)) {
            Falhar ('O PID {0} mudou desde a verificacao; nada mais foi parado.' -f $i.Pid)
        }
        Stop-Process -Id $i.Pid -Force
    }
    $limite = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $limite -and @(Get-Escutando $Porta).Count -gt 0) { Start-Sleep -Milliseconds 500 }
    if (@(Get-Escutando $Porta).Count -gt 0) { Falhar ('A porta {0} continua ocupada apos parar.' -f $Porta) }
    Gravar-Estado $Alvo $null ''
    Info ('{0} parado; porta {1} livre.' -f $Alvo, $Porta)
}

# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------
function Principal {
    $ErrorActionPreference = 'Stop'
    $script:Codigo = 0
    try {
        $script:Ctx.Base = $Base
        $script:Ctx.Cofre = $Cofre
        $script:Ctx.Pasta = $Pasta
        $script:Ctx.Simular = [bool]$Simular
        $script:Ctx.Confirmar = [bool]$Confirmar
        $script:Ctx.SemSondagem = [bool]$SemSondagem
        $script:Ctx.Destino = $Destino
        $script:Ctx.ConfirmarBackup = $ConfirmarBackup
        if ($SemSondagem -and ($Comando -ne 'status') -and (-not $Simular)) {
            Falhar '-SemSondagem so vale com status ou -Simular.'
        }
        switch ($Comando) {
            'configurar'     { Cmd-Configurar }
            'status'         { Cmd-Status }
            'versao-banco'   { Cmd-VersaoBanco }
            'pendentes'      { Cmd-Pendentes }
            'backup'         { Cmd-Backup }
            'ensaiar'        { Cmd-Ensaiar }
            'migrar'         { Cmd-Migrar }
            'registrar-acao-pagamento' { Cmd-RegistrarAcaoPagamento }
            'subir-backend'  { Cmd-SubirBackend }
            'parar-backend'  { Cmd-Parar 'backend' $script:PortaBackend }
            'subir-frontend' { Cmd-SubirFrontend }
            'parar-frontend' { Cmd-Parar 'frontend' $script:PortaFrontend }
        }
    }
    catch {
        if ($script:Codigo -eq 0) { $script:Codigo = 1 }
        $msg = Mascarar-Texto ([string]$_.Exception.Message)
        [Console]::Error.WriteLine('ERRO: ' + $msg)
    }
}

if ($PSCmdlet.ParameterSetName -eq 'Funcoes') { return }

Principal
exit $script:Codigo
