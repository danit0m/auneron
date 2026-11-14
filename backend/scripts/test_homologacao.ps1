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

    Write-Host '== Busca de pg_dump/pg_restore (arvore temporaria) =='
    $raizPg = Join-Path $tmp 'PostgreSQL'
    foreach ($v in @('9', '16', '17')) {
        [void](New-Item -ItemType Directory -Force -Path (Join-Path $raizPg ($v + '\bin')))
        Set-Content -LiteralPath (Join-Path $raizPg ($v + '\bin\pg_dump.exe')) -Value '' -Encoding ASCII
    }
    Set-Content -LiteralPath (Join-Path $raizPg '16\bin\pg_restore.exe') -Value '' -Encoding ASCII
    [void](New-Item -ItemType Directory -Force -Path (Join-Path $raizPg '18'))
    [void](New-Item -ItemType Directory -Force -Path (Join-Path $raizPg '99\bin'))
    [void](New-Item -ItemType Directory -Force -Path (Join-Path $raizPg 'abc\bin'))
    Set-Content -LiteralPath (Join-Path $raizPg 'abc\bin\pg_dump.exe') -Value '' -Encoding ASCII
    $f = Buscar-FerramentaPgEmRaiz $raizPg 'pg_dump'
    Verificar 'pg_dump: maior versao numerica (17, nao 9; ignora 18 sem bin, 99 sem exe, abc)' ($f -eq (Join-Path $raizPg '17\bin\pg_dump.exe')) $f
    $f = Buscar-FerramentaPgEmRaiz $raizPg 'pg_restore'
    Verificar 'pg_restore: so existe na 16' ($f -eq (Join-Path $raizPg '16\bin\pg_restore.exe')) $f
    Verificar 'ferramenta inexistente: $null' ($null -eq (Buscar-FerramentaPgEmRaiz $raizPg 'psql'))
    Verificar 'raiz inexistente: $null' ($null -eq (Buscar-FerramentaPgEmRaiz (Join-Path $tmp 'nada') 'pg_dump'))
    $irma = Resolver-FerramentaPgIrma (Join-Path $raizPg '16\bin\pg_restore.exe') 'pg_dump'
    Verificar 'ferramenta irma no mesmo diretorio' ($irma -eq (Join-Path $raizPg '16\bin\pg_dump.exe'))

    Write-Host '== Banco descartavel do ensaio (funcoes puras) =='
    Verificar 'nome valido aceito' (Test-NomeBancoEnsaio 'auneron_ensaio_20261114093015' 'auneron')
    Verificar 'nome igual ao banco real recusado' (-not (Test-NomeBancoEnsaio 'auneron_ensaio_20261114093015' 'auneron_ensaio_20261114093015'))
    foreach ($ruim in @('', 'auneron', 'auneron_ensaio_123', 'auneron_ensaio_20261114093015x', 'auneron_ensaio_20261114093015 ', 'auneron_ensaio_20261114093015;', 'auneron_ensaio_2026111409301"', 'auneron_ensaio_20261114093015.x', 'x auneron_ensaio_20261114093015', "auneron_ensaio_20261114093015`n", 'AUNERON_ENSAIO_20261114093015')) {
        Verificar ('nome recusado: [' + ($ruim -replace "`n", '\n') + ']') (-not (Test-NomeBancoEnsaio $ruim 'auneron'))
    }
    $u = Montar-UrlBancoEnsaio $fakeUrl 'auneron_ensaio_20261114093015'
    Verificar 'URL do descartavel: esquema, credenciais percent-encoded, host e porta preservados' ($u -eq ('postgresql+psycopg://fakeuser:' + $fakeSenhaCodificada + '@fakehost:5999/auneron_ensaio_20261114093015')) 'ok'
    $u = Montar-UrlBancoEnsaio 'postgresql://u:p%40q@h:5433/real?sslmode=require&x=1' 'auneron_ensaio_20261114093015'
    Verificar 'URL do descartavel preserva a query' ($u -eq 'postgresql://u:p%40q@h:5433/auneron_ensaio_20261114093015?sslmode=require&x=1') 'ok'
    $u = Montar-UrlBancoEnsaio 'postgresql+psycopg2://u@h/real' 'auneron_ensaio_20261114093015'
    Verificar 'URL sem senha e sem query' ($u -eq 'postgresql+psycopg2://u@h/auneron_ensaio_20261114093015')
    $m = Lancar { Montar-UrlBancoEnsaio $fakeUrl 'fakedb' }
    Verificar 'URL com nome fora do padrao recusada, sem eco da URL' ($m -ne '' -and $m -notmatch 'ZZfake')

    Write-Host '== Recibo de ensaio (funcao pura, recibos falsos) =='
    $agoraUtc = (Get-Date).ToUniversalTime()
    $fmt = "yyyy-MM-dd'T'HH:mm:ss'Z'"
    function Novo-Recibo {
        param([hashtable]$Mudar = @{})
        $r = [ordered]@{
            sha256 = 'aa11'; dump = 'x.dump'; revisao_inicial = 'r0'; revisao_final = 'h1'; head = 'h1'
            commit = 'c1'; arvore_limpa = $true; criado_em_utc = $agoraUtc.AddHours(-1).ToString($fmt)
        }
        foreach ($k in $Mudar.Keys) { $r[$k] = $Mudar[$k] }
        return [pscustomobject]$r
    }
    $sel = { param($lista) Selecionar-ReciboEnsaio $lista 'aa11' 'c1' 'h1' $agoraUtc }
    Verificar 'recibo ok aceito' ((& $sel @((Novo-Recibo))) -eq '')
    Verificar 'sem recibo: rode ensaiar antes' ((& $sel @()) -match 'rode ensaiar antes')
    Verificar 'SHA diferente recusado' ((& $sel @((Novo-Recibo @{ sha256 = 'bb22' }))) -match 'rode ensaiar antes')
    Verificar 'commit diferente recusado' ((& $sel @((Novo-Recibo @{ commit = 'c2' }))) -match 'commit.*rode ensaiar antes')
    Verificar 'recibo velho (30 h) recusado' ((& $sel @((Novo-Recibo @{ criado_em_utc = $agoraUtc.AddHours(-30).ToString($fmt) }))) -match 'h \(limite 24 h\).*rode ensaiar antes')
    Verificar 'arvore suja recusada' ((& $sel @((Novo-Recibo @{ arvore_limpa = $false }))) -match 'suja.*rode ensaiar antes')
    Verificar 'revisao final != head atual recusada' ((Selecionar-ReciboEnsaio @((Novo-Recibo)) 'aa11' 'c1' 'h2' $agoraUtc) -match 'head atual')
    Verificar 'data no futuro recusada' ((& $sel @((Novo-Recibo @{ criado_em_utc = $agoraUtc.AddHours(2).ToString($fmt) }))) -match 'rode ensaiar antes')
    Verificar 'um recibo ruim e um bom: aceita' ((& $sel @((Novo-Recibo @{ commit = 'c2' }), (Novo-Recibo))) -eq '')

    Write-Host '== ensaiar e migrar (recusas offline) =='
    $dumpFalso = Join-Path $tmp 'novo.dump'
    Set-Content -LiteralPath $dumpFalso -Value 'x' -Encoding ASCII
    $r = Rodar-Script (@('ensaiar', '-Pasta', $pasta) + $comuns)
    Verificar 'ensaiar sem -ConfirmarBackup: recusa' ($r.Codigo -ne 0 -and $r.Saida -match 'ConfirmarBackup') $r.Saida
    $r = Rodar-Script (@('ensaiar', '-ConfirmarBackup', $dumpFalso) + $comuns)
    Verificar 'ensaiar sem -Pasta: recusa' ($r.Codigo -ne 0 -and $r.Saida -match 'Pasta') $r.Saida
    $r = Rodar-Script (@('ensaiar', '-Simular', '-SemSondagem', '-Pasta', $pasta, '-ConfirmarBackup', $dumpFalso) + $comuns)
    $saidas['ensaiar-simular'] = $r.Saida
    Verificar 'ensaiar -Simular: exit 0, mostra descartavel e passos' ($r.Codigo -eq 0 -and $r.Saida -match 'auneron_ensaio_\d{14}' -and $r.Saida -match 'SIMULACAO' -and $r.Saida -match 'fakehost') $r.Saida
    Verificar 'ensaiar -Simular nao criou recibo nem pasta de ensaios' (-not (Test-Path -LiteralPath (Join-Path $base 'ensaios')))
    $r = Rodar-Script (@('migrar', '-Simular', '-SemSondagem', '-Pasta', $pasta, '-ConfirmarBackup', $dumpFalso) + $comuns)
    $saidas['migrar-sem-recibo'] = $r.Saida
    Verificar 'migrar sem recibo de ensaio: recusa pedindo ensaiar' ($r.Codigo -ne 0 -and $r.Saida -match 'rode ensaiar antes') $r.Saida
    $r = Rodar-Script (@('migrar', '-Pasta', $pasta, '-ConfirmarBackup', $dumpFalso, '-PularEnsaio') + $comuns)
    Verificar 'nao existe parametro para pular o ensaio' ($r.Codigo -ne 0 -and $r.Saida -match 'PularEnsaio')

    Write-Host '== registrar-acao-pagamento =='
    $r = Rodar-Script (@('registrar-acao-pagamento', '-Simular', '-SemSondagem') + $comuns)
    Verificar 'registrar-acao-pagamento sem -Pasta: recusa' ($r.Codigo -ne 0 -and $r.Saida -match 'Pasta') $r.Saida
    $r = Rodar-Script (@('registrar-acao-pagamento', '-Simular', '-SemSondagem', '-Pasta', $pasta) + $comuns)
    Verificar 'sem o .py no checkout: recusa' ($r.Codigo -ne 0 -and $r.Saida -match 'register_account_mark_paid_skill') $r.Saida
    [void](New-Item -ItemType Directory -Force -Path (Join-Path $pasta 'backend\scripts'))
    Set-Content -LiteralPath (Join-Path $pasta 'backend\scripts\register_account_mark_paid_skill.py') -Value '' -Encoding ASCII
    $r = Rodar-Script (@('registrar-acao-pagamento', '-Simular', '-SemSondagem', '-Pasta', $pasta) + $comuns)
    $saidas['registrar-acao-pagamento'] = $r.Saida
    Verificar 'registrar-acao-pagamento -Simular: exit 0 e mostra o modulo fixo' ($r.Codigo -eq 0 -and $r.Saida.Contains('-m scripts.register_account_mark_paid_skill')) $r.Saida
    $r = Rodar-Script (@('registrar-acao-pagamento', '-Simular', '-SemSondagem', '-Pasta', $pasta, '-Modulo', 'os') + $comuns)
    Verificar 'nenhum parametro escolhe o modulo' ($r.Codigo -ne 0 -and $r.Saida -match 'Modulo')

    Write-Host '== consultar (somente leitura) =='
    $r = Rodar-Script (@('consultar', '-Simular', '-SemSondagem') + $comuns)
    Verificar 'consultar sem -Relatorio: recusa' ($r.Codigo -ne 0 -and $r.Saida -match 'Relatorio') $r.Saida
    $r = Rodar-Script (@('consultar', '-Relatorio', 'qualquer', '-Simular') + $comuns)
    Verificar '-Relatorio fora do ValidateSet recusado' ($r.Codigo -ne 0 -and $r.Saida -match 'Relatorio') $r.Saida
    $r = Rodar-Script (@('consultar', '-Relatorio', 'pagamento', '-Simular') + $comuns)
    Verificar 'pagamento sem -Conta: recusa' ($r.Codigo -ne 0 -and $r.Saida -match 'Conta') $r.Saida
    foreach ($ruim in @('0', '-1', 'abc', '2147483648', '1;SELECT 1')) {
        $r = Rodar-Script (@('consultar', '-Relatorio', 'pagamento', '-Conta', $ruim, '-Simular') + $comuns)
        Verificar ('-Conta recusada: [' + $ruim + ']') ($r.Codigo -ne 0 -and $r.Saida -match 'Conta') $r.Saida
    }
    $r = Rodar-Script (@('consultar', '-Relatorio', 'catalogo', '-Conta', '5', '-Simular') + $comuns)
    Verificar 'catalogo com -Conta: recusa' ($r.Codigo -ne 0 -and $r.Saida -match 'Conta') $r.Saida
    $r = Rodar-Script (@('status', '-SemSondagem', '-Relatorio', 'catalogo') + $comuns)
    Verificar '-Relatorio fora de consultar: recusa' ($r.Codigo -ne 0 -and $r.Saida -match 'consultar') $r.Saida

    $r = Rodar-Script (@('consultar', '-Relatorio', 'pagamento', '-Conta', '42', '-Simular') + $comuns)
    $saidas['consultar-pagamento-simular'] = $r.Saida
    Verificar 'consultar pagamento -Simular: exit 0, banco sem usuario/senha, SQL fixo' ($r.Codigo -eq 0 -and $r.Saida -match 'SIMULACAO' -and $r.Saida -match 'fakehost' -and $r.Saida -match '5999' -and $r.Saida -match 'fakedb' -and $r.Saida -notmatch 'fakeuser') $r.Saida
    Verificar 'consultar pagamento -Simular: mostra BEGIN READ ONLY, ROLLBACK, PGOPTIONS e -v conta=42' ($r.Saida -match 'BEGIN READ ONLY' -and $r.Saida -match 'ROLLBACK' -and $r.Saida -match 'default_transaction_read_only=on' -and $r.Saida.Contains('-v conta=42') -and $r.Saida.Contains(':conta')) $r.Saida
    Verificar 'consultar pagamento -Simular: o id nao foi interpolado no SQL' (-not $r.Saida.Contains('= 42;') -and -not $r.Saida.Contains('account_id = 42'))
    $r = Rodar-Script (@('consultar', '-Relatorio', 'catalogo', '-Simular') + $comuns)
    $saidas['consultar-catalogo-simular'] = $r.Saida
    Verificar 'consultar catalogo -Simular: exit 0, mostra BEGIN READ ONLY e ROLLBACK' ($r.Codigo -eq 0 -and $r.Saida -match 'BEGIN READ ONLY' -and $r.Saida -match 'ROLLBACK' -and $r.Saida -match 'skill_versions') $r.Saida
    $r = Rodar-Script (@('consultar', '-Relatorio', 'catalogo', '-Simular', '-Executavel', 'x.exe') + $comuns)
    Verificar 'nenhum parametro escolhe executavel, SQL ou modulo' ($r.Codigo -ne 0 -and $r.Saida -match 'Executavel')
    $r = Rodar-Script (@('consultar', '-Relatorio', 'catalogo', '-Simular', '-Sql', 'select 1') + $comuns)
    Verificar '-Sql recusado como parametro desconhecido' ($r.Codigo -ne 0 -and $r.Saida -match 'Sql')

    $padraoEscrita = '(?i)\b(insert|update|delete|drop|alter|create|truncate|grant)\b'
    foreach ($par in @(@('pagamento', $script:SqlConsultaPagamento), @('catalogo', $script:SqlConsultaCatalogo))) {
        $nomeSql = [string]$par[0]
        $txtSql = [string]$par[1]
        $linhasSql = @($txtSql -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
        Verificar ('SQL ' + $nomeSql + ': comeca com BEGIN READ ONLY;') ($linhasSql[0].Trim() -eq 'BEGIN READ ONLY;')
        Verificar ('SQL ' + $nomeSql + ': termina com ROLLBACK;') ($linhasSql[$linhasSql.Count - 1].Trim() -eq 'ROLLBACK;')
        Verificar ('SQL ' + $nomeSql + ': sem INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/GRANT') (-not [regex]::IsMatch($txtSql, $padraoEscrita))
        Verificar ('SQL ' + $nomeSql + ': sem COMMIT e sem SET') ($txtSql -notmatch '(?i)\bcommit\b' -and $txtSql -notmatch '(?im)^\s*set\b')
        Verificar ('SQL ' + $nomeSql + ': somente ASCII') (-not ($txtSql.ToCharArray() | Where-Object { [int]$_ -gt 127 }))
    }
    Verificar 'SQL pagamento usa a variavel :conta e nenhum numero literal de conta' ($script:SqlConsultaPagamento.Contains('= :conta') -and $script:SqlConsultaPagamento -notmatch '(?i)(account_id|target_account_id|a\.id)\s*=\s*[0-9]')
    Verificar 'SQL catalogo nao usa variaveis' ($script:SqlConsultaCatalogo -notmatch ':conta')

    $ast = [Management.Automation.Language.Parser]::ParseFile($alvoScript, [ref]$null, [ref]$null)
    $nomesParam = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
    $esperados = @('Comando', 'Pasta', 'Cofre', 'Base', 'Destino', 'ConfirmarBackup', 'Relatorio', 'Conta', 'Confirmar', 'Simular', 'SemSondagem', 'CarregarFuncoes')
    $extras = @($nomesParam | Where-Object { $esperados -notcontains $_ })
    $faltam = @($esperados | Where-Object { $nomesParam -notcontains $_ })
    Verificar 'parametros: apenas os existentes mais -Relatorio e -Conta' ($extras.Count -eq 0 -and $faltam.Count -eq 0) (($extras + $faltam) -join ',')
    $pRel = @($ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'Relatorio' })[0]
    $vsRel = @($pRel.Attributes | Where-Object { $_.TypeName.Name -eq 'ValidateSet' } | ForEach-Object { $_.PositionalArguments | ForEach-Object { [string]$_.Value } })
    Verificar '-Relatorio: ValidateSet(pagamento, catalogo)' (($vsRel -join ',') -eq 'pagamento,catalogo') ($vsRel -join ',')
    $pCon = @($ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'Conta' })[0]
    $vrCon = @($pCon.Attributes | Where-Object { $_.TypeName.Name -eq 'ValidateRange' } | ForEach-Object { $_.PositionalArguments | ForEach-Object { [string]$_.Value } })
    Verificar '-Conta: [int] com ValidateRange(1, 2147483647)' ((($vrCon -join ',') -eq '1,2147483647') -and ($pCon.StaticType.Name -eq 'Int32')) ($vrCon -join ',')

    $mCons = [regex]::Match([IO.File]::ReadAllText($alvoScript), '(?s)function Cmd-Consultar \{.*?\r?\n\}\r?\n')
    Verificar 'consultar: read-only forcado por PGOPTIONS no ambiente do filho' ($mCons.Success -and $mCons.Value.Contains("'PGOPTIONS' = '-c default_transaction_read_only=on'"))
    Verificar 'consultar: conta passada como -v conta=<int> e SQL por stdin (-f -)' ($mCons.Success -and $mCons.Value.Contains("('conta=' + `$conta)") -and $mCons.Value.Contains("'-f', '-'") -and $mCons.Value.Contains('-Entrada $sql'))
    Verificar 'consultar: -X, ON_ERROR_STOP e pager desligado' ($mCons.Success -and $mCons.Value.Contains("'-X'") -and $mCons.Value.Contains("'ON_ERROR_STOP=1'") -and $mCons.Value.Contains("'pager=off'"))
    Verificar 'consultar: PGPASSWORD so no ambiente' ($mCons.Success -and $mCons.Value.Contains("'PGPASSWORD' =") -and $mCons.Value -cnotmatch "'-W'")

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
    $padraoDrop = '(?i)drop\s+database|dropdb'
    $todosDrop = [regex]::Matches($codigo, $padraoDrop)
    Verificar 'exatamente uma remocao de banco no script' ($todosDrop.Count -eq 1) ([string]$todosDrop.Count)
    $mLimpa = [regex]::Match($codigo, '(?s)function Limpar-BancoEnsaio \{.*?\r?\n\}\r?\n')
    $posDrop = if ($mLimpa.Success) { [regex]::Match($mLimpa.Value, $padraoDrop).Index } else { -1 }
    $posChecagem = if ($mLimpa.Success) { $mLimpa.Value.IndexOf('Test-NomeBancoEnsaio') } else { -1 }
    Verificar 'a remocao esta na funcao de limpeza do ensaio' ($mLimpa.Success -and $posDrop -ge 0 -and [regex]::Matches($mLimpa.Value, $padraoDrop).Count -eq 1)
    Verificar 'a remocao e precedida pela checagem do padrao do nome' ($posChecagem -ge 0 -and $posChecagem -lt $posDrop)
    Verificar 'limpeza do ensaio so e chamada com o nome criado nesta execucao' ([regex]::Matches($codigo, 'Limpar-BancoEnsaio \$psql \$db \$criado').Count -eq 1)
    $mReg = [regex]::Match($codigo, '(?s)function Cmd-RegistrarAcaoPagamento \{.*?\r?\n\}\r?\n')
    Verificar 'registrar-acao-pagamento: modulo e constante literal e unico uso' ($mReg.Success -and $mReg.Value.Contains("`$modulo = 'scripts.register_account_mark_paid_skill'") -and [regex]::Matches($mReg.Value, '\$modulo\s*=').Count -eq 1 -and $mReg.Value.Contains("@('-m', `$modulo)"))
    Verificar 'script e ASCII puro' (-not ($codigo.ToCharArray() | Where-Object { [int]$_ -gt 127 }))
}
finally {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
}

Write-Host ''
Write-Host ('Resultado: {0} verificacoes, {1} falha(s).' -f $script:Total, $script:Falhas)
if ($script:Falhas -gt 0) { exit 1 }
exit 0
