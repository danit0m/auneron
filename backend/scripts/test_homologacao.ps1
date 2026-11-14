<#
.SYNOPSIS
    Autoteste offline de homologacao.ps1 (sem banco, sem rede, sem processos reais).

.DESCRIPTION
    Usa cofre e pastas TEMPORARIOS com valores FALSOS; apaga tudo no fim.
    Nao toca no cofre padrao, nao sobe nem para servidor, nao consulta portas.

    Execucao:
      powershell.exe -NoProfile -ExecutionPolicy Bypass -File backend\scripts\test_homologacao.ps1
#>
$ErrorActionPreference = 'Stop'
$alvoScript = Join-Path $PSScriptRoot 'homologacao.ps1'
. $alvoScript -CarregarFuncoes

$script:Falhas = 0
$script:Total = 0

function Verificar {
    param([string]$Nome, [bool]$Condicao, [string]$Detalhe = '')
    $script:Total++
    if ($Condicao) { Write-Host ('  PASS  ' + $Nome) }
    else {
        $script:Falhas++
        Write-Host ('  FAIL  ' + $Nome + $(if ($Detalhe) { '  -> ' + $Detalhe } else { '' }))
    }
}

function Rodar-Script {
    param([string[]]$Argumentos)
    $psExe = Join-Path $PSHOME 'powershell.exe'
    $todos = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $alvoScript) + $Argumentos
    return (Invoke-Filho -Exe $psExe -Argumentos $todos)
}

function Lancar {
    param([scriptblock]$Bloco)
    try { & $Bloco | Out-Null; return '' } catch { return [string]$_.Exception.Message }
}

# Valores FALSOS (distintivos para busca nas saidas).
$fakeSenhaCodificada = 'ZZfake%40pw%2F9%3Aq'
$fakeSenha = 'ZZfake@pw/9:q'
$fakeUrl = 'postgresql+psycopg://fakeuser:' + $fakeSenhaCodificada + '@fakehost:5999/fakedb'
$fakeChave = 'FAKEAPIKEYQQ_0123456789abcdefghijklmnop'
$proibidos = @($fakeUrl, $fakeSenha, $fakeSenhaCodificada, 'ZZfake', $fakeChave, 'FAKEAPIKEYQQ')

