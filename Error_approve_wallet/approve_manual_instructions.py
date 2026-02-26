"""
Gera instruções para aprovar allowances MANUALMENTE via MetaMask
Use isso se não conseguir conectar aos RPCs da Polygon

Este script NÃO faz transações - só mostra como fazer você mesmo
"""
import sys
from pathlib import Path


# Polygon USDC
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Polymarket Exchanges
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# Max uint256
MAX_UINT256 = "115792089237316195423570985008687907853269984665640564039457584007913129639935"


def find_config():
    """Encontra config.json"""
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
        if config_path.exists():
            return config_path
    
    search_paths = [
        Path("config.json"),
        Path("../config.json"),
        Path(__file__).parent / "config.json",
        Path(__file__).parent.parent / "config.json",
    ]
    
    for path in search_paths:
        if path.exists():
            return path.resolve()
    
    return None


def load_config(config_path):
    """Carrega config.json"""
    import json
    with open(config_path) as f:
        return json.load(f)


def main():
    print("="*70)
    print("📝 INSTRUÇÕES PARA APROVAR ALLOWANCES MANUALMENTE")
    print("="*70)
    print()
    
    # Carregar config
    print("📋 Procurando configuração...")
    config_path = find_config()
    
    if not config_path:
        print("❌ config.json não encontrado!")
        return
    
    print(f"✅ Encontrado: {config_path}")
    print()
    
    config = load_config(config_path)
    private_key = config.get("private_key", "")
    
    if not private_key:
        print("❌ private_key não configurada no config.json")
        return
    
    # Derivar endereço
    from eth_account import Account
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key
    
    account = Account.from_key(private_key)
    wallet_address = account.address
    
    print(f"✅ Carteira do bot: {wallet_address}")
    print()
    
    print("="*70)
    print("🎯 COMO APROVAR VIA METAMASK")
    print("="*70)
    print()
    
    print("⚠️  IMPORTANTE: Você precisa importar a private_key no MetaMask primeiro")
    print()
    
    print("=" * 70)
    print("PASSO 1: Importar a private_key no MetaMask")
    print("=" * 70)
    print()
    print("1. Abra o MetaMask")
    print("2. Clique no ícone de perfil > Importar conta")
    print("3. Cole a private_key:")
    print(f"   {private_key}")
    print("4. Confirme e a conta será importada")
    print()
    print("⚠️  CUIDADO: Não compartilhe essa private key com ninguém!")
    print()
    
    print("=" * 70)
    print("PASSO 2: Aprovar Exchange Normal")
    print("=" * 70)
    print()
    print("1. Acesse: https://polygonscan.com/address/" + USDC_ADDRESS + "#writeContract")
    print()
    print("2. Clique em 'Connect to Web3' e conecte sua carteira")
    print()
    print("3. Encontre a função 'approve' e preencha:")
    print(f"   spender: {EXCHANGE_ADDRESS}")
    print(f"   value: {MAX_UINT256}")
    print()
    print("4. Clique em 'Write' e confirme a transação no MetaMask")
    print()
    print("5. Aguarde a confirmação (~30 segundos)")
    print()
    
    print("=" * 70)
    print("PASSO 3: Aprovar NegRisk Exchange")
    print("=" * 70)
    print()
    print("1. Ainda em https://polygonscan.com/address/" + USDC_ADDRESS + "#writeContract")
    print()
    print("2. Na função 'approve' novamente, preencha:")
    print(f"   spender: {NEG_RISK_EXCHANGE}")
    print(f"   value: {MAX_UINT256}")
    print()
    print("3. Clique em 'Write' e confirme a transação no MetaMask")
    print()
    print("4. Aguarde a confirmação (~30 segundos)")
    print()
    
    print("=" * 70)
    print("✅ PRONTO!")
    print("=" * 70)
    print()
    print("Depois de fazer os 2 approves acima, você pode rodar:")
    print("   python main.py")
    print()
    print("=" * 70)
    print("💡 ALTERNATIVA: Usar o site do Polymarket")
    print("=" * 70)
    print()
    print("Se preferir, você pode:")
    print()
    print("1. Importar a private_key no MetaMask (como acima)")
    print("2. Conectar no site do Polymarket: https://polymarket.com")
    print("3. Fazer UMA compra manual (qualquer valor pequeno)")
    print("4. O MetaMask vai pedir pra aprovar - confirme!")
    print("5. Pronto! Os allowances ficarão aprovados")
    print()
    print("Depois disso o bot vai funcionar normalmente.")
    print()
    
    # Gerar arquivo com as infos
    output_file = "allowances_manual_info.txt"
    with open(output_file, "w") as f:
        f.write("="*70 + "\n")
        f.write("INFORMAÇÕES PARA APROVAR ALLOWANCES MANUALMENTE\n")
        f.write("="*70 + "\n\n")
        f.write(f"Carteira: {wallet_address}\n")
        f.write(f"Private Key: {private_key}\n\n")
        f.write("USDC Contract: " + USDC_ADDRESS + "\n\n")
        f.write("Para aprovar, acesse:\n")
        f.write("https://polygonscan.com/address/" + USDC_ADDRESS + "#writeContract\n\n")
        f.write("=" * 70 + "\n")
        f.write("APPROVE #1 - Exchange Normal\n")
        f.write("=" * 70 + "\n")
        f.write(f"spender: {EXCHANGE_ADDRESS}\n")
        f.write(f"value: {MAX_UINT256}\n\n")
        f.write("=" * 70 + "\n")
        f.write("APPROVE #2 - NegRisk Exchange\n")
        f.write("=" * 70 + "\n")
        f.write(f"spender: {NEG_RISK_EXCHANGE}\n")
        f.write(f"value: {MAX_UINT256}\n\n")
    
    print(f"💾 Informações salvas em: {output_file}")
    print()


if __name__ == "__main__":
    main()
