# 🎯 Polymarket Copy Trading Bot

Bot que monitora carteiras no Polymarket e replica automaticamente seus trades com gerenciamento de risco configurável.

## Arquitetura

```
main.py                    # Entry point (CLI)
src/
├── config.py              # Configuração (JSON / env vars)
├── api_client.py          # Client unificado (Data API + Gamma + CLOB)
├── watcher.py             # Monitor de carteiras (polling)
├── risk_manager.py        # Limites de risco e exposição
├── executor.py            # Execução de ordens via py-clob-client
└── bot.py                 # Orquestrador principal
```

## Como funciona

1. **Monitor** → Faz polling da Data API a cada N segundos para detectar novos trades das carteiras-alvo
2. **Filtra** → Ignora trades muito pequenos, mercados de esportes (opcional), preços extremos
3. **Calcula** → Aplica o `copy_ratio` para dimensionar a posição proporcionalmente
4. **Valida** → Risk manager checa limites de exposição total, por mercado e perda diária
5. **Executa** → Submete ordem FOK (Fill-or-Kill) via CLOB API
6. **Registra** → Loga tudo em `trade_history.json` e no console

## Setup rápido

```bash
# 1. Clone e instale
git clone <repo-url>
cd polymarket-copy-trader
pip install -r requirements.txt

# 2. Configure
cp config.example.json config.json
# Edite config.json com suas carteiras-alvo e chaves

# 3. Teste em dry run
python main.py --dry-run

# 4. Quando estiver confiante, ative trading real
# Edite config.json → "dry_run": false
python main.py
```

## Configuração

### Via JSON (`config.json`)

```json
{
    "private_key": "0x...",
    "funder_address": "0x...",
    "signature_type": 2,
    "target_wallets": [
        {
            "address": "0x1d0034134e339a309700ff2d34e99fa2d48b031",
            "label": "Alpha Trader",
            "copy_ratio": 0.5
        },
        {
            "address": "0xabcdef...",
            "label": "Whale",
            "copy_ratio": 0.1
        }
    ],
    "dry_run": true,
    "max_trade_usdc": 500,
    "max_total_exposure": 5000,
    "max_daily_loss": 200,
    "poll_interval_seconds": 5,
    "skip_sports": false
}
```

### Via CLI

```bash
# Adicionar carteiras direto na linha de comando
python main.py --wallet 0x1d0034134e339a309700ff2d34e99fa2d48b031 --wallet 0xabc... --ratio 0.5 --dry-run

# Usar variáveis de ambiente
python main.py --env
```

### Parâmetros principais

| Parâmetro | Default | Descrição |
|-----------|---------|-----------|
| `copy_ratio` | 1.0 | Multiplicador do tamanho do trade (0.5 = metade) |
| `min_trade_usdc` | 5 | Trade mínimo em USDC |
| `max_trade_usdc` | 500 | Trade máximo em USDC |
| `max_total_exposure` | 5000 | Exposição total máxima |
| `max_per_market` | 1000 | Máximo por mercado |
| `max_daily_loss` | 200 | Perda diária máxima (para trading automático) |
| `poll_interval_seconds` | 5 | Intervalo de polling em segundos |
| `min_target_trade_usdc` | 10 | Ignora trades do alvo menores que isso |
| `max_price` | 0.95 | Não compra acima desse preço |
| `min_price` | 0.05 | Não compra abaixo desse preço |
| `skip_sports` | false | Pular mercados de esportes |

## Wallet e API Keys

### Obtendo suas credenciais

1. **Private Key**: Exporte do MetaMask ou gere uma nova wallet
2. **Funder Address**: Seu endereço proxy no Polymarket (visível no perfil)
3. **Signature Type**:
   - `0` = EOA (MetaMask direto)
   - `1` = Magic/email wallet
   - `2` = Safe proxy wallet (mais comum no Polymarket)

### Allowances

Antes de fazer trades reais, você precisa aprovar os smart contracts do Polymarket para movimentar seus USDC:

```python
from py_clob_client.client import ClobClient

client = ClobClient(
    host="https://clob.polymarket.com",
    key="YOUR_PRIVATE_KEY",
    chain_id=137,
    signature_type=2,
    funder="YOUR_FUNDER_ADDRESS",
)
client.set_api_creds(client.create_or_derive_api_creds())

# Aprovar allowances (só precisa fazer uma vez)
client.set_allowances()
```

## Risk Management

O bot implementa múltiplas camadas de proteção:

- **Limite por trade**: Cap no tamanho máximo de cada operação
- **Exposição total**: Para de comprar quando atinge o limite global
- **Exposição por mercado**: Evita concentração excessiva
- **Perda diária**: Halt automático se a perda do dia exceder o limite
- **Filtro de preço**: Não opera em preços extremos (>0.95 ou <0.05)
- **Filtro de slippage**: Alerta quando o preço atual difere >10% do trade copiado
- **Kill switch**: `risk.halt()` para parar imediatamente

## Logs e Monitoramento

- Console com emojis para fácil visualização
- Arquivo de log configurável (`copy_trader.log`)
- Histórico de trades em JSON (`trade_history.json`)

### Exemplo de output

```
2026-02-14 15:30:22 [INFO] 🔔 [Alpha Trader] New trade: BUY 500.00 tokens @ $0.6500 on 'Will BTC hit $150k by March?' (Yes)
2026-02-14 15:30:22 [INFO] 📥 Processing trade from [Alpha Trader]
2026-02-14 15:30:22 [INFO] 💰 Copy amount: $325.00 × 0.5x = $162.50
2026-02-14 15:30:23 [INFO] ✅ Trade executed: BUY $162.50 on 'Will BTC hit $150k by March?'
2026-02-14 15:30:23 [INFO] 📊 Position update: Total exposure $1,234.50 | Today: 7 trades
```

## Encontrando carteiras para copiar

Para encontrar boas carteiras no Polymarket:
- Acesse o [Leaderboard](https://polymarket.com/leaderboard)
- Use o perfil de qualquer trader: `https://polymarket.com/@username?tab=activity`
- O endereço proxy wallet aparece no URL ou pode ser resolvido via API

## ⚠️ Avisos importantes

1. **SEMPRE comece em DRY RUN** para validar o comportamento
2. Comece com `copy_ratio` baixo (0.1-0.3) e aumente gradualmente
3. Trading envolve risco significativo de perda
4. O bot depende da disponibilidade das APIs do Polymarket
5. Mantenha sua private key segura - nunca commite no git
6. Verifique as restrições geográficas do Polymarket

## Licença

MIT - Use por sua conta e risco.
