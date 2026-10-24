# Mage Cup Email Bridge — Python + Vercel

API serverless para o Mage Cup enviar e verificar códigos OTP por e-mail sem colocar a senha do serviço de e-mail dentro da Godot.

A versão atual **não usa Resend**. Ela usa Python + SMTP. Isso permite usar um provedor SMTP que aceite envio para os jogadores. O exemplo do `.env.example` usa Gmail SMTP.

## Arquitetura

```text
Godot
  ↓ HTTPS
Vercel /api/send-otp
  ↓
Python + SMTP
  ↓
E-mail do jogador
```

E para verificar:

```text
Godot
  ↓ HTTPS
Vercel /api/verify-otp
  ↓
HMAC SHA-256
  ↓
Godot
```

## Estrutura

```text
magecup-email-bridge/
├── api/
│   ├── index.py
│   ├── email_service.py
│   └── otp_store.py
├── requirements.txt
├── vercel.json
├── .env.example
├── .gitignore
└── README.md
```

## Variáveis de ambiente na Vercel

Configure em **Settings → Environment Variables**:

```text
SMTP_HOST
SMTP_PORT
SMTP_SECURITY
SMTP_USER
SMTP_PASSWORD
FROM_EMAIL
OTP_SECRET
OTP_TTL_SECONDS
```

Para Gmail:

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_SECURITY=ssl
SMTP_USER=seuemail@gmail.com
SMTP_PASSWORD=senha de app
FROM_EMAIL=Mage Cup <seuemail@gmail.com>
OTP_SECRET=um-segredo-grande-e-aleatorio
OTP_TTL_SECONDS=600
```

### Gmail

Não coloque a senha normal da sua conta Google na Vercel. Para usar Gmail SMTP, configure a segurança da conta Google e use uma **Senha de app** quando essa opção estiver disponível para sua conta. A senha de app fica somente na variável `SMTP_PASSWORD` da Vercel.

O remetente usado no `FROM_EMAIL` deve corresponder ao mailbox usado no SMTP ou ser permitido pelo servidor SMTP.

## GitHub

Depois de extrair o projeto:

```bash
git init
git add .
git commit -m "Mage Cup Email Bridge Python"
git branch -M main
git remote add origin URL_DO_REPOSITORIO
git push -u origin main
```

Nunca envie `.env` ou senhas para o GitHub.

## Vercel

1. Importe o repositório GitHub.
2. Faça o deploy.
3. Vá em **Settings → Environment Variables**.
4. Adicione as oito variáveis acima.
5. Faça um redeploy.

A Vercel suporta aplicações FastAPI/Python e detecta o objeto `app` no entrypoint Python. O `vercel.json` limita a duração da Function para 30 segundos. Consulte a documentação atual da Vercel para runtime Python e FastAPI: https://vercel.com/docs/functions/runtimes/python

## Health

```text
GET https://SEU-PROJETO.vercel.app/api/health
```

Resposta:

```json
{
  "success": true,
  "service": "Mage Cup Email Bridge",
  "status": "online"
}
```

## Enviar OTP

```http
POST https://SEU-PROJETO.vercel.app/api/send-otp
Content-Type: application/json
```

```json
{
  "email": "jogador@gmail.com"
}
```

Sucesso:

```json
{
  "success": true,
  "message": "Código enviado."
}
```

O código nunca é retornado.

## Enviar mensagem personalizada para o responsável

O endpoint `POST /api/send-message` recebe o texto escrito pelo jogador e envia um e-mail para o endereço fixo configurado no servidor (`cauanexel633@gmail.com`). O cliente não pode definir o destinatário.

```http
POST https://mage-cup-mdcb.vercel.app/api/send-message
Content-Type: application/json
```

Corpo JSON:

```json
{
  "message": "Texto digitado no campo da Godot"
}
```

Resposta de sucesso:

```json
{
  "success": true,
  "message": "Mensagem enviada com sucesso."
}
```

O texto é enviado como texto simples, com limite de 5.000 caracteres. Há um limite básico de uma mensagem a cada 30 segundos e dez por hora por IP. Como a limitação atual usa memória local, ela é apenas uma proteção básica para protótipo; para produção, use um limitador compartilhado e/ou CAPTCHA. As credenciais SMTP continuam somente nas variáveis de ambiente da Vercel.

Exemplo Godot 4 (troque `$LineEdit` pelo caminho do seu campo de texto):

```gdscript
const EMAIL_BRIDGE_URL := "https://mage-cup-mdcb.vercel.app"

