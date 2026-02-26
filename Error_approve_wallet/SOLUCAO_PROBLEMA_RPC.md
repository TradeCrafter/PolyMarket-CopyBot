# 🚨 PROBLEMA DE CONEXÃO RPC - SOLUÇÕES

## 🎯 Situação

Você não consegue conectar aos endpoints RPC públicos da Polygon.
Isso é comum e pode ser por:
- ISP/Firewall bloqueando
- Região geográfica
- Todos os endpoints públicos sobrecarregados

## ✅ SOLUÇÃO 1: Script Estendido (Mais RPCs)

**Execute:**
```bash
python approve_web3_extended.py
```

Este script testa **12+ endpoints diferentes** incluindo opções mais confiáveis.

Se ainda falhar, veja outras opções abaixo.

---

## ✅ SOLUÇÃO 2: Usar Infura ou Alchemy (Recomendado)

**Passo 1:** Criar conta grátis
- Infura: https://infura.io (clique em "Start for Free")
- Alchemy: https://alchemy.com (clique em "Get Started")

**Passo 2:** Pegar API Key
- No Infura: Create New Key → escolha "Polygon" → copie a key
- No Alchemy: Create App → escolha "Polygon" → copie a key

**Passo 3:** Configurar variável de ambiente

**Windows:**
```bash
set INFURA_API_KEY=sua_key_aqui
python approve_web3_extended.py
```

**Linux/Mac:**
```bash
export INFURA_API_KEY=sua_key_aqui
python approve_web3_extended.py
```

**OU coloque direto no script:**
Edite `approve_web3_extended.py`, linha ~60:
```python
infura_key = "SUA_KEY_AQUI"  # Substitua isso
```

---

## ✅ SOLUÇÃO 3: Aprovar Manualmente via MetaMask (MAIS FÁCIL)

**Execute:**
```bash
python approve_manual_instructions.py
```

Este script vai:
1. Mostrar sua private_key
2. Dar instruções passo-a-passo de como importar no MetaMask
3. Ensinar a aprovar via Polygonscan
4. Salvar tudo em um arquivo `allowances_manual_info.txt`

### Resumo Rápido:

1. **Importar a private_key no MetaMask:**
   - MetaMask → Importar Conta
   - Cole a private_key do `config.json`

2. **Fazer uma compra manual no Polymarket:**
   - Acesse https://polymarket.com
   - Conecte com a carteira importada
   - Faça UMA compra (qualquer valor pequeno, tipo $1)
   - MetaMask vai pedir para aprovar → **confirme!**

3. **Pronto!**
   - Os allowances foram aprovados
   - Agora o bot funciona normalmente

---

## ✅ SOLUÇÃO 4: VPN

Se sua região/ISP está bloqueando:
1. Conecte uma VPN (Proton VPN, Windscribe, etc)
2. Escolha servidor em EUA ou Europa
3. Rode novamente:
   ```bash
   python approve_web3_direct.py
   ```

---

## 🎯 QUAL SOLUÇÃO ESCOLHER?

| Solução | Dificuldade | Tempo | Recomendo? |
|---------|-------------|-------|------------|
| **Script Estendido** | Fácil | 2 min | ✅ Tente primeiro |
| **Infura/Alchemy** | Média | 5 min | ✅✅ Mais confiável |
| **Manual (MetaMask)** | Fácil | 3 min | ✅✅✅ **MAIS FÁCIL** |
| **VPN** | Fácil | 2 min | ✅ Se outros falharem |

## 💡 MINHA RECOMENDAÇÃO

**Faça via MetaMask (Solução 3):**

É o método mais simples e rápido:

```bash
python approve_manual_instructions.py
```

Siga as instruções, faça UMA compra manual pequena no Polymarket, e pronto! 🎉

---

## 📝 Arquivos Incluídos

| Arquivo | O Que Faz |
|---------|-----------|
| `approve_web3_extended.py` | Tenta 12+ RPCs |
| `approve_manual_instructions.py` | Gera instruções passo-a-passo |
| `SOLUCAO_PROBLEMA_RPC.md` | Este guia |

---

## 🚀 Depois de Aprovar

Rode o bot normalmente:
```bash
python main.py
```

Se ainda falhar, o problema NÃO é allowance. Verifique:
- A carteira tem USDC? (https://polygonscan.com/address/0xB1c313bCb3Cf20129819E24cbB1941B519D5D2A3)
- É a mesma carteira que você usa no Polymarket?

---

## ❓ Dúvidas?

Execute:
```bash
python verificar_carteira.py
```

Para ver informações da carteira do bot.

Qualquer problema, me manda print! 💪
