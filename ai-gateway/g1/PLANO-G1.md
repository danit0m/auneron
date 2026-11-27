# G1: prova de conexão pela Cloudflare (plano)

27/11/2026. **Preparado e testado offline. Nada foi executado na Cloudflare.** Não toca a VPS, a produção, o Ollama nem o Gateway do G2.

## O que o G1 responde

O plano V1 tem uma dúvida que só um teste resolve: **um nó Mesh registrado por token, sem usuário, consegue chegar ao hostname privado `ai.auneron.internal` publicado pelo cloudflared do PC?** Se não conseguir, a arquitetura volta ao consultor antes de qualquer código novo.

A prova também confere duas coisas:
- se a Cloudflare repassa o TLS **sem abrir**. O certificado que a sonda recebe tem que ser exatamente o nosso;
- se o mTLS recusa quem não está na lista.

## Montagem

```
container "aigw-g1-mesh" (nó Mesh descartável, no Docker Desktop)
   └ sonda (outro container na mesma rede) --mTLS--> Cloudflare ===> cloudflared do PC --> 127.0.0.1:8443 (servidor "ok")
```

- **A máquina descartável é o container**, não a VPS. Ele tem rede própria e não alcança o `127.0.0.1` do Windows. O único caminho até o servidor é pela Cloudflare. O controle negativo prova isso: com o nó Mesh parado, a sonda **não** pode chegar.
- **O servidor de teste só responde "ok G1".** Ele não é o Gateway e não fala com o Ollama. Usa mTLS 1.3, escuta só em 127.0.0.1, aceita só o cliente listado e desliga sozinho em 30 minutos.
- **PKI descartável**, válida por 2 dias. A chave da CA nem é gravada. Tudo é apagado no fim.
- Imagens fixadas por digest:
  - nó Mesh: `cloudflare/mesh@sha256:1c135f07…dec5e` (warp-cli 2026.8.2115.0, de 20/10/2026);
  - sonda: `python:3.11-slim@sha256:61e01f72…6c`, a mesma do G2.

## As provas da sonda

| | Prova | Passa se |
|---|---|---|
| S1 | O nome resolve pela Cloudflare | O IP está em `172.64.128.0/20` |
| S2 | Sem certificado de cliente | Recusado |
| S3 | Certificado da mesma CA, não listado | Recusado |
| S4 | Cliente listado | Responde "ok G1", TLS 1.3, e o certificado do servidor é **o nosso** |
| S5 | Sonda confiando em outra CA | A própria sonda recusa o servidor |
| S6 | Uma recusa seguida de sucesso, no mesmo processo | O sucesso não é contaminado (risco visto no G2) |
| C | **Controle negativo:** nó Mesh parado | A sonda **não** chega |

**Teste offline (feito em 27/11):** servidor e sonda no mesmo container sem rede. Resultado: **6/6**, e o controle negativo passou. O registro do servidor mostrou só as conexões esperadas.

## Passo a passo

**Daniel, no painel da Cloudflare (Zero Trust):**
1. **Networking > Tunnels.** Confira que o túnel do PC está **Healthy**. Na aba **Routes**, veja se já existe uma rota **Private hostname** `ai.auneron.internal`. Se não houver, crie: **Add route > Private hostname**, com o nome `ai.auneron.internal`.
2. **Traffic controls > Traffic settings.** Ligue **Allow Secure Web Gateway to proxy traffic**, com **TCP** e **UDP**.
3. **Team & Resources > Devices > Device profiles > Default > Local Domain Fallback.** Retire o domínio `internal` da lista. Sem isso, o nó não pergunta o nome à Cloudflare.
4. **TLS inspection:** deve ficar **desligada**. Só confira, não ligue.
5. **Networking > Mesh > Add participant > Add node > Create node**, com o nome `aigw-g1-descartavel`. **Copie o token** e não mande para ninguém, nem para mim.

**Daniel, no PC (PowerShell como administrador):**
6. Acrescente ao arquivo hosts a linha `127.0.0.1 ai.auneron.internal`. É assim que o cloudflared do PC sabe que o nome é a própria máquina. Eu passo o comando exato na hora.
7. Rode `mesh-iniciar.ps1` e cole o token quando ele pedir. O token não aparece na tela nem fica em arquivo. Ao fim, o script mostra o estado do nó.

**Eu (PO), no PC:**
8. Gero a PKI descartável numa pasta nova e ligo o servidor "ok" por 30 minutos.
9. Rodo a sonda na rede do nó Mesh (S1 a S6) e guardo a saída.
10. Paro o nó Mesh e rodo o controle negativo.
11. Junto as evidências numa pasta nova e limpo tudo (veja abaixo).

**Duração estimada:** de 30 a 60 minutos, a maior parte no painel.

## Critérios de parada

Para tudo e volto a você, sem tentar outra rota, se acontecer qualquer um destes:
- o nó não conecta;
- o nome não resolve pela Cloudflare;
- o certificado recebido não é o nosso, o que significaria que alguém abriu o TLS;
- qualquer cliente não listado é aceito;
- o controle negativo chega ao servidor.

Se o nó Mesh não conseguir chegar ao hostname, isso **não é falha de execução**: é a resposta do G1, e a arquitetura volta ao consultor.

## Limpeza (no mesmo dia)

- **Eu:** paro o servidor; removo o container `aigw-g1-mesh` e o volume `aigw-g1-mesh-estado`; apago a PKI.
- **Daniel:**
  - apaga o nó `aigw-g1-descartavel` no painel; isso revoga o token;
  - retira a linha do arquivo hosts;
  - decide se a rota **Private hostname** e as configurações dos passos 2 e 3 ficam para o G5 ou voltam ao estado anterior.

## Riscos e o que o G1 não prova

- **Configurações da conta inteira:** os passos 2 e 3 valem para todos os dispositivos WARP da conta. Se você usa o WARP em outro aparelho, me avise antes. Eles não mexem no DNS nem no proxy do site `auneron`.
- **Restrição por dispositivo:** a documentação lida **não mostra** um seletor de política para um nó Mesh sem usuário. As regras de exemplo usam o e-mail do usuário. Então, no G1, quem barra os outros clientes é o mTLS, não a política. A política `AUNERON-VPS-ONLY` fica como item para o consultor.
- **Token visível:** enquanto o container existe, o token aparece em `docker inspect`. Ele é revogado no fim, no passo da limpeza.
- **O G1 não prova:**
  - o nó Mesh na VPS de produção (isso é o G6, fora do plano);
  - tempo de resposta com o modelo (isso é o G3);
  - o funcionamento do PC sem login ou com suspensão (isso é o G4).

## Arquivos

`g1_pki.py`, `ok_server.py`, `probe.py`, `mesh-iniciar.ps1`. As somas SHA-256 estão em `SOMAS.txt`.