func enviar_mensagem() -> void:
    var texto: String = $LineEdit.text.strip_edges()

    if texto.is_empty():
        print("Digite uma mensagem antes de enviar.")
        return

    var http := HTTPRequest.new()
    add_child(http)

    var headers := PackedStringArray([
        "Content-Type: application/json"
    ])
    var body := JSON.stringify({"message": texto})

    var erro := http.request(
        EMAIL_BRIDGE_URL + "/api/send-message",
        headers,
        HTTPClient.METHOD_POST,
        body
    )

    if erro != OK:
        http.queue_free()
        print("Não foi possível iniciar o envio.")
        return

    var resposta = await http.request_completed
    var status_http: int = resposta[1]
    var dados = JSON.parse_string(
        resposta[3].get_string_from_utf8()
    )
    http.queue_free()

    if (
        status_http >= 200
        and status_http < 300
        and dados is Dictionary
        and dados.get("success", false)
    ):
        print("Mensagem enviada!")
        $LineEdit.clear()
    else:
        var motivo := "Erro ao enviar a mensagem."
        if dados is Dictionary:
            motivo = str(dados.get("error", motivo))
        print(motivo)
```

## Verificar OTP

```http
POST https://SEU-PROJETO.vercel.app/api/verify-otp
Content-Type: application/json
```

```json
{
  "email": "jogador@gmail.com",
  "code": "583214"
}
```

Sucesso:

```json
{
  "success": true,
  "message": "Código verificado.",
  "email": "jogador@gmail.com"
}
```

## Proteções

- OTP de 6 dígitos gerado com `secrets`.
- OTP nunca é armazenado em texto puro.
- Hash HMAC-SHA-256 usando `OTP_SECRET`.
- Comparação com `hmac.compare_digest`.
- Expiração padrão de 10 minutos.
- Cooldown de 60 segundos por e-mail.
- Limite adicional de envios por hora.
- Máximo de 5 tentativas de verificação.
- OTP apagado após uso correto.
- CORS habilitado para a Godot.
- Senha SMTP somente em variável de ambiente.
- Nenhuma credencial retorna para a Godot.

## Armazenamento

O armazenamento do OTP é feito em memória apenas para protótipo.

A Vercel pode reiniciar uma instância e múltiplas instâncias podem atender requisições diferentes. Portanto, o OTP em memória não é adequado como armazenamento de produção.

A camada `api/otp_store.py` foi isolada de propósito para poder ser trocada futuramente por:

```text
Redis / KV / Supabase
```

sem precisar alterar a API da Godot.

## Godot 4

A URL do bridge é:

```gdscript
const EMAIL_BRIDGE_URL := "https://SEU-PROJETO.vercel.app"
```

Enviar:

```gdscript
func enviar_codigo_email(email: String) -> bool:
    var http := HTTPRequest.new()
    add_child(http)

    var headers := PackedStringArray([
        "Content-Type: application/json"
    ])

    var body := JSON.stringify({
        "email": email.strip_edges().to_lower()
    })

    var erro := http.request(
        EMAIL_BRIDGE_URL + "/api/send-otp",
        headers,
        HTTPClient.METHOD_POST,
        body
    )

    if erro != OK:
        http.queue_free()
        return false

    var resposta = await http.request_completed
    var codigo_http: int = resposta[1]
    var dados = JSON.parse_string(
        resposta[3].get_string_from_utf8()
    )

    http.queue_free()

    return (
        codigo_http >= 200
        and codigo_http < 300
        and dados is Dictionary
        and dados.get("success", false)
    )
```

Verificar:

```gdscript
func verificar_codigo_email(email: String, codigo: String) -> bool:
    var http := HTTPRequest.new()
    add_child(http)

    var headers := PackedStringArray([
        "Content-Type: application/json"
    ])

    var body := JSON.stringify({
        "email": email.strip_edges().to_lower(),
        "code": codigo.strip_edges()
    })

    var erro := http.request(
        EMAIL_BRIDGE_URL + "/api/verify-otp",
        headers,
        HTTPClient.METHOD_POST,
        body
    )

    if erro != OK:
        http.queue_free()
        return false

    var resposta = await http.request_completed
    var codigo_http: int = resposta[1]
    var dados = JSON.parse_string(
        resposta[3].get_string_from_utf8()
    )

    http.queue_free()

    return (
        codigo_http >= 200
        and codigo_http < 300
        and dados is Dictionary
        and dados.get("success", false)
    )
```

## Observação sobre autenticação do Supabase

Este Bridge confirma que o jogador possui o e-mail. Ele não cria sozinho uma sessão do Supabase Auth. Se o fluxo desejado for **e-mail + OTP → sessão Supabase**, será necessário conectar a etapa de verificação à autenticação do Supabase de forma segura.