$tmp = Join-Path ([IO.Path]::GetTempPath()) ('auneron-hml-test-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Force -Path $tmp)

try {
    Write-Host '== Parser de DATABASE_URL =='
    $p = ConvertFrom-DatabaseUrl 'postgresql://u:p@h:5433/db'
    Verificar 'postgresql:// basico' ($p.Host -eq 'h' -and $p.Porta -eq 5433 -and $p.Usuario -eq 'u' -and $p.Senha -eq 'p' -and $p.Banco -eq 'db')
    $p = ConvertFrom-DatabaseUrl 'postgresql+psycopg://auneron:CHANGE%40ME%2F%3A%25@localhost:5432/auneron'
    Verificar 'psycopg + percent-encoding (@ / : %)' ($p.Senha -eq 'CHANGE@ME/:%' -and $p.Host -eq 'localhost' -and $p.Banco -eq 'auneron') $p.Senha
    $p = ConvertFrom-DatabaseUrl 'postgresql+psycopg2://u@h/db'
    Verificar 'psycopg2 sem senha, porta padrao' ($p.Senha -eq '' -and $p.Porta -eq 5432 -and $p.Usuario -eq 'u')
    $p = ConvertFrom-DatabaseUrl 'postgresql:///db'
    Verificar 'sem host vira localhost' ($p.Host -eq 'localhost' -and $p.Banco -eq 'db')
    $p = ConvertFrom-DatabaseUrl 'postgresql://u:p@[::1]:5433/db'
    Verificar 'host IPv6' ($p.Host -eq '::1' -and $p.Porta -eq 5433)
    $p = ConvertFrom-DatabaseUrl 'postgresql://u:p@ss@h/db'
    Verificar '@ nao codificado na senha (ultimo @ separa)' ($p.Senha -eq 'p@ss' -and $p.Host -eq 'h')
    $p = ConvertFrom-DatabaseUrl 'postgresql://u:p%2Bq+r@h/db?sslmode=require&x=1'
    Verificar 'sslmode na query; + literal; %2B decodificado' ($p.SslMode -eq 'require' -and $p.Senha -eq 'p+q+r')
    $p = ConvertFrom-DatabaseUrl $fakeUrl
    Verificar 'URL falsa de teste' ($p.Senha -eq $fakeSenha -and $p.Host -eq 'fakehost' -and $p.Porta -eq 5999 -and $p.Banco -eq 'fakedb')
    $m = Lancar { ConvertFrom-DatabaseUrl ('mysql://u:ZZfakeBAD@h/db') }
    Verificar 'esquema invalido recusado, sem eco da URL' ($m -ne '' -and $m -notmatch 'ZZfake')
    $m = Lancar { ConvertFrom-DatabaseUrl 'postgresql://u:p@h:5432' }
    Verificar 'sem banco recusado' ($m -match 'banco')
    $m = Lancar { ConvertFrom-DatabaseUrl 'postgresql://u:p@h:99999/db' }
    Verificar 'porta fora do intervalo recusada' ($m -match 'porta')
    $m = Lancar { ConvertFrom-DatabaseUrl 'postgresql://u:p@-oevil/db' }
    Verificar 'host com hifen inicial recusado' ($m -match 'hifen')

    Write-Host '== Filtro de processo do parar-* (funcao pura, dados falsos) =='
    $py = 'C:\x\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000'
    Verificar 'backend: python + uvicorn app.main:app casa' (Test-ProcessoAlvo 'python.exe' $py 'backend')
    Verificar 'backend: python3.11.exe casa' (Test-ProcessoAlvo 'python3.11.exe' $py 'backend')
    Verificar 'backend: python generico sem uvicorn NAO casa' (-not (Test-ProcessoAlvo 'python.exe' 'python.exe outro.py' 'backend'))
    Verificar 'backend: uvicorn de outro app NAO casa' (-not (Test-ProcessoAlvo 'python.exe' 'python.exe -m uvicorn outro.app:app' 'backend'))
    Verificar 'backend: nome node NAO casa' (-not (Test-ProcessoAlvo 'node.exe' $py 'backend'))
    Verificar 'backend: linha vazia NAO casa' (-not (Test-ProcessoAlvo 'python.exe' '' 'backend'))
    $nd = 'node.exe C:\a\frontend\node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5180 --strictPort'
    Verificar 'frontend: node + vite.js casa' (Test-ProcessoAlvo 'node.exe' $nd 'frontend')
    Verificar 'frontend: node sem vite NAO casa' (-not (Test-ProcessoAlvo 'node.exe' 'node.exe server.js' 'frontend'))
    Verificar 'frontend: vitest NAO casa' (-not (Test-ProcessoAlvo 'node.exe' 'node.exe C:\a\vitest\run.js' 'frontend'))
    Verificar 'frontend: pasta vite-project NAO casa' (-not (Test-ProcessoAlvo 'node.exe' 'node.exe C:\vite-project\app.js' 'frontend'))
    Verificar 'frontend: python com vite NAO casa' (-not (Test-ProcessoAlvo 'python.exe' $nd 'frontend'))
    Verificar 'alvo desconhecido NAO casa' (-not (Test-ProcessoAlvo 'node.exe' $nd 'banco'))

    Write-Host '== Mascaramento =='
    Registrar-Segredo $fakeSenha
    $t = Mascarar-Texto ('erro com ' + $fakeSenha + ' e ' + $fakeSenhaCodificada)
    Verificar 'valor e variante codificada mascarados' ($t -notmatch 'ZZfake')
    $t = Remover-ValoresDeLinha 'node.exe vite.js --api-key abc123secretXYZ postgresql://u:pw@h/db'
    Verificar 'linha de comando de terceiros sem credenciais' ($t -notmatch 'abc123secretXYZ' -and $t -notmatch ':pw@')

    Write-Host '== Execucao isolada (cofre temporario, valores falsos) =='
    $base = Join-Path $tmp 'base'
    $cofreArq = Join-Path $base 'cofre.xml'
    [void](New-Item -ItemType Directory -Force -Path $base)
    $script:Ctx.Cofre = $cofreArq
    $script:Ctx.Base = $base
    $cif = @{
        'DATABASE_URL' = (ConvertTo-SecureString -String $fakeUrl -AsPlainText -Force | ConvertFrom-SecureString)
        'API_KEY'      = (ConvertTo-SecureString -String $fakeChave -AsPlainText -Force | ConvertFrom-SecureString)
    }
    Salvar-Cofre $cif
    $txtCofre = [IO.File]::ReadAllText($cofreArq)
    $vazou = @($proibidos | Where-Object { $txtCofre.Contains($_) })
    Verificar 'arquivo do cofre nao contem texto puro' ($vazou.Count -eq 0) ($vazou -join ',')
    $lido = Ler-Cofre
    Verificar 'cofre: nomes lidos em claro' ($lido.ContainsKey('DATABASE_URL') -and $lido.ContainsKey('API_KEY'))
    Verificar 'cofre: ida e volta DPAPI' ((Decifrar-Valor $lido['API_KEY']) -eq $fakeChave)
    $script:Ctx.Cofre = ''
    $script:Ctx.Base = ''

    # pasta falsa de checkout
    $pasta = Join-Path $tmp 'checkout'
    [void](New-Item -ItemType Directory -Force -Path (Join-Path $pasta 'backend'))
    [void](New-Item -ItemType Directory -Force -Path (Join-Path $pasta 'frontend\node_modules\vite\bin'))
    Set-Content -LiteralPath (Join-Path $pasta 'backend\alembic.ini') -Value '[alembic]' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $pasta 'frontend\package.json') -Value '{}' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $pasta 'frontend\node_modules\vite\bin\vite.js') -Value '' -Encoding ASCII
    [void](New-Item -ItemType Directory -Force -Path (Join-Path $pasta 'backend\.venv\Scripts'))
    Set-Content -LiteralPath (Join-Path $pasta 'backend\.venv\Scripts\python.exe') -Value '' -Encoding ASCII
    $comuns = @('-Cofre', $cofreArq, '-Base', $base)

    $saidas = @{}
    $r = Rodar-Script (@('status', '-SemSondagem') + $comuns)
    $saidas['status'] = $r.Saida
    Verificar 'status: exit 0' ($r.Codigo -eq 0) $r.Saida
    Verificar 'status: mostra nomes definidos' ($r.Saida -match 'DATABASE_URL: definido' -and $r.Saida -match 'API_KEY: definido')

    $r = Rodar-Script (@('subir-backend', '-Simular', '-SemSondagem', '-Pasta', $pasta) + $comuns)
    $saidas['subir-backend'] = $r.Saida
    Verificar 'subir-backend -Simular: exit 0' ($r.Codigo -eq 0) $r.Saida
    Verificar 'subir-backend -Simular: mostra plano uvicorn e o interpretador (.venv do checkout)' ($r.Saida -match 'uvicorn' -and $r.Saida -match 'SIMULACAO' -and $r.Saida.Contains('backend\.venv\Scripts\python.exe')) $r.Saida

    $r = Rodar-Script (@('subir-frontend', '-Simular', '-SemSondagem', '-Pasta', $pasta) + $comuns)
    $saidas['subir-frontend'] = $r.Saida
    Verificar 'subir-frontend -Simular: exit 0 (se node no PATH)' (($r.Codigo -eq 0) -or ($r.Saida -match 'node.exe nao encontrado')) $r.Saida

    $r = Rodar-Script (@('backup', '-Simular', '-Destino', (Join-Path $tmp 'x.dump')) + $comuns)
    $saidas['backup'] = $r.Saida
    Verificar 'backup -Simular: exit 0, mostra banco sem senha' ($r.Codigo -eq 0 -and $r.Saida -match 'fakehost' -and $r.Saida -match '5999') $r.Saida

    $r = Rodar-Script (@('migrar', '-Simular', '-SemSondagem', '-Pasta', $pasta) + $comuns)
    $saidas['migrar-sem-backup'] = $r.Saida
    Verificar 'migrar sem -ConfirmarBackup: recusa (exit != 0)' ($r.Codigo -ne 0 -and $r.Saida -match 'ConfirmarBackup') $r.Saida

    $r = Rodar-Script (@('migrar', '-Simular', '-SemSondagem', '-Pasta', $pasta, '-ConfirmarBackup', (Join-Path $tmp 'nao-existe.dump')) + $comuns)
    $saidas['migrar-backup-inexistente'] = $r.Saida
    Verificar 'migrar com backup inexistente: recusa' ($r.Codigo -ne 0 -and $r.Saida -match 'nao existe') $r.Saida

    $velho = Join-Path $tmp 'velho.dump'
    Set-Content -LiteralPath $velho -Value 'x' -Encoding ASCII
    (Get-Item -LiteralPath $velho).LastWriteTime = (Get-Date).AddDays(-2)
    $r = Rodar-Script (@('migrar', '-Simular', '-SemSondagem', '-Pasta', $pasta, '-ConfirmarBackup', $velho) + $comuns)
    $saidas['migrar-backup-velho'] = $r.Saida
    Verificar 'migrar com backup > 24 h: recusa' ($r.Codigo -ne 0 -and $r.Saida -match '24 h') $r.Saida

    $r = Rodar-Script (@('migrar', '-SemSondagem', '-Pasta', $pasta, '-ConfirmarBackup', $velho) + $comuns)
    Verificar '-SemSondagem sem -Simular em migrar: recusa' ($r.Codigo -ne 0 -and $r.Saida -match 'SemSondagem') $r.Saida

    $r = Rodar-Script (@('subir-backend', '-Simular', '-SemSondagem') + $comuns)
    Verificar 'subir-backend sem -Pasta: recusa' ($r.Codigo -ne 0 -and $r.Saida -match 'Pasta') $r.Saida

    $r = Rodar-Script (@('subir-backend', '-Simular', '-SemSondagem', '-Pasta', $tmp) + $comuns)
    Verificar 'pasta sem alembic.ini/package.json: recusa' ($r.Codigo -ne 0 -and $r.Saida -match 'alembic.ini') $r.Saida

    $r = Rodar-Script (@('apagar-tudo') + $comuns)
    $saidas['desconhecido'] = $r.Saida
    Verificar 'comando desconhecido: recusa (exit != 0)' ($r.Codigo -ne 0) $r.Saida

    $r = Rodar-Script (@('exportar-segredos') + $comuns)
    Verificar 'comando de exportar valores nao existe' ($r.Codigo -ne 0)
    $r = Rodar-Script (@('subir-backend', '-Simular', '-SemSondagem', '-Pasta', $pasta, '-Python', 'C:\evil\env.exe') + $comuns)
    Verificar '-Python recusado como parametro desconhecido' ($r.Codigo -ne 0 -and $r.Saida -match 'Python')
    Verificar 'stub WindowsApps recusado' (Test-PythonStoreStub 'C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe')
    Verificar 'python real nao e stub' (-not (Test-PythonStoreStub 'C:\Python311\python.exe'))
    Verificar 'caminho vazio nao e stub' (-not (Test-PythonStoreStub ''))


    $base2 = Join-Path $tmp 'base2'
    $cofre2 = Join-Path $base2 'cofre.xml'
    $r = Rodar-Script @('configurar', '-Cofre', $cofre2, '-Base', $base2)
    $saidas['configurar'] = $r.Saida
    Verificar 'configurar nao interativo: recusa (exit != 0)' ($r.Codigo -ne 0 -and $r.Saida -match 'interativo') $r.Saida
    Verificar 'configurar recusado nao criou cofre nem pasta' (-not (Test-Path -LiteralPath $cofre2) -and -not (Test-Path -LiteralPath $base2))

    $r = Rodar-Script (@('subir-backend', '-Simular', '-SemSondagem', '-Pasta', $pasta, '-Cofre', (Join-Path $tmp 'sem-cofre.xml'), '-Base', $base2))
    Verificar 'sem cofre: subir-backend recusa citando os nomes' ($r.Codigo -ne 0 -and $r.Saida -match 'DATABASE_URL' -and $r.Saida -match 'configurar') $r.Saida

    Write-Host '== Varredura de vazamento =='
    foreach ($k in $saidas.Keys) {
        $achou = @($proibidos | Where-Object { $saidas[$k].Contains($_) })
        Verificar ('nenhum valor falso na saida de: ' + $k) ($achou.Count -eq 0) ($achou -join ',')
    }
    $arquivos = @(Get-ChildItem -LiteralPath $tmp -Recurse -File | Where-Object { $_.FullName -ne $cofreArq })
    $achouArq = @()
    foreach ($a in $arquivos) {
        $c = [IO.File]::ReadAllText($a.FullName)
        foreach ($pr in $proibidos) { if ($c.Contains($pr)) { $achouArq += $a.Name } }
    }
    Verificar 'nenhum valor falso em outros arquivos criados' ($achouArq.Count -eq 0) ($achouArq -join ',')

    Write-Host '== Garantias estaticas do codigo =='
    $codigo = [IO.File]::ReadAllText($alvoScript)
    Verificar 'sem downgrade no script' ($codigo -notmatch '(?i)alembic[^\r\n]*downgrade' -and $codigo -notmatch "'downgrade'")
    Verificar 'sem Stop-Process por nome' ($codigo -notmatch 'Stop-Process\s+-Name')
    Verificar 'PGPASSWORD so via ambiente (nao em argumentos)' ($codigo -cnotmatch "'--password'" -and $codigo -cnotmatch "'-W'")
    Verificar 'sem operadores indisponiveis no 5.1' ($codigo -notmatch '\s&&\s' -and $codigo -notmatch '\s\|\|\s' -and $codigo -notmatch '\?\?' -and $codigo -notmatch 'AsHashtable')
    Verificar 'script e ASCII puro' (-not ($codigo.ToCharArray() | Where-Object { [int]$_ -gt 127 }))
}
finally {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
}

Write-Host ''
Write-Host ('Resultado: {0} verificacoes, {1} falha(s).' -f $script:Total, $script:Falhas)
if ($script:Falhas -gt 0) { exit 1 }
exit 0
